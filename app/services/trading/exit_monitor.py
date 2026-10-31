import asyncio
import logging
import time
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.entities import TradingBot, BotConfiguration, Order, ExchangeConnection, AuditLog
from app.core.security import decrypt_secret
from app.services.binance.client import BinanceSpotClient

logger = logging.getLogger("tradepilot.exit_monitor")

_exit_monitor_running = False


async def close_bot_position(
    bot_id: str,
    reason: str,
    db = None
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Submits a MARKET SELL order to Binance to close the active bot's position,
    records the exit order in PostgreSQL, and transitions the bot status to closed.
    """
    should_close_db = False
    if db is None:
        db = AsyncSessionLocal()
        should_close_db = True

    try:
        stmt = select(TradingBot).options(
            selectinload(TradingBot.configuration)
        ).where(TradingBot.id == bot_id)
        result = await db.execute(stmt)
        bot = result.scalar_one_or_none()
        if not bot:
            return False, "Bot not found", None

        # If already stopped/completed, confirm as successfully closed
        if bot.status != "active":
            return True, f"Bot '{bot.name}' is already closed", None

        config = bot.configuration
        params = config.strategy_params if (config and config.strategy_params) else {}

        sym = bot.symbol.upper()
        fallback_qty = Decimal(str(params.get("executed_quantity", "0")))

        # Resolve credentials
        stmt_conn = select(ExchangeConnection).where(
            ExchangeConnection.user_id == bot.user_id,
            ExchangeConnection.is_active == True
        ).order_by(ExchangeConnection.last_synced_at.desc())
        res_c = await db.execute(stmt_conn)
        conn = res_c.scalars().first()
        if not conn:
            stmt_any = select(ExchangeConnection).where(
                ExchangeConnection.exchange == "binance",
                ExchangeConnection.is_active == True
            ).order_by(ExchangeConnection.last_synced_at.desc())
            res_any = await db.execute(stmt_any)
            conn = res_any.scalars().first()

        if not conn or not conn.api_key_encrypted or not conn.api_secret_encrypted:
            # Still allow user to close the bot on TradePilot even if API keys are missing
            bot.status = "stopped"
            params["exit_reason"] = f"{reason} (No API keys)"
            params["exit_at"] = datetime.now(timezone.utc).isoformat()
            if config:
                config.strategy_params = params
            await db.commit()
            return True, f"Bot '{bot.name}' stopped (No exchange credentials found)", None

        plain_key = decrypt_secret(conn.api_key_encrypted)
        plain_secret = decrypt_secret(conn.api_secret_encrypted)
        client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)

        # 1. Cancel any active pending / trigger orders on Binance for this symbol
        try:
            await client.cancel_open_orders(sym)
        except Exception as e:
            logger.warning(f"Failed to cancel open orders for {sym}: {e}")

        # 2. Check live open position on Binance to determine exact quantity and direction to close
        live_open_positions = await client.get_open_positions()
        live_pos_amt = live_open_positions.get(sym, Decimal("0"))

        order_ok = False
        order_data: Dict[str, Any] = {}
        order_err = None

        if live_pos_amt != Decimal("0"):
            # Position exists on Binance:
            # - If Long (pos > 0): side is SELL with reduceOnly=True
            # - If Short (pos < 0): side is BUY with reduceOnly=True
            exit_side = "SELL" if live_pos_amt > Decimal("0") else "BUY"
            exit_qty = abs(live_pos_amt)

            logger.info(f"Closing position on Binance for {sym}: side={exit_side}, qty={exit_qty}, reduce_only=True (live pos={live_pos_amt})")
            order_ok, order_data, order_err = await client.place_order(
                symbol=sym,
                side=exit_side,
                order_type="MARKET",
                quantity=exit_qty,
                reduce_only=True
            )

            # If rejected because position was already reduced or gone, mark as cleanly closed
            if not order_ok and order_err and ("reduceonly" in str(order_err).lower() or "-2022" in str(order_err) or "insufficient" in str(order_err).lower()):
                logger.info(f"Binance position already reduced or 0 for {sym}")
                order_ok = True
                order_data = {"orderId": "SYNC_REDUCED_ZERO", "status": "FILLED", "price": "0"}
        else:
            # Position is already 0 on Binance (already closed by TP, SL, or directly on exchange)
            logger.info(f"Position for {sym} is already 0 on Binance. Marking bot {bot.name} as closed.")
            order_ok = True
            order_data = {"orderId": "SYNC_CLOSED", "status": "FILLED", "price": "0"}

        if not order_ok:
            logger.warning(f"Binance order rejected when closing {bot.name} ({sym}): {order_err}")
            # If user manually initiated close, ALWAYS stop the bot so it doesn't get stuck active
            bot.status = "stopped"
            params["exit_reason"] = f"CLOSED_BINANCE_MSG ({order_err})"
            params["exit_at"] = datetime.now(timezone.utc).isoformat()
            if config:
                config.strategy_params = params
            await db.commit()
            return True, f"Trade closed on TradePilot (Binance: {order_err})", order_data

        sell_order_id = str(order_data.get("orderId", "SYNC_CLOSED"))
        sell_status = str(order_data.get("status", "FILLED"))
        sell_price = Decimal(str(order_data.get("price", "0")))

        # Update bot configuration
        params["exit_reason"] = reason
        params["exit_order_id"] = sell_order_id
        params["exit_status"] = sell_status
        params["exit_at"] = datetime.now(timezone.utc).isoformat()
        if sell_price > Decimal("0"):
            params["exit_price"] = float(sell_price)
        if config:
            config.strategy_params = params

        bot.status = "completed" if "PROFIT" in reason.upper() else "stopped"
        bot.last_active_at = datetime.now(timezone.utc)

        live_qty = abs(live_pos_amt)
        close_qty = live_qty if live_qty > Decimal("0") else fallback_qty
        exit_side_used = exit_side if live_pos_amt != Decimal("0") else "SELL"

        # Record closing Order in PostgreSQL if an actual order was executed
        if sell_order_id not in ("SYNC_CLOSED", "SYNC_REDUCED_ZERO"):
            exit_order = Order(
                user_id=bot.user_id,
                bot_id=bot.id,
                client_order_id=order_data.get("clientOrderId", f"TP_EXIT_{int(time.time())}_{sym[:4]}"),
                exchange_order_id=sell_order_id,
                symbol=sym,
                side=exit_side_used,
                order_type="MARKET",
                mode="live" if not conn.is_testnet else "demo",
                status=sell_status,
                price=sell_price,
                quantity=close_qty,
                executed_quantity=Decimal(str(order_data.get("executedQty", str(close_qty)))),
                cumulative_quote_qty=Decimal(str(order_data.get("cumQuote", order_data.get("cummulativeQuoteQty", "0"))))
            )
            db.add(exit_order)

        # Audit log
        audit = AuditLog(
            user_id=bot.user_id,
            action="AUTO_EXIT_POSITION",
            resource_type="TRADING_BOT",
            resource_id=bot.id,
            details={
                "reason": reason,
                "symbol": sym,
                "exit_order_id": sell_order_id,
                "quantity": str(close_qty)
            }
        )
        db.add(audit)

        await db.commit()
        await db.refresh(bot)

        logger.info(f"Position successfully closed on Binance for {bot.name} ({sym}). Order: #{sell_order_id}. Reason: {reason}")
        return True, f"Closed successfully on Binance (#{sell_order_id})", order_data
    except Exception as e:
        logger.error(f"Error in close_bot_position for {bot_id}: {e}")
        return False, str(e), None
    finally:
        if should_close_db and db:
            await db.close()


async def run_exit_monitor_cycle():
    """Single polling cycle checking all active bot positions against Take Profit, Stop Loss, and Binance real-time status."""
    async with AsyncSessionLocal() as session:
        try:
            stmt = select(TradingBot).options(
                selectinload(TradingBot.configuration)
            ).where(TradingBot.status == "active")
            result = await session.execute(stmt)
            active_bots = result.scalars().all()

            if not active_bots:
                return

            # Check Binance real open positions
            stmt_conn = select(ExchangeConnection).where(
                ExchangeConnection.is_active == True
            ).order_by(ExchangeConnection.last_synced_at.desc())
            res_c = await session.execute(stmt_conn)
            conn = res_c.scalars().first()

            open_binance_positions: Dict[str, Decimal] = {}
            if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
                try:
                    plain_key = decrypt_secret(conn.api_key_encrypted)
                    plain_secret = decrypt_secret(conn.api_secret_encrypted)
                    binance_client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)
                    open_binance_positions = await binance_client.get_open_positions()
                except Exception as b_err:
                    logger.warning(f"Failed to fetch Binance positions in exit cycle: {b_err}")

            # Check each bot against live Binance position and price thresholds
            for bot in active_bots:
                config = bot.configuration
                params = config.strategy_params if (config and config.strategy_params) else {}
                sym = bot.symbol

                # If bot was not executed on Binance or is a legacy placeholder, mark stopped
                if not params.get("binance_executed"):
                    logger.info(f"Retiring unexecuted bot {bot.name} ({sym}) from active fleet.")
                    bot.status = "stopped"
                    session.add(bot)
                    continue

                # If connection is active, verify if position is still open on Binance
                if conn and open_binance_positions is not None:
                    if sym not in open_binance_positions:
                        logger.info(f"⚡ Position for {bot.name} ({sym}) was closed on Binance (position = 0). Archiving bot.")
                        bot.status = "completed"
                        params["exit_reason"] = "CLOSED_ON_BINANCE"
                        params["exit_at"] = datetime.now(timezone.utc).isoformat()
                        config.strategy_params = params
                        session.add(bot)
                        session.add(config)
                        continue

                entry_p_raw = params.get("entry_price")
                if not entry_p_raw:
                    continue

                entry_price = Decimal(str(entry_p_raw))
                if entry_price <= Decimal("0"):
                    continue

                tp_pct = Decimal(str(params.get("take_profit_pct", "10.00")))
                sl_pct = Decimal(str(params.get("stop_loss_pct", "5.00")))
                tp_price = Decimal(str(params.get("take_profit_price", str(entry_price * (Decimal("1.0") + tp_pct / Decimal("100.0"))))))
                sl_price = Decimal(str(params.get("stop_loss_price", str(entry_price * (Decimal("1.0") - sl_pct / Decimal("100.0"))))))

                # Query current ticker price
                endpoint = params.get("exchange_endpoint", "futures_testnet")
                base_url = "https://testnet.binancefuture.com" if "futures" in endpoint else "https://api.binance.com"
                path = "/fapi/v1/ticker/price" if "futures" in endpoint else "/api/v3/ticker/price"

                import httpx
                cur_price = None
                async with httpx.AsyncClient(timeout=4.0) as http_client:
                    try:
                        p_res = await http_client.get(f"{base_url}{path}?symbol={sym}")
                        if p_res.status_code == 200:
                            cur_price = Decimal(str(p_res.json().get("price", "0")))
                    except Exception:
                        pass

                if not cur_price or cur_price <= Decimal("0"):
                    continue

                # Calculate PnL %
                pnl_pct = ((cur_price - entry_price) / entry_price) * Decimal("100.0")

                # Update current status
                params["current_price"] = float(cur_price)
                params["current_pnl_pct"] = float(round(pnl_pct, 2))
                config.strategy_params = params
                session.add(config)

                # Check Take Profit trigger (+10%)
                if cur_price >= tp_price or pnl_pct >= tp_pct:
                    logger.info(f"🎯 TAKE PROFIT TRIGGERED for {bot.name} ({sym})! Current: ${cur_price} vs Entry: ${entry_price} (PnL: +{pnl_pct:.2f}%)")
                    reason = f"TAKE_PROFIT_TRIGGERED (+{pnl_pct:.2f}% Profit Target Reached)"
                    await close_bot_position(bot.id, reason, db=session)
                    continue

                # Check Stop Loss trigger (-5%)
                if cur_price <= sl_price or pnl_pct <= -sl_pct:
                    logger.warning(f"🛑 STOP LOSS TRIGGERED for {bot.name} ({sym})! Current: ${cur_price} vs Entry: ${entry_price} (PnL: {pnl_pct:.2f}%)")
                    reason = f"STOP_LOSS_TRIGGERED ({pnl_pct:.2f}% Loss Protection Activated)"
                    await close_bot_position(bot.id, reason, db=session)
                    continue

            await session.commit()
        except Exception as e:
            logger.error(f"Error in run_exit_monitor_cycle: {e}")


async def exit_monitor_background_loop():
    """Background task continuously monitoring positions for automatic closure."""
    global _exit_monitor_running
    _exit_monitor_running = True
    logger.info("TradePilot automated exit monitor engine started (Take Profit + Stop Loss protection active).")
    while _exit_monitor_running:
        try:
            await run_exit_monitor_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Unexpected error in exit monitor loop: {e}")
        await asyncio.sleep(3.0)
    logger.info("TradePilot automated exit monitor engine stopped.")


def stop_exit_monitor():
    global _exit_monitor_running
    _exit_monitor_running = False
