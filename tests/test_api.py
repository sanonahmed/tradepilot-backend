import pytest
import pytest_asyncio
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.database import init_db


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["app"] == "TradePilot AI"


@pytest.mark.asyncio
async def test_full_auth_and_dashboard_lifecycle():
    user_email = f"pilot_{uuid.uuid4().hex[:8]}@tradepilot.ai"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register
        reg_payload = {
            "email": user_email,
            "password": "PilotPassword123!",
            "full_name": "Chief Trading Officer"
        }
        res_reg = await client.post("/api/v1/auth/register", json=reg_payload)
        assert res_reg.status_code == 201
        assert res_reg.json()["success"] is True

        # 2. Login
        login_payload = {
            "email": user_email,
            "password": "PilotPassword123!"
        }
        res_login = await client.post("/api/v1/auth/login", json=login_payload)
        assert res_login.status_code == 200
        token_data = res_login.json()["data"]
        access_token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # 3. Get /me
        res_me = await client.get("/api/v1/auth/me", headers=headers)
        assert res_me.status_code == 200
        assert res_me.json()["data"]["email"] == user_email

        # 4. Get Dashboard Summary
        res_dash = await client.get("/api/v1/dashboard/summary", headers=headers)
        assert res_dash.status_code == 200
        dash_data = res_dash.json()["data"]
        assert "total_equity_usdt" in dash_data
        assert dash_data["kill_switch_active"] is False

        # 5. Create a Trading Bot
        bot_payload = {
            "name": "Alpha BTC Scalper",
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "operating_mode": "demo",
            "allocated_budget": "1500.00",
            "risk_percentage": "0.50",
            "max_daily_trades": 5,
            "config": {
                "stop_loss_type": "atr",
                "stop_loss_value": "1.50",
                "take_profit_type": "rr_target",
                "take_profit_value": "2.00",
                "trailing_stop_enabled": True,
                "trailing_stop_callback": "0.50"
            }
        }
        res_bot = await client.post("/api/v1/bots", json=bot_payload, headers=headers)
        assert res_bot.status_code == 201
        bot_id = res_bot.json()["data"]["id"]
        assert res_bot.json()["data"]["status"] == "paused"

        # 6. Start the bot
        res_start = await client.post(f"/api/v1/bots/{bot_id}/start", headers=headers)
        assert res_start.status_code == 200
        assert res_start.json()["data"]["status"] == "active"

        # 7. Test Emergency Stop (Kill Switch)
        res_kill = await client.post("/api/v1/dashboard/emergency-stop", headers=headers)
        assert res_kill.status_code == 200
        assert res_kill.json()["data"]["kill_switch_active"] is True
        assert res_kill.json()["data"]["paused_bots"] >= 1

        # Check bot is now paused
        res_bot_check = await client.get(f"/api/v1/bots/{bot_id}", headers=headers)
        assert res_bot_check.json()["data"]["status"] == "paused"

        # 8. Reset Emergency Kill Switch
        res_reset = await client.post("/api/v1/dashboard/emergency-reset", headers=headers)
        assert res_reset.status_code == 200
        assert res_reset.json()["data"]["kill_switch_active"] is False

        # 8b. Deploy bot directly from Scanner setup
        scanner_bot_payload = {
            "symbol": "BEAMXUSDT",
            "signal_type": "BREAKOUT_BUY",
            "opportunity_score": "95.5",
            "entry_price": "0.00516",
            "take_profit_pct": "5.50",
            "stop_loss_pct": "1.80",
            "trailing_stop_enabled": True,
            "allocated_budget": "1200.00",
            "risk_percentage": "0.50",
            "operating_mode": "demo"
        }
        res_scanner_bot = await client.post("/api/v1/bots/from-scanner", json=scanner_bot_payload, headers=headers)
        assert res_scanner_bot.status_code == 201
        assert res_scanner_bot.json()["data"]["status"] == "active"
        assert res_scanner_bot.json()["data"]["symbol"] == "BEAMXUSDT"
        assert "Breakout" in res_scanner_bot.json()["data"]["name"]

        # 9. Check Markets Tickers
        res_tickers = await client.get("/api/v1/markets/tickers", headers=headers)
        assert res_tickers.status_code == 200
        tickers = res_tickers.json()["data"]
        assert len(tickers) > 0

        # 10. Check Market Scanner
        res_scanner = await client.get("/api/v1/markets/scanner", headers=headers)
        assert res_scanner.status_code == 200
        scanner_items = res_scanner.json()["data"]
        assert len(scanner_items) > 0

        # 11. Test Exchange Connection Persistence & Masking
        keys_payload = {
            "exchange": "binance",
            "api_key": "vmPUZE6mv9SD5VNHk4HlWFsOr6aKE2zvsw0MuIgwCIPy6utIco14y7Ju91duEh8A",
            "api_secret": "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j",
            "is_testnet": True
        }
        res_keys = await client.post("/api/v1/auth/exchange-keys", json=keys_payload, headers=headers)
        assert res_keys.status_code == 200
        assert res_keys.json()["success"] is True
        masked = res_keys.json()["data"]["api_key_masked"]
        assert masked.startswith("vmPU")
        assert masked.endswith("Eh8A")
        assert "..." in masked

        # Verify GET exchange-keys returns masked key
        res_get_keys = await client.get("/api/v1/auth/exchange-keys", headers=headers)
        assert res_get_keys.status_code == 200
        assert res_get_keys.json()["data"]["api_key_masked"] == masked

        # Verify Dashboard reflects real balance schema fields
        res_dash_with_keys = await client.get("/api/v1/dashboard/summary", headers=headers)
        assert res_dash_with_keys.status_code == 200
        summary_data = res_dash_with_keys.json()["data"]
        assert "is_real_balance" in summary_data
        assert "holdings" in summary_data
        assert "account_error" in summary_data
        assert summary_data["binance_connected"] is True

        # Test Disconnect Exchange Keys
        res_del_keys = await client.delete("/api/v1/auth/exchange-keys", headers=headers)
        assert res_del_keys.status_code == 200
        assert res_del_keys.json()["success"] is True

        # Confirm disconnected in dashboard
        res_dash_after = await client.get("/api/v1/dashboard/summary", headers=headers)
        assert res_dash_after.status_code == 200
        assert res_dash_after.json()["data"]["binance_connected"] is False
