import pytest
import asyncio
from decimal import Decimal
from app.core.security import hash_password, verify_password, encrypt_secret, decrypt_secret, mask_secret
from app.core.database import init_db, AsyncSessionLocal
from app.models.entities import User, RiskLimit, TradingBot, BotConfiguration, Order, Alert


def test_password_hashing():
    raw = "TradingBotP@ssw0rd!2026"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False


def test_aes_gcm_credential_encryption():
    secret_key = "d94b005e839e4a3b9347d483789ab2f4"
    encrypted = encrypt_secret(secret_key)
    assert encrypted != secret_key
    decrypted = decrypt_secret(encrypted)
    assert decrypted == secret_key


def test_secret_masking():
    key = "ab1234567890cdefghij"
    masked = mask_secret(key)
    assert masked.startswith("ab12")
    assert masked.endswith("ghij")
    assert "..." in masked


import uuid

@pytest.mark.asyncio
async def test_database_models_initialization():
    # Verify tables can be created cleanly
    await init_db()

    unique_email = f"trader_{uuid.uuid4().hex[:8]}@tradepilot.ai"
    async with AsyncSessionLocal() as session:
        # Test creating a user
        user = User(
            email=unique_email,
            hashed_password=hash_password("SecureTr@de2026"),
            full_name="Alex Mercer"
        )
        session.add(user)
        await session.flush()

        # Test risk limit initialization
        risk = RiskLimit(
            user_id=user.id,
            max_risk_per_trade_pct=Decimal("0.50"),
            max_daily_loss_pct=Decimal("2.00"),
            max_simultaneous_positions=3,
            max_portfolio_exposure_pct=Decimal("30.00")
        )
        session.add(risk)

        # Test bot creation
        bot = TradingBot(
            user_id=user.id,
            name="BTC Trend Pilot",
            symbol="BTCUSDT",
            timeframe="1h",
            operating_mode="demo",
            status="paused",
            allocated_budget=Decimal("2000.00"),
            risk_percentage=Decimal("0.50")
        )
        session.add(bot)
        await session.flush()

        bot_cfg = BotConfiguration(
            bot_id=bot.id,
            stop_loss_type="atr",
            stop_loss_value=Decimal("1.5000"),
            take_profit_type="rr_target",
            take_profit_value=Decimal("2.0000")
        )
        session.add(bot_cfg)

        await session.commit()

        assert user.id is not None
        assert risk.id is not None
        assert bot.id is not None
        assert bot_cfg.id is not None
