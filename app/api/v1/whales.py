from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.common import APIResponse
from app.services.whales.whale_tracker import whale_tracker_service

router = APIRouter(prefix="/whales", tags=["Whale Tracker & Order Flow"])


@router.get("/transactions", response_model=APIResponse[List[Dict[str, Any]]])
async def get_whale_transactions_endpoint(
    min_usd: float = Query(50000.0, ge=10000.0, description="Minimum order size in USD"),
    limit: int = Query(25, ge=1, le=100, description="Max transactions to return"),
    current_user: User = Depends(get_current_user)
):
    """
    Stream live high-value whale transactions ($50k - $250k+ USDT)
    directly detected from Binance market trade execution flows.
    """
    txs = await whale_tracker_service.fetch_recent_whale_transactions(min_usd=min_usd, limit=limit)
    return APIResponse(
        success=True,
        message=f"Retrieved {len(txs)} institutional whale transactions (>= ${min_usd:,.0f})",
        data=txs
    )


@router.get("/alerts", response_model=APIResponse[List[Dict[str, Any]]])
async def get_whale_alerts_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve real-time institutional anomaly alerts including whale accumulation zones,
    aggressive dumping, and order flow volume imbalances.
    """
    alerts = await whale_tracker_service.get_whale_alerts()
    return APIResponse(
        success=True,
        message=f"Retrieved {len(alerts)} active institutional whale alerts",
        data=alerts
    )


@router.get("/flow-summary", response_model=APIResponse[Dict[str, Any]])
async def get_whale_flow_summary_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve aggregated net whale inflows and outflows across major cryptocurrency assets.
    """
    summary = await whale_tracker_service.get_flow_summary()
    return APIResponse(
        success=True,
        message="Whale order flow summary calculated successfully",
        data=summary
    )
