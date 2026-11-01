from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict


class BinanceTradeHistoryItem(BaseModel):
    id: str
    order_id: str
    trade_id: Optional[str] = None
    symbol: str
    token: str
    side: str  # 'BUY' or 'SELL'
    price: Decimal
    quantity: Decimal
    quote_qty: Decimal
    commission: Decimal = Decimal("0.0")
    commission_asset: str = "USDT"
    realized_pnl: Decimal = Decimal("0.0")
    realized_pnl_pct: Decimal = Decimal("0.0")
    status: str = "FILLED"
    time: int  # Epoch milliseconds
    executed_at: str
    time_ago: str
    mode: str = "Binance Demo"  # 'Binance Demo' or 'Binance Live'
    bot_name: Optional[str] = None
    exit_reason: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AnalyticsSummaryResponse(BaseModel):
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    total_volume_usdt: Decimal
    total_realized_pnl_usdt: Decimal
    total_fees_usdt: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    max_drawdown_pct: Decimal
    active_positions: int
    portfolio_equity_usdt: Decimal = Decimal("0.0")
    available_balance_usdt: Decimal = Decimal("0.0")
    allocated_balance_usdt: Decimal = Decimal("0.0")
    unrealized_pnl_usdt: Decimal = Decimal("0.0")
    daily_pnl_24h_usdt: Decimal = Decimal("0.0")
    connection_mode: str = "Binance Demo"
    is_connected: bool = True
    last_sync_time: str = ""
    venue: str = "Binance Spot"

    model_config = ConfigDict(from_attributes=True)
