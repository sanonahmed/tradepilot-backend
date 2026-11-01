import time
import logging
import asyncio
import httpx
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.core.database import get_db
from app.api.deps import get_current_user
from app.core.security import decrypt_secret
from app.services.binance.client import BinanceSpotClient
from app.models.entities import User, TradingBot, BotConfiguration, RiskLimit, AuditLog, ExchangeConnection, Order
from app.schemas.bots import (
    TradingBotCreate, TradingBotUpdate, TradingBotResponse, BotConfigResponse, ScannerTradeRequest
)
from app.schemas.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots", tags=["Trading Bots"])


async def _fetch_live_marked_prices(symbols: List[str], is_testnet: bool = True) -> Dict[str, Decimal]:
    """
    Fetches real-time marked prices for all active trading symbols simultaneously.
    Uses Binance /premiumIndex on Futures and /ticker/price on Spot for sub-second live mark price precision.
    """
    prices: Dict[str, Decimal] = {}
    if not symbols:
        return prices

    unique_symbols = list(set(symbols))
    base_url = "https://testnet.binancefuture.com" if is_testnet else "https://api.binance.com"
    path = "/fapi/v1/premiumIndex" if is_testnet else "/api/v3/ticker/price"

    async with httpx.AsyncClient(timeout=3.5) as client:
        async def fetch_one(s: str):
            try:
                res = await client.get(f"{base_url}{path}?symbol={s}", headers={"User-Agent": "TradePilotAI/1.0"})
                if res.status_code == 200:
                    data = res.json()
                    val = data.get("markPrice") or data.get("price")
                    if val:
                        prices[s] = Decimal(str(val))
            except Exception:
                pass

        await asyncio.gather(*(fetch_one(s) for s in unique_symbols), return_exceptions=True)

    # For any symbols that missed, try fallback
    missing = [s for s in unique_symbols if s not in prices]
    if missing:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get("https://api.binance.com/api/v3/ticker/price")
                if res.status_code == 200:
                    for item in res.json():
                        sym = item.get("symbol")
                        if sym in missing and item.get("price"):
                            prices[sym] = Decimal(str(item["price"]))
        except Exception:
            pass

    return prices


