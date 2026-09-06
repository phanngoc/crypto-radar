"""
WSTicker: Stream giá realtime từ OKX WebSocket.

Chạy trên background thread riêng.
Guardian đọc giá từ cache thay vì gọi REST mỗi 8s → sub-second latency.

Endpoint: wss://ws.okx.com:8443/ws/v5/public
Channel : tickers (public, không cần API key)
Format  : BTC-USDT (dash, không phải slash như ccxt)
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

console = Console()

# OKX WebSocket endpoints
WS_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"

# Timeout tối đa chờ WebSocket init (giây)
WS_INIT_TIMEOUT = 10


class WSTicker:
    """
    Stream ticker realtime từ OKX WebSocket.

    Sử dụng:
        ws = WSTicker(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        ws.start()

        price = ws.get_price("BTC/USDT")   # sub-second cache
        ws.stop()
    """

    def __init__(self, symbols: list[str]):
        # symbols dạng ccxt: "BTC/USDT" → chuyển sang OKX: "BTC-USDT"
        self._symbols  = symbols
        self._inst_ids = [_to_inst_id(s) for s in symbols]

        # Cache giá: {"BTC/USDT": {"price": ..., "bid": ..., "ask": ..., "ts": ...}}
        self._cache: dict[str, dict] = {}
        self._cache_lock = threading.Lock()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._ready   = threading.Event()  # set khi WS đã subscribe xong

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Khởi động WebSocket trong background thread."""
        self._running = True
        self._thread  = threading.Thread(
            target=self._run_loop, daemon=True, name="WSTicker"
        )
        self._thread.start()

        # Chờ WS sẵn sàng (hoặc timeout)
        if not self._ready.wait(timeout=WS_INIT_TIMEOUT):
            console.print("[yellow]⚠  WS init timeout — fallback REST sẽ được dùng[/yellow]")

    def stop(self) -> None:
        """Dừng WebSocket stream."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def get_price(self, symbol: str) -> Optional[float]:
        """Lấy giá mới nhất từ cache. None nếu chưa có."""
        with self._cache_lock:
            entry = self._cache.get(symbol)
            return entry["price"] if entry else None

    def get_ticker(self, symbol: str) -> Optional[dict]:
        """Lấy full ticker dict. None nếu chưa có."""
        with self._cache_lock:
            return dict(self._cache[symbol]) if symbol in self._cache else None

    def is_ready(self) -> bool:
        return self._ready.is_set()

    def cache_age(self, symbol: str) -> float:
        """Số giây kể từ lần cập nhật cuối. 999 nếu chưa có data."""
        with self._cache_lock:
            entry = self._cache.get(symbol)
            if not entry:
                return 999.0
            return time.time() - entry.get("local_ts", 0)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Chạy asyncio event loop trong thread riêng."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._ws_main())
        except Exception as e:
            console.print(f"[red dim]WS loop error: {e}[/red dim]")
        finally:
            self._loop.close()

    async def _ws_main(self) -> None:
        """Vòng lặp chính với auto-reconnect."""
        retry_delay = 2.0

        while self._running:
            try:
                await self._ws_connect()
                retry_delay = 2.0  # Reset sau khi kết nối thành công
            except Exception as e:
                if self._running:
                    console.print(
                        f"[yellow dim]WS disconnected ({e}), reconnect sau {retry_delay:.0f}s...[/yellow dim]"
                    )
                    self._ready.clear()
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 60)  # exponential backoff

    async def _ws_connect(self) -> None:
        """Kết nối và xử lý messages."""
        try:
            import websockets
        except ImportError:
            raise ImportError("Thiếu websockets. Chạy: pip install websockets")

        async with websockets.connect(
            WS_PUBLIC_URL,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            # Subscribe tickers
            sub_msg = {
                "op": "subscribe",
                "args": [
                    {"channel": "tickers", "instId": inst_id}
                    for inst_id in self._inst_ids
                ],
            }
            await ws.send(json.dumps(sub_msg))

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    self._handle_message(msg)
                except Exception:
                    pass

    def _handle_message(self, msg: dict) -> None:
        """Parse OKX ticker message và cập nhật cache."""
        # Subscribe confirm
        if msg.get("event") == "subscribe":
            self._ready.set()
            console.print(
                f"[dim green]✓ WS subscribed: {[a.get('instId') for a in msg.get('arg', [msg.get('arg', {})][:1])]}[/dim green]"
            )
            return

        # Error từ OKX
        if msg.get("event") == "error":
            console.print(f"[yellow dim]WS error: {msg.get('msg')}[/yellow dim]")
            return

        # Data message
        if msg.get("arg", {}).get("channel") != "tickers":
            return

        for tick in msg.get("data", []):
            inst_id = tick.get("instId", "")
            symbol  = _from_inst_id(inst_id)
            if not symbol:
                continue

            price = float(tick.get("last") or tick.get("bidPx") or 0)
            bid   = float(tick.get("bidPx") or 0)
            ask   = float(tick.get("askPx") or 0)

            if price <= 0:
                continue

            with self._cache_lock:
                self._cache[symbol] = {
                    "price":    price,
                    "bid":      bid,
                    "ask":      ask,
                    "change_pct": float(tick.get("sodUtc8") or 0),  # change since 8AM UTC+8
                    "vol_24h":  float(tick.get("vol24h") or 0),
                    "ts":       tick.get("ts", ""),
                    "local_ts": time.time(),
                }

            # Signal ready sau khi nhận tick đầu tiên
            if not self._ready.is_set():
                self._ready.set()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_inst_id(symbol: str) -> str:
    """BTC/USDT → BTC-USDT (OKX instId format)."""
    return symbol.replace("/", "-")


def _from_inst_id(inst_id: str) -> str:
    """BTC-USDT → BTC/USDT (ccxt format)."""
    return inst_id.replace("-", "/") if inst_id else ""
