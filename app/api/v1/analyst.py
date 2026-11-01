from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.common import APIResponse
from app.services.analyst.gemini_analyst import gemini_analyst_service

router = APIRouter(prefix="/analyst", tags=["Gemini AI Analyst"])


class TokenAnalysisRequest(BaseModel):
    symbol: str = Field(..., description="Trading pair or cryptocurrency token (e.g. BTC, ETH, SOL, PEPE, DOGE, KITE, BTCUSDT)")
    prompt: Optional[str] = Field(None, description="Optional custom question or focus for the AI analyst")
    gemini_api_key: Optional[str] = Field(None, description="Optional custom Gemini API key")


@router.post("/analyze", response_model=APIResponse[Dict[str, Any]])
async def analyze_token_endpoint(
    request: TokenAnalysisRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Generate deep technical analysis, verdict, and trade targets for ANY Binance listed coin/token
    powered by Google Gemini 3.8 Flash and TradePilot's Quantitative Neural Engine.
    Supports raw coin names (e.g. PEPE, DOGE, SOL, KITE), full pairs (BTCUSDT, ETHBTC), and futures multipliers (1000PEPE).
    """
    res = await gemini_analyst_service.analyze_token(
        symbol=request.symbol,
        user_prompt=request.prompt,
        custom_gemini_key=request.gemini_api_key
    )
    return APIResponse(
        success=True,
        message=f"Analysis for {res.get('symbol', request.symbol.upper())} generated successfully ({res.get('provider')})",
        data=res
    )


@router.get("/tokens", response_model=APIResponse[List[Dict[str, Any]]])
@router.get("/search", response_model=APIResponse[List[Dict[str, Any]]])
async def search_analyst_tokens_endpoint(
    query: Optional[str] = Query(None, description="Search query for token or pair (e.g. BTC, PEPE, SOL)"),
    limit: int = Query(50, ge=1, le=200, description="Max tokens to return"),
    current_user: User = Depends(get_current_user)
):
    """
    Search and discover ANY Binance listed cryptocurrency token or pair for custom AI analysis.
    Supports real-time filtering across 770+ Binance spot & futures listed coins.
    """
    tokens = await gemini_analyst_service.search_listed_tokens(query=query, limit=limit)
    return APIResponse(
        success=True,
        message=f"Found {len(tokens)} Binance listed tokens matching '{query or 'ALL'}'",
        data=tokens
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
