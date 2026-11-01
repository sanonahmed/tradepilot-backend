import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_gemini_ai_analyst_token_analysis():
    """Verifies that the Gemini AI Analyst generates multi-factor technical verdicts and targets."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register and login test user
        email = "analyst_test@tradepilot.ai"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "PilotPassword123!",
            "full_name": "Analyst Tester"
        })
        res_login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "PilotPassword123!"
        })
        token = res_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Analyze BTCUSDT
        payload = {
            "symbol": "BTCUSDT",
            "prompt": "Evaluate 15m breakout continuation potential."
        }
        res_analysis = await client.post("/api/v1/analyst/analyze", json=payload, headers=headers)
        assert res_analysis.status_code == 200
        data = res_analysis.json()["data"]
        assert data["symbol"] == "BTCUSDT"
        assert data["verdict"] in ["STRONG_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "STRONG_BEARISH"]
        assert "confidence_score" in data
        assert "target_take_profit" in data
        assert "target_stop_loss" in data
        assert "support_levels" in data
        assert "resistance_levels" in data
        assert len(data["key_catalysts"]) >= 1

        # 2. Get Macro Briefing
        res_briefing = await client.get("/api/v1/analyst/briefing", headers=headers)
        assert res_briefing.status_code == 200
        briefing_data = res_briefing.json()["data"]
        assert "market_sentiment_score" in briefing_data
        assert "sentiment_label" in briefing_data
        assert "executive_briefing" in briefing_data


@pytest.mark.asyncio
async def test_whale_tracking_and_order_flow():
    """Verifies whale transactions, anomaly alerts, and flow summary."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email = "whale_test@tradepilot.ai"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "PilotPassword123!",
            "full_name": "Whale Tester"
        })
        res_login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "PilotPassword123!"
        })
        token = res_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Whale transactions
        res_txs = await client.get("/api/v1/whales/transactions?min_usd=25000&limit=15", headers=headers)
        assert res_txs.status_code == 200
        txs = res_txs.json()["data"]
        assert isinstance(txs, list)

        # 2. Whale alerts
        res_alerts = await client.get("/api/v1/whales/alerts", headers=headers)
        assert res_alerts.status_code == 200
        alerts = res_alerts.json()["data"]
        assert isinstance(alerts, list)
        assert len(alerts) >= 1

        # 3. Flow summary
        res_summary = await client.get("/api/v1/whales/flow-summary", headers=headers)
        assert res_summary.status_code == 200
        summary = res_summary.json()["data"]
        assert len(summary) >= 1
        assert "net_inflow_usd" in summary[0]


@pytest.mark.asyncio
async def test_crypto_news_and_sentiment_feed():
    """Verifies real-time crypto news feed and aggregate market sentiment overview."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email = "news_test@tradepilot.ai"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "PilotPassword123!",
            "full_name": "News Tester"
        })
        res_login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "PilotPassword123!"
        })
        token = res_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. News feed
        res_feed = await client.get("/api/v1/news/feed?limit=10", headers=headers)
        assert res_feed.status_code == 200
        feed = res_feed.json()["data"]
        assert len(feed) >= 1
        assert "sentiment" in feed[0]
        assert "sentiment_score" in feed[0]
        assert "urgency" in feed[0]

        # 2. Sentiment overview
        res_overview = await client.get("/api/v1/news/sentiment-overview", headers=headers)
        assert res_overview.status_code == 200
        overview = res_overview.json()["data"]
        assert "sentiment_score" in overview
        assert "bullish_percentage" in overview


@pytest.mark.asyncio
async def test_autonomous_autopilot_controls_and_execution():
    """Verifies Auto-Pilot autonomous engine configuration, toggle, and manual cycle evaluation."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        email = "auto_test@tradepilot.ai"
        await client.post("/api/v1/auth/register", json={
            "email": email,
            "password": "PilotPassword123!",
            "full_name": "Auto Tester"
        })
        res_login = await client.post("/api/v1/auth/login", json={
            "email": email,
            "password": "PilotPassword123!"
        })
        token = res_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Get config
        res_cfg = await client.get("/api/v1/autonomous/config", headers=headers)
        assert res_cfg.status_code == 200
        assert "min_score" in res_cfg.json()["data"]

        # 2. Update config
        res_update = await client.post("/api/v1/autonomous/config", json={
            "min_score": 92.0,
            "max_active_bots": 4,
            "capital_allocation_pct": 20.0
        }, headers=headers)
        assert res_update.status_code == 200
        assert res_update.json()["data"]["min_score"] == 92.0
        assert res_update.json()["data"]["max_active_bots"] == 4

        # 3. Toggle Auto-Pilot
        res_toggle = await client.post("/api/v1/autonomous/toggle", headers=headers)
        assert res_toggle.status_code == 200
        assert "is_running" in res_toggle.json()["data"]

        # 4. Read logs
        res_logs = await client.get("/api/v1/autonomous/logs", headers=headers)
        assert res_logs.status_code == 200
        assert len(res_logs.json()["data"]) >= 1

        # 5. Trigger evaluation cycle
        res_eval = await client.post("/api/v1/autonomous/scan-and-execute-now", headers=headers)
        assert res_eval.status_code == 200
        assert "executed" in res_eval.json()["data"]
