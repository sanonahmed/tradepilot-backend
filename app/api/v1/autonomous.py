from typing import Dict, Any, List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.common import APIResponse
from app.services.trading.autonomous_engine import autonomous_service

router = APIRouter(prefix="/autonomous", tags=["Autonomous Auto-Pilot Execution"])


class AutonomousConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Enable or disable autonomous auto-pilot mode")
    min_score: Optional[float] = Field(None, ge=50.0, le=100.0, description="Minimum scanner opportunity score (e.g. 90.0)")
    max_active_bots: Optional[int] = Field(None, ge=1, le=10, description="Maximum simultaneous active bots (e.g. 3)")
    capital_allocation_pct: Optional[float] = Field(None, ge=5.0, le=100.0, description="Capital allocation % per autonomous trade")
    take_profit_pct: Optional[float] = Field(None, ge=1.0, le=100.0, description="Take profit target % (e.g. 10.0)")
    stop_loss_pct: Optional[float] = Field(None, ge=0.5, le=50.0, description="Stop loss protection % (e.g. 5.0)")
    cooldown_minutes: Optional[int] = Field(None, ge=1, le=180, description="Cooldown minutes between autonomous trades")


@router.get("/config", response_model=APIResponse[Dict[str, Any]])
async def get_autonomous_config_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve current Autonomous Auto-Pilot operational parameters,
    active cooldown status, and execution configuration.
    """
    cfg = autonomous_service.get_config()
    return APIResponse(
        success=True,
        message="Autonomous Auto-Pilot configuration retrieved successfully",
        data=cfg
    )


@router.post("/config", response_model=APIResponse[Dict[str, Any]])
async def update_autonomous_config_endpoint(
    request: AutonomousConfigUpdateRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Update Autonomous Auto-Pilot trading rules, minimum score threshold,
    and capital allocation parameters.
    """
    updates = request.model_dump(exclude_unset=True)
    cfg = autonomous_service.update_config(updates)
    return APIResponse(
        success=True,
        message="Autonomous Auto-Pilot configuration updated successfully",
        data=cfg
    )


@router.post("/toggle", response_model=APIResponse[Dict[str, Any]])
async def toggle_autonomous_autopilot_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Toggle Autonomous Auto-Pilot trading mode ON or OFF.
    When enabled, TradePilot AI continuously screens scanner opportunities
    and automatically executes high-conviction trades onto Binance.
    """
    cfg = autonomous_service.toggle_autopilot()
    st = "enabled" if cfg["enabled"] else "disabled"
    return APIResponse(
        success=True,
        message=f"Autonomous Auto-Pilot mode is now {st.upper()}",
        data=cfg
    )


@router.get("/logs", response_model=APIResponse[List[Dict[str, Any]]])
async def get_autonomous_logs_endpoint(
    limit: int = 40,
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve real-time audit logs of autonomous engine cycles,
    trade evaluations, scanner filters, and execution events.
    """
    logs = autonomous_service.get_logs(limit=limit)
    return APIResponse(
        success=True,
        message=f"Retrieved {len(logs)} autonomous execution decision logs",
        data=logs
    )


@router.post("/scan-and-execute-now", response_model=APIResponse[Dict[str, Any]])
async def trigger_autonomous_evaluation_now_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Manually trigger an immediate autonomous evaluation cycle to screen
    the whole market and execute high-conviction setups if eligible.
    """
    res = await autonomous_service.run_evaluation_cycle()
    status_msg = "Autonomous trade executed successfully" if res.get("executed") else f"Cycle completed: {res.get('reason')}"
    return APIResponse(
        success=True,
        message=status_msg,
        data=res
    )
