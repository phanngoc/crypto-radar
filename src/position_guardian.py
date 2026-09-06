"""
PositionGuardian: Giám sát vị thế mở realtime, xử lý SL/TP/Trailing stop.

Nâng cấp v2:
  • Đọc giá từ WSTicker cache (sub-second) thay vì REST poll 8s
  • Fallback về REST nếu WS cache quá cũ (>15s)
  • Tốc độ check: 1s (đủ nhanh để bắt SL ngay cả trên scalp 15m)
  • TP3 support — thoát toàn bộ khi hit TP3
  • Telegram notifications cho mọi sự kiện
  • Update SL order trên sàn sau khi trail (dùng update_sl_order)

Ưu tiên:
  1. Stop Loss  → thoát ngay, hủy tất cả TP
  2. TP3 hit    → thoát phần còn lại (sau TP1+TP2)
  3. TP2 hit    → thoát 35% (sau TP1)
  4. TP1 hit    → kéo SL lên entry (breakeven), giữ phần còn lại
  5. Trailing   → kéo SL theo đỉnh/đáy
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from rich.console import Console

from .config import (
    AGENT_GUARDIAN_INTERVAL,
    AGENT_TRAILING_STOP,
    AGENT_TRAILING_ACTIVATION,
    AGENT_TRAILING_DISTANCE,
)

if TYPE_CHECKING:
    from .auto_executor import AutoExecutor
    from .okx_fetcher import OKXFetcher
    from .ws_ticker import WSTicker
    from .notifier import Notifier

console = Console()

# Cache quá cũ (giây) → fallback REST
WS_MAX_STALE = 15.0


class PositionGuardian:
    """
    Chạy liên tục trong background thread.
    Kiểm tra giá vs SL/TP mỗi AGENT_GUARDIAN_INTERVAL giây.
    Ưu tiên đọc giá từ WSTicker (realtime), fallback REST.
    """

    def __init__(
        self,
        positions: dict,
        executor: "AutoExecutor",
        fetcher: "OKXFetcher",
        lock: threading.Lock,
        ws_ticker: Optional["WSTicker"] = None,
        notifier: Optional["Notifier"] = None,
        on_exit_callback=None,
    ):
        self.positions  = positions
        self.executor   = executor
        self.fetcher    = fetcher
        self.lock       = lock
        self.ws         = ws_ticker
        self.notifier   = notifier
        self.on_exit    = on_exit_callback
        self._running   = False
        self._thread: Optional[threading.Thread] = None

    # ── Control ───────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="PositionGuardian"
        )
        self._thread.start()
        interval = AGENT_GUARDIAN_INTERVAL
        src = "WS+REST" if self.ws else "REST"
        console.print(
            f"[dim]Guardian started — check mỗi {interval}s  "
            f"| giá từ [{src}][/dim]"
        )

    def stop(self) -> None:
        self._running = False

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                self._check_all()
            except Exception as e:
                console.print(f"[red dim]Guardian loop error: {e}[/red dim]")
            time.sleep(AGENT_GUARDIAN_INTERVAL)

    def _check_all(self) -> None:
        with self.lock:
            symbols = list(self.positions.keys())

        for symbol in symbols:
            try:
                self._check_one(symbol)
            except Exception as e:
                console.print(f"[yellow dim]Guardian {symbol}: {e}[/yellow dim]")

    def _check_one(self, symbol: str) -> None:
        with self.lock:
            if symbol not in self.positions:
                return
            pos = dict(self.positions[symbol])

        direction = pos["direction"]
        entry     = pos["entry_price"]
        sl        = pos["stop_loss"]
        tp1       = pos.get("tp1")
        tp2       = pos.get("tp2")
        tp3       = pos.get("tp3")
        peak      = pos.get("peak_price", entry)
        tp1_hit   = pos.get("tp1_hit", False)
        tp2_hit   = pos.get("tp2_hit", False)

        # ── Lấy giá: WS trước, fallback REST ──────────────────────────────
        price = self._get_price(symbol)
        if price is None or price <= 0:
            return

        # ── Cập nhật peak ──────────────────────────────────────────────────
        new_peak = peak
        if direction == "long" and price > peak:
            new_peak = price
        elif direction == "short" and price < peak:
            new_peak = price

        if new_peak != peak:
            with self.lock:
                if symbol in self.positions:
                    self.positions[symbol]["peak_price"] = new_peak

        # ── 1. Stop Loss (ưu tiên cao nhất) ───────────────────────────────
        sl_hit = (direction == "long" and price <= sl) or \
                 (direction == "short" and price >= sl)

        if sl_hit:
            self._exit(symbol, price, "STOP_LOSS", pos)
            return

        # ── 2. TP3 (toàn bộ phần còn lại sau TP1+TP2) ────────────────────
        if tp3 and tp1_hit and tp2_hit:
            tp3_hit = (direction == "long" and price >= tp3) or \
                      (direction == "short" and price <= tp3)
            if tp3_hit:
                self._exit(symbol, price, "TP3", pos)
                return

        # ── 3. TP2 (35% sau TP1) ──────────────────────────────────────────
        if tp2 and tp1_hit and not tp2_hit:
            tp2_hit_now = (direction == "long" and price >= tp2) or \
                          (direction == "short" and price <= tp2)
            if tp2_hit_now:
                self._partial_exit_tp2(symbol, price, pos)
                return

        # ── 4. TP1 (40% đầu tiên) ─────────────────────────────────────────
        if tp1 and not tp1_hit:
            tp1_hit_now = (direction == "long" and price >= tp1) or \
                          (direction == "short" and price <= tp1)
            if tp1_hit_now:
                self._partial_exit_tp1(symbol, price, pos)
                return

        # ── 5. Trailing Stop ───────────────────────────────────────────────
        if AGENT_TRAILING_STOP:
            self._update_trailing(symbol, price, new_peak, pos, direction, entry, sl)

    # ── TP / SL handlers ─────────────────────────────────────────────────────

    def _partial_exit_tp1(self, symbol: str, price: float, pos: dict) -> None:
        """TP1 hit: đánh dấu, kéo SL về entry (breakeven)."""
        entry     = pos["entry_price"]
        direction = pos["direction"]
        pnl_pct   = _pnl(direction, entry, price)

        console.print(
            f"[green]🎯 TP1 [bold]{symbol}[/bold] @ ${price:,.4f}  "
            f"PnL: [bold]+{pnl_pct:.2f}%[/bold]  → SL → breakeven[/green]"
        )

        # Tính SL mới tại entry + buffer nhỏ
        new_sl = entry * (1.001 if direction == "long" else 0.999)

        with self.lock:
            if symbol in self.positions:
                self.positions[symbol]["tp1_hit"]   = True
                if direction == "long" and new_sl > self.positions[symbol]["stop_loss"]:
                    self.positions[symbol]["stop_loss"] = new_sl
                elif direction == "short" and new_sl < self.positions[symbol]["stop_loss"]:
                    self.positions[symbol]["stop_loss"] = new_sl

        # Cập nhật SL order trên sàn
        if self.executor.mode == "live":
            remaining_coins = pos["position_coins"] * 0.60  # 60% còn sau TP1
            new_sl_id = self.executor.update_sl_order(
                symbol,
                pos.get("sl_order_id"),
                new_sl,
                remaining_coins,
                direction,
            )
            if new_sl_id:
                with self.lock:
                    if symbol in self.positions:
                        self.positions[symbol]["sl_order_id"] = new_sl_id

        # Notify
        if self.notifier:
            pnl_usdt = pnl_pct / 100 * pos["position_usdt"] * pos.get("tp_allocation", [0.4])[0]
            remain_pct = 60.0
            self.notifier.tp_hit(symbol, 1, price, pnl_pct, pnl_usdt, remain_pct)

    def _partial_exit_tp2(self, symbol: str, price: float, pos: dict) -> None:
        """TP2 hit: thoát 35% phần còn lại."""
        entry     = pos["entry_price"]
        direction = pos["direction"]
        pnl_pct   = _pnl(direction, entry, price)

        console.print(
            f"[green]🎯🎯 TP2 [bold]{symbol}[/bold] @ ${price:,.4f}  "
            f"PnL: [bold]+{pnl_pct:.2f}%[/bold][/green]"
        )

        with self.lock:
            if symbol in self.positions:
                self.positions[symbol]["tp2_hit"] = True

        # Paper: không cần exit thực sự (TP3 sẽ thoát nốt)
        # Live: TP2 limit order đã được đặt trên sàn, sẽ tự fill
        # Nếu cần manual exit (paper hoặc lệnh không fill được):
        if self.executor.mode == "paper":
            alloc    = pos.get("tp_allocation", [0.4, 0.35, 0.25])
            tp2_pct  = alloc[1] if len(alloc) > 1 else 0.35
            tp2_coins = pos["position_coins"] * tp2_pct
            self.executor.exit(symbol, direction, tp2_coins, "TP2", price)

        if self.notifier:
            alloc    = pos.get("tp_allocation", [0.4, 0.35, 0.25])
            tp2_pct  = alloc[1] if len(alloc) > 1 else 0.35
            pnl_usdt = pnl_pct / 100 * pos["position_usdt"] * tp2_pct
            remain_pct = (1 - alloc[0] - alloc[1]) * 100
            self.notifier.tp_hit(symbol, 2, price, pnl_pct, pnl_usdt, remain_pct)

    def _exit(self, symbol: str, price: float, reason: str, pos: dict) -> None:
        """Thoát toàn bộ vị thế (SL hoặc TP3)."""
        entry     = pos["entry_price"]
        direction = pos["direction"]
        amount    = pos["position_coins"]
        pnl_pct   = _pnl(direction, entry, price)
        pnl_usdt  = pnl_pct / 100 * pos["position_usdt"]

        color = "green" if pnl_pct >= 0 else "red"
        icon  = "✅" if pnl_pct >= 0 else "🔴"

        console.print(
            f"[{color}]{icon} EXIT [{reason}] [bold]{symbol}[/bold] @ ${price:,.4f}  "
            f"PnL: [bold]{pnl_pct:+.2f}%[/bold] ({pnl_usdt:+.2f} USDT)[/{color}]"
        )

        # Thực thi exit
        self.executor.exit(symbol, direction, amount, reason, price)

        # Xóa khỏi positions dict
        with self.lock:
            self.positions.pop(symbol, None)

        # Notifications
        if self.notifier:
            if reason == "STOP_LOSS":
                self.notifier.sl_hit(
                    symbol, price, pnl_pct, pnl_usdt,
                    trailed=pos.get("sl_moved", False),
                )
            elif reason.startswith("TP"):
                lvl = int(reason.replace("TP", "") or "3")
                self.notifier.tp_hit(symbol, lvl, price, pnl_pct, pnl_usdt)

        # Callback agent (cooldown, stats)
        if self.on_exit:
            try:
                self.on_exit(symbol, reason, pnl_pct, pnl_usdt)
            except Exception:
                pass

    # ── Trailing Stop ─────────────────────────────────────────────────────────

    def _update_trailing(
        self,
        symbol: str,
        price: float,
        peak: float,
        pos: dict,
        direction: str,
        entry: float,
        current_sl: float,
    ) -> None:
        if direction == "long":
            profit_pct = (peak - entry) / entry * 100
            if profit_pct >= AGENT_TRAILING_ACTIVATION:
                new_sl = peak * (1 - AGENT_TRAILING_DISTANCE / 100)
                if new_sl > current_sl + (entry * 0.001):
                    self._apply_trail(symbol, current_sl, new_sl, peak, pos, direction)
        else:
            profit_pct = (entry - peak) / entry * 100
            if profit_pct >= AGENT_TRAILING_ACTIVATION:
                new_sl = peak * (1 + AGENT_TRAILING_DISTANCE / 100)
                if new_sl < current_sl - (entry * 0.001):
                    self._apply_trail(symbol, current_sl, new_sl, peak, pos, direction)

    def _apply_trail(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        peak: float,
        pos: dict,
        direction: str,
    ) -> None:
        arrow = "📈" if direction == "long" else "📉"
        console.print(
            f"[cyan]{arrow} Trail SL [bold]{symbol}[/bold]  "
            f"${old_sl:,.4f} → [bold]${new_sl:,.4f}[/bold]  "
            f"(peak ${peak:,.4f})[/cyan]"
        )

        with self.lock:
            if symbol in self.positions:
                self.positions[symbol]["stop_loss"] = new_sl
                self.positions[symbol]["sl_moved"]  = True

        # Cập nhật SL order trên sàn
        if self.executor.mode == "live":
            # Tính coins còn lại: trừ đi phần đã TP
            total = pos["position_coins"]
            alloc = pos.get("tp_allocation", [0.4, 0.35, 0.25])
            remaining = total
            if pos.get("tp1_hit"):
                remaining -= total * alloc[0]
            if pos.get("tp2_hit"):
                remaining -= total * (alloc[1] if len(alloc) > 1 else 0.35)

            if remaining > 0:
                new_sl_id = self.executor.update_sl_order(
                    symbol,
                    pos.get("sl_order_id"),
                    new_sl,
                    remaining,
                    direction,
                )
                if new_sl_id:
                    with self.lock:
                        if symbol in self.positions:
                            self.positions[symbol]["sl_order_id"] = new_sl_id

        # Notify (không spam — chỉ khi trail cách xa ít nhất 0.5%)
        if self.notifier:
            move_pct = abs(new_sl - old_sl) / old_sl * 100
            if move_pct >= 0.5:
                self.notifier.trail_update(symbol, old_sl, new_sl, peak, direction)

    # ── Price feed ────────────────────────────────────────────────────────────

    def _get_price(self, symbol: str) -> Optional[float]:
        """
        Lấy giá từ WSTicker nếu sẵn và không quá cũ.
        Fallback: REST fetch_ticker.
        """
        if self.ws and self.ws.is_ready():
            age = self.ws.cache_age(symbol)
            if age < WS_MAX_STALE:
                p = self.ws.get_price(symbol)
                if p and p > 0:
                    return p

        # Fallback REST
        try:
            ticker = self.fetcher.fetch_ticker(symbol)
            return ticker.get("price")
        except Exception:
            return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pnl(direction: str, entry: float, price: float) -> float:
    if direction == "long":
        return (price - entry) / entry * 100
    return (entry - price) / entry * 100


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")