def _map_bot_response(
    bot: TradingBot,
    live_price: Optional[Decimal] = None,
    live_pos_detail: Optional[Dict[str, Any]] = None
) -> TradingBotResponse:
    config_resp = None
    if bot.configuration:
        config_resp = BotConfigResponse.model_validate(bot.configuration)

    params = bot.configuration.strategy_params if (bot.configuration and bot.configuration.strategy_params) else {}

    # 1. Token & Binance Order ID
    token = bot.symbol.replace("USDT", "")
    binance_order_id = str(params.get("binance_order_id") or "")
    if binance_order_id in ("None", "null", ""):
        binance_order_id = None

    # 2. Entry Price
    entry_price = None
    if params.get("entry_price") is not None:
        try:
            entry_price = Decimal(str(params["entry_price"]))
        except Exception:
            pass

    # 3. Trade Size USDT & Executed Quantity
    trade_size_usdt = Decimal(str(params.get("allocated_trade_usdt") or bot.allocated_budget or "1000.0"))
    executed_qty = None
    if params.get("executed_quantity") is not None:
        try:
            executed_qty = Decimal(str(params["executed_quantity"]))
        except Exception:
            pass
    if (not executed_qty or executed_qty <= Decimal("0")) and entry_price and entry_price > Decimal("0"):
        executed_qty = round(trade_size_usdt / entry_price, 4)

    # 4. Live Marked Price
    marked_price = live_price
    if not marked_price and params.get("current_price"):
        try:
            marked_price = Decimal(str(params["current_price"]))
        except Exception:
            pass
    if not marked_price:
        marked_price = entry_price

    # 5. Live Unrealized PnL ($ USDT and %)
    unrealized_pnl_usdt = Decimal("0.0")
    unrealized_pnl_pct = Decimal("0.0")

    # If official Binance live position exists, use official Binance fields directly!
    if live_pos_detail:
        if live_pos_detail.get("entry_price") and live_pos_detail["entry_price"] > Decimal("0"):
            entry_price = live_pos_detail["entry_price"]
        if live_pos_detail.get("mark_price") and live_pos_detail["mark_price"] > Decimal("0"):
            marked_price = live_pos_detail["mark_price"]
        if live_pos_detail.get("position_amt"):
            executed_qty = abs(live_pos_detail["position_amt"])
        if live_pos_detail.get("notional") and abs(live_pos_detail["notional"]) > Decimal("0"):
            trade_size_usdt = abs(live_pos_detail["notional"])
        elif executed_qty and marked_price:
            trade_size_usdt = round(executed_qty * marked_price, 2)

        raw_pnl = Decimal(str(live_pos_detail.get("unrealized_pnl_usdt", "0.0")))
        unrealized_pnl_usdt = round(raw_pnl, 4) if abs(raw_pnl) < Decimal("1.0") else round(raw_pnl, 2)
        unrealized_pnl_pct = Decimal(str(live_pos_detail.get("roe_pct", "0.0")))
    else:
        # Fallback calculation if exchange connection is offline
        if entry_price and marked_price and entry_price > Decimal("0"):
            unrealized_pnl_pct = round(((marked_price - entry_price) / entry_price) * Decimal("100.0"), 2)
            if executed_qty and executed_qty > Decimal("0"):
                unrealized_pnl_usdt = round((marked_price - entry_price) * executed_qty, 2)
            else:
                unrealized_pnl_usdt = round(trade_size_usdt * (unrealized_pnl_pct / Decimal("100.0")), 2)

    # 6. TP & SL Target Prices
    tp_price = None
    if params.get("take_profit_price") is not None:
        try:
            tp_price = Decimal(str(params["take_profit_price"]))
        except Exception:
            pass
    sl_price = None
    if params.get("stop_loss_price") is not None:
        try:
            sl_price = Decimal(str(params["stop_loss_price"]))
        except Exception:
            pass

    return TradingBotResponse(
        id=bot.id,
        user_id=bot.user_id,
        name=bot.name,
        symbol=bot.symbol,
        token=token,
        timeframe=bot.timeframe,
        operating_mode=bot.operating_mode,
        status=bot.status,
        allocated_budget=bot.allocated_budget,
        risk_percentage=bot.risk_percentage,
        max_daily_trades=bot.max_daily_trades,
        trades_count_today=bot.trades_count_today,
        last_active_at=bot.last_active_at,
        created_at=bot.created_at,
        updated_at=bot.updated_at,
        configuration=config_resp,
        trade_size_usdt=round(trade_size_usdt, 2),
        executed_qty=round(executed_qty, 4) if executed_qty else None,
        entry_price=round(entry_price, 6) if entry_price else None,
        marked_price=round(marked_price, 6) if marked_price else None,
        unrealized_pnl_usdt=unrealized_pnl_usdt,
        unrealized_pnl_pct=unrealized_pnl_pct,
        take_profit_price=round(tp_price, 6) if tp_price else None,
        stop_loss_price=round(sl_price, 6) if sl_price else None,
        binance_order_id=binance_order_id
    )


