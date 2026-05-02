# ============================================================
# FILE: ENTRY/pattern_math.py
# ROLE: Оптимизированная математика паттернов стакана (HFT)
# ============================================================

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Literal, Any, TYPE_CHECKING
from CORE.models_fsm import EntryPayload
from c_log import UnifiedLogger

logger = UnifiedLogger("pattern_math")

class StakanEntryPattern:
    def __init__(self, phemex_cfg: dict[str, Any]):
        self.cfg = phemex_cfg
        
        self.enabled: bool = self.cfg["enable"]
        self.depth: int = self.cfg.get("depth", 5) # Default to 5 if missing
        self.min_vol: Optional[float] = self.cfg.get("min_first_row_usdt_notional")
        self.max_vol: Optional[float] = self.cfg.get("max_first_row_usdt_notional")

        self.max_spread_pct: Optional[float] = self.cfg.get("max_spread_pct")
        self.min_imbalance: Optional[float] = self.cfg.get("min_imbalance")

    def analyze(self, bids: list[tuple[float, float]], asks: list[tuple[float, float]], symbol: str, target_direction: Literal["LONG", "SHORT"]) -> Optional[EntryPayload]:
        if not self.enabled:
            return None
            
        # Нам нужно как минимум 2 уровня для p1 и p2
        if len(bids) < 2 or len(asks) < 2:
            return None

        if target_direction == "LONG":
            return self._check_pattern(asks, bids, "LONG", symbol)
        else:
            return self._check_pattern(bids, asks, "SHORT", symbol)

    def _check_pattern(self, side: list[tuple[float, float]], opp_side: list[tuple[float, float]], direction: Literal["LONG", "SHORT"], symbol: str) -> Optional[EntryPayload]:
        p1, p2 = side[0][0], side[1][0]
        opp_p1 = opp_side[0][0]
        
        # 1. Проверка объема первой строки
        row_vol_asset = side[0][1]
        vol_usdt = row_vol_asset * p1

        if self.min_vol is not None and vol_usdt < self.min_vol:
            logger.debug(f"[{symbol}] [{direction}] Skip: Vol {vol_usdt:.1f} < {self.min_vol}")
            return None
        if self.max_vol is not None and vol_usdt > self.max_vol:
            logger.debug(f"[{symbol}] [{direction}] Skip: Vol {vol_usdt:.1f} > {self.max_vol}")
            return None

        ob_spread = abs(p1 - opp_p1) / opp_p1 * 100
        if self.max_spread_pct is not None and ob_spread > self.max_spread_pct:
            logger.debug(f"[{symbol}] [{direction}] Skip: OB Spread {ob_spread:.2f}% > {self.max_spread_pct}%")
            return None

        # 2. Проверка дисбаланса объемов (Imbalance)
        if self.min_imbalance is not None:
            side_total_usdt = sum(row[0] * row[1] for row in side)
            opp_total_usdt = sum(row[0] * row[1] for row in opp_side)
            
            if side_total_usdt > 0:
                imbalance = opp_total_usdt / side_total_usdt
                if imbalance < self.min_imbalance:
                    logger.debug(f"[{symbol}] [{direction}] Skip: Imbalance {imbalance:.1f} < {self.min_imbalance}")
                    return None

        return EntryPayload(
            side=direction,
            price=p1,
            init_ask1=p1 if direction == "LONG" else opp_p1,
            init_bid1=opp_p1 if direction == "LONG" else p1,
            row_vol_usdt=vol_usdt,
            row_vol_asset=row_vol_asset,
            base_target_price_100=p2,
            mid_price=(p1 + opp_p1) / 2
        )
