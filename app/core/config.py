import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    APP_NAME: str = "TradePilot AI"
    VERSION: str = "1.0.0"

    # Security
    SECRET_KEY: str = "tradepilot-dev-secret-key-at-least-32-characters-long-12345"
    ENCRYPTION_KEY: str = "tradepilot-aes256-key-32bytes-exactly!!"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./tradepilot.db"

    # Redis
    REDIS_URL: Optional[str] = None

    # Binance Defaults
    BINANCE_USE_TESTNET: bool = True
    BINANCE_REST_BASE: str = "https://testnet.binance.vision"
    BINANCE_WS_BASE: str = "wss://testnet.binance.vision/ws"
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None

    # Gemini & FCM
    GEMINI_API_KEY: Optional[str] = None
    FCM_SERVER_KEY: Optional[str] = None

    # Risk Defaults (Mandatory)
    DEFAULT_MAX_RISK_PER_TRADE_PCT: float = 0.50
    DEFAULT_MAX_DAILY_LOSS_PCT: float = 2.00
    DEFAULT_MAX_SIMULTANEOUS_POSITIONS: int = 3
    DEFAULT_MAX_PORTFOLIO_EXPOSURE_PCT: float = 30.00
    DEFAULT_MAX_CONSECUTIVE_LOSSES: int = 3

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
