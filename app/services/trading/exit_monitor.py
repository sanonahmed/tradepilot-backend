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


async def fetch_price_with_mirrors(sym: str, endpoint: str) -> Optional[Decimal]:
    """Fetches real-time ticker price with multi-mirror fallback to survive cloud datacenter geo-blocks."""
    import httpx
    if "futures" in endpoint:
        urls = [
            f"https://testnet.binancefuture.com/fapi/v1/ticker/price?symbol={sym}" if "testnet" in endpoint else f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}",
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}",
            f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}"
        ]
    else:
        urls = [
            f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}",
            f"https://api1.binance.com/api/v3/ticker/price?symbol={sym}",
            f"https://api3.binance.com/api/v3/ticker/price?symbol={sym}",
            f"https://api.binance.com/api/v3/ticker/price?symbol={sym}"
        ]
    async with httpx.AsyncClient(timeout=3.5) as http_client:
        for u in urls:
            try:
                r = await http_client.get(u, headers={"User-Agent": "TradePilotAI/1.0.0"})
                if r.status_code == 200:
                    p = r.json().get("price")
                    if p:
                        val = Decimal(str(p))
                        if val > Decimal("0"):
                            return val
            except Exception:
                continue
    return None


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

            live_positions_details: Dict[str, Dict[str, Any]] = {}
            open_binance_positions: Dict[str, Decimal] = {}
            if conn and conn.api_key_encrypted and conn.api_secret_encrypted:
                try:
                    plain_key = decrypt_secret(conn.api_key_encrypted)
                    plain_secret = decrypt_secret(conn.api_secret_encrypted)
                    binance_client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)
                    live_positions_details = await binance_client.get_live_positions_details()
                    open_binance_positions = {s: p["position_amt"] for s, p in live_positions_details.items()}
                except Exception as b_err:
                    logger.warning(f"Failed to fetch Binance positions in exit cycle: {b_err}")

            now_utc = datetime.now(timezone.utc)

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

                # Calculate bot age for opening grace period
                bot_created = bot.created_at
                if bot_created:
                    if bot_created.tzinfo is None:
                        bot_created = bot_created.replace(tzinfo=timezone.utc)
                    age_seconds = (now_utc - bot_created).total_seconds()
                else:
                    age_seconds = 999999.0

                # If connection is active, verify if position is still open on Binance
                if conn and open_binance_positions is not None:
                    if sym not in open_binance_positions:
                        # 60s Opening Grace Period: Never archive brand new bots while Binance fills/indexes the position
                        if age_seconds < 60.0:
                            logger.debug(f"Bot {bot.name} ({sym}) is within 60s opening grace period (age={age_seconds:.1f}s), waiting for exchange.")
                        else:
                            logger.info(f"⚡ Position for {bot.name} ({sym}) was closed on Binance (position = 0, age={age_seconds:.1f}s). Archiving bot.")
                            bot.status = "completed"
                            params["exit_reason"] = "CLOSED_ON_BINANCE"
                            params["exit_at"] = datetime.now(timezone.utc).isoformat()
                            config.strategy_params = params
                            session.add(bot)
                            session.add(config)
                            continue

                entry_p_raw = params.get("entry_price")
                entry_price = Decimal(str(entry_p_raw)) if entry_p_raw else Decimal("0")

                # If position is actively tracked by Binance, update official entry and mark prices
                cur_price: Optional[Decimal] = None
                if sym in live_positions_details:
                    bp = live_positions_details[sym]
                    if bp.get("entry_price") and bp["entry_price"] > Decimal("0"):
                        entry_price = bp["entry_price"]
                        params["entry_price"] = float(entry_price)
                    if bp.get("mark_price") and bp["mark_price"] > Decimal("0"):
                        cur_price = bp["mark_price"]
                        params["current_price"] = float(cur_price)

                if entry_price <= Decimal("0"):
                    continue

                tp_pct = Decimal(str(params.get("take_profit_pct", "10.00")))
                sl_pct = Decimal(str(params.get("stop_loss_pct", "5.00")))
                initial_sl_price = round(entry_price * (Decimal("1.0") - sl_pct / Decimal("100.0")), 6)
                tp_price = round(entry_price * (Decimal("1.0") + tp_pct / Decimal("100.0")), 6)
                params["take_profit_price"] = float(tp_price)
                params["initial_stop_loss_price"] = float(initial_sl_price)

                # Query current ticker price if not already resolved from position details
                if cur_price is None or cur_price <= Decimal("0"):
                    cur_price = await fetch_price_with_mirrors(sym, params.get("exchange_endpoint", "futures_testnet"))

                if not cur_price or cur_price <= Decimal("0"):
                    continue

                # Calculate PnL %
                pnl_pct = ((cur_price - entry_price) / entry_price) * Decimal("100.0")

                # Track highest price and highest unrealized PnL reached
                highest_pnl = Decimal(str(params.get("highest_pnl_pct", "0.0")))
                if pnl_pct > highest_pnl:
                    highest_pnl = pnl_pct
                    params["highest_pnl_pct"] = float(round(highest_pnl, 2))
                    params["highest_price"] = float(cur_price)

                # UPGRADE 1: DYNAMIC BREAK-EVEN LOCK (+3.0% threshold)
                # Automatically shifts Stop Loss to Entry + 0.3% (covers exchange fees) so winning trades never turn into losses
                break_even_price = round(entry_price * Decimal("1.003"), 6)
                effective_sl_price = initial_sl_price

                if highest_pnl >= Decimal("3.0") or pnl_pct >= Decimal("3.0"):
                    if not params.get("break_even_locked"):
                        params["break_even_locked"] = True
                        logger.info(f"🛡️ BREAK-EVEN ACTIVATED for {bot.name} ({sym})! PnL reached +{pnl_pct:.2f}%. Stop Loss raised to Entry Price (${break_even_price}).")
                    effective_sl_price = max(effective_sl_price, break_even_price)

                # UPGRADE 2: DYNAMIC TRAILING PROFIT LOCK (+6.0% threshold)
                # Trails peak price with a 2.0% cushion to lock in gains if a breakout begins to exhaust
                if highest_pnl >= Decimal("6.0"):
                    peak_p = Decimal(str(params.get("highest_price", cur_price)))
                    trailing_sl = round(peak_p * Decimal("0.98"), 6)
                    if trailing_sl > effective_sl_price:
                        effective_sl_price = trailing_sl
                        params["trailing_sl_price"] = float(trailing_sl)
                        params["trailing_active"] = True

                params["stop_loss_price"] = float(effective_sl_price)
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

                # Stabilization Cooldown: Don't trigger initial Stop Loss during the first 15 seconds of a trade
                if age_seconds < 15.0 and not params.get("break_even_locked") and not params.get("trailing_active"):
                    logger.debug(f"Bot {bot.name} ({sym}) in 15s entry cooldown ({age_seconds:.1f}s), holding SL trigger.")
                    continue

                # Check Trailing Profit Lock trigger
                if params.get("trailing_active") and cur_price <= effective_sl_price:
                    logger.info(f"✨ TRAILING PROFIT LOCK TRIGGERED for {bot.name} ({sym})! Peak: +{highest_pnl:.2f}%, Exit: +{pnl_pct:.2f}% at ${cur_price}")
                    reason = f"TRAILING_PROFIT_TRIGGERED (Locked in +{pnl_pct:.2f}% profit after peak of +{highest_pnl:.2f}%)"
                    await close_bot_position(bot.id, reason, db=session)
                    continue

                # Check Break-Even trigger
                if params.get("break_even_locked") and cur_price <= effective_sl_price:
                    logger.info(f"🛡️ BREAK-EVEN EXIT TRIGGERED for {bot.name} ({sym})! Position protected from loss at ${cur_price} (PnL: +{pnl_pct:.2f}%)")
                    reason = f"BREAK_EVEN_EXIT (Position protected from loss at +{pnl_pct:.2f}%)"
                    await close_bot_position(bot.id, reason, db=session)
                    continue

                # Check Standard Stop Loss trigger
                if (cur_price <= effective_sl_price or pnl_pct <= -sl_pct) and cur_price > Decimal("0") and effective_sl_price > Decimal("0"):
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
