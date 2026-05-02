# ============================================================
# FILE: API/DEX/dexscreener.py
# ROLE: Fetching price from Dexscreener via public API (curl_cffi)
# ============================================================
import json
import asyncio
from typing import Optional, Dict, Any
from curl_cffi.requests import AsyncSession
from c_log import UnifiedLogger

logger = UnifiedLogger("dex_api")

class DexscreenerAPI:
    BASE_URL = "https://api.dexscreener.com/latest/dex"

    def __init__(self, session: Optional[AsyncSession] = None):
        self.session = session or AsyncSession(
            impersonate="chrome120",
            http_version=2,
            verify=True
        )

    async def get_price_by_symbol(self, symbol: str, ref_price: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Search for a pair by symbol and return the best match.
        If ref_price is provided, picks the pair with the closest price.
        Otherwise, picks the most liquid pair.
        """
        # Cleanup symbol for search (e.g., BTCUSDT -> BTC)
        base_asset = symbol.replace("USDT", "").replace("USDC", "")
        url = f"{self.BASE_URL}/search?q={base_asset}"
        
        try:
            resp = await self.session.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = json.loads(resp.content)
                pairs = data.get("pairs", [])
                if pairs:
                    # Фильтруем пары с валидной ценой
                    valid_pairs = []
                    for p in pairs:
                        if p.get("priceUsd"):
                            # Извлекаем ликвидность для сортировки
                            liq = 0.0
                            try:
                                liq = float(p.get("liquidity", {}).get("usd", 0.0))
                            except:
                                pass
                            p["_liq"] = liq
                            valid_pairs.append(p)
                    
                    if not valid_pairs:
                        return None

                    # Если передана опорная цена, ищем максимально близкую по значению (защита от коллизий тикеров)
                    if ref_price and ref_price > 0:
                        # Сортируем по отклонению от референса в %
                        valid_pairs.sort(key=lambda x: abs(float(x["priceUsd"]) - ref_price) / ref_price)
                        
                        # Берем самый близкий, но только если отклонение не космическое (например, в пределах 50%)
                        best_match = valid_pairs[0]
                        price_diff = abs(float(best_match["priceUsd"]) - ref_price) / ref_price
                        if price_diff < 0.5:
                            return best_match

                    # Сортируем по ликвидности (от большего к меньшему)
                    valid_pairs.sort(key=lambda x: x["_liq"], reverse=True)
                    
                    # Возвращаем самую ликвидную пару
                    return valid_pairs[0]
            else:
                logger.debug(f"Dexscreener error: HTTP {resp.status_code} for {symbol}")
        except Exception as e:
            logger.debug(f"Error fetching Dexscreener for {symbol}: {e}")
        return None

    async def log_price_for_report(self, symbol: str, ref_price: Optional[float] = None):
        """
        Background task to log Dexscreener price for a symbol.
        """
        pair_data = await self.get_price_by_symbol(symbol, ref_price=ref_price)
        if pair_data:
            price = pair_data.get("priceUsd")
            dex_name = pair_data.get("dexId")
            pair_addr = pair_data.get("pairAddress")
            logger.info(f"[DEXCHECK] {symbol} | DexPrice: {price}$ | Ref: {ref_price}$ | DEX: {dex_name} | Pair: {pair_addr}")
        else:
            logger.debug(f"[DEXCHECK] {symbol} | Pair not found on Dexscreener")

if __name__ == "__main__":
    async def test():
        api = DexscreenerAPI()
        # Тестируем на монетке BEATUSDT (будет искать BEAT)
        print("Testing Dexscreener API for BEATUSDT with ref_price 0.5971...")
        await api.log_price_for_report("BEATUSDT", ref_price=0.5971)
        
        # Тестируем на BTCUSDT
        print("Testing Dexscreener API for BTCUSDT...")
        await api.log_price_for_report("BTCUSDT")

    asyncio.run(test())

# python -m API.DEX.dexscreener