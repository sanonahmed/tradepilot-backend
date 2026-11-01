from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.models.entities import User
from app.schemas.common import APIResponse
from app.services.news.news_service import news_service

router = APIRouter(prefix="/news", tags=["Crypto News & Sentiment"])


@router.get("/feed", response_model=APIResponse[List[Dict[str, Any]]])
async def get_news_feed_endpoint(
    limit: int = Query(25, ge=1, le=50, description="Max news items to return"),
    token: Optional[str] = Query(None, description="Filter news by token ticker (e.g. BTC, ETH, SOL)"),
    current_user: User = Depends(get_current_user)
):
    """
    Stream live cryptocurrency news headlines with AI-evaluated sentiment tags
    (BULLISH, BEARISH, NEUTRAL), urgency ratings, and coin mentions.
    """
    articles = await news_service.fetch_news_feed(limit=limit, token_filter=token)
    return APIResponse(
        success=True,
        message=f"Retrieved {len(articles)} real-time news headlines with sentiment classification",
        data=articles
    )


@router.get("/sentiment-overview", response_model=APIResponse[Dict[str, Any]])
async def get_sentiment_overview_endpoint(
    current_user: User = Depends(get_current_user)
):
    """
    Retrieve aggregate cryptocurrency market sentiment index,
    bullish vs bearish distribution, and fear & greed ratings.
    """
    overview = await news_service.get_sentiment_overview()
    return APIResponse(
        success=True,
        message="Market sentiment overview calculated successfully",
        data=overview
    )
