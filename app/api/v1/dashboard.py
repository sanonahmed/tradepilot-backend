import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.api.deps import get_current_user
from app.core.security import decrypt_secret
from app.services.binance.client import BinanceSpotClient
from app.models.entities import (
    User, ExchangeConnection, TradingBot, Order, RiskLimit,
    RiskEvent, AuditLog, PortfolioSnapshot
)
from app.schemas.dashboard import (
    DashboardSummaryResponse, PositionInfo, AssetHoldingInfo, OpenOrderInfo
)
from app.schemas.bots import TradingBotResponse, BotConfigResponse
from app.api.v1.bots import _map_bot_response, _fetch_live_marked_prices
from app.schemas.common import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=APIResponse[DashboardSummaryResponse])
async def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Exchange connection status
    stmt_conn = select(ExchangeConnection).where(
        ExchangeConnection.user_id == current_user.id,
        ExchangeConnection.exchange == "binance",
        ExchangeConnection.is_active == True
    ).order_by(ExchangeConnection.last_synced_at.desc())
    result_conn = await db.execute(stmt_conn)
    conn = result_conn.scalars().first()

    binance_connected = conn is not None
    connection_mode = "demo" if (conn and conn.is_testnet) else ("live" if conn else "demo")
    is_real_balance = False
    account_error: Optional[str] = None
    holdings: List[AssetHoldingInfo] = []

    # 2. Risk Limits & Kill switch
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()
    kill_switch_active = risk_limit.kill_switch_active if risk_limit else False

    # 3. Active Bots
    stmt_bots = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(
        TradingBot.user_id == current_user.id
    )
    result_bots = await db.execute(stmt_bots)
    all_bots = result_bots.scalars().all()

    active_bots = [b for b in all_bots if b.status == "active"]
    active_bots_count = len(active_bots)

    # 4. Open Orders (Database fallback)
    stmt_orders = select(Order).where(
        Order.user_id == current_user.id,
        Order.status.in_(["NEW", "PARTIALLY_FILLED"])
    )
    result_orders = await db.execute(stmt_orders)
    db_open_orders = result_orders.scalars().all()
    open_orders_count = len(db_open_orders)
    live_open_orders_info: List[OpenOrderInfo] = []

    total_equity = Decimal("0.0")
    available_balance = Decimal("0.0")
    allocated_balance = Decimal("0.0")
    unrealized_pnl = Decimal("0.0")
    realized_pnl_24h = Decimal("0.0")
    total_realized_pnl = Decimal("0.0")
    crypto_holdings_value = Decimal("0.0")
    live_positions_details: Dict[str, Dict[str, Any]] = {}

    # 5. Fetch Real Balances, PnL & Open Orders from Binance
    if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
        try:
            plain_key = decrypt_secret(conn.api_key_encrypted)
            plain_secret = decrypt_secret(conn.api_secret_encrypted)
            client = BinanceSpotClient(
                api_key=plain_key,
                api_secret=plain_secret,
                is_testnet=conn.is_testnet
            )

            success, equity_data, err = await client.calculate_real_portfolio_equity()
            if success:
                # Auto-sync connection environment if client detected Live vs Testnet
                if conn.is_testnet != client.is_testnet:
                    conn.is_testnet = client.is_testnet

                is_real_balance = True
                connection_mode = "demo" if conn.is_testnet else "live"
                total_equity = equity_data["total_equity_usdt"]
                available_balance = equity_data["available_balance_usdt"]
                unrealized_pnl = equity_data.get("unrealized_pnl_usdt", Decimal("0.0"))
                crypto_holdings_value = equity_data.get("crypto_holdings_value_usdt", Decimal("0.0"))

                # Official Realized PnL from Binance income / trades
                try:
                    pnl_24h, total_pnl = await client.get_realized_pnl()
                    realized_pnl_24h = pnl_24h
                    total_realized_pnl = total_pnl
                except Exception as r_err:
                    logger.warning(f"Failed to fetch realized PnL in dashboard: {r_err}")

                # Live Open Orders directly from Binance API
                try:
                    binance_open_orders = await client.get_open_orders()
                    live_open_orders_info = [
                        OpenOrderInfo(
                            order_id=o["order_id"],
                            client_order_id=o.get("client_order_id"),
                            symbol=o["symbol"],
                            token=o["token"],
                            side=o["side"],
                            order_type=o["order_type"],
                            price=o["price"],
                            stop_price=o.get("stop_price"),
                            orig_qty=o["orig_qty"],
                            executed_qty=o["executed_qty"],
                            status=o["status"],
                            time=o["time"],
                            reduce_only=o.get("reduce_only", False)
                        )
                        for o in binance_open_orders
                    ]
                    open_orders_count = len(live_open_orders_info)
                except Exception as o_err:
                    logger.warning(f"Failed to fetch open orders in dashboard: {o_err}")

                # Synchronize open positions with Binance
                try:
                    live_positions_details = await client.get_live_positions_details()
                    open_pos = {s: p["position_amt"] for s, p in live_positions_details.items()}
                    cleaned_active = []
                    now_utc = datetime.now(timezone.utc)
                    for b in active_bots:
                        cfg = b.configuration
                        params = cfg.strategy_params if (cfg and cfg.strategy_params) else {}
                        
                        # Grace period: do not mark brand new bots completed while Binance settles
                        bot_created = b.created_at
                        if bot_created:
                            if bot_created.tzinfo is None:
                                bot_created = bot_created.replace(tzinfo=timezone.utc)
                            age_sec = (now_utc - bot_created).total_seconds()
                        else:
                            age_sec = 999999.0

                        if age_sec < 60.0:
                            cleaned_active.append(b)
                            continue

                        if not params.get("binance_executed") or b.symbol not in open_pos:
                            b.status = "completed"
                            db.add(b)
                        else:
                            cleaned_active.append(b)
                    active_bots = cleaned_active
                    active_bots_count = len(active_bots)
                except Exception as p_err:
                    logger.warning(f"Could not sync open positions in dashboard: {p_err}")

                # Accurate allocated budget: bot allocation or exchange limit orders
                bot_allocated = sum((Decimal(str(b.allocated_budget)) for b in active_bots), Decimal("0.0"))
                locked_orders = equity_data.get("locked_in_orders_usdt", Decimal("0.0"))
                allocated_balance = max(bot_allocated, locked_orders)
                
                # Format asset holdings
                holdings = [
                    AssetHoldingInfo(
                        asset=h["asset"],
                        free=h["free"],
                        locked=h["locked"],
                        total=h["total"],
                        unit_price_usdt=h["unit_price_usdt"],
                        value_usdt=h["value_usdt"]
                    )
                    for h in equity_data.get("holdings", [])
                ]

                # Update timestamp on connection
                conn.last_synced_at = datetime.now(timezone.utc)
                conn.can_trade = equity_data.get("can_trade", True)

                # Persist real portfolio snapshot
                snapshot = PortfolioSnapshot(
                    user_id=current_user.id,
                    mode=connection_mode,
                    total_equity_usdt=total_equity,
                    available_balance_usdt=available_balance,
                    allocated_balance_usdt=allocated_balance,
                    unrealized_pnl_usdt=unrealized_pnl,
                    realized_pnl_24h_usdt=realized_pnl_24h,
                    positions_json=[h.model_dump() for h in holdings]
                )
                db.add(snapshot)
                await db.commit()

            else:
                account_error = err
                logger.warning(f"Failed to fetch Binance account balance: {err}")
        except Exception as e:
            account_error = f"Error syncing Binance balances: {str(e)}"
            logger.error(account_error, exc_info=True)

    # 6. Fallback when keys are not active or not yet configured (No fake paper balances)
    if not is_real_balance:
        connection_mode = "demo" if (conn and conn.is_testnet) else ("live" if conn else "demo")
        total_equity = Decimal("0.00")
        available_balance = Decimal("0.00")
        allocated_balance = Decimal("0.00")
        unrealized_pnl = Decimal("0.00")
        realized_pnl_24h = Decimal("0.00")
        total_realized_pnl = Decimal("0.00")
        if not conn:
            account_error = "No Binance connection. Configure your Binance Demo (Testnet) or Live API keys in Settings."

    # Risk utilization = allocated_balance / total_equity * 100
    risk_utilization_pct = Decimal("0.0")
    if total_equity > Decimal("0.0"):
        risk_utilization_pct = (allocated_balance / total_equity) * Decimal("100.0")

    # Fetch live marked prices for active bots
    live_prices: Dict[str, Decimal] = {}
    if active_bots:
        active_syms = [b.symbol for b in active_bots]
        is_testnet = conn.is_testnet if conn else True
        live_prices = await _fetch_live_marked_prices(active_syms, is_testnet=is_testnet)

    active_bots_response = [
        _map_bot_response(
            b,
            live_price=live_prices.get(b.symbol),
            live_pos_detail=live_positions_details.get(b.symbol)
        )
        for b in active_bots
    ]

    summary = DashboardSummaryResponse(
        binance_connected=binance_connected,
        connection_mode=connection_mode,
        is_real_balance=is_real_balance,
        account_error=account_error,
        total_equity_usdt=round(total_equity, 2),
        available_balance_usdt=round(available_balance, 2),
        allocated_balance_usdt=round(allocated_balance, 2),
        realized_pnl_24h=round(realized_pnl_24h, 2),
        total_realized_pnl=round(total_realized_pnl, 2),
        unrealized_pnl=round(unrealized_pnl, 2),
        crypto_holdings_value_usdt=round(crypto_holdings_value, 2),
        active_bots_count=active_bots_count,
        open_orders_count=open_orders_count,
        risk_utilization_pct=round(risk_utilization_pct, 2),
        kill_switch_active=kill_switch_active,
        active_bots=active_bots_response,
        open_positions=[],
        open_orders=live_open_orders_info,
        holdings=holdings,
        recent_performance_pct=Decimal("0.0")
    )

    return APIResponse(success=True, data=summary)


