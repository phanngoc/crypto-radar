"""
RiskCalculator: Tính Entry, Stop Loss, Take Profit và Position Size.

Logic:
  - Entry zone: giá hiện tại ± một khoảng nhỏ (0.2% buffer)
  - Stop Loss (LONG): entry - ATR * 1.5, kẹp bởi nearest support - 0.5%
  - Take Profit: RR 1.5 / 2.5 / 4.0 từ risk distance
  - Position size: Capital * risk_pct / (entry - stop_loss)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from .config import ATR_SL_MULTIPLIER, TP_RATIOS, TP_ALLOCATION
from .indicators import IndicatorSet


@dataclass
class TradePlan:
    side:           str         # LONG | SHORT | WAIT
    entry_price:    float       # Giá vào lệnh đề xuất
    entry_low:      float       # Vùng vào: thấp
    entry_high:     float       # Vùng vào: cao
    stop_loss:      float       # Cắt lỗ
    take_profits:   List[float] = field(default_factory=list)   # [TP1, TP2, TP3]
    tp_allocation:  List[float] = field(default_factory=list)   # % chốt mỗi TP
    risk_distance:  float = 0.0   # |entry - SL|
    risk_pct_price: float = 0.0   # risk_distance / entry * 100
    reward_tp1:     float = 0.0   # % lợi nhuận TP1
    reward_tp2:     float = 0.0
    reward_tp3:     float = 0.0
    rr_ratio:       float = 0.0   # Risk/Reward trung bình (dùng TP2)
    capital:        float = 0.0   # Vốn đầu vào (USDT)
    risk_amount:    float = 0.0   # USDT rủi ro tối đa
    position_usdt:  float = 0.0   # Kích thước vị thế (USDT)
    position_coins: float = 0.0   # Kích thước vị thế (coin)
    max_loss_usdt:  float = 0.0   # Thua tối đa (USDT)
    leverage:       int   = 1     # Đòn bẩy (mặc định spot = 1x)


class RiskCalculator:

    def calculate(
        self,
        indicators: IndicatorSet,
        ticker: dict,
        capital: float,
        risk_pct: float,   # ví dụ 0.02 = 2%
        score: int,
    ) -> TradePlan:
        """Xây dựng kế hoạch giao dịch hoàn chỉnh."""
        price  = ticker["price"]
        atr    = indicators.atr
        side   = "LONG" if score >= 10 else ("SHORT" if score <= -10 else "WAIT")

        if side == "WAIT":
            return self._wait_plan(price, capital, risk_pct)

        if side == "LONG":
            return self._long_plan(price, atr, indicators, capital, risk_pct)
        return self._short_plan(price, atr, indicators, capital, risk_pct)

    # ── LONG ──────────────────────────────────────────────────────────────────

    def _long_plan(
        self,
        price: float,
        atr: float,
        ind: IndicatorSet,
        capital: float,
        risk_pct: float,
    ) -> TradePlan:
        entry = price

        # SL: entry - ATR*1.5, không thấp hơn nearest_support - 0.5%
        sl_atr  = entry - atr * ATR_SL_MULTIPLIER
        sl_sup  = ind.nearest_support * 0.995 if ind.nearest_support else sl_atr
        sl      = max(sl_atr, sl_sup)   # lấy SL an toàn hơn (cao hơn)
        sl      = min(sl, entry * 0.97) # SL không vượt quá 3% dưới entry

        risk_dist = entry - sl
        tp1 = entry + risk_dist * TP_RATIOS[0]
        tp2 = entry + risk_dist * TP_RATIOS[1]
        tp3 = entry + risk_dist * TP_RATIOS[2]

        return self._build_plan("LONG", entry, sl, [tp1, tp2, tp3], capital, risk_pct)

    # ── SHORT ─────────────────────────────────────────────────────────────────

    def _short_plan(
        self,
        price: float,
        atr: float,
        ind: IndicatorSet,
        capital: float,
        risk_pct: float,
    ) -> TradePlan:
        entry = price

        sl_atr  = entry + atr * ATR_SL_MULTIPLIER
        sl_res  = ind.nearest_resistance * 1.005 if ind.nearest_resistance else sl_atr
        sl      = min(sl_atr, sl_res)
        sl      = max(sl, entry * 1.03)

        risk_dist = sl - entry
        tp1 = entry - risk_dist * TP_RATIOS[0]
        tp2 = entry - risk_dist * TP_RATIOS[1]
        tp3 = entry - risk_dist * TP_RATIOS[2]

        return self._build_plan("SHORT", entry, sl, [tp1, tp2, tp3], capital, risk_pct)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_plan(
        self,
        side: str,
        entry: float,
        sl: float,
        tps: List[float],
        capital: float,
        risk_pct: float,
    ) -> TradePlan:
        risk_dist    = abs(entry - sl)
        risk_amount  = capital * risk_pct
        pos_coins    = risk_amount / risk_dist if risk_dist > 0 else 0.0
        pos_usdt     = pos_coins * entry

        # Đảm bảo position không vượt quá toàn bộ vốn
        if pos_usdt > capital:
            pos_usdt  = capital
            pos_coins = pos_usdt / entry

        max_loss = pos_coins * risk_dist

        if side == "LONG":
            rewards = [(tp - entry) / entry * 100 for tp in tps]
        else:
            rewards = [(entry - tp) / entry * 100 for tp in tps]

        rr_ratio = rewards[1] / (risk_dist / entry * 100) if risk_dist else 0

        return TradePlan(
            side=side,
            entry_price=entry,
            entry_low=entry * 0.998,
            entry_high=entry * 1.002,
            stop_loss=sl,
            take_profits=tps,
            tp_allocation=list(TP_ALLOCATION),
            risk_distance=risk_dist,
            risk_pct_price=risk_dist / entry * 100,
            reward_tp1=rewards[0],
            reward_tp2=rewards[1],
            reward_tp3=rewards[2],
            rr_ratio=rr_ratio,
            capital=capital,
            risk_amount=risk_amount,
            position_usdt=pos_usdt,
            position_coins=pos_coins,
            max_loss_usdt=max_loss,
        )

    @staticmethod
    def _wait_plan(price: float, capital: float, risk_pct: float) -> TradePlan:
        return TradePlan(
            side="WAIT",
            entry_price=price,
            entry_low=price * 0.995,
            entry_high=price * 1.005,
            stop_loss=0.0,
            take_profits=[],
            tp_allocation=[],
            capital=capital,
            risk_amount=capital * risk_pct,
        )
