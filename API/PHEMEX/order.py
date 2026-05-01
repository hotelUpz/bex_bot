# ============================================================
# FILE: API/PHEMEX/order.py
# ROLE: Private Phemex G-API client (Unified Trading) via curl_cffi.
# ============================================================
import time
import ujson as json
import hmac
import hashlib
import asyncio
from typing import Any, Dict, Optional
from curl_cffi.requests import AsyncSession
from c_log import UnifiedLogger
from utils import float_to_str

logger = UnifiedLogger("api")

class PhemexPrivateClient:
    BASE_URL = "https://api.phemex.com"

    def __init__(self, api_key: str, api_secret: str, session: Optional[AsyncSession] = None, retries: int = 2):
        self.api_key = api_key
        self.api_secret = api_secret
        self.retries = retries
        
        self.session = session or AsyncSession(
            impersonate="chrome120",
            http_version=2,
            verify=True
        )

        # Контроль частоты запросов (Rate Limit)
        self._lock = asyncio.Lock()
        self._last_send_time = 0
        self.MIN_SEND_INTERVAL = 0.01  # 20ms между приватными запросами

    async def aclose(self):
        await self.session.close()

    def _get_signature(self, path: str, query_no_question: str, expiry: int, body_str: str) -> str:
        message = f"{path}{query_no_question}{expiry}{body_str}"
        return hmac.new(self.api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _request(self, method: str, path: str, query_no_q: str = "", body: Optional[Dict[str, Any]] = None, timeout_sec: float = 15.0) -> Dict[str, Any]:
        async with self._lock:
            elapsed = time.monotonic() - self._last_send_time
            if elapsed < self.MIN_SEND_INTERVAL:
                await asyncio.sleep(self.MIN_SEND_INTERVAL - elapsed)
            self._last_send_time = time.monotonic()        
        
        query_for_url = f"?{query_no_q}" if query_no_q else ""
        url = f"{self.BASE_URL}{path}{query_for_url}"
        body_str = json.dumps(body, separators=(',', ':')) if body else ""
        
        # Только безопасные методы можно ретраить
        attempts = self.retries if method.upper() in ("GET", "DELETE", "PUT") else 1
        last_err = None

        for attempt in range(1, attempts + 1):
            try:
                expiry = int(time.time() + 60)
                signature = self._get_signature(path, query_no_q, expiry, body_str)
                headers = {
                    "Content-Type": "application/json",
                    "x-phemex-access-token": self.api_key,
                    "x-phemex-request-expiry": str(expiry),
                    "x-phemex-request-signature": signature
                }
                
                resp = await self.session.request(
                    method, url, 
                    headers=headers, 
                    data=body_str if body else None, 
                    timeout=timeout_sec
                )
                
                if resp.status_code not in (200, 201, 202, 204):
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
                
                data = json.loads(resp.content)
                code = int(data.get("code", 0))
                if code != 0:
                    # Специальная обработка 429 от биржи (если будет)
                    raise RuntimeError(f"Phemex Error [{code}]: {data.get('msg', '')}")
                
                return data
                
            except Exception as e:
                last_err = e
                if attempt < attempts: 
                    await asyncio.sleep(0.5 * attempt)
        
        logger.error(f"Private API Failed ({method} {path}): {last_err}")
        raise RuntimeError(f"Private API request failed: {last_err}")

    async def set_leverage(self, symbol: str, leverage: float, mode: str = "hedged") -> Dict[str, Any]:
        """Установка плеча (G-API)"""
        lev_str = str(int(leverage)) if float(leverage).is_integer() else str(float(leverage))
        if mode == "hedged":
            query_no_q = f"longLeverageRr={lev_str}&shortLeverageRr={lev_str}&symbol={symbol}"
        else:
            query_no_q = f"leverageRr={lev_str}&symbol={symbol}"
        return await self._request("PUT", "/g-positions/leverage", query_no_q=query_no_q)

    async def place_market_order(self, symbol: str, side: str, qty: float, pos_side: str, reduce_only: bool = False) -> Dict[str, Any]:
        """Рыночный ордер (G-API)"""
        body = {
            "symbol": symbol, 
            "side": side, 
            "orderQtyRq": float_to_str(qty),
            "ordType": "Market", 
            "posSide": pos_side
        }
        if reduce_only:
            body["reduceOnly"] = True
        return await self._request("POST", "/g-orders", body=body)

    async def place_limit_order(self, symbol: str, side: str, qty: float, price: float, pos_side: str, reduce_only: bool = False) -> Dict[str, Any]:
        """Лимитный ордер (G-API)"""
        body = {
            "symbol": symbol, 
            "side": side, 
            "orderQtyRq": float_to_str(qty),
            "priceRp": float_to_str(price), 
            "ordType": "Limit", 
            "timeInForce": "GoodTillCancel", 
            "posSide": pos_side
        }
        if reduce_only:
            body["reduceOnly"] = True
        return await self._request("POST", "/g-orders", body=body)

    async def cancel_order(self, symbol: str, order_id: str, pos_side: str) -> Dict[str, Any]:
        """Отмена ордера (G-API)"""
        query_no_q = f"orderID={order_id}&posSide={pos_side}&symbol={symbol}"
        return await self._request("DELETE", "/g-orders/cancel", query_no_q=query_no_q)

    async def cancel_all_orders(self, symbol: str) -> Dict[str, Any]:
        """Отмена всех ордеров по символу (G-API)"""
        if not symbol or len(symbol) < 3: return {}
        return await self._request("DELETE", "/g-orders/all", query_no_q=f"symbol={symbol}")

    async def get_active_positions(self) -> Dict[str, Any]:
        """Получение активных позиций (G-API)"""
        return await self._request("GET", "/g-accounts/accountPositions", query_no_q="currency=USDT")

    async def switch_position_mode(self, currency: str = "USDT", mode: str = "Hedged") -> Dict[str, Any]:
        """Переключение режима позиций (Hedged / OneWay)"""
        body = {"currency": currency, "targetPosMode": mode}
        return await self._request("PUT", "/g-positions/switch-pos-mode-sync", body=body)
    
    async def get_equity(self, currency: str = "USDT") -> float:
        """Получение Equity (Balance + Unrealized PnL)"""
        resp = await self._request("GET", "/g-accounts/accountPositions", query_no_q=f"currency={currency}")
        
        data_block = resp.get("data")
        if not data_block:
            raise RuntimeError(f"Phemex API error: Empty data object returned")

        account = data_block.get("account")
        if not account:
            raise RuntimeError(f"Phemex API error: Missing account object")

        def _to_float(val: Any) -> float:
            try:
                return float(val) if val not in (None, "") else 0.0
            except (TypeError, ValueError):
                return 0.0

        account_balance = _to_float(account.get("accountBalanceRv"))
        positions = data_block.get("positions") or []
        total_unrealized = sum(_to_float(p.get("unRealisedPnlRv")) for p in positions)

        return account_balance + total_unrealized