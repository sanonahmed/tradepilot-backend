from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.common import APIResponse
from app.services.analyst.gemini_analyst import gemini_analyst_service

router = APIRouter(prefix="/analyst", tags=["Gemini AI Analyst"])


class TokenAnalysisRequest(BaseModel):
    symbol: str = Field(..., description="Trading pair symbol (e.g. BTCUSDT, ETHUSDT, SOLUSDT)")
    prompt: Optional[str] = Field(None, description="Optional custom question or focus for the AI analyst")
    gemini_api_key: Optional[str] = Field(None, description="Optional custom Gemini API key")


@router.post("/analyze", response_model=APIResponse[Dict[str, Any]])
async def analyze_token_endpoint(
    request: TokenAnalysisRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Generate deep technical analysis, verdict, and trade targets for a specific cryptocurrency
    powered by Google Gemini 2.0 Flash and TradePilot's Quantitative Neural Engine.
    """
    res = await gemini_analyst_service.analyze_token(
        symbol=request.symbol,
        user_prompt=request.prompt,
        custom_gemini_key=request.gemini_api_key
    )
    return APIResponse(
        success=True,
        message=f"Analysis for {request.symbol.upper()} generated successfully ({res.get('provider')})",
        data=res
    )


@router.get("/briefing", response_model=APIResponse[Dict[str, Any]])
async def get_macro_briefing_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve comprehensive daily macro cryptocurrency market briefing,
    sentiment index, and trending market breakouts.
    """
    res = await gemini_analyst_service.generate_macro_briefing()
    return APIResponse(
        success=True,
        message="Daily macro crypto briefing generated successfully",
        data=res
    )
