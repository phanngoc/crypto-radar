"""
Indicators: Tính toán tất cả chỉ báo kỹ thuật từ dữ liệu OHLCV.
Không phụ thuộc thư viện TA ngoài — chỉ dùng pandas.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List


@dataclass
class IndicatorSet:
    """Tập hợp tất cả chỉ báo đã tính toán."""
    # Giá hiện tại
    close:          float = 0.0
    open_:          float = 0.0
    high:           float = 0.0
    low:            float = 0.0
    volume:         float = 0.0
    price_change:   float = 0.0   # % so nến trước

    # RSI
    rsi:            float = 50.0

    # MACD
    macd:           float = 0.0
    macd_signal:    float = 0.0
    macd_hist:      float = 0.0
    macd_hist_prev: float = 0.0

    # Bollinger Bands
    bb_upper:       float = 0.0
    bb_mid:         float = 0.0
    bb_lower:       float = 0.0
    bb_pct:         float = 50.0  # % vị trí trong dải (0-100)
    bb_width:       float = 0.0   # Độ rộng dải (volatility)

    # EMA
    ema20:          float = 0.0
    ema50:          float = 0.0
    ema200:         float = 0.0

    # ATR
    atr:            float = 0.0
    atr_pct:        float = 0.0   # ATR / Close * 100

    # Volume
    vol_sma20:      float = 0.0
    vol_ratio:      float = 1.0   # Volume / SMA20

    # Stochastic
    stoch_k:        float = 50.0
    stoch_d:        float = 50.0

    # Support / Resistance
    support_levels:    List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    nearest_support:   float = 0.0
    nearest_resistance:float = 0.0

    # Xu hướng tổng thể
    trend:          str = "NEUTRAL"    # UPTREND | DOWNTREND | NEUTRAL
    trend_strength: int = 0            # 0-3


class TechnicalAnalyzer:
    """Tính toán tất cả chỉ báo kỹ thuật."""

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()

    def compute_all(self) -> IndicatorSet:
        df = self.df
        ind = IndicatorSet()

        # ── Giá hiện tại ──────────────────────────────────────────────────────
        ind.close  = df["close"].iloc[-1]
        ind.open_  = df["open"].iloc[-1]
        ind.high   = df["high"].iloc[-1]
        ind.low    = df["low"].iloc[-1]
        ind.volume = df["volume"].iloc[-1]
        ind.price_change = (
            (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] * 100
            if len(df) > 1 else 0.0
        )

        # ── RSI (14) ──────────────────────────────────────────────────────────
        ind.rsi = self._rsi(df["close"], 14)

        # ── MACD (12, 26, 9) ──────────────────────────────────────────────────
        ind.macd, ind.macd_signal, ind.macd_hist, ind.macd_hist_prev = (
            self._macd(df["close"])
        )

        # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────
        ind.bb_upper, ind.bb_mid, ind.bb_lower = self._bb(df["close"], 20, 2)
        band_range = ind.bb_upper - ind.bb_lower
        ind.bb_pct   = (ind.close - ind.bb_lower) / band_range * 100 if band_range else 50
        ind.bb_width = band_range / ind.bb_mid * 100 if ind.bb_mid else 0

        # ── EMA ───────────────────────────────────────────────────────────────
        ind.ema20  = self._ema(df["close"], 20)
        ind.ema50  = self._ema(df["close"], 50)
        ind.ema200 = self._ema(df["close"], 200)

        # ── ATR (14) ──────────────────────────────────────────────────────────
        ind.atr     = self._atr(df, 14)
        ind.atr_pct = ind.atr / ind.close * 100 if ind.close else 0

        # ── Volume ────────────────────────────────────────────────────────────
        ind.vol_sma20 = df["volume"].rolling(20).mean().iloc[-1]
        ind.vol_ratio = ind.volume / ind.vol_sma20 if ind.vol_sma20 else 1.0

        # ── Stochastic (14, 3) ────────────────────────────────────────────────
        ind.stoch_k, ind.stoch_d = self._stochastic(df, 14, 3)

        # ── Support / Resistance ──────────────────────────────────────────────
        sup, res = self._support_resistance(df, window=10, n=4)
        ind.support_levels    = sup
        ind.resistance_levels = res
        ind.nearest_support    = max((s for s in sup if s < ind.close), default=ind.close * 0.95)
        ind.nearest_resistance = min((r for r in res if r > ind.close), default=ind.close * 1.05)

        # ── Xu hướng tổng thể ─────────────────────────────────────────────────
        ind.trend, ind.trend_strength = self._determine_trend(ind)

        return ind

    # ── Private calculators ───────────────────────────────────────────────────

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float:
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - (100 / (1 + rs))
        val   = rsi.iloc[-1]
        return float(val) if not np.isnan(val) else 50.0

    @staticmethod
    def _macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[float, float, float, float]:
        ema_fast   = close.ewm(span=fast, adjust=False).mean()
        ema_slow   = close.ewm(span=slow, adjust=False).mean()
        macd_line  = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        hist       = macd_line - signal_line
        return (
            float(macd_line.iloc[-1]),
            float(signal_line.iloc[-1]),
            float(hist.iloc[-1]),
            float(hist.iloc[-2]) if len(hist) > 1 else 0.0,
        )

    @staticmethod
    def _bb(close: pd.Series, period: int = 20, std: float = 2.0) -> tuple[float, float, float]:
        sma   = close.rolling(period).mean()
        sigma = close.rolling(period).std()
        return (
            float((sma + std * sigma).iloc[-1]),
            float(sma.iloc[-1]),
            float((sma - std * sigma).iloc[-1]),
        )

    @staticmethod
    def _ema(close: pd.Series, period: int) -> float:
        val = close.ewm(span=period, adjust=False).mean().iloc[-1]
        return float(val) if not np.isnan(val) else float(close.iloc[-1])

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        high  = df["high"]
        low   = df["low"]
        close = df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        val = tr.rolling(period).mean().iloc[-1]
        return float(val) if not np.isnan(val) else float(high.iloc[-1] - low.iloc[-1])

    @staticmethod
    def _stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> tuple[float, float]:
        low_min  = df["low"].rolling(k_period).min()
        high_max = df["high"].rolling(k_period).max()
        rng = high_max - low_min
        k   = ((df["close"] - low_min) / rng.replace(0, float("nan")) * 100)
        d   = k.rolling(d_period).mean()
        kv  = float(k.iloc[-1]) if not np.isnan(k.iloc[-1]) else 50.0
        dv  = float(d.iloc[-1]) if not np.isnan(d.iloc[-1]) else 50.0
        return kv, dv

    @staticmethod
    def _support_resistance(
        df: pd.DataFrame, window: int = 10, n: int = 4
    ) -> tuple[list[float], list[float]]:
        """Tìm vùng hỗ trợ / kháng cự dựa trên pivot points."""
        supports, resistances = [], []
        highs = df["high"].values
        lows  = df["low"].values

        for i in range(window, len(df) - window):
            if highs[i] == max(highs[i - window:i + window + 1]):
                resistances.append(highs[i])
            if lows[i] == min(lows[i - window:i + window + 1]):
                supports.append(lows[i])

        # Gộp các mức gần nhau (trong 0.5% của nhau)
        def cluster(levels: list[float]) -> list[float]:
            if not levels:
                return []
            levels = sorted(set(levels))
            clustered = [levels[0]]
            for lvl in levels[1:]:
                if abs(lvl - clustered[-1]) / clustered[-1] > 0.005:
                    clustered.append(lvl)
            return clustered

        sup = sorted(cluster(supports), reverse=True)[:n]
        res = sorted(cluster(resistances))[:n]
        return sup, res

    @staticmethod
    def _determine_trend(ind: IndicatorSet) -> tuple[str, int]:
        score = 0
        if ind.close > ind.ema20:  score += 1
        if ind.close > ind.ema50:  score += 1
        if ind.close > ind.ema200: score += 1
        if ind.ema20 > ind.ema50:  score += 1
        if ind.ema50 > ind.ema200: score += 1

        if score >= 4:
            return "UPTREND", min(score - 3, 3)
        if score <= 1:
            return "DOWNTREND", 3 - score
        return "NEUTRAL", 0
