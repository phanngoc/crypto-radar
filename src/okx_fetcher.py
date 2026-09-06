"""
OKXFetcher: Lấy dữ liệu OHLCV và ticker từ OKX qua ccxt.

Không cần API key để lấy dữ liệu public (OHLCV, ticker, orderbook).
API key chỉ cần khi trading thật.

Output DataFrame chuẩn hoá cùng format với fetcher.py (Binance)
để TechnicalAnalyzer & SignalEngine dùng được ngay.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Optional

import pandas as pd

try:
    import ccxt
except ImportError:
    raise ImportError("Thiếu thư viện ccxt. Chạy: pip install ccxt")

from .config import (
    CANDLE_LIMIT,
    OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE,
    OKX_SANDBOX, OKX_MARKET_TYPE,
    FEAR_GREED_URL,
)


class OKXFetcher:
    """Fetch dữ liệu thị trường từ OKX."""

    # Mapping timeframe alias → OKX/ccxt interval
    _TF_MAP: dict[str, str] = {
        "scalp": "15m",
        "1h":    "1h",
        "swing": "4h",
        "4h":    "4h",
        "1d":    "1d",
        "position": "1d",
        "1w":    "1w",
        "15m":   "15m",
        "30m":   "30m",
    }

    def __init__(self, authenticated: bool = False):
        """
        authenticated=False → chỉ dùng public API (market data).
        authenticated=True  → dùng API key để trade.
        """
        params: dict = {
            "enableRateLimit": True,
            "options": {"defaultType": OKX_MARKET_TYPE},
        }
        if authenticated and OKX_API_KEY:
            params.update({
                "apiKey":   OKX_API_KEY,
                "secret":   OKX_SECRET,
                "password": OKX_PASSPHRASE,
            })

        self.exchange = ccxt.okx(params)

        if OKX_SANDBOX:
            self.exchange.set_sandbox_mode(True)

        self._authenticated = authenticated

    # ── Public data ───────────────────────────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = CANDLE_LIMIT,
    ) -> pd.DataFrame:
        """
        Trả về DataFrame chuẩn với cột:
          open_time, open, high, low, close, volume
        Sắp xếp tăng dần theo thời gian (nến cũ → mới).
        """
        tf = self._TF_MAP.get(timeframe, timeframe)

        for attempt in range(3):
            try:
                raw = self.exchange.fetch_ohlcv(symbol, tf, limit=limit)
                break
            except ccxt.NetworkError:
                if attempt == 2:
                    raise
                time.sleep(1.5)

        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["open_time"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df[["open_time", "open", "high", "low", "close", "volume"]]
        df = df.astype({
            "open": float, "high": float, "low": float,
            "close": float, "volume": float,
        })
        return df.reset_index(drop=True)

    def fetch_ticker(self, symbol: str) -> dict:
        """
        Trả về dict:
          price, change_pct, high_24h, low_24h, volume_24h, quote_vol_24h
        (Tương thích với fetcher.py Binance)
        """
        for attempt in range(3):
            try:
                t = self.exchange.fetch_ticker(symbol)
                break
            except ccxt.NetworkError:
                if attempt == 2:
                    raise
                time.sleep(1.5)

        return {
            "price":        float(t["last"] or t["close"] or 0),
            "change_pct":   float(t.get("percentage") or 0),
            "high_24h":     float(t.get("high") or 0),
            "low_24h":      float(t.get("low") or 0),
            "volume_24h":   float(t.get("baseVolume") or 0),
            "quote_vol_24h":float(t.get("quoteVolume") or 0),
        }

    def fetch_fear_greed(self) -> dict:
        """Fear & Greed Index từ alternative.me (không liên quan OKX)."""
        import requests
        try:
            r = requests.get(FEAR_GREED_URL, timeout=5)
            data = r.json()["data"][0]
            return {
                "value": int(data["value"]),
                "classification": data["value_classification"],
            }
        except Exception:
            return {"value": 50, "classification": "Neutral"}

    def validate_symbol(self, symbol: str) -> bool:
        """Kiểm tra symbol có tồn tại trên OKX không."""
        try:
            markets = self.exchange.load_markets()
            return symbol in markets
        except Exception:
            return False

    def fetch_balance(self) -> dict:
        """Lấy số dư tài khoản (cần authenticated=True)."""
        if not self._authenticated:
            raise PermissionError("Cần API key để xem số dư.")
        return self.exchange.fetch_balance()

    def fetch_open_orders(self, symbol: str) -> list:
        """Lấy danh sách lệnh đang mở (cần authenticated=True)."""
        if not self._authenticated:
            return []
        try:
            return self.exchange.fetch_open_orders(symbol)
        except Exception:
            return []

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def normalize_symbol(raw: str) -> str:
        """
        Chuẩn hoá input người dùng → OKX symbol.
        BTC → BTC/USDT
        btc  → BTC/USDT
        BTCUSDT → BTC/USDT (cố gắng)
        """
        raw = raw.strip().upper()
        if "/" in raw:
            return raw
        if raw.endswith("USDT"):
            base = raw[:-4]
            return f"{base}/USDT"
        return f"{raw}/USDT"
