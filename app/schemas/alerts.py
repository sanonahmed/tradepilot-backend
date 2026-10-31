from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class AlertResponse(BaseModel):
    id: str
    category: str
    title: str
    message: str
    level: str
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationPreferencesUpdate(BaseModel):
    fcm_token: Optional[str] = None
    notify_signals: Optional[bool] = None
    notify_orders: Optional[bool] = None
    notify_risk_events: Optional[bool] = None
    notify_price_thresholds: Optional[bool] = None


class NotificationPreferencesResponse(BaseModel):
    fcm_token: Optional[str] = None
    notify_signals: bool
    notify_orders: bool
    notify_risk_events: bool
    notify_price_thresholds: bool
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
