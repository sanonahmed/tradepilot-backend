from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict
from app.schemas.bots import TradingBotResponse


class PositionInfo(BaseModel):
    symbol: str
    side: str
    entry_price: Decimal
    current_price: Decimal
    quantity: Decimal
    unrealized_pnl: Decimal
    pnl_percentage: Decimal


class AssetHoldingInfo(BaseModel):
    asset: str
    free: float
    locked: float
    total: float
    unit_price_usdt: float
    value_usdt: float


class OpenOrderInfo(BaseModel):
    order_id: str
    client_order_id: Optional[str] = None
    symbol: str
    token: str
    side: str
    order_type: str
    price: Decimal
    stop_price: Optional[Decimal] = None
    orig_qty: Decimal
    executed_qty: Decimal
    status: str
    time: int
    reduce_only: bool = False


class DashboardSummaryResponse(BaseModel):
    binance_connected: bool
    connection_mode: str  # 'live', 'demo'
    is_real_balance: bool  # True when fetched directly from Binance Spot API
    account_error: Optional[str] = None
    total_equity_usdt: Decimal
    available_balance_usdt: Decimal
    allocated_balance_usdt: Decimal
    realized_pnl_24h: Decimal
    total_realized_pnl: Decimal = Decimal("0.0")
    unrealized_pnl: Decimal
    crypto_holdings_value_usdt: Decimal = Decimal("0.0")
    active_bots_count: int
    open_orders_count: int
    risk_utilization_pct: Decimal
    kill_switch_active: bool
    active_bots: List[TradingBotResponse] = []
    open_positions: List[PositionInfo] = []
    open_orders: List[OpenOrderInfo] = []
    holdings: List[AssetHoldingInfo] = []
    recent_performance_pct: Decimal = Decimal("0.0")

    model_config = ConfigDict(from_attributes=True)
