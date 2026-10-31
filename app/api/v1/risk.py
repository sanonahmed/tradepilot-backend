from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.entities import User, RiskLimit, RiskEvent, AuditLog
from app.schemas.risk import RiskLimitResponse, RiskLimitUpdate
from app.schemas.common import APIResponse

router = APIRouter(prefix="/risk", tags=["Risk Management"])


@router.get("/limits", response_model=APIResponse[RiskLimitResponse])
async def get_risk_limits(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result = await db.execute(stmt)
    risk_limit = result.scalar_one_or_none()

    if not risk_limit:
        risk_limit = RiskLimit(user_id=current_user.id)
        db.add(risk_limit)
        await db.commit()
        await db.refresh(risk_limit)

    return APIResponse(success=True, data=RiskLimitResponse.model_validate(risk_limit))


@router.put("/limits", response_model=APIResponse[RiskLimitResponse])
async def update_risk_limits(
    request: RiskLimitUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Updates user risk limits.
    Notice: System enforces hard ceilings to prevent catastrophic blowout:
    - Max risk per trade <= 1.0%
    - Max daily loss <= 5.0%
    - Max simultaneous positions <= 5
    - Max portfolio exposure <= 50.0%
    """
    stmt = select(RiskLimit).where(RiskLimit.user_id == current_user.id)
    result = await db.execute(stmt)
    risk_limit = result.scalar_one_or_none()

    if not risk_limit:
        risk_limit = RiskLimit(user_id=current_user.id)
        db.add(risk_limit)

    updated_fields = {}
    if request.max_risk_per_trade_pct is not None:
        risk_limit.max_risk_per_trade_pct = request.max_risk_per_trade_pct
        updated_fields["max_risk_per_trade_pct"] = str(request.max_risk_per_trade_pct)

    if request.max_daily_loss_pct is not None:
        risk_limit.max_daily_loss_pct = request.max_daily_loss_pct
        updated_fields["max_daily_loss_pct"] = str(request.max_daily_loss_pct)

    if request.max_simultaneous_positions is not None:
        risk_limit.max_simultaneous_positions = request.max_simultaneous_positions
        updated_fields["max_simultaneous_positions"] = request.max_simultaneous_positions

    if request.max_portfolio_exposure_pct is not None:
        risk_limit.max_portfolio_exposure_pct = request.max_portfolio_exposure_pct
        updated_fields["max_portfolio_exposure_pct"] = str(request.max_portfolio_exposure_pct)

    if request.max_consecutive_losses is not None:
        risk_limit.max_consecutive_losses = request.max_consecutive_losses
        updated_fields["max_consecutive_losses"] = request.max_consecutive_losses

    if request.max_slippage_pct is not None:
        risk_limit.max_slippage_pct = request.max_slippage_pct
        updated_fields["max_slippage_pct"] = str(request.max_slippage_pct)

    if request.trade_allocation_pct is not None:
        risk_limit.trade_allocation_pct = request.trade_allocation_pct
        updated_fields["trade_allocation_pct"] = str(request.trade_allocation_pct)

    if request.take_profit_pct is not None:
        risk_limit.take_profit_pct = request.take_profit_pct
        updated_fields["take_profit_pct"] = str(request.take_profit_pct)

    if request.stop_loss_pct is not None:
        risk_limit.stop_loss_pct = request.stop_loss_pct
        updated_fields["stop_loss_pct"] = str(request.stop_loss_pct)

    if request.trailing_stop_enabled is not None:
        risk_limit.trailing_stop_enabled = request.trailing_stop_enabled
        updated_fields["trailing_stop_enabled"] = request.trailing_stop_enabled

    if request.trailing_stop_callback_pct is not None:
        risk_limit.trailing_stop_callback_pct = request.trailing_stop_callback_pct
        updated_fields["trailing_stop_callback_pct"] = str(request.trailing_stop_callback_pct)

    audit = AuditLog(
        user_id=current_user.id,
        action="UPDATE_RISK_LIMITS",
        resource_type="RISK_LIMITS",
        resource_id=risk_limit.id,
        details=updated_fields
    )
    db.add(audit)

    await db.commit()
    await db.refresh(risk_limit)

    return APIResponse(
        success=True,
        message="Risk limits updated successfully",
        data=RiskLimitResponse.model_validate(risk_limit)
    )
