from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, ConfigDict


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password at least 8 characters")
    full_name: Optional[str] = None


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool
    is_verified: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExchangeConnectionRequest(BaseModel):
    exchange: str = "binance"
    api_key: str = Field(..., min_length=16)
    api_secret: str = Field(..., min_length=16)
    is_testnet: bool = True


class ExchangeConnectionResponse(BaseModel):
    id: str
    exchange: str
    api_key_masked: str
    is_testnet: bool
    is_active: bool
    permissions_verified: bool
    can_trade: bool
    can_withdraw: bool
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
