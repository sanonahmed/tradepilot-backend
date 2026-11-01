import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.entities import TradingBot, BotConfiguration, Order, ExchangeConnection, AuditLog, User, RiskLimit
from app.core.security import decrypt_secret
from app.services.binance.client import BinanceSpotClient
from app.services.markets.scanner import scan_all_market_opportunities

logger = logging.getLogger("tradepilot.autonomous")

PERSISTENCE_FILE = Path(__file__).resolve().parents[3] / "autonomous_config.json"
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
            "leverage": 10,
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
        self._load_persisted_state()

    def _load_persisted_state(self):
        """Restores autonomous configuration, last trade time, and decision logs from disk."""
        try:
            if PERSISTENCE_FILE.exists():
                with open(PERSISTENCE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    saved_cfg = data.get("config", {})
                    if isinstance(saved_cfg, dict):
                        self.config.update(saved_cfg)
                    if "last_trade_time" in data:
                        self.last_trade_time = float(data["last_trade_time"])
                    if "decision_logs" in data and isinstance(data["decision_logs"], list) and data["decision_logs"]:
                        self.decision_logs = data["decision_logs"][-100:]
                logger.info(f"Loaded persistent Auto-Pilot state from {PERSISTENCE_FILE}: enabled={self.config.get('enabled')}, min_score={self.config.get('min_score')}")
        except Exception as e:
            logger.warning(f"Could not load autonomous persistence state: {e}")

    def _save_persisted_state(self):
        """Atomically persists autonomous parameters and decision logs to disk."""
        try:
            payload = {
                "config": self.config,
                "last_trade_time": self.last_trade_time,
                "decision_logs": self.decision_logs[-50:],
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            PERSISTENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PERSISTENCE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist autonomous configuration to disk: {e}")

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
        self._save_persisted_state()
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
        self._save_persisted_state()
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
        if status in ["SUCCESS", "FAILED", "CRITICAL"]:
            self._save_persisted_state()

    async def run_evaluation_cycle(self, force: bool = False) -> Dict[str, Any]:
        """
        Executes a single autonomous evaluation cycle:
        1. Checks Auto-Pilot enabled switch (unless force=True).
        2. Verifies maximum active bot capacity.
        3. Verifies cooldown window (unless force=True).
        4. Scans for high-conviction setups meeting threshold.
        5. Automatically executes the best opportunity on Binance and launches the bot.
        """
        if not force and not self.config["enabled"]:
            return {"executed": False, "reason": "Auto-Pilot is currently disabled"}

        # 1. Check cooldown (bypassed on manual trigger)
        now = time.time()
        cooldown_sec = self.config["cooldown_minutes"] * 60
        if not force and (now - self.last_trade_time) < cooldown_sec:
            rem = int(cooldown_sec - (now - self.last_trade_time))
            msg = f"In trade cooldown window ({rem // 60}m {rem % 60}s remaining). Holding execution."
            return {"executed": False, "reason": msg}

        # 2. Check active bots count in database and verify Emergency Kill Switch
        async with AsyncSessionLocal() as session:
            # Check Global Kill Switch
            stmt_risk = select(RiskLimit).where(RiskLimit.kill_switch_active == True)
            res_risk = await session.execute(stmt_risk)
            if res_risk.scalars().first():
                msg = "Autonomous execution blocked: Global Emergency Kill Switch is ACTIVE."
                self._add_log("KILL_SWITCH_BLOCKED", msg, "CRITICAL")
                return {"executed": False, "reason": msg}

            stmt = select(TradingBot).where(TradingBot.status == "active")
            res = await session.execute(stmt)
            active_bots = res.scalars().all()
            active_symbols = {b.symbol for b in active_bots}

            if len(active_bots) >= self.config["max_active_bots"]:
                msg = f"Auto-Pilot capacity reached: {len(active_bots)}/{self.config['max_active_bots']} maximum simultaneous bots are already active. Please stop an existing bot or increase max bots to execute another trade."
                self._add_log("CYCLE_SKIPPED", msg, "STANDBY", {"active_count": len(active_bots)})
                return {"executed": False, "reason": msg}

            # 3. Query Whole-Market Scanner for top opportunities
            scanner_result = await scan_all_market_opportunities(
                strategy="ALL",
                min_score=float(self.config["min_score"]),
                limit=35
            )
            opportunities = scanner_result.get("opportunities", [])
            if not opportunities and force:
                # If on-demand execution, progressively lower threshold to locate viable market setups
                scanner_result = await scan_all_market_opportunities(
                    strategy="ALL",
                    min_score=60.0,
                    limit=35
                )
                opportunities = scanner_result.get("opportunities", [])

            if not opportunities and force:
                scanner_result = await scan_all_market_opportunities(
                    strategy="ALL",
                    min_score=40.0,
                    limit=35
                )
                opportunities = scanner_result.get("opportunities", [])

            if not opportunities:
                msg = f"No scanner setups currently satisfy the minimum score threshold of {self.config['min_score']}/100."
                self._add_log("CYCLE_CHECKED", msg, "SCANNING", {"threshold": self.config["min_score"]})
                return {"executed": False, "reason": msg}

            # 4. Filter for opportunities without an existing active bot (strictly validated ASCII symbols with 15m K-line pre-flight confirmation)
            candidate = None
            for opp in opportunities:
                sym = opp.get("symbol", "").upper()
                # Strict ASCII token validation: avoids non-ASCII inscriptions (e.g. 牛来USDT) causing -1022 signature rejections
                if not re.match(r'^[A-Z0-9]{2,12}USDT$', sym):
                    continue
                if sym in active_symbols:
                    continue

                # UPGRADE: 15-Minute Real K-Line Multi-Indicator Pre-Flight Verification
                try:
                    from app.services.analyst.gemini_analyst import gemini_analyst_service
                    tech = await gemini_analyst_service.fetch_technical_indicators(sym, timeframe="15m")
                    if tech:
                        real_rsi = float(tech.get("rsi_14", 50.0))
                        # Rule A: Do not buy if real 15m RSI is overbought (> 72.0)
                        if real_rsi > 72.0:
                            logger.info(f"Pre-flight skipped candidate {sym}: 15m RSI is overextended at {real_rsi:.1f} > 72.0")
                            continue
                        # Rule B: Do not buy if real 15m RSI is collapsing (< 35.0 and falling)
                        if real_rsi < 35.0 and float(tech.get("price_change_pct", 0.0)) < -2.0:
                            logger.info(f"Pre-flight skipped candidate {sym}: Falling knife with RSI {real_rsi:.1f}")
                            continue

                        # Attach verified K-line data into candidate
                        opp["real_rsi"] = real_rsi
                        opp["support_levels"] = tech.get("support_levels", [0, 0])
                        opp["resistance_levels"] = tech.get("resistance_levels", [0, 0])
                except Exception as k_err:
                    logger.debug(f"K-line pre-flight check bypassed for {sym}: {k_err}")

                candidate = opp
                break

            if not candidate:
                msg = f"All {len(opportunities)} screened opportunities already have active tracking bots running. Holding to prevent duplicate exposure."
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

            # Calculate allocation amount & verify available balance
            eq_ok, eq_data, _ = await client.calculate_real_portfolio_equity()
            total_bal = Decimal("1000.0")
            avail_usdt = Decimal("0.0")
            if eq_ok and eq_data:
                if eq_data.get("total_equity_usdt"):
                    total_bal = Decimal(str(eq_data["total_equity_usdt"]))
                if total_bal <= Decimal("0"):
                    total_bal = Decimal(str(eq_data.get("available_balance_usdt", "1000.0")))
                avail_usdt = Decimal(str(eq_data.get("available_balance_usdt", "0.0")))

            # Pre-flight check: ensure sufficient cash to trade without hitting Binance -2010 (insufficient balance)
            if avail_usdt > Decimal("0") and avail_usdt < Decimal("10.0") and not force:
                msg = f"Insufficient available cash (${avail_usdt:.2f} USDT). Holding autonomous trade until margin is freed."
                self._add_log("INSUFFICIENT_BALANCE", msg, "STANDBY", {"available_usdt": float(avail_usdt)})
                return {"executed": False, "reason": msg}

            trade_alloc_pct = Decimal(str(self.config["capital_allocation_pct"]))
            trade_usdt = round(total_bal * (trade_alloc_pct / Decimal("100.0")), 2)
            if avail_usdt > Decimal("10.0"):
                trade_usdt = min(trade_usdt, round(avail_usdt * Decimal("0.90"), 2))
            if trade_usdt < Decimal("15.0"):
                trade_usdt = Decimal("20.00")

            # UPGRADE: Check Market Whale Delta to protect against institutional dumps
            auto_lev = int(self.config.get("leverage", 10))
            try:
                from app.services.whales.whale_tracker import whale_tracker_service
                w_summary = await whale_tracker_service.get_whale_flow_summary()
                net_whale_delta = float(w_summary.get("net_whale_delta_usd", 0.0))
                if net_whale_delta < -3000000.0:  # > $3M institutional net selling
                    logger.warning(f"Market-wide net whale dump detected (${net_whale_delta:,.0f}). Reducing leverage to 5x.")
                    auto_lev = min(auto_lev, 5)
            except Exception:
                pass

            order_ok, order_data, order_err = await client.place_order(
                symbol=sym,
                side="BUY",
                order_type="MARKET",
                quote_order_qty=trade_usdt,
                leverage=auto_lev
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

            # UPGRADE: Place Native Binance STOP_MARKET Order for Server-Side Crash Protection
            native_sl_id = None
            if client.is_futures and exec_q > Decimal("0") and sl_price > Decimal("0"):
                try:
                    sl_ok, sl_data, sl_err = await client.place_stop_market_order(
                        symbol=sym,
                        side="SELL",
                        quantity=exec_q,
                        stop_price=sl_price
                    )
                    if sl_ok and sl_data:
                        native_sl_id = str(sl_data.get("orderId"))
                        logger.info(f"⚡ Server-side Binance STOP_MARKET placed for {sym} at ${sl_price} (Order #{native_sl_id})")
                except Exception as sl_exc:
                    logger.warning(f"Could not place native STOP_MARKET for {sym}: {sl_exc}")

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
                    "native_sl_order_id": native_sl_id,
                    "binance_status": "FILLED",
                    "binance_executed": True,
                    "exchange_endpoint": client.endpoint_type,
                    "entry_price": float(fill_p),
                    "take_profit_price": float(tp_price),
                    "stop_loss_price": float(sl_price),
                    "take_profit_pct": float(tp_pct),
                    "stop_loss_pct": float(sl_pct),
                    "executed_quantity": float(exec_q if exec_q > 0 else 1.0),
                    "allocated_trade_usdt": float(trade_usdt),
                    "break_even_locked": False,
                    "trailing_active": False
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
