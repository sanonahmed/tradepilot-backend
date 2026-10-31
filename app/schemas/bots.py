from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, ConfigDict


class BotConfigCreate(BaseModel):
    stop_loss_type: str = "atr"  # fixed, percentage, atr
    stop_loss_value: Decimal = Field(default=Decimal("1.5000"), ge=0.1)
    take_profit_type: str = "rr_target"  # fixed, percentage, rr_target
    take_profit_value: Decimal = Field(default=Decimal("2.0000"), ge=0.5)
    trailing_stop_enabled: bool = False
    trailing_stop_callback: Decimal = Field(default=Decimal("0.50"), ge=0.1, le=5.0)
    strategy_params: Dict[str, Any] = Field(default_factory=dict)


class BotConfigResponse(BotConfigCreate):
    id: str
    bot_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TradingBotCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    symbol: str = Field(..., json_schema_extra={"example": "BTCUSDT"})
    timeframe: str = Field(default="1h", json_schema_extra={"example": "1h"})
    operating_mode: str = Field(default="demo", json_schema_extra={"example": "demo"})  # demo, manual_approval, automatic
    allocated_budget: Decimal = Field(default=Decimal("1000.00"), ge=1.0)
    risk_percentage: Decimal = Field(default=Decimal("0.50"), ge=0.01, le=100.0)
    max_daily_trades: int = Field(default=5, ge=1, le=100)
    config: BotConfigCreate = Field(default_factory=BotConfigCreate)


class TradingBotUpdate(BaseModel):
    name: Optional[str] = None
    operating_mode: Optional[str] = None
    allocated_budget: Optional[Decimal] = None
    risk_percentage: Optional[Decimal] = None
    max_daily_trades: Optional[int] = None
    config: Optional[BotConfigCreate] = None


class ScannerTradeRequest(BaseModel):
    symbol: str = Field(..., description="Binance symbol to trade, e.g. BEAMXUSDT")
    signal_type: Optional[str] = Field(default="POTENTIAL_BUY", description="Scanner signal: POTENTIAL_BUY, BREAKOUT_BUY, DIP_BUY")
    opportunity_score: Optional[Decimal] = Field(default=Decimal("85.0"), description="Opportunity score 0-100")
    entry_price: Optional[Decimal] = Field(default=None, description="Current price at time of scan")
    take_profit_pct: Decimal = Field(default=Decimal("10.00"), ge=0.1, le=1000.0, description="Take profit target in percent")
    stop_loss_pct: Decimal = Field(default=Decimal("5.00"), ge=0.1, le=100.0, description="Stop loss protection in percent")
    trailing_stop_enabled: bool = Field(default=True, description="Enable trailing stop to lock in profits")
    trailing_stop_callback: Decimal = Field(default=Decimal("0.50"), ge=0.01, le=20.0, description="Trailing stop callback in percent")
    capital_allocation_pct: Optional[Decimal] = Field(default=None, ge=0.1, le=100.0, description="Capital allocation % of portfolio")
    allocated_budget: Decimal = Field(default=Decimal("1000.00"), ge=1.0, description="USDT capital allocated to this bot")
    risk_percentage: Decimal = Field(default=Decimal("0.50"), ge=0.01, le=100.0, description="Risk percentage per trade")
    operating_mode: Optional[str] = Field(default="demo", description="Trading mode: demo or live")
    timeframe: Optional[str] = Field(default="15m", description="Trading timeframe")


class TradingBotResponse(BaseModel):
    id: str
    user_id: str
    name: str
    symbol: str
    token: Optional[str] = None
    timeframe: str
    operating_mode: str
    status: str
    allocated_budget: Decimal
    risk_percentage: Decimal
    max_daily_trades: int
    trades_count_today: int
    last_active_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    configuration: Optional[BotConfigResponse] = None
    # Live Trade Position Details
    trade_size_usdt: Optional[Decimal] = None
    executed_qty: Optional[Decimal] = None
    entry_price: Optional[Decimal] = None
    marked_price: Optional[Decimal] = None
    unrealized_pnl_usdt: Optional[Decimal] = None
    unrealized_pnl_pct: Optional[Decimal] = None
    take_profit_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    binance_order_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
