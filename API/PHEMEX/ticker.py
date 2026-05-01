# ============================================================
# python -m API.PHEMEX.ticker
# ROLE: Phemex 24h ticker snapshot (curl_cffi)
# ============================================================
import ujson
from dataclasses import dataclass
from typing import Dict, Optional
from curl_cffi.requests import AsyncSession
from c_log import UnifiedLogger

logger = UnifiedLogger("api")

@dataclass
class TickerData:
    price: float
    volume_24h_usd: float

class PhemexTickerAPI:
    BASE_URL = "https://api.phemex.com"

    def __init__(self, session: Optional[AsyncSession] = None):
        self.session = session or AsyncSession(
            impersonate="chrome120",
            http_version=2,
            verify=True
        )

    async def get_all_tickers(self) -> Dict[str, TickerData]:
        url = f"{self.BASE_URL}/md/v3/ticker/24hr/all"
        try:
            resp = await self.session.get(url, timeout=10.0)
            if resp.status_code != 200:
                logger.error(f"Ticker fetch error: HTTP {resp.status_code}")
                return {}
                
            data = ujson.loads(resp.content)
            items = data.get("result", [])
            
            result: Dict[str, TickerData] = {}
            for item in items:
                if not isinstance(item, dict): continue
                sym = item.get("symbol")
                raw_price = item.get("lastRp") or item.get("lastPriceRp") or item.get("lastPrice")
                raw_volume = item.get("turnoverRv") or item.get("turnoverRp") or item.get("turnover24hRp") or "0"
                
                if not sym or raw_price is None: continue
                
                try:
                    price = float(raw_price)
                    volume = float(raw_volume)
                    if price > 0:
                        result[sym] = TickerData(price=price, volume_24h_usd=volume)
                except (ValueError, TypeError):
                    continue
            return result
            
        except Exception as e:
            logger.error(f"Error fetching tickers: {e}")
            return {}

    async def get_all_prices(self) -> Dict[str, float]:
        tickers = await self.get_all_tickers()
        return {sym: t.price for sym, t in tickers.items()}

    async def aclose(self):
        await self.session.close()

if __name__ == "__main__":
    import asyncio
    async def test():
        api = PhemexTickerAPI()
        try:
            res = await api.get_all_tickers()
            print(f"Fetched {len(res)} tickers")
            if "BTCUSDT" in res:
                print(f"BTC Price: {res['BTCUSDT'].price}")
        finally:
            await api.aclose()
    asyncio.run(test())

# python -m API.PHEMEX.ticker

# # --- Блок для локального тестирования ---
# if __name__ == "__main__":
#     import asyncio

#     async def main():
#         api = PhemexTickerAPI()
#         try:
#             prices = await api.get_all_prices()
#             print(f"Получено {len(prices)} тикеров от Phemex")
            
#             # Выведем первые 10 тикеров для проверки
#             for i, (sym, price) in enumerate(prices.items()):
#                 print(f"{sym}: {price}")
#                 if i >= 9:
#                     break
#         finally:
#             await api.aclose()

#     asyncio.run(main())