"""
Market scanner helper service for autonomous trading engine and background workers.
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("tradepilot.markets.scanner")


async def scan_all_market_opportunities(
    strategy: str = "all",
    min_score: Optional[float] = None,
    limit: int = 15
) -> Dict[str, Any]:
    """
    Scans the Binance market (~770+ coins) and returns candidate trade opportunities.
    """
    try:
        from app.api.v1.markets import fetch_binance_tickers, compute_market_scan
        tickers = await fetch_binance_tickers()
        results = compute_market_scan(
            tickers=tickers,
            strategy=strategy or "all",
            min_score=min_score,
            limit=limit
        )

        opportunities = [
            {
                "symbol": item.symbol,
                "price": float(item.price),
                "change_24h": float(item.change_24h),
                "rsi_14": float(item.rsi_14),
                "macd_signal": item.macd_signal,
                "ema_trend": item.ema_trend,
                "bollinger_state": item.bollinger_state,
                "volume_surge_ratio": float(item.volume_surge_ratio),
                "opportunity_score": float(item.opportunity_score),
                "strategy_type": item.signal_type or "BREAKOUT",
                "signal_type": item.signal_type,
                "reasons": item.reasons
            }
            for item in results
        ]
        return {
            "opportunities": opportunities,
            "total": len(opportunities),
            "status": "success"
        }
    except Exception as e:
        logger.error(f"Error in scan_all_market_opportunities: {e}", exc_info=True)
        return {
            "opportunities": [],
            "total": 0,
            "status": "error",
            "error": str(e)
        }
