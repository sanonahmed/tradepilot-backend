from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.core.database import get_db
from app.api.deps import get_current_user
from app.models.entities import User, Order, TradeProposal, TradingSignal, AuditLog
from app.schemas.orders import OrderResponse, TradeProposalResponse, ProposalDecisionRequest
from app.schemas.common import APIResponse

router = APIRouter(prefix="/orders", tags=["Orders & Execution"])


@router.get("", response_model=APIResponse[List[OrderResponse]])
async def list_orders(
    mode: Optional[str] = None,
    symbol: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Order).where(Order.user_id == current_user.id)
    if mode:
        stmt = stmt.where(Order.mode == mode)
    if symbol:
        stmt = stmt.where(Order.symbol == symbol.upper())
    stmt = stmt.order_by(Order.created_at.desc()).limit(100)

    result = await db.execute(stmt)
    orders = result.scalars().all()
    return APIResponse(success=True, data=[OrderResponse.model_validate(o) for o in orders])


@router.get("/proposals/pending", response_model=APIResponse[List[TradeProposalResponse]])
async def list_pending_proposals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    now = datetime.now(timezone.utc)
    stmt = select(TradeProposal).where(
        TradeProposal.user_id == current_user.id,
        TradeProposal.status == "AWAITING_APPROVAL",
        TradeProposal.expires_at > now
    ).order_by(TradeProposal.created_at.desc())

    result = await db.execute(stmt)
    proposals = result.scalars().all()
    return APIResponse(success=True, data=[TradeProposalResponse.model_validate(p) for p in proposals])


@router.post("/proposals/{proposal_id}/decision", response_model=APIResponse[dict])
async def decide_proposal(
    proposal_id: str,
    request: ProposalDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(TradeProposal).where(
        TradeProposal.id == proposal_id,
        TradeProposal.user_id == current_user.id
    )
    result = await db.execute(stmt)
    proposal = result.scalar_one_or_none()

    if not proposal:
        raise HTTPException(status_code=404, detail="Trade proposal not found")

    if proposal.status != "AWAITING_APPROVAL":
        raise HTTPException(status_code=400, detail=f"Proposal is already {proposal.status}")

    now = datetime.now(timezone.utc)
    if proposal.expires_at < now:
        proposal.status = "EXPIRED"
        await db.commit()
        raise HTTPException(status_code=400, detail="Proposal has expired")

    decision = request.decision.upper()
    if decision not in ["APPROVE", "DECLINE"]:
        raise HTTPException(status_code=400, detail="Decision must be 'APPROVE' or 'DECLINE'")

    proposal.status = "APPROVED" if decision == "APPROVE" else "DECLINED"
    proposal.responded_at = now

    audit = AuditLog(
        user_id=current_user.id,
        action=f"PROPOSAL_{proposal.status}",
        resource_type="TRADE_PROPOSAL",
        resource_id=proposal.id,
        details={"symbol": proposal.symbol, "decision": decision, "reason": request.reason}
    )
    db.add(audit)

    await db.commit()
    return APIResponse(
        success=True,
        message=f"Trade proposal {proposal.status.lower()}",
        data={"proposal_id": proposal.id, "status": proposal.status}
    )
