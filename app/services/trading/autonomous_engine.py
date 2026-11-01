import asyncio
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.entities import TradingBot, BotConfiguration, Order, ExchangeConnection, AuditLog, User
from app.core.security import decrypt_secret
from app.services.binance.client import BinanceSpotClient
from app.services.markets.scanner import scan_all_market_opportunities

logger = logging.getLogger("tradepilot.autonomous")

_autonomous_loop_running = False


class AutonomousExecutionService:
    """
    Autonomous Trading & Auto-Pilot Engine.
    Continuously monitors Whole-Market Scanner setups, validates risk boundaries,
    and automatically executes high-conviction trades on Binance without requiring manual user intervention.
    """

    def __init__(self):
        self.config = {
            "enabled": False,
            "min_score": 90.0,
            "max_active_bots": 3,
            "capital_allocation_pct": 25.0,
            "take_profit_pct": 10.0,
            "stop_loss_pct": 5.0,
            "cooldown_minutes": 15,
            "operating_mode": "demo",
            "allowed_strategies": ["TOP_PICKS", "BREAKOUT", "VOLUME_SURGE", "DIP_BUY"]
        }
        self.last_trade_time: float = 0.0
        self.decision_logs: List[Dict[str, Any]] = [
            {
                "id": "INIT_LOG",
                "timestamp": time.strftime("%H:%M:%S UTC", time.gmtime()),
                "action": "ENGINE_INITIALIZED",
                "message": "Autonomous Auto-Pilot engine ready. Awaiting user activation.",
                "status": "STANDBY"
            }
        ]

    def get_config(self) -> Dict[str, Any]:
        """Returns current autonomous engine parameters and operational status."""
        active_cooldown_sec = max(0, int((self.last_trade_time + (self.config["cooldown_minutes"] * 60)) - time.time()))
        return {
            **self.config,
            "is_running": self.config["enabled"],
            "cooldown_remaining_sec": active_cooldown_sec,
            "cooldown_remaining_minutes": round(active_cooldown_sec / 60.0, 1),
            "last_trade_timestamp": time.strftime("%H:%M:%S UTC", time.gmtime(self.last_trade_time)) if self.last_trade_time > 0 else "None",
            "total_decisions_logged": len(self.decision_logs)
        }

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Updates autonomous operational parameters."""
        for k, v in updates.items():
            if k in self.config and v is not None:
                self.config[k] = v
        self._add_log(
            action="CONFIG_UPDATED",
            message=f"Auto-Pilot parameters updated: Min Score={self.config['min_score']}, Max Bots={self.config['max_active_bots']}, Alloc={self.config['capital_allocation_pct']}%",
            status="INFO"
        )
        return self.get_config()

    def toggle_autopilot(self) -> Dict[str, Any]:
        """Toggles autonomous execution mode on or off."""
        self.config["enabled"] = not self.config["enabled"]
        st = "ACTIVATED" if self.config["enabled"] else "DEACTIVATED"
        self._add_log(
            action=f"AUTOPILOT_{st}",
            message=f"Auto-Pilot Autonomous Mode was {st.lower()} by user. Scanner scanning target pairs.",
            status="SUCCESS" if self.config["enabled"] else "PAUSED"
        )
        return self.get_config()

    def get_logs(self, limit: int = 40) -> List[Dict[str, Any]]:
        """Returns recent autonomous execution decision logs."""
        return self.decision_logs[-limit:]

    def _add_log(self, action: str, message: str, status: str, details: Optional[Dict[str, Any]] = None):
        """Records an immutable autonomous audit event."""
        log_entry = {
            "id": f"AUTO_{int(time.time() * 1000)}",
            "timestamp": time.strftime("%H:%M:%S UTC", time.gmtime()),
            "action": action,
            "message": message,
            "status": status,
            "details": details or {}
        }
        self.decision_logs.append(log_entry)
        if len(self.decision_logs) > 200:
            self.decision_logs = self.decision_logs[-200:]

    async def run_evaluation_cycle(self) -> Dict[str, Any]:
        """
        Executes a single autonomous evaluation cycle:
        1. Checks Auto-Pilot enabled switch.
        2. Verifies maximum active bot capacity.
        3. Verifies cooldown window.
        4. Scans for high-conviction setups meeting threshold.
        5. Automatically executes the best opportunity on Binance and launches the bot.
        """
        if not self.config["enabled"]:
            return {"executed": False, "reason": "Auto-Pilot is currently disabled"}

        # 1. Check cooldown
        now = time.time()
        cooldown_sec = self.config["cooldown_minutes"] * 60
        if (now - self.last_trade_time) < cooldown_sec:
            rem = int(cooldown_sec - (now - self.last_trade_time))
            msg = f"In trade cooldown window ({rem // 60}m {rem % 60}s remaining). Holding execution."
            return {"executed": False, "reason": msg}

        # 2. Check active bots count in database
        async with AsyncSessionLocal() as session:
            stmt = select(TradingBot).where(TradingBot.status == "active")
            res = await session.execute(stmt)
            active_bots = res.scalars().all()
            active_symbols = {b.symbol for b in active_bots}

            if len(active_bots) >= self.config["max_active_bots"]:
                msg = f"Capacity full: {len(active_bots)}/{self.config['max_active_bots']} maximum simultaneous bots are already active."
                self._add_log("CYCLE_SKIPPED", msg, "STANDBY", {"active_count": len(active_bots)})
                return {"executed": False, "reason": msg}

            # 3. Query Whole-Market Scanner for top opportunities
            scanner_result = await scan_all_market_opportunities(
                strategy="ALL",
                min_score=float(self.config["min_score"]),
                limit=15
            )
            opportunities = scanner_result.get("opportunities", [])
            if not opportunities:
                msg = f"No scanner setups currently satisfy the strict minimum score threshold of {self.config['min_score']}/100."
                self._add_log("CYCLE_CHECKED", msg, "SCANNING", {"threshold": self.config["min_score"]})
                return {"executed": False, "reason": msg}

            # 4. Filter for opportunities without an existing active bot
            candidate = None
            for opp in opportunities:
                sym = opp.get("symbol", "").upper()
                if sym not in active_symbols:
                    candidate = opp
                    break

            if not candidate:
                msg = "Top opportunities already have active tracking bots running. Holding to prevent duplicate exposure."
                self._add_log("CYCLE_SKIPPED", msg, "STANDBY")
                return {"executed": False, "reason": msg}

            # 5. Resolve active trader and Binance exchange credentials
            stmt_conn = select(ExchangeConnection).where(
                ExchangeConnection.is_active == True
            ).order_by(ExchangeConnection.last_synced_at.desc())
            res_c = await session.execute(stmt_conn)
            conn = res_c.scalars().first()

            if not conn or not conn.api_key_encrypted or not conn.api_secret_encrypted:
                msg = "Active Binance credentials not configured. Please connect API keys in Settings."
                self._add_log("EXECUTION_ERROR", msg, "ERROR")
                return {"executed": False, "reason": msg}

            # Find user
            stmt_u = select(User).order_by(User.created_at.asc())
            res_u = await session.execute(stmt_u)
            primary_user = res_u.scalars().first()
            if not primary_user:
                return {"executed": False, "reason": "No user account found"}

            sym = candidate["symbol"]
            score = candidate.get("opportunity_score", 90.0)
            sig_type = candidate.get("strategy_type", "BREAKOUT")
            base_tok = sym.replace("USDT", "")
            bot_name = f"{base_tok} Auto-Pilot Scalper"

            # Execute real order on Binance API
            plain_key = decrypt_secret(conn.api_key_encrypted)
            plain_secret = decrypt_secret(conn.api_secret_encrypted)
            client = BinanceSpotClient(api_key=plain_key, api_secret=plain_secret, is_testnet=conn.is_testnet)

            # Calculate allocation amount
            eq_ok, eq_data, _ = await client.calculate_real_portfolio_equity()
            total_bal = Decimal("1000.0")
            if eq_ok and eq_data and eq_data.get("total_equity_usdt"):
                total_bal = Decimal(str(eq_data["total_equity_usdt"]))
                if total_bal <= Decimal("0"):
                    total_bal = Decimal(str(eq_data.get("available_balance_usdt", "1000.0")))

            trade_alloc_pct = Decimal(str(self.config["capital_allocation_pct"]))
            trade_usdt = round(total_bal * (trade_alloc_pct / Decimal("100.0")), 2)
            if trade_usdt < Decimal("15.0"):
                trade_usdt = Decimal("25.00")

            order_ok, order_data, order_err = await client.place_order(
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                quote_order_qty=trade_usdt
            )

            if not order_ok:
                err_msg = f"Binance rejected autonomous trade for {sym}: {order_err}"
                self._add_log("ORDER_REJECTED", err_msg, "FAILED", {"symbol": sym, "error": order_err})
                return {"executed": False, "reason": err_msg}

            order_id = str(order_data.get("orderId"))
            avg_p = Decimal(str(order_data.get("avgPrice", "0")))
            p_raw = Decimal(str(order_data.get("price", "0")))
            exec_q = Decimal(str(order_data.get("executedQty", order_data.get("origQty", "0"))))
            cum_q = Decimal(str(order_data.get("cumQuote", order_data.get("cummulativeQuoteQty", "0"))))

            fill_p = avg_p if avg_p > 0 else (p_raw if p_raw > 0 else ((cum_q / exec_q) if exec_q > 0 and cum_q > 0 else Decimal(str(candidate.get("price", "1.0")))))
            tp_pct = Decimal(str(self.config["take_profit_pct"]))
            sl_pct = Decimal(str(self.config["stop_loss_pct"]))
            tp_price = round(fill_p * (Decimal("1.0") + tp_pct / Decimal("100.0")), 6)
            sl_price = round(fill_p * (Decimal("1.0") - sl_pct / Decimal("100.0")), 6)

            # Create Bot in Database
            new_bot = TradingBot(
                user_id=primary_user.id,
                name=bot_name,
                symbol=sym,
                timeframe="15m",
                operating_mode="live" if not conn.is_testnet else "demo",
                status="active",
                allocated_budget=trade_usdt,
                risk_percentage=Decimal("0.50"),
                max_daily_trades=8,
                trades_count_today=1,
                last_active_at=datetime.now(timezone.utc)
            )
            session.add(new_bot)
            await session.flush()

            bot_config = BotConfiguration(
                bot_id=new_bot.id,
                stop_loss_type="percentage",
                stop_loss_value=float(sl_pct),
                take_profit_type="percentage",
                take_profit_value=float(tp_pct),
                trailing_stop_enabled=True,
                trailing_stop_callback=0.5,
                strategy_params={
                    "origin": "autonomous_autopilot",
                    "opportunity_score": float(score),
                    "signal_type": sig_type,
                    "binance_order_id": order_id,
                    "binance_status": "FILLED",
                    "binance_executed": True,
                    "exchange_endpoint": client.endpoint_type,
                    "entry_price": float(fill_p),
                    "take_profit_price": float(tp_price),
                    "stop_loss_price": float(sl_price),
                    "take_profit_pct": float(tp_pct),
                    "stop_loss_pct": float(sl_pct),
                    "executed_quantity": float(exec_q if exec_q > 0 else 1.0),
                    "allocated_trade_usdt": float(trade_usdt)
                }
            )
            session.add(bot_config)

            # Record Order
            record_order = Order(
                user_id=primary_user.id,
                bot_id=new_bot.id,
                client_order_id=order_data.get("clientOrderId", f"AUTO_{int(time.time())}_{sym[:4]}"),
                exchange_order_id=order_id,
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                mode="live" if not conn.is_testnet else "demo",
                status="FILLED",
                price=fill_p,
                quantity=exec_q if exec_q > 0 else Decimal("1.0"),
                executed_quantity=exec_q if exec_q > 0 else Decimal("1.0"),
                cumulative_quote_qty=cum_q if cum_q > 0 else trade_usdt
            )
            session.add(record_order)

            # Audit log
            audit = AuditLog(
                user_id=primary_user.id,
                action="AUTONOMOUS_TRADE_EXECUTED",
                resource_type="TRADING_BOT",
                resource_id=new_bot.id,
                details={
                    "name": bot_name,
                    "symbol": sym,
                    "score": float(score),
                    "order_id": order_id,
                    "amount_usdt": str(trade_usdt)
                }
            )
            session.add(audit)
            await session.commit()

            self.last_trade_time = time.time()
            success_msg = (
                f"⚡ AUTONOMOUS TRADE EXECUTED: Bought {sym} (Score: {score}/100) for ${trade_usdt} USDT. "
                f"Order #{order_id} filled @ ${fill_p}. TP: +{tp_pct}% (${tp_price}) | SL: -{sl_pct}% (${sl_price})."
            )
            self._add_log(
                action="TRADE_EXECUTED",
                message=success_msg,
                status="SUCCESS",
                details={"symbol": sym, "score": score, "order_id": order_id, "bot_id": new_bot.id}
            )
            logger.info(success_msg)

            return {
                "executed": True,
                "symbol": sym,
                "score": score,
                "order_id": order_id,
                "bot_id": new_bot.id,
                "allocated_usdt": float(trade_usdt),
                "fill_price": float(fill_p)
            }


autonomous_service = AutonomousExecutionService()


async def autonomous_background_loop():
    """Background engine loop checking scanner setups every 60 seconds when auto-pilot is enabled."""
    global _autonomous_loop_running
    _autonomous_loop_running = True
    logger.info("TradePilot Autonomous Auto-Pilot background engine started.")
    while _autonomous_loop_running:
        try:
            if autonomous_service.config.get("enabled"):
                await autonomous_service.run_evaluation_cycle()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in autonomous execution loop: {e}")
        await asyncio.sleep(60.0)
    logger.info("TradePilot Autonomous Auto-Pilot background engine stopped.")


def stop_autonomous_loop():
    global _autonomous_loop_running
    _autonomous_loop_running = False