@router.get("", response_model=APIResponse[List[TradingBotResponse]])
async def list_bots(
    include_closed: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Lists trading bots. If a trade is closed from the app or from Binance,
    it is automatically synced and NOT displayed in the active bots list.
    """
    # Check Binance real open positions to sync trades closed on Binance directly
    stmt_conn = select(ExchangeConnection).where(
        ExchangeConnection.is_active == True
    ).order_by(ExchangeConnection.last_synced_at.desc())
    res_c = await db.execute(stmt_conn)
    conn = res_c.scalars().first()

    live_positions_details: Dict[str, Dict[str, Any]] = {}
    if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
        try:
            plain_key = decrypt_secret(conn.api_key_encrypted)
            plain_secret = decrypt_secret(conn.api_secret_encrypted)
            binance_client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)
            live_positions_details = await binance_client.get_live_positions_details()
        except Exception as e:
            logger.warning(f"Could not sync open positions from Binance in list_bots: {e}")

    open_binance_positions = {s: p["position_amt"] for s, p in live_positions_details.items()} if live_positions_details else {}

    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.user_id == current_user.id).order_by(TradingBot.created_at.desc())
    result = await db.execute(stmt)
    all_bots = result.scalars().all()

    # Sync any bots whose trade has been closed on Binance
    has_changes = False
    now_utc = datetime.now(timezone.utc)
    for bot in all_bots:
        config = bot.configuration
        params = config.strategy_params if (config and config.strategy_params) else {}
        if bot.status == "active":
            # If bot was not executed on Binance or is legacy mock, retire it
            if not params.get("binance_executed"):
                bot.status = "stopped"
                has_changes = True
                continue

            # Calculate bot age for opening grace period
            bot_created = bot.created_at
            if bot_created:
                if bot_created.tzinfo is None:
                    bot_created = bot_created.replace(tzinfo=timezone.utc)
                age_sec = (now_utc - bot_created).total_seconds()
            else:
                age_sec = 999999.0

            # If we fetched positions from Binance and the symbol is NOT open on Binance:
            if live_positions_details is not None and bot.symbol not in open_binance_positions:
                # 60s Opening Grace Period: Never archive brand new bots while Binance fills/indexes the position
                if age_sec >= 60.0:
                    bot.status = "completed"
                    params["exit_reason"] = "CLOSED_ON_BINANCE"
                    params["exit_at"] = datetime.now(timezone.utc).isoformat()
                    config.strategy_params = params
                    has_changes = True
            elif bot.symbol in live_positions_details:
                # Update bot configuration with exact official entry and mark prices from Binance
                bp = live_positions_details[bot.symbol]
                if bp.get("entry_price") and bp["entry_price"] > Decimal("0"):
                    real_entry = bp["entry_price"]
                    params["entry_price"] = float(real_entry)
                    tp_pct = Decimal(str(params.get("take_profit_pct", "10.0")))
                    sl_pct = Decimal(str(params.get("stop_loss_pct", "5.0")))
                    params["take_profit_price"] = float(round(real_entry * (Decimal("1.0") + tp_pct / Decimal("100.0")), 6))
                    params["stop_loss_price"] = float(round(real_entry * (Decimal("1.0") - sl_pct / Decimal("100.0")), 6))
                if bp.get("mark_price") and bp["mark_price"] > Decimal("0"):
                    params["current_price"] = float(bp["mark_price"])
                    if bp.get("entry_price") and bp["entry_price"] > Decimal("0"):
                        pnl = ((bp["mark_price"] - bp["entry_price"]) / bp["entry_price"]) * Decimal("100.0")
                        params["current_pnl_pct"] = float(round(pnl, 2))
                if bp.get("position_amt"):
                    params["executed_quantity"] = float(abs(bp["position_amt"]))
                config.strategy_params = params
                has_changes = True

    # Auto-discover any active positions on Binance that don't have an active tracking bot yet!
    active_bot_symbols = {b.symbol for b in all_bots if b.status == "active"}
    for sym, bp in live_positions_details.items():
        pos_amt = abs(bp.get("position_amt", Decimal("0")))
        if sym not in active_bot_symbols and pos_amt > Decimal("0"):
            ep = bp.get("entry_price") or Decimal("1.0")
            notional = bp.get("notional") or (pos_amt * ep)
            new_tracking_bot = TradingBot(
                user_id=current_user.id,
                name=f"{sym.replace('USDT', '')} Alpha Scanner Bot",
                symbol=sym,
                timeframe="15m",
                operating_mode="live" if (conn and not conn.is_testnet) else "demo",
                status="active",
                allocated_budget=round(abs(notional), 2),
                risk_percentage=Decimal("0.5"),
                max_daily_trades=5
            )
            db.add(new_tracking_bot)
            await db.flush()
            new_conf = BotConfiguration(
                bot_id=new_tracking_bot.id,
                stop_loss_type="percentage",
                stop_loss_value=Decimal("2.5"),
                take_profit_type="percentage",
                take_profit_value=Decimal("5.0"),
                strategy_params={
                    "binance_executed": True,
                    "entry_price": float(ep),
                    "current_price": float(bp.get("mark_price") or ep),
                    "executed_quantity": float(pos_amt),
                    "allocated_trade_usdt": float(abs(notional)),
                    "take_profit_price": float(ep * Decimal("1.05")),
                    "stop_loss_price": float(ep * Decimal("0.975")),
                }
            )
            db.add(new_conf)
            new_tracking_bot.configuration = new_conf
            all_bots = list(all_bots) + [new_tracking_bot]
            has_changes = True

    if has_changes:
        await db.commit()

    # If trade is closed from app or Binance, don't show it in the bots list
    if not include_closed:
        visible_bots = [b for b in all_bots if b.status == "active"]
    else:
        visible_bots = all_bots

    # Fetch live marked prices for active bots
    live_prices: Dict[str, Decimal] = {}
    if visible_bots:
        active_syms = [b.symbol for b in visible_bots if b.status == "active"]
        is_testnet = conn.is_testnet if conn else True
        live_prices = await _fetch_live_marked_prices(active_syms, is_testnet=is_testnet)

    return APIResponse(
        success=True,
        data=[
            _map_bot_response(
                b,
                live_price=live_prices.get(b.symbol),
                live_pos_detail=live_positions_details.get(b.symbol)
            )
            for b in visible_bots
        ]
    )


@router.post("", response_model=APIResponse[TradingBotResponse], status_code=status.HTTP_201_CREATED)
async def create_bot(
    request: TradingBotCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Check Emergency Kill Switch if active
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()
    if risk_limit and risk_limit.kill_switch_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot create bot while Emergency Kill Switch is active. Reset kill switch first."
        )

    # Create Bot
    new_bot = TradingBot(
        user_id=current_user.id,
        name=request.name,
        symbol=request.symbol.upper(),
        timeframe=request.timeframe,
        operating_mode=request.operating_mode,
        status="paused",
        allocated_budget=request.allocated_budget,
        risk_percentage=request.risk_percentage,
        max_daily_trades=request.max_daily_trades
    )
    db.add(new_bot)
    await db.flush()

    # Create Bot Configuration
    bot_config = BotConfiguration(
        bot_id=new_bot.id,
        stop_loss_type=request.config.stop_loss_type,
        stop_loss_value=request.config.stop_loss_value,
        take_profit_type=request.config.take_profit_type,
        take_profit_value=request.config.take_profit_value,
        trailing_stop_enabled=request.config.trailing_stop_enabled,
        trailing_stop_callback=request.config.trailing_stop_callback,
        strategy_params=request.config.strategy_params
    )
    db.add(bot_config)

    # Audit log
    audit = AuditLog(
        user_id=current_user.id,
        action="CREATE_TRADING_BOT",
        resource_type="TRADING_BOT",
        resource_id=new_bot.id,
        details={"name": new_bot.name, "symbol": new_bot.symbol, "mode": new_bot.operating_mode}
    )
    db.add(audit)

    await db.commit()
    await db.refresh(new_bot)
    await db.refresh(bot_config)
    new_bot.configuration = bot_config

    return APIResponse(
        success=True,
        message="Trading bot created successfully (initial state: paused)",
        data=_map_bot_response(new_bot)
    )


@router.post("/from-scanner", response_model=APIResponse[TradingBotResponse], status_code=status.HTTP_201_CREATED)
async def create_bot_from_scanner(
    request: ScannerTradeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Deploy an automated trading bot directly from a high-probability Scanner setup.
    Initializes the bot with professional risk-to-reward ratio, trailing stop, and active execution state.
    """
    # 1. Check Emergency Kill Switch if active
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()

    if risk_limit and risk_limit.kill_switch_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot deploy bot while Emergency Kill Switch is active. Reset kill switch first."
        )

    # 2. Derive professional bot name from token & signal
    sym = request.symbol.upper()
    base_sym = sym.replace("USDT", "")
    sig_name = request.signal_type or "ALPHA"
    if "BREAKOUT" in sig_name:
        bot_name = f"{base_sym} Breakout Scalper"
    elif "DIP" in sig_name:
        bot_name = f"{base_sym} Rebound Sniper"
    else:
        bot_name = f"{base_sym} Alpha Scanner Bot"

    # 3. Create Bot directly in ACTIVE state
    now = datetime.now(timezone.utc)
    new_bot = TradingBot(
        user_id=current_user.id,
        name=bot_name,
        symbol=sym,
        timeframe=request.timeframe or "15m",
        operating_mode=request.operating_mode or "demo",
        status="active",
        allocated_budget=request.allocated_budget,
        risk_percentage=request.risk_percentage,
        max_daily_trades=8,
        trades_count_today=1,
        last_active_at=now
    )
    db.add(new_bot)
    await db.flush()

    # 4. Check for user's active Binance Exchange Connection and execute real order
    stmt_conn = select(ExchangeConnection).where(
        ExchangeConnection.user_id == current_user.id,
        ExchangeConnection.is_active == True
    )
    result_conn = await db.execute(stmt_conn)
    conn = result_conn.scalars().first()

    # Fallback to any active Binance connection in the database if user id differs
    if not conn:
        stmt_any = select(ExchangeConnection).where(
            ExchangeConnection.exchange == "binance",
            ExchangeConnection.is_active == True
        ).order_by(ExchangeConnection.last_synced_at.desc())
        result_any = await db.execute(stmt_any)
        conn = result_any.scalars().first()

    if not conn or not conn.api_key_encrypted or not conn.api_secret_encrypted:
        raise HTTPException(
            status_code=400,
            detail="Binance API is not connected! Please go to Settings and enter your Binance API Key and Secret first."
        )

    # Sizing & Exit Rules: Resolve user's configured settings for Capital Allocation %, Take Profit %, Stop Loss %
    trade_alloc_pct = Decimal("30.00")
    tp_pct = Decimal("10.00")
    sl_pct = Decimal("5.00")
    if risk_limit:
        trade_alloc_pct = getattr(risk_limit, "trade_allocation_pct", None) or Decimal("30.00")
        tp_pct = getattr(risk_limit, "take_profit_pct", None) or Decimal("10.00")
        sl_pct = getattr(risk_limit, "stop_loss_pct", None) or Decimal("5.00")

    # Request overrides if explicitly supplied
    if getattr(request, "capital_allocation_pct", None) is not None:
        trade_alloc_pct = request.capital_allocation_pct
    if request.take_profit_pct is not None:
        tp_pct = request.take_profit_pct
    if request.stop_loss_pct is not None:
        sl_pct = request.stop_loss_pct

    strategy_params = {
        "origin": "scanner",
        "opportunity_score": float(request.opportunity_score) if request.opportunity_score else 85.0,
        "signal_type": request.signal_type or "POTENTIAL_BUY",
        "entry_price": float(request.entry_price) if request.entry_price else None,
        "trade_allocation_pct": float(trade_alloc_pct),
        "take_profit_pct": float(tp_pct),
        "stop_loss_pct": float(sl_pct),
        "risk_reward_ratio": round(float(tp_pct) / float(sl_pct), 2) if sl_pct > 0 else 2.0
    }

    try:
        plain_key = decrypt_secret(conn.api_key_encrypted)
        plain_secret = decrypt_secret(conn.api_secret_encrypted)
        binance_client = BinanceSpotClient(
            api_key=plain_key,
            api_secret=plain_secret,
            is_testnet=conn.is_testnet
        )

        # Accurate Capital Allocation: calculate exact USDT size from real Binance portfolio balance
        # Example: $4,500 balance with 30% allocation = $1,350.00 USDT
        eq_ok, eq_data, _ = await binance_client.calculate_real_portfolio_equity()
        total_balance = Decimal("1000.00")
        if eq_ok and eq_data and eq_data.get("total_equity_usdt"):
            total_balance = Decimal(str(eq_data["total_equity_usdt"]))
            if total_balance <= Decimal("0"):
                total_balance = Decimal(str(eq_data.get("available_balance_usdt", "1000.0")))

        # Calculate exact trade allocation amount (e.g. 30% of $4,500 = $1,350 USDT)
        trade_amount = round(total_balance * (trade_alloc_pct / Decimal("100.0")), 2)
        if trade_amount < Decimal("15.0"):
            trade_amount = Decimal("25.00")

        new_bot.allocated_budget = trade_amount

        req_leverage = getattr(request, "leverage", None)
        order_ok, order_data, order_err = await binance_client.place_order(
            symbol=sym,
            side="BUY",
            order_type="MARKET",
            quote_order_qty=trade_amount,
            leverage=req_leverage
        )

        if not order_ok:
            raise HTTPException(
                status_code=400,
                detail=f"Binance rejected order for {sym}: {order_err}"
            )

        binance_order_id = str(order_data.get("orderId"))
        binance_status = str(order_data.get("status", "FILLED"))

        # Calculate exact fill price from Binance execution (Futures avgPrice, Spot fills, or quote/qty ratio)
        avg_p = Decimal(str(order_data.get("avgPrice", "0")))
        p_raw = Decimal(str(order_data.get("price", "0")))
        exec_qty = Decimal(str(order_data.get("executedQty", order_data.get("origQty", "0"))))
        cum_quote = Decimal(str(order_data.get("cumQuote", order_data.get("cummulativeQuoteQty", "0"))))
        fills = order_data.get("fills", [])

        fill_price = Decimal("0")
        if avg_p > Decimal("0"):
            fill_price = avg_p
        elif p_raw > Decimal("0"):
            fill_price = p_raw
        elif exec_qty > Decimal("0") and cum_quote > Decimal("0"):
            fill_price = cum_quote / exec_qty
        elif fills and isinstance(fills, list) and len(fills) > 0:
            fill_price = Decimal(str(fills[0].get("price", "0")))
        elif request.entry_price and request.entry_price > Decimal("0"):
            fill_price = request.entry_price
        
        if fill_price <= Decimal("0"):
            fill_price = Decimal("1.0")

        if exec_qty <= Decimal("0"):
            exec_qty = Decimal(str(order_data.get("origQty", "1.0")))

        tp_price = round(fill_price * (Decimal("1.0") + tp_pct / Decimal("100.0")), 6)
        sl_price = round(fill_price * (Decimal("1.0") - sl_pct / Decimal("100.0")), 6)

        strategy_params["binance_order_id"] = binance_order_id
        strategy_params["binance_status"] = binance_status
        strategy_params["binance_executed"] = True
        strategy_params["exchange_endpoint"] = binance_client.endpoint_type
        strategy_params["entry_price"] = float(fill_price)
        strategy_params["take_profit_price"] = float(tp_price)
        strategy_params["stop_loss_price"] = float(sl_price)
        strategy_params["executed_quantity"] = float(exec_qty)
        strategy_params["allocated_trade_usdt"] = float(trade_amount)

        # Create Order entity in PostgreSQL
        new_order = Order(
            user_id=current_user.id,
            bot_id=new_bot.id,
            client_order_id=order_data.get("clientOrderId", f"TP_{int(time.time())}_{sym[:4]}"),
            exchange_order_id=binance_order_id,
            symbol=sym,
            side="BUY",
            order_type="MARKET",
            mode="live" if not conn.is_testnet else "demo",
            status=binance_status,
            price=fill_price,
            quantity=exec_qty,
            executed_quantity=exec_qty,
            cumulative_quote_qty=Decimal(str(order_data.get("cumQuote", order_data.get("cummulativeQuoteQty", str(trade_amount)))))
        )
        db.add(new_order)

        trade_message = (
            f"✓ REAL BINANCE ORDER EXECUTED! Order #{binance_order_id} ({sym}) filled for ${trade_amount} USDT ({trade_alloc_pct}% allocation). "
            f"TP Target: +{tp_pct}% (${tp_price}) | SL Protection: -{sl_pct}% (${sl_price})."
        )
        new_bot.operating_mode = "live" if not conn.is_testnet else "demo"
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing trade on Binance for {sym}: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to execute Binance order for {sym}: {str(e)}"
        )

    bot_config = BotConfiguration(
        bot_id=new_bot.id,
        stop_loss_type="percentage",
        stop_loss_value=request.stop_loss_pct,
        take_profit_type="percentage",
        take_profit_value=request.take_profit_pct,
        trailing_stop_enabled=request.trailing_stop_enabled,
        trailing_stop_callback=request.trailing_stop_callback,
        strategy_params=strategy_params
    )
    db.add(bot_config)

    # 5. Audit log
    audit = AuditLog(
        user_id=current_user.id,
        action="DEPLOY_SCANNER_BOT",
        resource_type="TRADING_BOT",
        resource_id=new_bot.id,
        details={
            "name": new_bot.name,
            "symbol": new_bot.symbol,
            "signal": request.signal_type,
            "score": str(request.opportunity_score),
            "take_profit": str(request.take_profit_pct),
            "stop_loss": str(request.stop_loss_pct),
            "binance_order_id": binance_order_id,
            "binance_status": binance_status,
            "mode": new_bot.operating_mode
        }
    )
    db.add(audit)

    await db.commit()
    await db.refresh(new_bot)
    await db.refresh(bot_config)
    new_bot.configuration = bot_config

    return APIResponse(
        success=True,
        message=trade_message,
        data=_map_bot_response(new_bot)
    )


