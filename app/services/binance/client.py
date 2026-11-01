import hmac
import hashlib
import time
import re
import logging
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple
import httpx
from app.core.config import settings

logger = logging.getLogger("binance_client")

STABLECOIN_ASSETS = {"USDT", "USD", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP"}


def resolve_asset_price(asset: str, prices: Dict[str, Decimal]) -> Decimal:
    """
    Resolves the exact USDT unit price for any asset using direct pairs,
    stablecoin cross-pairs, or major base currency cross-pairs (BTC, ETH, BNB).
    """
    asset_upper = asset.upper()
    if asset_upper in {"USDT", "USD"}:
        return Decimal("1.0")
    if asset_upper in {"USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP"}:
        pair = f"{asset_upper}USDT"
        if pair in prices:
            return prices[pair]
        return Decimal("1.0")

    # 1. Direct USDT pair (e.g. BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT)
    if f"{asset_upper}USDT" in prices:
        return prices[f"{asset_upper}USDT"]

    # 2. Direct USDC pair
    if f"{asset_upper}USDC" in prices:
        usdc_rate = prices.get("USDCUSDT", Decimal("1.0"))
        return prices[f"{asset_upper}USDC"] * usdc_rate

    # 3. Direct FDUSD pair
    if f"{asset_upper}FDUSD" in prices:
        fdusd_rate = prices.get("FDUSDUSDT", Decimal("1.0"))
        return prices[f"{asset_upper}FDUSD"] * fdusd_rate

    # 4. BTC cross-rate (e.g. ADABTC, XRPBTC, LTCBTC)
    btc_usdt = prices.get("BTCUSDT", Decimal("0.0"))
    if btc_usdt > Decimal("0") and f"{asset_upper}BTC" in prices:
        return prices[f"{asset_upper}BTC"] * btc_usdt

    # 5. ETH cross-rate (e.g. ADAETH, XRPETH, LINKETH)
    eth_usdt = prices.get("ETHUSDT", Decimal("0.0"))
    if eth_usdt > Decimal("0") and f"{asset_upper}ETH" in prices:
        return prices[f"{asset_upper}ETH"] * eth_usdt

    # 6. BNB cross-rate (e.g. SOLBNB, ADABNB, TRXBNB)
    bnb_usdt = prices.get("BNBUSDT", Decimal("0.0"))
    if bnb_usdt > Decimal("0") and f"{asset_upper}BNB" in prices:
        return prices[f"{asset_upper}BNB"] * bnb_usdt

    return Decimal("0.0")


class BinanceSpotClient:
    """
    Official Binance REST API Client.
    Supports Spot & Futures (Binance Global, US, Spot Testnet, and Futures Testnet / Mock Trading).
    Enforces HMAC-SHA256 request signing, 60s recvWindow, and automatic endpoint resolution.
    """

    def __init__(self, api_key: str = "", api_secret: str = "", is_testnet: bool = True):
        # Sanitize keys: strip whitespace, newlines, and surrounding quotes
        self.api_key = api_key.strip().strip('"\'')
        self.api_secret = api_secret.strip().strip('"\'')
        self.is_testnet = is_testnet
        self.is_futures = False
        self.endpoint_type = "futures_testnet" if is_testnet else "spot_live"
        self.base_url = (
            "https://testnet.binancefuture.com" if is_testnet else "https://api.binance.com"
        )
        self._time_offset_ms: int = 0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "TradePilotAI/1.0.0"
        }
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key
        return headers

    async def _try_fetch_account(
        self,
        base_url: str,
        account_path: str = "/api/v3/account",
        time_path: str = "/api/v3/time"
    ) -> Tuple[int, Any]:
        """Attempts an account query against a specific Binance base URL with server time sync."""
        offset_ms = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                time_url = f"{base_url}{time_path}"
                t_res = await client.get(time_url, headers={"User-Agent": "TradePilotAI/1.0.0"})
                if t_res.status_code == 200:
                    server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                    offset_ms = server_time - int(time.time() * 1000)
            except Exception as e:
                logger.warning(f"Failed to fetch time from {base_url}: {e}")

            ts = int(time.time() * 1000) + offset_ms
            # Use max allowed recvWindow of 60000ms (60 seconds) to prevent latency/clock rejects
            query_str = f"recvWindow=60000&timestamp={ts}"
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                query_str.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()
            url = f"{base_url}{account_path}?{query_str}&signature={signature}"

            try:
                res = await client.get(url, headers=self._get_headers())
                try:
                    data = res.json()
                except Exception:
                    data = {"msg": res.text}
                return res.status_code, data
            except Exception as e:
                return 0, {"msg": str(e)}

    async def _format_binance_error(self, status_code: int, data: Any, tried_urls: List[str]) -> str:
        """Formats actionable diagnostic instructions when Binance rejects authentication."""
        raw_msg = data.get("msg", str(data)) if isinstance(data, dict) else str(data)
        err_code = data.get("code") if isinstance(data, dict) else None

        # Check if an IP address was explicitly given by Binance in the error
        ip_match = re.search(r"request ip:\s*([0-9a-fA-F.:]+)", raw_msg)
        detected_ip = ip_match.group(1) if ip_match else None

        if not detected_ip:
            try:
                async with httpx.AsyncClient(timeout=2.0) as ip_client:
                    r = await ip_client.get("https://api.ipify.org?format=json")
                    if r.status_code == 200:
                        detected_ip = r.json().get("ip")
            except Exception:
                pass

        if err_code in (-2015, -2008) or status_code == 401:
            ip_line = f"\n- Outbound Server IP: {detected_ip} (Whitelist this IP in Binance if IP restriction is on)" if detected_ip else ""
            return (
                f"Binance API 401 Unauthorized (Invalid API Key, IP, or Permissions).\n\n"
                f"To resolve this in your Binance Account:\n"
                f"1. Open Binance -> Profile -> API Management -> Edit your API key.\n"
                f"2. IP Access: Select 'Unrestricted (Less Secure)' OR whitelist your IP.{ip_line}\n"
                f"3. Permissions: Ensure 'Enable Reading' and 'Enable Spot & Margin Trading' are checked.\n"
                f"4. Secret Key: Ensure you pasted the exact Secret Key (not masked '***').\n"
                f"5. Environment: Binance Live keys only work on 'Binance Live'. For 'Binance Demo', create keys on testnet.binance.vision or testnet.binancefuture.com."
            )

        if err_code == -2014:
            return (
                "Binance API Key Format Invalid (Code -2014): "
                "The API Key contains invalid characters or was truncated. Please copy the complete key."
            )

        if err_code == -1022:
            return (
                "Binance Signature Error (Code -1022): "
                "The API Secret does not match this API Key. Please re-copy the Secret Key from Binance."
            )

        if err_code == -1021:
            return (
                "Binance Clock Sync Error (Code -1021): "
                "System clock drifted outside the 60,000ms window. Please synchronize your system time."
            )

        return f"Binance API Error ({status_code}): {raw_msg}"

    async def get_account_information(self) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Calls account endpoint to retrieve real balances and account status.
        Auto-detects environment across candidate endpoints (Futures Testnet, Spot Testnet, Live Spot, Live Futures, Binance US).
        Returns: (success, account_data, error_message)
        """
        if not self.api_key or not self.api_secret:
            return False, {}, "No Binance API Key or Secret provided."

        # Candidate endpoints: (name, base_url, account_path, time_path, is_futures, is_testnet)
        if self.is_testnet:
            candidates = [
                ("futures_testnet", "https://testnet.binancefuture.com", "/fapi/v2/account", "/fapi/v1/time", True, True),
                ("spot_testnet", "https://testnet.binance.vision", "/api/v3/account", "/api/v3/time", False, True),
                ("spot_live", "https://api.binance.com", "/api/v3/account", "/api/v3/time", False, False),
                ("futures_live", "https://fapi.binance.com", "/fapi/v2/account", "/fapi/v1/time", True, False),
                ("spot_us", "https://api.binance.us", "/api/v3/account", "/api/v3/time", False, False),
            ]
        else:
            candidates = [
                ("spot_live", "https://api.binance.com", "/api/v3/account", "/api/v3/time", False, False),
                ("futures_live", "https://fapi.binance.com", "/fapi/v2/account", "/fapi/v1/time", True, False),
                ("spot_us", "https://api.binance.us", "/api/v3/account", "/api/v3/time", False, False),
                ("futures_testnet", "https://testnet.binancefuture.com", "/fapi/v2/account", "/fapi/v1/time", True, True),
                ("spot_testnet", "https://testnet.binance.vision", "/api/v3/account", "/api/v3/time", False, True),
            ]

        last_status = 0
        last_data: Any = {}
        tried_urls = []

        for name, url, acct_path, time_path, is_fut, is_test in candidates:
            tried_urls.append(url)
            status_code, data = await self._try_fetch_account(url, acct_path, time_path)
            if status_code == 200:
                self.endpoint_type = name
                self.base_url = url
                self.is_futures = is_fut
                self.is_testnet = is_test
                logger.info(f"Connected to Binance endpoint: {name} ({url})")

                # If live spot and withdrawal permission is enabled, enforce security block
                if name == "spot_live" and data.get("canWithdraw", False) is True:
                    return (
                        False,
                        {},
                        "SECURITY VIOLATION: API Key has withdrawal permissions enabled. TradePilot AI requires trading-only permissions."
                    )
                return True, data, None

            last_status = status_code
            last_data = data

        # If all candidates failed, format actionable diagnostic message
        err_msg = await self._format_binance_error(last_status, last_data, tried_urls)
        return False, {}, err_msg

    async def get_all_ticker_prices(self) -> Dict[str, Decimal]:
        """Fetches latest prices for all symbols, prioritizing live market prices."""
        prices: Dict[str, Decimal] = {}
        urls = [
            "https://api.binance.com/api/v3/ticker/price",
            "https://fapi.binance.com/fapi/v1/ticker/price"
        ]
        if self.is_testnet:
            urls.append("https://testnet.binancefuture.com/fapi/v1/ticker/price")
            urls.append("https://testnet.binance.vision/api/v3/ticker/price")

        async with httpx.AsyncClient(timeout=8.0) as client:
            for url in urls:
                try:
                    res = await client.get(url, headers={"User-Agent": "TradePilotAI/1.0.0"})
                    if res.status_code == 200:
                        for item in res.json():
                            sym = item.get("symbol")
                            p = item.get("price")
                            if sym and p and sym not in prices:
                                prices[sym] = Decimal(str(p))
                except Exception as e:
                    logger.warning(f"Could not load prices from {url}: {e}")
        return prices

    async def calculate_real_portfolio_equity(self) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Connects to Binance API, calculates exact real USDT equity from positive asset balances,
        and aggregates free liquid trading balance, locked balance, crypto value, and asset holdings.
        Seamlessly handles both Spot and Futures/Mock accounts.
        """
        success, acct_data, err = await self.get_account_information()
        if not success:
            return False, {}, err

        try:
            prices = await self.get_all_ticker_prices()
        except Exception:
            prices = {}

        holdings = []

        if self.is_futures:
            # Futures / Mock Trading Account schema
            total_equity_usdt = Decimal(str(acct_data.get("totalMarginBalance", acct_data.get("totalWalletBalance", "0"))))
            available_usdt = Decimal(str(acct_data.get("availableBalance", "0")))
            locked_in_orders_usdt = Decimal(str(acct_data.get("totalInitialMargin", "0")))
            unrealized_pnl_usdt = Decimal(str(acct_data.get("totalUnrealizedProfit", "0")))

            for a in acct_data.get("assets", []):
                wb = Decimal(str(a.get("walletBalance", "0")))
                avail = Decimal(str(a.get("availableBalance", "0")))
                if wb > Decimal("0"):
                    asset_name = a.get("asset")
                    unit_p = resolve_asset_price(asset_name, prices)
                    val_usdt = wb * unit_p
                    holdings.append({
                        "asset": asset_name,
                        "free": float(avail),
                        "locked": float(wb - avail),
                        "total": float(wb),
                        "unit_price_usdt": float(unit_p),
                        "value_usdt": float(val_usdt)
                    })

            for p in acct_data.get("positions", []):
                amt = Decimal(str(p.get("positionAmt", "0")))
                if abs(amt) > Decimal("0"):
                    sym = p.get("symbol")
                    entry_p = Decimal(str(p.get("entryPrice", "0")))
                    un_pnl = Decimal(str(p.get("unrealizedProfit", "0")))
                    pos_val = abs(amt) * entry_p
                    holdings.append({
                        "asset": f"{sym} ({'LONG' if amt > 0 else 'SHORT'})",
                        "free": float(abs(amt)),
                        "locked": 0.0,
                        "total": float(abs(amt)),
                        "unit_price_usdt": float(entry_p),
                        "value_usdt": float(pos_val),
                        "unrealized_pnl_usdt": float(un_pnl)
                    })

            crypto_val = max(Decimal("0.0"), total_equity_usdt - available_usdt)

            return True, {
                "total_equity_usdt": total_equity_usdt,
                "available_balance_usdt": available_usdt,
                "locked_in_orders_usdt": locked_in_orders_usdt,
                "crypto_holdings_value_usdt": crypto_val,
                "allocated_balance_usdt": locked_in_orders_usdt,
                "unrealized_pnl_usdt": unrealized_pnl_usdt,
                "holdings": holdings,
                "can_trade": acct_data.get("canTrade", True),
                "is_testnet": self.is_testnet,
                "account_type": "FUTURES_TESTNET" if self.is_testnet else "FUTURES_LIVE"
            }, None

        # Spot Account schema
        raw_balances = acct_data.get("balances", [])
        positive_balances = [
            b for b in raw_balances
            if Decimal(str(b.get("free", "0"))) > Decimal("0") or Decimal(str(b.get("locked", "0"))) > Decimal("0")
        ]

        total_equity_usdt = Decimal("0.0")
        available_usdt = Decimal("0.0")
        locked_in_orders_usdt = Decimal("0.0")
        crypto_holdings_value_usdt = Decimal("0.0")

        for b in positive_balances:
            asset = b["asset"]
            free = Decimal(str(b.get("free", "0")))
            locked = Decimal(str(b.get("locked", "0")))
            total_qty = free + locked
            if total_qty <= Decimal("0"):
                continue

            unit_price = resolve_asset_price(asset, prices)
            value_usdt = total_qty * unit_price
            free_value_usdt = free * unit_price
            locked_value_usdt = locked * unit_price

            total_equity_usdt += value_usdt
            locked_in_orders_usdt += locked_value_usdt

            if asset.upper() in STABLECOIN_ASSETS:
                available_usdt += free_value_usdt
            else:
                crypto_holdings_value_usdt += value_usdt

            # Include holding if meaningful value or quantity
            if value_usdt >= Decimal("0.0001") or total_qty >= Decimal("0.001"):
                holdings.append({
                    "asset": asset,
                    "free": float(free),
                    "locked": float(locked),
                    "total": float(total_qty),
                    "unit_price_usdt": float(unit_price),
                    "value_usdt": float(value_usdt)
                })

        holdings.sort(key=lambda x: x["value_usdt"], reverse=True)

        return True, {
            "total_equity_usdt": total_equity_usdt,
            "available_balance_usdt": available_usdt,
            "locked_in_orders_usdt": locked_in_orders_usdt,
            "crypto_holdings_value_usdt": crypto_holdings_value_usdt,
            "allocated_balance_usdt": locked_in_orders_usdt,
            "unrealized_pnl_usdt": Decimal("0.0"),
            "holdings": holdings,
            "can_trade": acct_data.get("canTrade", False),
            "account_type": acct_data.get("accountType", "SPOT"),
            "is_testnet": self.is_testnet
        }, None

    async def get_live_positions_details(self) -> Dict[str, Dict[str, Any]]:
        """
        Retrieves official real-time position details directly from Binance.
        On Futures: queries /fapi/v2/positionRisk and returns exact entryPrice, markPrice, unRealizedProfit, notional, and ROE %.
        On Spot: queries /api/v3/account and returns non-stablecoin asset quantities and marked prices.
        """
        if not self.endpoint_type or (self.endpoint_type == "futures_testnet" and not self.is_futures):
            await self.get_account_information()

        positions_map: Dict[str, Dict[str, Any]] = {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if self.is_futures:
                    time_path = "/fapi/v1/time"
                    offset_ms = 0
                    try:
                        t_res = await client.get(f"{self.base_url}{time_path}", headers={"User-Agent": "TradePilotAI/1.0.0"})
                        if t_res.status_code == 200:
                            server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                            offset_ms = server_time - int(time.time() * 1000)
                    except Exception:
                        pass
                    ts = int(time.time() * 1000) + offset_ms
                    query = f"recvWindow=60000&timestamp={ts}"
                    sig = hmac.new(self.api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
                    pos_url = f"{self.base_url}/fapi/v2/positionRisk?{query}&signature={sig}"
                    res = await client.get(pos_url, headers=self._get_headers())
                    if res.status_code == 200:
                        data = res.json()
                        if isinstance(data, list):
                            for p in data:
                                amt = Decimal(str(p.get("positionAmt", "0")))
                                if abs(amt) > Decimal("0"):
                                    sym = p.get("symbol", "")
                                    entry_p = Decimal(str(p.get("entryPrice", "0")))
                                    mark_p = Decimal(str(p.get("markPrice", "0")))
                                    unrealized_pnl = Decimal(str(p.get("unRealizedProfit", "0")))
                                    notional = Decimal(str(p.get("notional", "0")))
                                    leverage = Decimal(str(p.get("leverage", "1")))

                                    # Calculate official Return on Equity (ROE %) matching Binance app
                                    initial_margin = (abs(notional) / leverage) if leverage > 0 else Decimal("0")
                                    if initial_margin <= Decimal("0") and entry_p > Decimal("0"):
                                        initial_margin = (abs(amt) * entry_p) / leverage if leverage > 0 else (abs(amt) * entry_p)

                                    if initial_margin > Decimal("0"):
                                        roe_pct = round((unrealized_pnl / initial_margin) * Decimal("100.0"), 2)
                                    elif entry_p > Decimal("0"):
                                        if amt > 0:
                                            roe_pct = round(((mark_p - entry_p) / entry_p) * Decimal("100.0"), 2)
                                        else:
                                            roe_pct = round(((entry_p - mark_p) / entry_p) * Decimal("100.0"), 2)
                                    else:
                                        roe_pct = Decimal("0.0")

                                    positions_map[sym] = {
                                        "symbol": sym,
                                        "position_amt": amt,
                                        "entry_price": entry_p,
                                        "mark_price": mark_p,
                                        "unrealized_pnl_usdt": unrealized_pnl,
                                        "roe_pct": roe_pct,
                                        "notional": notional,
                                        "leverage": int(leverage)
                                    }
                else:
                    ok, acct_data, _ = await self.get_account_information()
                    if ok:
                        ticker_res = await client.get(f"{self.base_url}/api/v3/ticker/price")
                        prices: Dict[str, Decimal] = {}
                        if ticker_res.status_code == 200:
                            for item in ticker_res.json():
                                prices[item["symbol"]] = Decimal(str(item["price"]))

                        for b in acct_data.get("balances", []):
                            asset = b.get("asset", "")
                            free = Decimal(str(b.get("free", "0")))
                            locked = Decimal(str(b.get("locked", "0")))
                            total = free + locked
                            sym = f"{asset}USDT"
                            if asset not in STABLECOIN_ASSETS and total > Decimal("0"):
                                mark_p = prices.get(sym, Decimal("0"))
                                positions_map[sym] = {
                                    "symbol": sym,
                                    "position_amt": total,
                                    "entry_price": mark_p,
                                    "mark_price": mark_p,
                                    "unrealized_pnl_usdt": Decimal("0.0"),
                                    "roe_pct": Decimal("0.0"),
                                    "notional": total * mark_p,
                                    "leverage": 1
                                }
            except Exception as e:
                logger.error(f"Error fetching live position details from Binance: {e}")

        return positions_map

    async def get_open_positions(self) -> Dict[str, Decimal]:
        """
        Returns a dictionary of symbol -> open_quantity for all currently open positions on Binance.
        """
        details = await self.get_live_positions_details()
        return {s: p["position_amt"] for s, p in details.items()}

    def _sign_query(self, query_params: Dict[str, Any]) -> str:
        """Generates HMAC-SHA256 signature for signed endpoints."""
        query_params["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        query_params["recvWindow"] = 5000
        sorted_items = sorted(query_params.items())
        query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return f"{query_str}&signature={signature}"

    async def cancel_open_orders(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """Cancels all open / pending orders for the specified symbol on Binance."""
        if not self.endpoint_type or (self.endpoint_type == "futures_testnet" and not self.is_futures):
            await self.get_account_information()
        
        sym = symbol.upper()
        cancel_path = "/fapi/v1/allOpenOrders" if self.is_futures else "/api/v3/openOrders"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                time_path = "/fapi/v1/time" if self.is_futures else "/api/v3/time"
                offset_ms = 0
                try:
                    t_res = await client.get(f"{self.base_url}{time_path}", headers={"User-Agent": "TradePilotAI/1.0.0"})
                    if t_res.status_code == 200:
                        server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                        offset_ms = server_time - int(time.time() * 1000)
                except Exception:
                    pass

                ts = int(time.time() * 1000) + offset_ms
                query_params = {
                    "symbol": sym,
                    "recvWindow": 60000,
                    "timestamp": ts
                }
                sorted_items = sorted(query_params.items())
                query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
                sig = hmac.new(self.api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
                url = f"{self.base_url}{cancel_path}?{query_str}&signature={sig}"
                res = await client.delete(url, headers=self._get_headers())
                if res.status_code == 200:
                    logger.info(f"Cancelled all open orders on Binance for {sym}")
                    return True, None
                else:
                    err_data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {"msg": res.text}
                    logger.warning(f"Failed to cancel open orders for {sym}: {err_data}")
                    return False, str(err_data)
            except Exception as e:
                logger.warning(f"Exception cancelling orders for {sym}: {e}")
                return False, str(e)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves all currently active open/pending orders directly from Binance API.
        On Futures: /fapi/v1/openOrders
        On Spot: /api/v3/openOrders
        """
        if not self.endpoint_type or (self.endpoint_type == "futures_testnet" and not self.is_futures):
            await self.get_account_information()

        open_orders_path = "/fapi/v1/openOrders" if self.is_futures else "/api/v3/openOrders"
        orders: List[Dict[str, Any]] = []

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                time_path = "/fapi/v1/time" if self.is_futures else "/api/v3/time"
                offset_ms = 0
                try:
                    t_res = await client.get(f"{self.base_url}{time_path}", headers={"User-Agent": "TradePilotAI/1.0.0"})
                    if t_res.status_code == 200:
                        server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                        offset_ms = server_time - int(time.time() * 1000)
                except Exception:
                    pass

                ts = int(time.time() * 1000) + offset_ms
                query_params: Dict[str, Any] = {
                    "recvWindow": 60000,
                    "timestamp": ts
                }
                if symbol:
                    query_params["symbol"] = symbol.upper()

                sorted_items = sorted(query_params.items())
                query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
                sig = hmac.new(self.api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
                url = f"{self.base_url}{open_orders_path}?{query_str}&signature={sig}"
                res = await client.get(url, headers=self._get_headers())
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        for ord_item in data:
                            sym = ord_item.get("symbol", "")
                            tok = sym.replace("USDT", "")
                            p = Decimal(str(ord_item.get("price", "0")))
                            sp = Decimal(str(ord_item.get("stopPrice", "0")))
                            oq = Decimal(str(ord_item.get("origQty", "0")))
                            eq = Decimal(str(ord_item.get("executedQty", "0")))
                            orders.append({
                                "order_id": str(ord_item.get("orderId")),
                                "client_order_id": str(ord_item.get("clientOrderId", "")),
                                "symbol": sym,
                                "token": tok,
                                "side": ord_item.get("side", ""),
                                "order_type": ord_item.get("type", "LIMIT"),
                                "price": p,
                                "stop_price": sp,
                                "orig_qty": oq,
                                "executed_qty": eq,
                                "status": ord_item.get("status", "NEW"),
                                "time": ord_item.get("time", int(time.time() * 1000)),
                                "reduce_only": bool(ord_item.get("reduceOnly", False))
                            })
                else:
                    logger.warning(f"Failed to fetch open orders from Binance: {res.text}")
            except Exception as e:
                logger.warning(f"Exception fetching open orders from Binance: {e}")

        return orders

    async def get_realized_pnl(self) -> Tuple[Decimal, Decimal]:
        """
        Retrieves official realized PnL directly from Binance.
        Returns: (realized_pnl_24h, total_realized_pnl)
        """
        if not self.endpoint_type or (self.endpoint_type == "futures_testnet" and not self.is_futures):
            await self.get_account_information()

        if not self.is_futures:
            return Decimal("0.0"), Decimal("0.0")

        total_pnl = Decimal("0.0")
        pnl_24h = Decimal("0.0")

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                time_path = "/fapi/v1/time"
                offset_ms = 0
                try:
                    t_res = await client.get(f"{self.base_url}{time_path}", headers={"User-Agent": "TradePilotAI/1.0.0"})
                    if t_res.status_code == 200:
                        server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                        offset_ms = server_time - int(time.time() * 1000)
                except Exception:
                    pass

                ts = int(time.time() * 1000) + offset_ms
                query_str = f"incomeType=REALIZED_PNL&limit=100&recvWindow=60000&timestamp={ts}"
                sig = hmac.new(self.api_secret.encode("utf-8"), query_str.encode("utf-8"), hashlib.sha256).hexdigest()
                url = f"{self.base_url}/fapi/v1/income?{query_str}&signature={sig}"
                res = await client.get(url, headers=self._get_headers())
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        now_ms = int(time.time() * 1000)
                        cutoff_24h = now_ms - (86400 * 1000)
                        for item in data:
                            inc = Decimal(str(item.get("income", "0")))
                            t = int(item.get("time", 0))
                            total_pnl += inc
                            if t >= cutoff_24h:
                                pnl_24h += inc
            except Exception as e:
                logger.warning(f"Exception fetching realized PnL from Binance: {e}")

        return round(pnl_24h, 4), round(total_pnl, 4)

    async def place_order(
        self,
        symbol: str,
        side: str = "BUY",
        order_type: str = "MARKET",
        quantity: Optional[Decimal] = None,
        quote_order_qty: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """
        Submits an actual order to Binance (Spot or Futures/Mock Trading).
        Automatically formats parameters, applies precision rules, signs with HMAC-SHA256,
        and synchronizes with Binance server time.
        """
        # Ensure account information / endpoint type is established
        if not self.endpoint_type or (self.endpoint_type == "futures_testnet" and not self.is_futures):
            await self.get_account_information()

        sym = symbol.upper()

        # Step 1: Server time offset
        time_path = "/fapi/v1/time" if self.is_futures else "/api/v3/time"
        offset_ms = 0
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                t_res = await client.get(f"{self.base_url}{time_path}", headers={"User-Agent": "TradePilotAI/1.0.0"})
                if t_res.status_code == 200:
                    server_time = t_res.json().get("serverTime", int(time.time() * 1000))
                    offset_ms = server_time - int(time.time() * 1000)
            except Exception as e:
                logger.warning(f"Failed to sync time before order: {e}")

            ts = int(time.time() * 1000) + offset_ms
            client_order_id = f"TP_{int(time.time())}_{sym[:4]}"

            # Step 2: Format parameters based on Futures vs Spot
            params_dict: Dict[str, Any] = {
                "symbol": sym,
                "side": side.upper(),
                "type": order_type.upper(),
                "recvWindow": 60000,
                "timestamp": ts,
            }

            if self.is_futures:
                order_path = "/fapi/v1/order"
                cur_p = Decimal("1.0")
                try:
                    p_res = await client.get(f"{self.base_url}/fapi/v1/ticker/price?symbol={sym}")
                    if p_res.status_code == 200:
                        cur_p = Decimal(str(p_res.json().get("price", "1.0")))
                        if quantity is None and quote_order_qty is not None and cur_p > 0:
                            quantity = Decimal(str(quote_order_qty)) / cur_p
                except Exception as p_err:
                    logger.warning(f"Failed to fetch price for {sym}: {p_err}")

                # Fetch symbol LOT_SIZE and MIN_NOTIONAL precision
                step_size = Decimal("0.001")
                min_qty = Decimal("0.001")
                max_qty = Decimal("1000000000.0")
                min_notional = Decimal("5.0")
                try:
                    ei_res = await client.get(f"{self.base_url}/fapi/v1/exchangeInfo")
                    if ei_res.status_code == 200:
                        s_info = next((s for s in ei_res.json().get("symbols", []) if s["symbol"] == sym), None)
                        if s_info:
                            lot_f = next((f for f in s_info.get("filters", []) if f.get("filterType") == "LOT_SIZE"), None)
                            if lot_f:
                                step_size = Decimal(str(lot_f.get("stepSize", "0.001")))
                                min_qty = Decimal(str(lot_f.get("minQty", "0.001")))
                                if lot_f.get("maxQty"):
                                    max_qty = Decimal(str(lot_f["maxQty"]))
                            market_lot_f = next((f for f in s_info.get("filters", []) if f.get("filterType") == "MARKET_LOT_SIZE"), None)
                            if market_lot_f and market_lot_f.get("maxQty"):
                                market_max = Decimal(str(market_lot_f["maxQty"]))
                                if market_max > Decimal("0"):
                                    max_qty = min(max_qty, market_max)
                            notional_f = next((f for f in s_info.get("filters", []) if f.get("filterType") == "MIN_NOTIONAL"), None)
                            if notional_f:
                                min_notional = Decimal(str(notional_f.get("notional", "5.0")))
                except Exception as e:
                    logger.warning(f"Could not load precision for {sym}: {e}")

                final_qty = max(quantity or Decimal("1.0"), min_qty)

                # Ensure order meets Binance MIN_NOTIONAL requirement when opening new position
                if not reduce_only and cur_p > 0 and (final_qty * cur_p) < min_notional:
                    needed_qty = (min_notional / cur_p) * Decimal("1.05")
                    final_qty = max(final_qty, needed_qty)

                # Cap at Binance maximum allowed order quantity
                if max_qty > Decimal("0"):
                    final_qty = min(final_qty, max_qty)

                # Precision rounding: round up to nearest step_size
                exp = abs(step_size.as_tuple().exponent)
                remainder = final_qty % step_size
                if remainder > 0:
                    final_qty = final_qty - remainder + step_size
                if exp > 0:
                    final_qty = round(final_qty, exp)
                else:
                    final_qty = int(final_qty)

                params_dict["quantity"] = str(final_qty)
                params_dict["newClientOrderId"] = client_order_id
                if reduce_only:
                    params_dict["reduceOnly"] = "true"
            else:
                # Spot API (/api/v3/order)
                order_path = "/api/v3/order"
                params_dict["newClientOrderId"] = client_order_id
                if order_type.upper() == "MARKET":
                    if quote_order_qty is not None and side.upper() == "BUY":
                        params_dict["quoteOrderQty"] = str(quote_order_qty)
                    elif quantity is not None:
                        params_dict["quantity"] = str(quantity)
                    else:
                        params_dict["quoteOrderQty"] = "20.0"  # default $20 USDT
                else:
                    if quantity is not None:
                        params_dict["quantity"] = str(quantity)
                    if price is not None:
                        params_dict["price"] = str(price)
                        params_dict["timeInForce"] = time_in_force

            # Sign query string
            sorted_items = sorted(params_dict.items())
            query_str = "&".join(f"{k}={v}" for k, v in sorted_items)
            signature = hmac.new(
                self.api_secret.encode("utf-8"),
                query_str.encode("utf-8"),
                hashlib.sha256
            ).hexdigest()

            submit_url = f"{self.base_url}{order_path}?{query_str}&signature={signature}"
            try:
                res = await client.post(submit_url, headers=self._get_headers())
                try:
                    data = res.json()
                except Exception:
                    data = {"raw": res.text}

                if res.status_code == 200:
                    logger.info(f"Binance order placed successfully for {sym}: {data.get('orderId')}")
                    return True, data, None
                else:
                    err_msg = data.get("msg", str(data))
                    code = data.get("code")
                    logger.error(f"Binance order rejected: {code} - {err_msg}")
                    return False, data, f"Binance order rejected ({code}): {err_msg}"
            except Exception as e:
                logger.error(f"Failed to submit order to {submit_url}: {e}")
                return False, {}, f"Network error submitting order to Binance: {str(e)}"
