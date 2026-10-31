import httpx
import uuid
import json
import sys

# Ensure UTF-8 output on Windows console
sys.stdout.reconfigure(encoding='utf-8')

base = "http://127.0.0.1:8000/api/v1"

def run_test():
    # 1. Register unique user
    email = f"scanner_trader_{uuid.uuid4().hex[:6]}@tradepilot.ai"
    password = "Password123!"
    r = httpx.post(f"{base}/auth/register", json={
        "email": email,
        "username": email.split("@")[0],
        "password": password,
        "full_name": "Scanner Test Trader"
    }, timeout=15)
    print("1. Register status:", r.status_code)

    # 2. Login
    r = httpx.post(f"{base}/auth/login", json={"email": email, "password": password}, timeout=15)
    print("2. Login status:", r.status_code)
    login_data = r.json()
    token = login_data.get("data", {}).get("access_token")
    if not token:
        print("Failed to get token:", login_data)
        return
    headers = {"Authorization": f"Bearer {token}"}

    # 3. Connect Binance testnet credentials to /auth/exchange-keys
    conn_payload = {
        "exchange": "binance",
        "api_key": "D4Qfcmmo0dlSYuqE6xFkWgi9OIt8sbW15WEYi4Hl5sv1YLwmsDjyWteRPSPVYLt4",
        "api_secret": "Q7YbbekgX9NFCgQDjlbClGALYeTtw87Y61XUDA4XeWVk3vmGu0i8NmroxD8xbAmx",
        "is_testnet": True
    }
    r = httpx.post(f"{base}/auth/exchange-keys", json=conn_payload, headers=headers, timeout=15)
    print("3. Exchange connect status:", r.status_code, r.json().get("message", r.json()))

    # 4. Trigger trade from scanner
    trade_payload = {
        "symbol": "XRPUSDT",
        "entry_price": 1.25,
        "target_price": 1.35,
        "stop_loss": 1.20,
        "allocated_budget": 250.0,
        "risk_percentage": 0.5,
        "timeframe": "1h",
        "signal_type": "BREAKOUT"
    }
    r = httpx.post(f"{base}/bots/from-scanner", json=trade_payload, headers=headers, timeout=25)
    print("4. From-scanner status:", r.status_code)
    res_data = r.json()
    print("   Message:", res_data.get("message"))
    bot_info = res_data.get("data", {})
    print("   Bot ID:", bot_info.get("id"))
    print("   Bot Name:", bot_info.get("name"))
    print("   Bot Status:", bot_info.get("status"))
    cfg = bot_info.get("configuration", {})
    strat = cfg.get("strategy_params", {})
    print("   Binance Order ID:", strat.get("binance_order_id"))
    print("   Binance Status:", strat.get("binance_status"))
    print("   Binance Executed:", strat.get("binance_executed"))
    print("   Exchange Endpoint:", strat.get("exchange_endpoint"))

    # 5. List bots
    r = httpx.get(f"{base}/bots", headers=headers, timeout=15)
    bots_resp = r.json().get("data", [])
    print(f"5. Total bots in fleet: {len(bots_resp)}")
    for b in bots_resp:
        c = b.get("configuration", {})
        sp = c.get("strategy_params", {})
        print(f"   -> Bot: {b.get('name')} | Mode: {b.get('operating_mode')} | OrderID: {sp.get('binance_order_id')} | Status: {b.get('status')}")

    # 6. Dashboard summary
    r = httpx.get(f"{base}/dashboard/summary", headers=headers, timeout=15)
    dash = r.json().get("data", {})
    print("6. Dashboard Summary:")
    print("   Binance Connected:", dash.get("binance_connected"))
    print("   Connection Mode:", dash.get("connection_mode"))
    print("   Total Equity USDT:", dash.get("total_equity_usdt"))
    print("   Available Balance USDT:", dash.get("available_balance_usdt"))
    print("   Allocated Balance USDT:", dash.get("allocated_balance_usdt"))
    print("   Active Bots Count:", dash.get("active_bots_count"))
    print("   Kill Switch Active:", dash.get("kill_switch_active"))
    print("   Holdings:")
    for h in dash.get("holdings", []):
        print(f"     * {h.get('asset')}: Total={h.get('total')}, USDT Val=${h.get('value_usdt')}")

if __name__ == "__main__":
    run_test()
