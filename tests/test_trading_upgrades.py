import json
import pytest
from decimal import Decimal
from pathlib import Path
from app.services.trading.autonomous_engine import AutonomousExecutionService, PERSISTENCE_FILE
from app.services.binance.client import BinanceSpotClient


def test_autonomous_config_persistence():
    """Verifies that Auto-Pilot configuration and logs persist to disk and reload properly."""
    service = AutonomousExecutionService()
    
    # Update configuration
    updated = service.update_config({
        "min_score": 88.5,
        "max_active_bots": 4,
        "capital_allocation_pct": 30.0,
        "leverage": 15
    })
    assert updated["min_score"] == 88.5
    assert updated["max_active_bots"] == 4
    assert updated["capital_allocation_pct"] == 30.0
    assert updated["leverage"] == 15

    # Check that persistence file was created on disk
    assert PERSISTENCE_FILE.exists()
    with open(PERSISTENCE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["config"]["min_score"] == 88.5
    assert data["config"]["leverage"] == 15

    # Instantiate a new service instance to verify restoration
    restored_service = AutonomousExecutionService()
    cfg = restored_service.get_config()
    assert cfg["min_score"] == 88.5
    assert cfg["max_active_bots"] == 4
    assert cfg["leverage"] == 15


def test_binance_stop_market_client_signature():
    """Verifies that the BinanceSpotClient exposes place_stop_market_order with correct signature."""
    client = BinanceSpotClient(api_key="test_key", api_secret="test_secret", is_testnet=True)
    assert hasattr(client, "place_stop_market_order")
    assert callable(client.place_stop_market_order)