@router.get("/{bot_id}", response_model=APIResponse[TradingBotResponse])
async def get_bot(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.id == bot_id, TradingBot.user_id == current_user.id)
    result = await db.execute(stmt)
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    live_prices = await _fetch_live_marked_prices([bot.symbol])
    live_pos = None
    stmt_conn = select(ExchangeConnection).where(ExchangeConnection.is_active == True).order_by(ExchangeConnection.last_synced_at.desc())
    conn = (await db.execute(stmt_conn)).scalars().first()
    if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
        try:
            pk = decrypt_secret(conn.api_key_encrypted)
            ps = decrypt_secret(conn.api_secret_encrypted)
            binance_client = BinanceSpotClient(api_key=pk, api_secret=ps, is_testnet=conn.is_testnet)
            positions = await binance_client.get_live_positions_details()
            live_pos = positions.get(bot.symbol)
        except Exception:
            pass

    return APIResponse(success=True, data=_map_bot_response(bot, live_prices.get(bot.symbol), live_pos_detail=live_pos))


@router.post("/{bot_id}/start", response_model=APIResponse[TradingBotResponse])
async def start_bot(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt_risk = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result_risk = await db.execute(stmt_risk)
    risk_limit = result_risk.scalar_one_or_none()

    if risk_limit and risk_limit.kill_switch_active:
        raise HTTPException(
            status_code=400,
            detail="Cannot start bot while Emergency Kill Switch is active. Reset kill switch first."
        )

    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.id == bot_id, TradingBot.user_id == current_user.id)
    result = await db.execute(stmt)
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    bot.status = "active"
    bot.last_active_at = datetime.now(timezone.utc)

    # If the bot has not executed an entry order on Binance yet, execute it now!
    config = bot.configuration
    params = config.strategy_params if (config and config.strategy_params) else {}
    if not params.get("binance_executed"):
        stmt_conn = select(ExchangeConnection).where(
            ExchangeConnection.user_id == current_user.id,
            ExchangeConnection.is_active == True
        )
        res_conn = await db.execute(stmt_conn)
        conn = res_conn.scalars().first()
        if not conn:
            stmt_any = select(ExchangeConnection).where(
                ExchangeConnection.exchange == "binance",
                ExchangeConnection.is_active == True
            ).order_by(ExchangeConnection.last_synced_at.desc())
            res_any = await db.execute(stmt_any)
            conn = res_any.scalars().first()

        if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
            try:
                plain_key = decrypt_secret(conn.api_key_encrypted)
                plain_secret = decrypt_secret(conn.api_secret_encrypted)
                client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)
                trade_amt = Decimal("25.00")
                if bot.allocated_budget >= Decimal("100.0"):
                    trade_amt = min(bot.allocated_budget * Decimal("0.10"), Decimal("100.00"))
                bot_lev = params.get("leverage") if params else None
                order_ok, order_data, order_err = await client.place_order(
                    symbol=bot.symbol,
                    side="BUY",
                    order_type="MARKET",
                    quote_order_qty=trade_amt,
                    leverage=bot_lev
                )
                if order_ok:
                    oid = str(order_data.get("orderId"))
                    avg_p = Decimal(str(order_data.get("avgPrice", "0")))
                    p_raw = Decimal(str(order_data.get("price", "0")))
                    exec_q = Decimal(str(order_data.get("executedQty", order_data.get("origQty", "0"))))
                    cum_q = Decimal(str(order_data.get("cumQuote", order_data.get("cummulativeQuoteQty", "0"))))
                    f_price = avg_p if avg_p > 0 else (p_raw if p_raw > 0 else ((cum_q / exec_q) if exec_q > 0 and cum_q > 0 else Decimal("0")))

                    params["binance_order_id"] = oid
                    params["binance_status"] = str(order_data.get("status", "FILLED"))
                    params["binance_executed"] = True
                    params["exchange_endpoint"] = client.endpoint_type
                    if f_price > 0:
                        params["entry_price"] = float(f_price)
                    if config:
                        config.strategy_params = params
                    new_order = Order(
                        user_id=current_user.id,
                        bot_id=bot.id,
                        client_order_id=order_data.get("clientOrderId", f"TP_{int(time.time())}_{bot.symbol[:4]}"),
                        exchange_order_id=oid,
                        symbol=bot.symbol,
                        side="BUY",
                        order_type="MARKET",
                        mode="live" if not conn.is_testnet else "demo",
                        status=params["binance_status"],
                        price=f_price if f_price > 0 else Decimal(str(order_data.get("price", "0"))),
                        quantity=Decimal(str(order_data.get("origQty", order_data.get("executedQty", "1.0")))),
                        executed_quantity=exec_q if exec_q > 0 else Decimal("1.0"),
                        cumulative_quote_qty=cum_q if cum_q > 0 else trade_amt
                    )
                    db.add(new_order)
                    bot.operating_mode = "live" if not conn.is_testnet else "demo"
                else:
                    raise HTTPException(status_code=400, detail=f"Binance rejected order for {bot.symbol}: {order_err}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to place Binance order for {bot.symbol}: {str(e)}")

    audit = AuditLog(
        user_id=current_user.id,
        action="START_TRADING_BOT",
        resource_type="TRADING_BOT",
        resource_id=bot.id
    )
    db.add(audit)
    await db.commit()
    await db.refresh(bot)

    return APIResponse(success=True, message=f"Bot '{bot.name}' started", data=_map_bot_response(bot))


