# ============================================================
# FILE: EXIT/scenarios/base.py
# ============================================================
from __future__ import annotations
from typing import TYPE_CHECKING
from c_log import UnifiedLogger

if TYPE_CHECKING:
    from CORE.models_fsm import ActivePosition
    from API.PHEMEX.stakan import DepthTop

logger = UnifiedLogger("base")

class BaseScenario:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.enable = cfg["enable"] 
        self.stab_ttl = cfg["stabilization_ttl"]
        self.min_target_rate = cfg["min_target_rate"]
        self.spread_to_exit_pct = cfg["spread_to_exit_pct"]

    def _calc_virtual_tp(self, pos: ActivePosition) -> float:
        """Вычисляет виртуальный TP без сайд-эффектов."""
        if pos.side == "LONG":
            return pos.entry_price + (pos.base_target_price_100 - pos.entry_price) * self.min_target_rate
        return pos.entry_price - (pos.entry_price - pos.base_target_price_100) * self.min_target_rate

    def scen_base_analyze(self, depth: DepthTop, pos: ActivePosition, current_price_spread: float, now: float) -> bool | None:
        if not self.enable:
            return None   

        if pos.current_qty <= 0:
            return None

        if (now - pos.opened_at) < self.stab_ttl:
            return None

        # 1. Base checking:
        if pos.side == "LONG":
            if current_price_spread >= self.spread_to_exit_pct:
                return None
        else:
            if current_price_spread <= self.spread_to_exit_pct:
                return None

        if self.min_target_rate is None:
            return True

        # 2. 
        virtual_tp = self._calc_virtual_tp(pos)

        stakan_condition = False
        max_vol = -1.0

        if pos.side == "LONG":
            for price, vol in depth.bids:
                if price >= virtual_tp:
                    if vol > max_vol:
                        max_vol = vol
                        stakan_condition = True
                else:
                    break
        else:
            for price, vol in depth.asks:
                if price <= virtual_tp:
                    if vol > max_vol:
                        max_vol = vol
                        stakan_condition = True
                else:
                    break

        return stakan_condition