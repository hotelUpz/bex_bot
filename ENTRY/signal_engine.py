# ============================================================
# FILE: ENTRY/engine.py
# ROLE: Оркестратор логики входа (Pattern math, Binance filter, TTLs, Funding)
# ============================================================
from __future__ import annotations

import time
import asyncio
from typing import Dict, Any, Optional, TYPE_CHECKING, Literal

from ENTRY.pattern_math import StakanEntryPattern
from CORE.models_fsm import EntryPayload
from c_log import UnifiedLogger

if TYPE_CHECKING:
    # from API.PHEMEX.funding import PhemexFunding
    # from API.BINANCE.funding import BinanceFunding
    from API.PHEMEX.stakan import DepthTop
    # from ENTRY.funding_filters import FundingFilter1, FundingFilter2
    from ENTRY.funding_manager import FundingManager
    from CORE.rsi_manager import RSIManager
    from API.DEX.dexscreener import DexscreenerAPI


logger = UnifiedLogger("signal_engine")

class SignalEngine:
    def __init__(self, cfg: Dict[str, Any], funding_manager: 'FundingManager', rsi_manager: Optional['RSIManager'] = None, dex_api: Optional['DexscreenerAPI'] = None):
        self.cfg = cfg
        self.funding_manager = funding_manager  
        self.rsi_manager = rsi_manager
        self.dex_api = dex_api
        
        self.binance_trigger_cfg = cfg["pattern"]["binance_trigger"]
        self.ob_cfg = cfg["pattern"]["orderbook_filter"]
        self.fair_cfg = cfg["pattern"].get("fair_price_filter", {"enable": False})
        self.rsi_cfg = cfg["pattern"].get("rsi_filter", {"enable": False})
        self.dex_cfg = cfg["pattern"].get("dex_filter", {"enable": False})
        
        self.pattern_math = StakanEntryPattern(self.ob_cfg)
        
        # binance_trigger
        self.spread_enabled = self.binance_trigger_cfg["enable"]
        self.spread_to_entry_pct = abs(self.binance_trigger_cfg["spread_to_entry_pct"])
        self.spread_ttl = self.binance_trigger_cfg["ttl_sec"]
        
        # orderbook_filter
        self.ob_enabled = self.ob_cfg["enable"]
        self.ob_ttl = self.ob_cfg["pattern_ttl_sec"]
        
        # fair_price_filter
        self.fair_enabled = self.fair_cfg.get("enable", False)
        self.min_fair_spread_pct = self.fair_cfg.get("min_fair_spread_pct", 0.0)

        # rsi_filter
        self.rsi_enabled = self.rsi_cfg.get("enable", False)
        self.rsi_overbought = self.rsi_cfg.get("overbought", 70)
        self.rsi_oversold = self.rsi_cfg.get("oversold", 30)

        # dex_filter
        self.dex_enabled = self.dex_cfg.get("enable", False)
        self.min_dex_spread_pct = abs(self.dex_cfg.get("min_dex_spread_pct", 0.0))

        self.allowed_directions: list[str] = []
        self._setup_directions(cfg)
        
        self._pattern_first_seen: Dict[str, float] = {}
        self._spread_first_seen: Dict[str, float] = {}
        
        self._max_spreads: Dict[str, float] = {}
        self._last_spread_log_ts = time.time()

    async def analyze(self, depth: DepthTop, b_price: float, p_price: float, b_fair: float = 0.0, p_fair: float = 0.0) -> Optional[EntryPayload]:
        symbol: str = depth.symbol
        now: float = time.time()

        # --- МОНИТОРИНГ СПРЕДА ---
        spread_pct = 0.0
            
        if not b_price or not p_price:
            return None

        spread_pct = (b_price - p_price) / p_price * 100
        abs_spread = abs(spread_pct)
        if abs_spread > self._max_spreads.get(symbol, 0.0):
            self._max_spreads[symbol] = abs_spread
        
        if now - self._last_spread_log_ts >= 60:
            self._flush_spread_logs()
            self._last_spread_log_ts = now
    # -------------------------
        
        # 0. Проверка фандинга (глобальный фильтр)
        if not self.funding_manager.is_trade_allowed(symbol):
            return None

        # 1. Триггер: binance_trigger
        if not self.spread_enabled:
            return None

        direction: Optional[Literal["LONG", "SHORT"]] = None
        if spread_pct >= self.spread_to_entry_pct:
            direction = "LONG"
        elif spread_pct <= -self.spread_to_entry_pct:
            direction = "SHORT"
            
        if not direction or direction not in self.allowed_directions:
            keys_to_pop = [f"{symbol}_LONG", f"{symbol}_SHORT"]
            for k in keys_to_pop:
                self._spread_first_seen.pop(k, None)
                self._pattern_first_seen.pop(k, None)
            return None

        # 1.1 Фильтр: Справедливая цена Phemex
        if self.fair_enabled and p_fair > 0:
            fair_diff_pct = (p_fair - p_price) / p_price * 100
            if direction == "LONG":
                if fair_diff_pct < self.min_fair_spread_pct:
                    return None
            else: # SHORT
                if fair_diff_pct > -self.min_fair_spread_pct:
                    return None
            
        # 1.2 Фильтр: RSI
        if self.rsi_enabled and self.rsi_manager:
            rsi = self.rsi_manager.get_rsi(symbol)
            if rsi is not None:
                if direction == "LONG" and rsi >= self.rsi_overbought:
                    logger.debug(f"⚠️ RSI Filter [LONG]: {symbol} RSI={rsi:.1f} >= {self.rsi_overbought} (Overbought)")
                    return None
                if direction == "SHORT" and rsi <= self.rsi_oversold:
                    logger.debug(f"⚠️ RSI Filter [SHORT]: {symbol} RSI={rsi:.1f} <= {self.rsi_oversold} (Oversold)")
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

        # 1.3 Фильтр: DEX Price (Dexscreener)
        if self.dex_enabled and self.dex_api:
            # Запрашиваем цену на DEX (с учетом референса p_price для защиты от коллизий)
            pair_data = await self.dex_api.get_price_by_symbol(symbol, ref_price=p_price)
            if not pair_data:
                logger.debug(f"⚠️ DEX Filter: {symbol} не найден на Dexscreener или нет ликвидности. Пропуск.")
                return None
            
            dex_price = float(pair_data.get("priceUsd", 0))
            if dex_price <= 0:
                return None
            
            dex_spread_pct = (dex_price - p_price) / p_price * 100
            
            if direction == "LONG":
                # Для лонга: цена на DEX должна быть ВЫШЕ цены Phemex на порог
                if dex_spread_pct < self.min_dex_spread_pct:
                    logger.debug(f"⚠️ DEX Filter [LONG]: {symbol} DexPrice({dex_price}) <= Phemex({p_price}) + {self.min_dex_spread_pct}%")
                    return None
            else: # SHORT
                # Для шорта: цена на DEX должна быть НИЖЕ цены Phemex на порог
                if dex_spread_pct > -self.min_dex_spread_pct:
                    logger.debug(f"⚠️ DEX Filter [SHORT]: {symbol} DexPrice({dex_price}) >= Phemex({p_price}) - {self.min_dex_spread_pct}%")
                    return None
            
            # Если прошли — логируем успех фильтра
            logger.info(f"✅ DEX Filter OK: {symbol} | DEX: {dex_price}$ | Phemex: {p_price}$ | Spread: {dex_spread_pct:.2f}%")

        # Сброс TTL после успешного прохождения всех фильтров
        self._spread_first_seen.pop(pos_key, None)
        self._pattern_first_seen.pop(pos_key, None)
        
        # Дополняем сигнал данными спреда для репортов
        signal.b_price = b_price
        signal.p_price = p_price
        signal.spread = spread_pct
        
        # Запускаем фоновую проверку Dexscreener для отчетности (уже не фоном, если фильтр включен, но оставим для совместимости)
        if self.dex_api and not self.dex_enabled:
            asyncio.create_task(self.dex_api.log_price_for_report(symbol, ref_price=p_price))
        
        return signal

    def _setup_directions(self, cfg: Dict[str, Any]) -> None:
        """Парсинг и валидация разрешенных направлений торговли."""
        raw_dirs = cfg.get("allowed_directions", ["LONG", "SHORT"])
        if not isinstance(raw_dirs, list):
            raw_dirs = [raw_dirs]
            
        self.allowed_directions = [str(x).strip().upper() for x in raw_dirs if x]
        
        # Гарантируем, что есть хотя бы одно валидное направление
        if not any(d in ("LONG", "SHORT") for d in self.allowed_directions):
            logger.warning(f"⚠️ Критическая ошибка в конфиге allowed_directions: {self.allowed_directions}. Сброс на LONG+SHORT.")
            self.allowed_directions = ["LONG", "SHORT"]

    def _flush_spread_logs(self) -> None:
        if not self._max_spreads:
            return
        
        sorted_items = sorted(self._max_spreads.items(), key=lambda x: x[1], reverse=True)
        top_10 = sorted_items[:10]
        
        msg = "📊 [MAX SPREADS 1m]: " + " | ".join([f"{s}: {v:.3f}%" for s, v in top_10])
        logger.info(msg)
        self._max_spreads.clear()
