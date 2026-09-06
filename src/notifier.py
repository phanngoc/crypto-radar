"""
Notifier: Gửi alert qua Telegram khi có sự kiện trading.

Cấu hình trong .env:
  TELEGRAM_BOT_TOKEN=123456:ABC...
  TELEGRAM_CHAT_ID=-1001234567890  (hoặc @username)

Nếu không có token → im lặng (không crash).

Events:
  • Entry   — vào lệnh LONG/SHORT
  • TP hit  — chốt lời từng phần
  • SL hit  — cắt lỗ
  • Trail   — SL di chuyển theo trailing
  • Error   — lỗi nghiêm trọng
  • Summary — thống kê cuối phiên
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


class Notifier:
    """
    Gửi thông báo Telegram không đồng bộ (non-blocking).
    Mọi message được gửi qua background thread → không delay trading loop.
    """

    def __init__(self):
        self._enabled = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
        if not self._enabled:
            return

        self._token   = TELEGRAM_BOT_TOKEN
        self._chat_id = TELEGRAM_CHAT_ID
        self._url     = f"https://api.telegram.org/bot{self._token}/sendMessage"

    # ── Trade events ──────────────────────────────────────────────────────────

    def entry(
        self,
        symbol: str,
        direction: str,   # 'LONG' | 'SHORT'
        price: float,
        sl: float,
        tp1: float,
        size_usdt: float,
        score: int,
        mode: str = "paper",
    ) -> None:
        arrow = "🟢 LONG" if direction.upper() == "LONG" else "🔴 SHORT"
        sl_pct = abs(price - sl) / price * 100
        tp1_pct = abs(tp1 - price) / price * 100
        mode_tag = "📋 PAPER" if mode == "paper" else "💰 LIVE"

        msg = (
            f"*{mode_tag} — {arrow}*\n"
            f"`{symbol}`\n\n"
            f"📍 Vào:  `${price:,.4f}`\n"
            f"🛑 SL:   `${sl:,.4f}` *(-{sl_pct:.2f}%)*\n"
            f"🎯 TP1:  `${tp1:,.4f}` *(+{tp1_pct:.2f}%)*\n"
            f"💵 Size: `${size_usdt:,.2f}`\n"
            f"📊 Score: `{score:+d}/100`\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def tp_hit(
        self,
        symbol: str,
        tp_level: int,   # 1, 2, 3
        price: float,
        pnl_pct: float,
        pnl_usdt: float,
        remaining_pct: float = 0,
    ) -> None:
        remain_str = f" | Còn: `{remaining_pct:.0f}%`" if remaining_pct > 0 else ""
        msg = (
            f"🎯 *TP{tp_level} HIT* — `{symbol}`\n\n"
            f"💰 Giá:  `${price:,.4f}`\n"
            f"📈 PnL:  `+{pnl_pct:.2f}%` *(+${pnl_usdt:,.2f})*{remain_str}\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def sl_hit(
        self,
        symbol: str,
        price: float,
        pnl_pct: float,
        pnl_usdt: float,
        trailed: bool = False,
    ) -> None:
        trailed_str = " *(trailed)*" if trailed else ""
        msg = (
            f"🔴 *STOP LOSS{trailed_str}* — `{symbol}`\n\n"
            f"💸 Giá:  `${price:,.4f}`\n"
            f"📉 PnL:  `{pnl_pct:.2f}%` *({pnl_usdt:+,.2f} USDT)*\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def trail_update(
        self,
        symbol: str,
        old_sl: float,
        new_sl: float,
        peak: float,
        direction: str,
    ) -> None:
        arrow = "📈" if direction == "long" else "📉"
        msg = (
            f"{arrow} *Trail SL* — `{symbol}`\n"
            f"SL: `${old_sl:,.4f}` → `${new_sl:,.4f}`\n"
            f"Peak: `${peak:,.4f}` | {_ts()}"
        )
        self._send_async(msg)

    def circuit_break(
        self,
        reason: str,
        daily_pnl: float,
        limit: float,
    ) -> None:
        msg = (
            f"🚨 *CIRCUIT BREAKER*\n\n"
            f"⚠️ {reason}\n"
            f"📉 Daily PnL: `${daily_pnl:,.2f}`\n"
            f"🔒 Limit: `${limit:,.2f}`\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def error(self, context: str, detail: str) -> None:
        msg = (
            f"❌ *ERROR — {context}*\n"
            f"`{detail[:200]}`\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def session_summary(
        self,
        total_trades: int,
        wins: int,
        losses: int,
        total_pnl: float,
        mode: str = "paper",
    ) -> None:
        wr = wins / total_trades * 100 if total_trades else 0
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        mode_tag = "📋 PAPER" if mode == "paper" else "💰 LIVE"

        msg = (
            f"📊 *SESSION SUMMARY — {mode_tag}*\n\n"
            f"🔢 Lệnh:     `{total_trades}` ({wins}W / {losses}L)\n"
            f"🎯 Win Rate: `{wr:.1f}%`\n"
            f"{pnl_emoji} PnL:     `{total_pnl:+,.2f} USDT`\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    def agent_start(self, mode: str, watchlist: list[str], timeframe: str) -> None:
        mode_tag = "📋 PAPER" if mode == "paper" else "💰 LIVE"
        msg = (
            f"🤖 *Agent khởi động — {mode_tag}*\n\n"
            f"📋 Watchlist: `{', '.join(watchlist)}`\n"
            f"⏱ TF: `{timeframe}`\n"
            f"⏰ {_ts()}"
        )
        self._send_async(msg)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _send_async(self, text: str) -> None:
        """Gửi message trong background thread để không block trading loop."""
        if not self._enabled:
            return
        t = threading.Thread(target=self._send, args=(text,), daemon=True)
        t.start()

    def _send(self, text: str) -> None:
        """Gọi Telegram Bot API."""
        try:
            import requests
            requests.post(
                self._url,
                json={
                    "chat_id":    self._chat_id,
                    "text":       text,
                    "parse_mode": "Markdown",
                },
                timeout=8,
            )
        except Exception:
            pass  # Lỗi mạng không crash agent


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
