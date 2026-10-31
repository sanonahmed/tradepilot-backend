import pytest
import hmac
import hashlib
from app.services.binance.client import BinanceSpotClient


def test_binance_query_signing():
    api_key = "test_api_key_1234567890"
    api_secret = "test_api_secret_abcdef123456"
    client = BinanceSpotClient(api_key=api_key, api_secret=api_secret, is_testnet=True)

    params = {"symbol": "BTCUSDT"}
    signed_query = client._sign_query(params)

    assert "symbol=BTCUSDT" in signed_query
    assert "timestamp=" in signed_query
    assert "recvWindow=5000" in signed_query
    assert "signature=" in signed_query

    # Verify signature mathematically
    parts = signed_query.split("&signature=")
    query_payload = parts[0]
    expected_sig = hmac.new(
        api_secret.encode("utf-8"),
        query_payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    assert parts[1] == expected_sig


@pytest.mark.asyncio
async def test_binance_withdrawal_permission_block():
    client = BinanceSpotClient(api_key="mock_key", api_secret="mock_secret", is_testnet=True)
    # Test that client rejects missing keys
    empty_client = BinanceSpotClient(api_key="", api_secret="", is_testnet=True)
    success, data, err = await empty_client.get_account_information()
    assert success is False
    assert "No Binance API Key or Secret provided" in err
