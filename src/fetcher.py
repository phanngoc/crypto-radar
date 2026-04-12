"""
Fetcher: Tải dữ liệu thị trường từ Binance và Alternative.me.
"""
from __future__ import annotations

import time
import requests
import pandas as pd
from typing import Optional

from .config import (
    BINANCE_BASE, FEAR_GREED_URL,
    CANDLE_LIMIT, COIN_ALIASES,
)


class CryptoFetcher:
    """Lấy dữ liệu OHLCV, ticker 24h và chỉ số Fear & Greed."""

    SESSION_TIMEOUT = 10  # giây

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ── Public API ────────────────────────────────────────────────────────────

    def get_ohlcv(self, symbol: str, interval: str) -> pd.DataFrame:
        """Trả về DataFrame OHLCV từ Binance."""
        pair = self._to_pair(symbol)
        url  = f"{BINANCE_BASE}/api/v3/klines"
        params = {
            "symbol":   pair,
            "interval": interval,
            "limit":    CANDLE_LIMIT,
        }
        raw = self._get(url, params)
        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ])
        df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype(float)
        return df.reset_index(drop=True)

    def get_ticker(self, symbol: str) -> dict:
        """Trả về thông tin ticker 24h (giá, % thay đổi, volume...)."""
        pair = self._to_pair(symbol)
        url  = f"{BINANCE_BASE}/api/v3/ticker/24hr"
        data = self._get(url, {"symbol": pair})
        return {
            "symbol":       symbol,
            "pair":         pair,
            "price":        float(data["lastPrice"]),
            "change_pct":   float(data["priceChangePercent"]),
            "high_24h":     float(data["highPrice"]),
            "low_24h":      float(data["lowPrice"]),
            "volume_24h":   float(data["volume"]),
            "quote_vol_24h":float(data["quoteVolume"]),
        }

    def get_fear_greed(self) -> dict:
        """Trả về chỉ số Fear & Greed index (Alternative.me)."""
        try:
            resp = self._session.get(FEAR_GREED_URL, timeout=self.SESSION_TIMEOUT)
            resp.raise_for_status()
            entry = resp.json()["data"][0]
            return {
                "value":        int(entry["value"]),
                "classification": entry["value_classification"],
            }
        except Exception:
            return {"value": 50, "classification": "Neutral"}

    def validate_symbol(self, symbol: str) -> bool:
        """Kiểm tra symbol có tồn tại trên Binance không."""
        pair = self._to_pair(symbol)
        url  = f"{BINANCE_BASE}/api/v3/ticker/price"
        try:
            self._get(url, {"symbol": pair})
            return True
        except ValueError:
            return False

    # ── Internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _to_pair(symbol: str) -> str:
        """BTC → BTCUSDT"""
        sym = COIN_ALIASES.get(symbol.upper(), symbol.upper())
        if sym.endswith("USDT"):
            return sym
        return sym + "USDT"

    def _get(self, url: str, params: dict) -> dict | list:
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=self.SESSION_TIMEOUT)
                if resp.status_code == 400:
                    raise ValueError(f"Symbol không hợp lệ hoặc không tồn tại: {params}")
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.ConnectionError:
                if attempt == 2:
                    raise ConnectionError("Không thể kết nối Binance API. Kiểm tra mạng.")
                time.sleep(1.5 * (attempt + 1))
            except ValueError:
                raise
