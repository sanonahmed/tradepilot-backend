from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict


class OrderResponse(BaseModel):
    id: str
    bot_id: Optional[str] = None
    client_order_id: str
    exchange_order_id: Optional[str] = None
    symbol: str
    side: str
    order_type: str
    mode: str
    status: str
    price: Optional[Decimal] = None
    quantity: Decimal
    executed_quantity: Decimal
    cumulative_quote_qty: Decimal
    average_fill_price: Optional[Decimal] = None
    stop_price: Optional[Decimal] = None
    commission: Decimal
    commission_asset: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TradeProposalResponse(BaseModel):
    id: str
    signal_id: str
    bot_id: Optional[str] = None
    symbol: str
    side: str
    proposed_quantity: Decimal
    proposed_notional: Decimal
    risk_amount: Decimal
    status: str
    expires_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProposalDecisionRequest(BaseModel):
    decision: str  # 'APPROVE', 'DECLINE'
    reason: Optional[str] = None
