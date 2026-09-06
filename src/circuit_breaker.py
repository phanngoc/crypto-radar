"""
CircuitBreaker: Bảo vệ tài khoản khỏi thua lỗ quá mức.

Logic:
  • Nếu daily PnL <= -DAILY_LOSS_LIMIT → dừng mọi entry mới (không thoát lệnh đang có)
  • Nếu session PnL <= -SESSION_LOSS_LIMIT → dừng toàn bộ
  • Sau khi trigger: agent vẫn chạy giám sát vị thế cũ, chỉ block entry mới
  • Reset daily counter lúc 00:00 UTC

Cấu hình (.env):
  CIRCUIT_DAILY_LOSS_LIMIT   = 50   (USDT, dương)
  CIRCUIT_SESSION_LOSS_LIMIT = 100  (USDT, dương)
  CIRCUIT_MAX_CONSEC_LOSSES  = 4    (số lệnh thua liên tiếp)
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from rich.console import Console

from .config import (
    CIRCUIT_DAILY_LOSS_LIMIT,
    CIRCUIT_SESSION_LOSS_LIMIT,
    CIRCUIT_MAX_CONSEC_LOSSES,
)

console = Console()


class CircuitBreaker:
    """
    Thread-safe circuit breaker cho trading agent.

    Cách dùng:
        cb = CircuitBreaker()
        cb.record_trade(pnl_usdt=-15.0, reason="STOP_LOSS")

        if cb.is_open():         # True = đang bị trip, không vào lệnh mới
            console.print("Circuit breaker mở!")
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Daily tracking
        self._daily_pnl:    float = 0.0
        self._daily_date:   str   = _today()

        # Session tracking
        self._session_pnl:  float = 0.0
        self._total_trades: int   = 0
        self._wins:         int   = 0
        self._losses:       int   = 0

        # Consecutive losses
        self._consec_losses: int  = 0

        # Trip flags
        self._daily_tripped:   bool = False
        self._session_tripped: bool = False
        self._emergency:       bool = False   # manual emergency stop

        # Trip reason
        self._trip_reason: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(self, pnl_usdt: float, reason: str = "") -> None:
        """Ghi nhận kết quả một lệnh và kiểm tra ngưỡng."""
        with self._lock:
            self._maybe_reset_daily()

            self._daily_pnl   += pnl_usdt
            self._session_pnl += pnl_usdt
            self._total_trades += 1

            if pnl_usdt >= 0:
                self._wins  += 1
                self._consec_losses = 0
            else:
                self._losses += 1
                self._consec_losses += 1

            self._check_limits()

    def is_open(self) -> bool:
        """True = circuit breaker đang trip → không vào lệnh mới."""
        with self._lock:
            self._maybe_reset_daily()
            return self._daily_tripped or self._session_tripped or self._emergency

    def trip_reason(self) -> str:
        with self._lock:
            return self._trip_reason

    def emergency_stop(self, reason: str = "Manual emergency stop") -> None:
        """Kích hoạt emergency stop từ bên ngoài."""
        with self._lock:
            self._emergency    = True
            self._trip_reason  = reason
        console.print(f"\n[bold red]🚨 EMERGENCY STOP: {reason}[/bold red]")

    def reset_emergency(self) -> None:
        with self._lock:
            self._emergency   = False
            self._trip_reason = ""

    def reset_daily(self) -> None:
        """Reset daily counter thủ công (thường dùng cho testing)."""
        with self._lock:
            self._daily_pnl     = 0.0
            self._daily_tripped = False
            if not self._session_tripped and not self._emergency:
                self._trip_reason = ""

    def stats(self) -> dict:
        with self._lock:
            self._maybe_reset_daily()
            wr = self._wins / self._total_trades * 100 if self._total_trades else 0
            return {
                "daily_pnl":       self._daily_pnl,
                "session_pnl":     self._session_pnl,
                "daily_limit":     -CIRCUIT_DAILY_LOSS_LIMIT,
                "session_limit":   -CIRCUIT_SESSION_LOSS_LIMIT,
                "total_trades":    self._total_trades,
                "wins":            self._wins,
                "losses":          self._losses,
                "win_rate":        round(wr, 1),
                "consec_losses":   self._consec_losses,
                "is_open":         self._daily_tripped or self._session_tripped or self._emergency,
                "trip_reason":     self._trip_reason,
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_limits(self) -> None:
        """Gọi trong lock. Kiểm tra các ngưỡng và trip nếu cần."""

        # Daily loss limit
        if not self._daily_tripped and self._daily_pnl <= -CIRCUIT_DAILY_LOSS_LIMIT:
            self._daily_tripped = True
            self._trip_reason = (
                f"Daily loss limit: ${self._daily_pnl:.2f} ≤ -${CIRCUIT_DAILY_LOSS_LIMIT:.2f}"
            )
            console.print(
                f"\n[bold red]🔴 CIRCUIT BREAKER — Daily Loss Limit[/bold red]\n"
                f"  Daily PnL: [red]${self._daily_pnl:.2f}[/red]  "
                f"(limit: -${CIRCUIT_DAILY_LOSS_LIMIT:.2f})\n"
                f"  [yellow]Tạm dừng entry mới cho đến 00:00 UTC[/yellow]"
            )

        # Session loss limit
        if not self._session_tripped and self._session_pnl <= -CIRCUIT_SESSION_LOSS_LIMIT:
            self._session_tripped = True
            self._trip_reason = (
                f"Session loss limit: ${self._session_pnl:.2f} ≤ -${CIRCUIT_SESSION_LOSS_LIMIT:.2f}"
            )
            console.print(
                f"\n[bold red]🔴 CIRCUIT BREAKER — Session Loss Limit[/bold red]\n"
                f"  Session PnL: [red]${self._session_pnl:.2f}[/red]  "
                f"(limit: -${CIRCUIT_SESSION_LOSS_LIMIT:.2f})\n"
                f"  [red]Dừng agent — cần khởi động lại thủ công[/red]"
            )

        # Consecutive losses
        if self._consec_losses >= CIRCUIT_MAX_CONSEC_LOSSES:
            if not self._daily_tripped:
                self._daily_tripped = True
                self._trip_reason = (
                    f"Consecutive losses: {self._consec_losses} ≥ {CIRCUIT_MAX_CONSEC_LOSSES}"
                )
                console.print(
                    f"\n[bold yellow]⚠  CIRCUIT BREAKER — {self._consec_losses} lệnh thua liên tiếp[/bold yellow]\n"
                    f"  [yellow]Tạm dừng entry mới — chờ thị trường ổn định[/yellow]"
                )

    def _maybe_reset_daily(self) -> None:
        """Reset daily PnL nếu sang ngày mới (UTC)."""
        today = _today()
        if today != self._daily_date:
            self._daily_date    = today
            self._daily_pnl     = 0.0
            self._daily_tripped = False
            self._consec_losses = 0
            if self._trip_reason.startswith("Daily loss") or \
               self._trip_reason.startswith("Consecutive"):
                self._trip_reason = ""


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
