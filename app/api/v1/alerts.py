from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.entities import User, Alert, NotificationPreference
from app.schemas.alerts import AlertResponse, NotificationPreferencesResponse, NotificationPreferencesUpdate
from app.schemas.common import APIResponse

router = APIRouter(prefix="/alerts", tags=["Alerts & Notifications"])


@router.get("", response_model=APIResponse[List[AlertResponse]])
async def list_alerts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Alert).where(Alert.user_id == current_user.id).order_by(Alert.created_at.desc()).limit(50)
    result = await db.execute(stmt)
    alerts = result.scalars().all()
    return APIResponse(success=True, data=[AlertResponse.model_validate(a) for a in alerts])


@router.post("/{alert_id}/read", response_model=APIResponse[dict])
async def mark_alert_read(
    alert_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Alert).where(Alert.id == alert_id, Alert.user_id == current_user.id)
    result = await db.execute(stmt)
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_read = True
    await db.commit()
    return APIResponse(success=True, message="Alert marked as read", data={"id": alert_id})


@router.get("/preferences", response_model=APIResponse[NotificationPreferencesResponse])
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(NotificationPreference).where(NotificationPreference.user_id == current_user.id)
    result = await db.execute(stmt)
    prefs = result.scalar_one_or_none()
    if not prefs:
        prefs = NotificationPreference(user_id=current_user.id)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return APIResponse(success=True, data=NotificationPreferencesResponse.model_validate(prefs))


@router.put("/preferences", response_model=APIResponse[NotificationPreferencesResponse])
async def update_preferences(
    request: NotificationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(NotificationPreference).where(NotificationPreference.user_id == current_user.id)
    result = await db.execute(stmt)
    prefs = result.scalar_one_or_none()
    if not prefs:
        prefs = NotificationPreference(user_id=current_user.id)
        db.add(prefs)

    if request.fcm_token is not None:
        prefs.fcm_token = request.fcm_token
    if request.notify_signals is not None:
        prefs.notify_signals = request.notify_signals
    if request.notify_orders is not None:
        prefs.notify_orders = request.notify_orders
    if request.notify_risk_events is not None:
        prefs.notify_risk_events = request.notify_risk_events
    if request.notify_price_thresholds is not None:
        prefs.notify_price_thresholds = request.notify_price_thresholds

    await db.commit()
    await db.refresh(prefs)
    return APIResponse(success=True, message="Preferences updated", data=NotificationPreferencesResponse.model_validate(prefs))
