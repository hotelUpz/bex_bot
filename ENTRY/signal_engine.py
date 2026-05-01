# ============================================================
# FILE: ENTRY/engine.py
# ROLE: Оркестратор логики входа (Pattern math, Binance filter, TTLs, Funding)
# ============================================================
from __future__ import annotations

import time
from typing import Dict, Any, Optional, TYPE_CHECKING

from ENTRY.pattern_math import StakanEntryPattern
from CORE.models_fsm import EntryPayload

if TYPE_CHECKING:
    from API.PHEMEX.funding import PhemexFunding
    from API.BINANCE.funding import BinanceFunding
    from API.PHEMEX.stakan import DepthTop
    from ENTRY.funding_filters import FundingFilter1, FundingFilter2
    from ENTRY.funding_manager import FundingManager


class SignalEngine:
    def __init__(self, cfg: Dict[str, Any], funding_manager: 'FundingManager'):
        self.cfg = cfg
        self.funding_manager = funding_manager  
        
        self.spread_cfg = cfg["pattern"]["main_spread_pattern"]
        self.ob_cfg = cfg["pattern"]["orderbook_filter"]
        
        self.pattern_math = StakanEntryPattern(self.ob_cfg)
        
        # main_spread_pattern
        self.spread_enabled = self.spread_cfg["enable"]
        self.spread_to_entry_pct = abs(self.spread_cfg["spread_to_entry_pct"])
        self.spread_ttl = self.spread_cfg["ttl_sec"]
        
        # orderbook_filter
        self.ob_enabled = self.ob_cfg["enable"]
        self.ob_ttl = self.ob_cfg["pattern_ttl_sec"]
        
        self._pattern_first_seen: Dict[str, float] = {}
        self._spread_first_seen: Dict[str, float] = {}

    def analyze(self, depth: DepthTop, b_price: float, p_price: float) -> Optional[EntryPayload]:
        symbol: str = depth.symbol
        now: float = time.time()
        
        # 0. Проверка фандинга (глобальный фильтр)
        if not self.funding_manager.is_trade_allowed(symbol):
            return None

        # 1. Главный сигнал: main_spread_pattern
        if not self.spread_enabled:
            return None
            
        if not b_price or not p_price:
            return None

        # Расчет спреда: (binance - phemex) / phemex
        spread_pct = (b_price - p_price) / p_price * 100
        
        direction: Optional[Literal["LONG", "SHORT"]] = None
        if spread_pct >= self.spread_to_entry_pct:
            direction = "LONG"
        elif spread_pct <= -self.spread_to_entry_pct:
            direction = "SHORT"
            
        if not direction:
            keys_to_pop = [f"{symbol}_LONG", f"{symbol}_SHORT"]
            for k in keys_to_pop:
                self._spread_first_seen.pop(k, None)
                self._pattern_first_seen.pop(k, None)
            return None
            
        pos_key = f"{symbol}_{direction}"

        # TTL для спреда
        if self.spread_ttl > 0:
            first_seen_s = self._spread_first_seen.setdefault(pos_key, now)
            if now - first_seen_s < self.spread_ttl:
                return None
        
        # 2. Вспомогательный сигнал: фильтр стакана
        signal: Optional[EntryPayload] = None
        if self.ob_enabled:
            # StakanEntryPattern.analyze вернет сигнал если стакан "красивый"
            # Внутри StakanEntryPattern.analyze уже есть проверка на enabled
            
            # Мы передаем только нужную сторону для оптимизации, но StakanEntryPattern.analyze 
            # проверяет обе стороны и возвращает EntrySignal с side.
            # Нам нужно убедиться, что EntrySignal.side совпадает с нашим direction.
            
            bids_sliced = depth.bids[:3] # Мы используем только 2-3 уровня для базовых расчетов
            asks_sliced = depth.asks[:3]
            
            # Для упрощения вызываем полный анализ и проверяем соответствие стороны
            full_ob_signal = self.pattern_math.analyze(bids_sliced, asks_sliced)
            if full_ob_signal and full_ob_signal.side == direction:
                signal = full_ob_signal
            else:
                self._pattern_first_seen.pop(pos_key, None)
                return None
        else:
            # Если фильтр стакана выключен, генерируем базовый сигнал
            bid1 = depth.bids[0][0]
            ask1 = depth.asks[0][0]
            price = ask1 if direction == "LONG" else bid1
            
            signal = EntryPayload(
                side=direction,
                price=price,
                init_ask1=ask1,
                init_bid1=bid1,
                row_vol_usdt=0, # Не важно если фильтр выключен
                row_vol_asset=0,
                base_target_price_100=depth.asks[1][0] if direction == "LONG" else depth.bids[1][0],
                mid_price=(bid1 + ask1) / 2
            )

        # TTL для паттерна стакана
        if self.ob_enabled and self.ob_ttl > 0:
            first_seen_p = self._pattern_first_seen.setdefault(pos_key, now)
            if now - first_seen_p < self.ob_ttl:
                return None

        # Сброс TTL после успешного прохождения всех фильтров
        self._spread_first_seen.pop(pos_key, None)
        self._pattern_first_seen.pop(pos_key, None)
        
        # Дополняем сигнал данными спреда для репортов
        signal.b_price = b_price
        signal.p_price = p_price
        signal.spread = spread_pct
        
        return signal