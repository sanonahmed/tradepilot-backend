from fastapi import APIRouter
from app.api.v1.auth import router as auth_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.bots import router as bots_router
from app.api.v1.risk import router as risk_router
from app.api.v1.orders import router as orders_router
from app.api.v1.markets import router as markets_router
from app.api.v1.alerts import router as alerts_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.analyst import router as analyst_router
from app.api.v1.whales import router as whales_router
from app.api.v1.news import router as news_router
from app.api.v1.autonomous import router as autonomous_router

api_router = APIRouter(prefix="/v1")

api_router.include_router(auth_router)
api_router.include_router(dashboard_router)
api_router.include_router(bots_router)
api_router.include_router(risk_router)
api_router.include_router(orders_router)
api_router.include_router(markets_router)
api_router.include_router(alerts_router)
api_router.include_router(analytics_router)
api_router.include_router(analyst_router)
api_router.include_router(whales_router)
api_router.include_router(news_router)
api_router.include_router(autonomous_router)