@router.post("/{bot_id}/pause", response_model=APIResponse[TradingBotResponse])
async def pause_bot(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.id == bot_id, TradingBot.user_id == current_user.id)
    result = await db.execute(stmt)
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    bot.status = "paused"

    audit = AuditLog(
        user_id=current_user.id,
        action="PAUSE_TRADING_BOT",
        resource_type="TRADING_BOT",
        resource_id=bot.id
    )
    db.add(audit)
    await db.commit()
    await db.refresh(bot)

    return APIResponse(success=True, message=f"Bot '{bot.name}' paused", data=_map_bot_response(bot))


@router.post("/{bot_id}/stop", response_model=APIResponse[TradingBotResponse])
async def stop_bot(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.id == bot_id, TradingBot.user_id == current_user.id)
    result = await db.execute(stmt)
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    bot.status = "stopped"

    audit = AuditLog(
        user_id=current_user.id,
        action="STOP_TRADING_BOT",
        resource_type="TRADING_BOT",
        resource_id=bot.id
    )
    db.add(audit)
    await db.commit()
    await db.refresh(bot)

    return APIResponse(success=True, message=f"Bot '{bot.name}' stopped", data=_map_bot_response(bot))


@router.delete("/{bot_id}", response_model=APIResponse[dict])
async def delete_bot(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(TradingBot).where(TradingBot.id == bot_id, TradingBot.user_id == current_user.id)
    result = await db.execute(stmt)
    bot = result.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    if bot.status == "active":
        raise HTTPException(status_code=400, detail="Cannot delete an active bot. Pause or stop it first.")

    bot_name = bot.name
    await db.delete(bot)

    audit = AuditLog(
        user_id=current_user.id,
        action="DELETE_TRADING_BOT",
        resource_type="TRADING_BOT",
        resource_id=bot_id,
        details={"name": bot_name}
    )
    db.add(audit)
    await db.commit()

    return APIResponse(success=True, message=f"Bot '{bot_name}' deleted successfully", data={"id": bot_id})


@router.post("/{bot_id}/close", response_model=APIResponse[TradingBotResponse])
async def close_bot_endpoint(
    bot_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually closes an active bot's position immediately on Binance API (Market Sell),
    locking in the current profit/loss and recording the closing order.
    """
    from app.services.trading.exit_monitor import close_bot_position
    success, msg, data = await close_bot_position(bot_id, "MANUAL_USER_CLOSE", db=db)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    # Reload bot with configuration to return full mapped bot response
    stmt = select(TradingBot).options(
        selectinload(TradingBot.configuration)
    ).where(TradingBot.id == bot_id)
    res = await db.execute(stmt)
    bot = res.scalar_one_or_none()
    if not bot:
        raise HTTPException(status_code=404, detail="Trading bot not found")

    return APIResponse(success=True, message=msg, data=_map_bot_response(bot))
