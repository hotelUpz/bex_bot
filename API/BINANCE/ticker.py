# ============================================================
# FILE: API/BINANCE/ticker.py
# ROLE: Binance 24h ticker snapshot (curl_cffi)
# ============================================================
import ujson
from typing import Dict, Optional
from curl_cffi.requests import AsyncSession
from c_log import UnifiedLogger

logger = UnifiedLogger("api")

class BinanceTickerAPI:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, session: Optional[AsyncSession] = None):
        self.session = session or AsyncSession(
            impersonate="chrome120",
            http_version=2,
            verify=True
        )

    async def aclose(self):
        await self.session.close()

    async def get_all_prices(self) -> Dict[str, float]:
        """Получает горячие цены (last price) по всем символам Binance разом"""
        url = f"{self.BASE_URL}/fapi/v1/ticker/price"
        try:
            resp = await self.session.get(url, timeout=10.0)
            if resp.status_code != 200:
                logger.error(f"Binance ticker error: HTTP {resp.status_code}")
                return {}
                
            data = ujson.loads(resp.content)
            if not isinstance(data, list):
                return {}

            result = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                    
                sym = item.get("symbol")
                raw_price = item.get("price")
                
                if sym and raw_price is not None:
                    try:
                        price = float(raw_price)
                        if price > 0:
                            result[sym] = price
                    except (ValueError, TypeError):
                        continue
                        
            return result
        except Exception as e:
            logger.error(f"Error fetching Binance tickers: {e}")
            return {}

# --- Блок для локального тестирования ---
if __name__ == "__main__":
    import asyncio

    async def main():
        api = BinanceTickerAPI()
        try:
            prices = await api.get_all_prices()
            print(f"Получено {len(prices)} тикеров от Binance")
            
            # Вывести первые 10 пар
            for i, (symbol, price) in enumerate(prices.items()):
                print(f"{symbol}: {price}")
                if i >= 9:
                    break

            # Получить конкретную цену
            print(f"\nBTCUSDT: {prices.get('BTCUSDT')}")
        finally:
            await api.aclose()

    asyncio.run(main())
    
# python -m API.BINANCE.ticker