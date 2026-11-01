import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy.future import select

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.entities import TradingBot, BotConfiguration, ExchangeConnection
from app.services.trading.exit_monitor import run_exit_monitor_cycle


@pytest.mark.asyncio
async def test_scanner_trade_does_not_auto_close_in_two_seconds():
    """
    Verifies that when a trade is launched from the Scanner:
    1. The bot is created in active state.
    2. Over multiple cycles of run_exit_monitor_cycle and /api/v1/bots polling,
       the bot remains strictly ACTIVE and is NOT closed or archived due to grace period protection.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register and login test user
        email = f"persist_{int(time.time())}@tradepilot.ai"
        res_reg = await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "PilotPassword123!",
            "full_name": "Persistence Tester"
        })
        assert res_reg.status_code == 201

        res_login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "PilotPassword123!"
        })
        assert res_login.status_code == 200
        token = res_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Take trade from scanner for XRPUSDT
        payload = {
            "symbol": "XRPUSDT",
            "signal_type": "BREAKOUT",
            "opportunity_score": "95.0",
            "entry_price": "1.248",
            "take_profit_pct": "10.00",
            "stop_loss_pct": "5.00",
            "trailing_stop_enabled": True,
            "allocated_budget": "50.00",
            "risk_percentage": "0.50",
            "operating_mode": "demo"
        }
        res_trade = await client.post("/api/v1/bots/from-scanner", json=payload, headers=headers)
        assert res_trade.status_code == 201
        bot_data = res_trade.json()["data"]
        bot_id = bot_data["id"]
        assert bot_data["status"] == "active"

        # Simulate 10 seconds of background exit cycles and frontend polling
        for second in range(1, 6):
            # Run exit monitor cycle
            await run_exit_monitor_cycle()

            # Poll GET /api/v1/bots (just like the mobile app / preview does)
            res_bots = await client.get("/api/v1/bots", headers=headers)
            assert res_bots.status_code == 200
            bots = res_bots.json()["data"]
            my_bot = next((b for b in bots if b["id"] == bot_id), None)
            assert my_bot is not None, f"Bot was prematurely removed from active list at second {second}!"
            assert my_bot["status"] == "active", f"Bot status changed to {my_bot['status']} at second {second}!"

            # Poll GET /api/v1/dashboard/summary (just like the mobile dashboard does)
            res_dash = await client.get("/api/v1/dashboard/summary", headers=headers)
            assert res_dash.status_code == 200
            active_count = res_dash.json()["data"]["active_bots_count"]
            assert active_count >= 1, f"Dashboard summary dropped active bot at second {second}!"

            await asyncio.sleep(1.0)

        # Confirm bot is still active in database
        async with AsyncSessionLocal() as session:
            stmt = select(TradingBot).where(TradingBot.id == bot_id)
            res = await session.execute(stmt)
            bot_in_db = res.scalar_one_or_none()
            assert bot_in_db is not None
            assert bot_in_db.status == "active", f"Bot in DB became {bot_in_db.status} instead of active!"
