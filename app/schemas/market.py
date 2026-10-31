from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict


class TickerPrice(BaseModel):
    symbol: str
    price: Decimal
    price_change_24h_pct: Decimal
    high_24h: Decimal
    low_24h: Decimal
    volume_24h: Decimal
    quote_volume_24h: Decimal


class CandleData(BaseModel):
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class ScannerResultItem(BaseModel):
    symbol: str
    price: Decimal
    change_24h: Decimal
    rsi_14: Decimal
    macd_signal: str  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    ema_trend: str    # 'UPTREND', 'DOWNTREND', 'RANGING'
    bollinger_state: str  # 'SQUEEZE', 'UPPER_BREAKOUT', 'LOWER_BREAKOUT', 'NORMAL'
    volume_surge_ratio: Decimal
    opportunity_score: Decimal  # 0 to 100
    signal_type: Optional[str] = None  # 'POTENTIAL_BUY', 'POTENTIAL_SELL', None
    reasons: List[str] = []