@router.post("/emergency-stop", response_model=APIResponse[dict])
async def emergency_kill_switch(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()
    if not risk_limit:
        risk_limit = RiskLimit(user_id=current_user.id)
        db.add(risk_limit)
    risk_limit.kill_switch_active = True

    stmt_bots = select(TradingBot).where(
        TradingBot.user_id == current_user.id,
        TradingBot.status == "active"
    )
    result_bots = await db.execute(stmt_bots)
    active_bots = result_bots.scalars().all()
    paused_bot_count = len(active_bots)
    for bot in active_bots:
        bot.status = "paused"

    stmt_orders = select(Order).where(
        Order.user_id == current_user.id,
        Order.status.in_(["NEW", "PARTIALLY_FILLED"])
    )
    result_orders = await db.execute(stmt_orders)
    open_orders = result_orders.scalars().all()
    canceled_order_count = len(open_orders)
    for ord_obj in open_orders:
        ord_obj.status = "CANCELED"

    risk_event = RiskEvent(
        user_id=current_user.id,
        rule_violated="GLOBAL_KILL_SWITCH_TRIGGERED",
        context_data={"paused_bots": paused_bot_count, "canceled_orders": canceled_order_count},
        action_taken="KILL_SWITCH"
    )
    db.add(risk_event)

    audit = AuditLog(
        user_id=current_user.id,
        action="EMERGENCY_KILL_SWITCH_ACTIVATED",
        resource_type="RISK",
        resource_id=current_user.id,
        details={"paused_bots": paused_bot_count, "canceled_orders": canceled_order_count}
    )
    db.add(audit)

    await db.commit()

    return APIResponse(
        success=True,
        message="EMERGENCY STOP EXECUTED: All active bots paused and open orders canceled.",
        data={
            "kill_switch_active": True,
            "paused_bots": paused_bot_count,
            "canceled_orders": canceled_order_count
        }
    )


@router.post("/emergency-reset", response_model=APIResponse[dict])
async def emergency_reset(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()
    if risk_limit:
        risk_limit.kill_switch_active = False

    audit = AuditLog(
        user_id=current_user.id,
        action="EMERGENCY_KILL_SWITCH_RESET",
        resource_type="RISK",
        resource_id=current_user.id
    )
    db.add(audit)
    await db.commit()

    return APIResponse(
        success=True,
        message="Kill switch reset. Bots remain paused for safety and can be resumed individually.",
        data={"kill_switch_active": False}
    )
