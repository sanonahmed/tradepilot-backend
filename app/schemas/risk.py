from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class RiskLimitResponse(BaseModel):
    id: str
    user_id: str
    max_risk_per_trade_pct: Decimal
    max_daily_loss_pct: Decimal
    max_simultaneous_positions: int
    max_portfolio_exposure_pct: Decimal
    max_consecutive_losses: int
    max_slippage_pct: Decimal
    trade_allocation_pct: Decimal = Decimal("30.00")
    take_profit_pct: Decimal = Decimal("10.00")
    stop_loss_pct: Decimal = Decimal("5.00")
    trailing_stop_enabled: bool = True
    trailing_stop_callback_pct: Decimal = Decimal("0.50")
    kill_switch_active: bool
    daily_trading_locked_until: Optional[datetime] = None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RiskLimitUpdate(BaseModel):
    max_risk_per_trade_pct: Optional[Decimal] = Field(None, ge=0.01, le=100.0)
    max_daily_loss_pct: Optional[Decimal] = Field(None, ge=0.01, le=100.0)
    max_simultaneous_positions: Optional[int] = Field(None, ge=1, le=100)
    max_portfolio_exposure_pct: Optional[Decimal] = Field(None, ge=0.01, le=100.0)
    max_consecutive_losses: Optional[int] = Field(None, ge=1, le=100)
    max_slippage_pct: Optional[Decimal] = Field(None, ge=0.01, le=20.0)
    trade_allocation_pct: Optional[Decimal] = Field(None, ge=0.1, le=100.0)
    take_profit_pct: Optional[Decimal] = Field(None, ge=0.1, le=1000.0)
    stop_loss_pct: Optional[Decimal] = Field(None, ge=0.1, le=100.0)
    trailing_stop_enabled: Optional[bool] = None
    trailing_stop_callback_pct: Optional[Decimal] = Field(None, ge=0.01, le=50.0)


class KillSwitchRequest(BaseModel):
    reason: str = Field(default="User manual emergency stop", min_length=3)
