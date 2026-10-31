import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.database import init_db
from app.api.v1.router import api_router

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("tradepilot")


# WebSocket Manager for live updates to Android clients
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.info(f"WebSocket client connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        logger.info(f"WebSocket client disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        text = json.dumps(message)
        for connection in list(self.active_connections):
            try:
                await connection.send_text(text)
            except Exception:
                self.active_connections.discard(connection)


ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database schema
    logger.info(f"Starting {settings.APP_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]")
    try:
        await init_db()
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization error: {e}")

    # Launch Automated Exit Engine (Monitors Take Profit & Stop Loss 24/7)
    monitor_task = None
    try:
        from app.services.trading.exit_monitor import exit_monitor_background_loop, stop_exit_monitor
        monitor_task = asyncio.create_task(exit_monitor_background_loop())
        logger.info("Automated Take-Profit and Stop-Loss Exit Engine running in background.")
    except Exception as em_err:
        logger.error(f"Failed to start exit monitor: {em_err}")

    yield

    # Shutdown
    if monitor_task:
        try:
            from app.services.trading.exit_monitor import stop_exit_monitor
            stop_exit_monitor()
            monitor_task.cancel()
        except Exception:
            pass
    logger.info(f"Shutting down {settings.APP_NAME}")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="Production-grade Binance Crypto Trading Assistant and Automated Trading Platform",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API v1
app.include_router(api_router, prefix="/api")


@app.get("/health", tags=["Health & Status"])
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "database": "connected"
    }


@app.get("/ready", tags=["Health & Status"])
async def ready_check():
    return {"ready": True}


@app.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket):
    """
    Live stream endpoint for Android app (tickers, signals, execution events).
    """
    await ws_manager.connect(websocket)
    try:
        # Send initial welcome and heartbeat
        await websocket.send_json({
            "type": "CONNECTION_ESTABLISHED",
            "message": "Connected to TradePilot AI live market stream"
        })
        while True:
            data = await websocket.receive_text()
            # Handle client heartbeats or subscription messages
            try:
                msg = json.loads(data)
                if msg.get("action") == "PING":
                    await websocket.send_json({"type": "PONG"})
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
        ws_manager.disconnect(websocket)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "message": "An internal server error occurred.",
            "error_code": "INTERNAL_ERROR"
        }
    )
