"""
SignalEngine: Chấm điểm tín hiệu -100 đến +100 từ tập chỉ báo.
Mỗi chỉ báo đóng góp điểm theo trọng số, kết quả tổng hợp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from .indicators import IndicatorSet


@dataclass
class SignalDetail:
    name:       str
    direction:  str   # MUA | BÁN | TRUNG LẬP
    strength:   str   # MẠNH | VỪA | YẾU
    value:      str   # Giá trị hiển thị
    score:      int   # Điểm đóng góp (có thể âm)


@dataclass
class SignalResult:
    score:      int                       # Tổng điểm -100..+100
    label:      str                       # Nhãn văn bản
    emoji:      str                       # Emoji đại diện
    color:      str                       # Màu Rich
    side:       str                       # LONG | SHORT | WAIT
    confidence: int                       # Độ tin cậy 0-100%
    details:    Dict[str, SignalDetail] = field(default_factory=dict)


class SignalEngine:
    """
    Trọng số tối đa:
        RSI        25  (quá bán/mua cực mạnh)
        MACD       20  (momentum + crossover)
        BB         15  (vị trí trong dải)
        EMA Trend  20  (cấu trúc xu hướng)
        Volume     15  (xác nhận volume)
        Stochastic  5  (bổ sung)
    Tổng: ±100
    """

    MAX_SCORE = 100

    def score(self, ind: IndicatorSet) -> Tuple[int, Dict[str, SignalDetail]]:
        total = 0
        details: Dict[str, SignalDetail] = {}

        # 1. RSI ──────────────────────────────────────────────────────── ±25
        rsi_score, rsi_detail = self._score_rsi(ind.rsi)
        total += rsi_score
        details["RSI"] = rsi_detail

        # 2. MACD ─────────────────────────────────────────────────────── ±20
        macd_score, macd_detail = self._score_macd(
            ind.macd, ind.macd_signal, ind.macd_hist, ind.macd_hist_prev
        )
        total += macd_score
        details["MACD"] = macd_detail

        # 3. Bollinger Bands ──────────────────────────────────────────── ±15
        bb_score, bb_detail = self._score_bb(ind.bb_pct, ind.bb_width)
        total += bb_score
        details["BB"] = bb_detail

        # 4. EMA Trend ────────────────────────────────────────────────── ±20
        ema_score, ema_detail = self._score_ema(
            ind.close, ind.ema20, ind.ema50, ind.ema200
        )
        total += ema_score
        details["EMA"] = ema_detail

        # 5. Volume ───────────────────────────────────────────────────── ±15
        vol_score, vol_detail = self._score_volume(ind.vol_ratio, ind.price_change)
        total += vol_score
        details["VOL"] = vol_detail

        # 6. Stochastic ───────────────────────────────────────────────── ±5
        stoch_score, stoch_detail = self._score_stochastic(ind.stoch_k, ind.stoch_d)
        total += stoch_score
        details["STOCH"] = stoch_detail

        total = max(-self.MAX_SCORE, min(self.MAX_SCORE, total))
        result = self._build_result(total, details)
        return total, result.details

    # ── Scorers ───────────────────────────────────────────────────────────────

    def _score_rsi(self, rsi: float) -> Tuple[int, SignalDetail]:
        if rsi < 20:
            return 25, SignalDetail("RSI", "MUA", "MẠNH", f"{rsi:.1f}", 25)
        if rsi < 30:
            return 20, SignalDetail("RSI", "MUA", "VỪA", f"{rsi:.1f}", 20)
        if rsi < 40:
            return 10, SignalDetail("RSI", "MUA", "YẾU", f"{rsi:.1f}", 10)
        if rsi < 60:
            return 0,  SignalDetail("RSI", "TRUNG LẬP", "—", f"{rsi:.1f}", 0)
        if rsi < 70:
            return -10, SignalDetail("RSI", "BÁN", "YẾU", f"{rsi:.1f}", -10)
        if rsi < 80:
            return -20, SignalDetail("RSI", "BÁN", "VỪA", f"{rsi:.1f}", -20)
        return -25, SignalDetail("RSI", "BÁN", "MẠNH", f"{rsi:.1f}", -25)

    def _score_macd(
        self,
        macd: float,
        signal: float,
        hist: float,
        hist_prev: float,
    ) -> Tuple[int, SignalDetail]:
        momentum_up   = hist > hist_prev
        momentum_down = hist < hist_prev
        bullish_cross = macd > signal
        bearish_cross = macd < signal

        if bullish_cross and hist > 0 and momentum_up:
            return 20, SignalDetail("MACD", "MUA", "MẠNH", f"Hist:{hist:+.4f}", 20)
        if bullish_cross and momentum_up:
            return 12, SignalDetail("MACD", "MUA", "VỪA", f"Cross↑ momentum↑", 12)
        if bullish_cross:
            return 8,  SignalDetail("MACD", "MUA", "YẾU", f"Cross↑", 8)
        if bearish_cross and hist < 0 and momentum_down:
            return -20, SignalDetail("MACD", "BÁN", "MẠNH", f"Hist:{hist:+.4f}", -20)
        if bearish_cross and momentum_down:
            return -12, SignalDetail("MACD", "BÁN", "VỪA", f"Cross↓ momentum↓", -12)
        if bearish_cross:
            return -8,  SignalDetail("MACD", "BÁN", "YẾU", f"Cross↓", -8)
        return 0, SignalDetail("MACD", "TRUNG LẬP", "—", f"Hist:{hist:+.4f}", 0)

    def _score_bb(self, bb_pct: float, bb_width: float) -> Tuple[int, SignalDetail]:
        pct = max(0, min(100, bb_pct))
        # Squeeze (dải hẹp) → giảm ý nghĩa tín hiệu
        squeeze = bb_width < 2.0
        factor  = 0.6 if squeeze else 1.0

        if pct < 5:
            sc = int(15 * factor)
            return sc, SignalDetail("BB", "MUA", "MẠNH", f"BB%={pct:.0f}%", sc)
        if pct < 20:
            sc = int(10 * factor)
            return sc, SignalDetail("BB", "MUA", "VỪA", f"BB%={pct:.0f}%", sc)
        if pct < 35:
            sc = int(5 * factor)
            return sc, SignalDetail("BB", "MUA", "YẾU", f"BB%={pct:.0f}%", sc)
        if pct < 65:
            return 0,  SignalDetail("BB", "TRUNG LẬP", "—", f"BB%={pct:.0f}%", 0)
        if pct < 80:
            sc = int(-5 * factor)
            return sc, SignalDetail("BB", "BÁN", "YẾU", f"BB%={pct:.0f}%", sc)
        if pct < 95:
            sc = int(-10 * factor)
            return sc, SignalDetail("BB", "BÁN", "VỪA", f"BB%={pct:.0f}%", sc)
        sc = int(-15 * factor)
        return sc, SignalDetail("BB", "BÁN", "MẠNH", f"BB%={pct:.0f}%", sc)

    def _score_ema(
        self, close: float, ema20: float, ema50: float, ema200: float
    ) -> Tuple[int, SignalDetail]:
        sc    = 0
        parts = []

        if close > ema200:
            sc += 8; parts.append("P>EMA200")
        else:
            sc -= 8; parts.append("P<EMA200")

        if ema20 > ema50:
            sc += 6; parts.append("EMA20>50")
        else:
            sc -= 6; parts.append("EMA20<50")

        if ema50 > ema200:
            sc += 6; parts.append("Golden✓")
        else:
            sc -= 6; parts.append("Death X")

        label = "TRUNG LẬP"
        strength = "—"
        dir_ = "TRUNG LẬP"
        if sc >= 15:
            dir_, strength = "MUA", "MẠNH"
        elif sc >= 8:
            dir_, strength = "MUA", "VỪA"
        elif sc > 0:
            dir_, strength = "MUA", "YẾU"
        elif sc <= -15:
            dir_, strength = "BÁN", "MẠNH"
        elif sc <= -8:
            dir_, strength = "BÁN", "VỪA"
        elif sc < 0:
            dir_, strength = "BÁN", "YẾU"

        return sc, SignalDetail("EMA", dir_, strength, " | ".join(parts), sc)

    def _score_volume(self, vol_ratio: float, price_change_pct: float) -> Tuple[int, SignalDetail]:
        is_green = price_change_pct >= 0
        vr = vol_ratio

        if vr >= 3.0 and is_green:
            return 15, SignalDetail("VOL", "MUA", "MẠNH", f"{vr:.1f}x nến xanh", 15)
        if vr >= 2.0 and is_green:
            return 10, SignalDetail("VOL", "MUA", "VỪA", f"{vr:.1f}x nến xanh", 10)
        if vr >= 1.5 and is_green:
            return 5,  SignalDetail("VOL", "MUA", "YẾU", f"{vr:.1f}x nến xanh", 5)
        if vr >= 3.0 and not is_green:
            return -15, SignalDetail("VOL", "BÁN", "MẠNH", f"{vr:.1f}x nến đỏ", -15)
        if vr >= 2.0 and not is_green:
            return -10, SignalDetail("VOL", "BÁN", "VỪA", f"{vr:.1f}x nến đỏ", -10)
        if vr >= 1.5 and not is_green:
            return -5,  SignalDetail("VOL", "BÁN", "YẾU", f"{vr:.1f}x nến đỏ", -5)
        return 0, SignalDetail("VOL", "TRUNG LẬP", "—", f"{vr:.1f}x", 0)

    def _score_stochastic(self, k: float, d: float) -> Tuple[int, SignalDetail]:
        if k < 20 and d < 20 and k > d:
            return 5,  SignalDetail("STOCH", "MUA", "VỪA", f"K={k:.0f} D={d:.0f}", 5)
        if k < 20:
            return 3,  SignalDetail("STOCH", "MUA", "YẾU", f"K={k:.0f}", 3)
        if k > 80 and d > 80 and k < d:
            return -5, SignalDetail("STOCH", "BÁN", "VỪA", f"K={k:.0f} D={d:.0f}", -5)
        if k > 80:
            return -3, SignalDetail("STOCH", "BÁN", "YẾU", f"K={k:.0f}", -3)
        return 0,  SignalDetail("STOCH", "TRUNG LẬP", "—", f"K={k:.0f} D={d:.0f}", 0)

    # ── Build result label ─────────────────────────────────────────────────────

    @staticmethod
    def _build_result(score: int, details: dict) -> SignalResult:
        if score >= 60:
            label, emoji, color, side = "MUA MẠNH",  "🟢🟢", "bold green", "LONG"
        elif score >= 25:
            label, emoji, color, side = "MUA",        "🟢",   "green",      "LONG"
        elif score >= 10:
            label, emoji, color, side = "TÍCH CỰC",  "🟡",   "yellow",     "LONG"
        elif score >= -10:
            label, emoji, color, side = "TRUNG LẬP", "⚪",   "white",      "WAIT"
        elif score >= -25:
            label, emoji, color, side = "TIÊU CỰC",  "🟠",   "yellow",     "WAIT"
        elif score >= -60:
            label, emoji, color, side = "BÁN",        "🔴",   "red",        "SHORT"
        else:
            label, emoji, color, side = "BÁN MẠNH",  "🔴🔴", "bold red",   "SHORT"

        confidence = min(100, abs(score))
        return SignalResult(
            score=score, label=label, emoji=emoji, color=color,
            side=side, confidence=confidence, details=details,
        )


def build_signal_result(score: int, details: dict) -> SignalResult:
    """Helper để tạo SignalResult từ bên ngoài module."""
    return SignalEngine._build_result(score, details)
