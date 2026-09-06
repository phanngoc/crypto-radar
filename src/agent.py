"""
CryptoAgent v2: Agent giám sát và thực thi giao dịch tự động qua OKX.

Kiến trúc:
  ┌─────────────────────────────────────────────────────────────────┐
  │  WSTicker Thread  — stream giá realtime từ OKX WebSocket        │
  ├─────────────────────────────────────────────────────────────────┤
  │  Main Thread: Market Scanner (SCAN_INTERVAL giây)               │
  │    → Scan từng symbol trong watchlist                           │
  │    → Tính indicators + signal score                             │
  │    → Momentum filter: xác nhận trend đang tăng tốc             │
  │    → Multi-TF confirmation                                      │
  │    → Circuit breaker check                                      │
  │    → Vào lệnh nếu tất cả điều kiện thoả                        │
  ├─────────────────────────────────────────────────────────────────┤
  │  Guardian Thread (GUARDIAN_INTERVAL = 1s)                       │
  │    → Đọc giá từ WSTicker (sub-second)                          │
  │    → Kiểm tra SL ngay khi giá chạm → thoát lỗ ngay             │
  │    → Kiểm tra TP1/TP2/TP3                                      │
  │    → Cập nhật trailing stop                                     │
  └─────────────────────────────────────────────────────────────────┘

Cách dùng:
  python main.py --agent paper     # Paper trade (không cần API)
  python main.py --agent live      # Live trade trên OKX
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .config import (
    AGENT_WATCHLIST,
    AGENT_TIMEFRAME,
    AGENT_CONFIRM_TF,
    AGENT_SCAN_INTERVAL,
    AGENT_GUARDIAN_INTERVAL,
    AGENT_SIGNAL_THRESHOLD,
    AGENT_REQUIRE_CONFIRM,
    AGENT_MAX_POSITIONS,
    AGENT_CAPITAL_PER_TRADE,
    AGENT_RISK_PCT,
    AGENT_COOLDOWN_BARS,
    TIMEFRAME_PROFILES,
    AGENT_MOMENTUM_BARS,
    AGENT_USE_WEBSOCKET,
)
from .okx_fetcher import OKXFetcher
from .indicators import TechnicalAnalyzer
from .signals import SignalEngine, build_signal_result
from .risk import RiskCalculator
from .auto_executor import AutoExecutor
from .position_guardian import PositionGuardian
from .ws_ticker import WSTicker
from .notifier import Notifier
from .circuit_breaker import CircuitBreaker

console = Console()

EXIT_LOG_FILE = Path(__file__).parent.parent / "data" / "agent_exits.json"


class CryptoAgent:
    """
    Agent tự động giám sát thị trường và thực thi giao dịch.

    Điểm nâng cấp so v1:
      • WSTicker: giá realtime, guardian phản ứng < 1s
      • Momentum filter: chỉ vào lệnh khi trend đang tăng tốc
      • CircuitBreaker: ngưỡng thua lỗ ngày / phiên
      • Notifier: Telegram alert mọi sự kiện
      • Đặt TP1/TP2/TP3 đầy đủ lên sàn ngay khi entry
    """

    def __init__(
        self,
        mode: str = "paper",
        watchlist: Optional[list[str]] = None,
        timeframe: str = AGENT_TIMEFRAME,
        confirm_tf: str = AGENT_CONFIRM_TF,
        capital: float = AGENT_CAPITAL_PER_TRADE,
        risk_pct: float = AGENT_RISK_PCT,
        signal_threshold: int = AGENT_SIGNAL_THRESHOLD,
    ):
        self.mode       = mode.lower()
        self.watchlist  = watchlist or AGENT_WATCHLIST
        self.timeframe  = self._resolve_tf(timeframe)
        self.confirm_tf = self._resolve_tf(confirm_tf)
        self.capital    = capital
        self.risk_pct   = risk_pct
        self.threshold  = signal_threshold

        # Shared state
        self.positions: dict[str, dict] = {}
        self.lock       = threading.Lock()

        # Cooldown: symbol → expiry timestamp
        self._cooldown: dict[str, float] = {}
        # Lưu score lịch sử để tính momentum
        self._score_history: dict[str, list[int]] = defaultdict(list)
        # Exit log
        self._exits: list[dict] = []

        EXIT_LOG_FILE.parent.mkdir(exist_ok=True)

        # Components
        self.fetcher  = OKXFetcher(authenticated=(mode == "live"))
        self.executor = AutoExecutor(mode=mode)
        self.notifier = Notifier()
        self.circuit  = CircuitBreaker()

        # WebSocket ticker (realtime price feed)
        self.ws: Optional[WSTicker] = None
        if AGENT_USE_WEBSOCKET:
            self.ws = WSTicker(self.watchlist)

        # Guardian — pass ws + notifier
        self.guardian = PositionGuardian(
            positions=self.positions,
            executor=self.executor,
            fetcher=self.fetcher,
            lock=self.lock,
            ws_ticker=self.ws,
            notifier=self.notifier,
            on_exit_callback=self._on_position_exit,
        )

        self._running    = False
        self._scan_count = 0
        self._start_time: Optional[datetime] = None

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Khởi động agent — blocking."""
        self._running    = True
        self._start_time = datetime.now(timezone.utc)

        # Khôi phục positions
        self._restore_positions()

        # Signal handler
        signal.signal(signal.SIGINT, self._handle_interrupt)

        # WebSocket
        if self.ws:
            self.ws.start()
            console.print("[dim]WSTicker khởi động...[/dim]")

        # Guardian
        self.guardian.start()

        self._print_banner()

        # Notify Telegram
        self.notifier.agent_start(self.mode, self.watchlist, self.timeframe)

        # Main scan loop
        try:
            while self._running:
                self._scan_cycle()
                if self._running:
                    self._sleep_with_countdown(AGENT_SCAN_INTERVAL)
        except Exception as e:
            console.print(f"\n[red]Agent crashed: {e}[/red]")
            self.notifier.error("Agent crash", str(e))
            raise
        finally:
            self._shutdown()

    def stop(self) -> None:
        self._running = False
        self.guardian.stop()
        if self.ws:
            self.ws.stop()

    # ── Scan cycle ────────────────────────────────────────────────────────────

    def _scan_cycle(self) -> None:
        self._scan_count += 1
        ts = _ts()

        # Circuit breaker status
        cb_status = ""
        if self.circuit.is_open():
            cb_status = f"  [red bold]⛔ CIRCUIT OPEN: {self.circuit.trip_reason()}[/red bold]"

        console.rule(
            f"[dim]Scan #{self._scan_count}  {ts}  "
            f"Pos: {len(self.positions)}/{AGENT_MAX_POSITIONS}  "
            f"Mode: [bold]{self.mode.upper()}[/bold][/dim]{cb_status}"
        )

        for symbol in self.watchlist:
            if not self._running:
                break
            try:
                self._evaluate_symbol(symbol)
            except Exception as e:
                console.print(f"  [red dim]✗ {symbol}: {e}[/red dim]")

        self._print_positions_summary()
        self._print_circuit_stats()

    def _evaluate_symbol(self, symbol: str) -> None:
        # Đã có position → skip
        with self.lock:
            if symbol in self.positions:
                return

        # Max position
        if len(self.positions) >= AGENT_MAX_POSITIONS:
            return

        # Cooldown
        if self._in_cooldown(symbol):
            console.print(f"  [dim]{symbol}: cooldown[/dim]")
            return

        # Circuit breaker → không vào lệnh mới
        if self.circuit.is_open():
            return

        # Fetch + tính signal
        df = self.fetcher.fetch_ohlcv(symbol, self.timeframe)
        if df is None or len(df) < 50:
            return

        ticker = self.fetcher.fetch_ticker(symbol)
        price  = ticker["price"]

        ind    = TechnicalAnalyzer(df).compute_all()
        engine = SignalEngine()
        score, details = engine.score(ind)
        result = build_signal_result(score, details)

        # Log
        score_color = "green" if score > 0 else ("red" if score < 0 else "white")
        ws_tag = "⚡" if (self.ws and self.ws.cache_age(symbol) < 5) else "~"
        console.print(
            f"  {ws_tag} {symbol}  ${price:,.4f}  "
            f"[{score_color}]{score:+d}[/{score_color}] {result.label}  "
            f"RSI={ind.rsi:.0f} {ind.trend}"
        )

        # Cập nhật score history cho momentum
        self._score_history[symbol].append(score)
        if len(self._score_history[symbol]) > AGENT_MOMENTUM_BARS:
            self._score_history[symbol].pop(0)

        # Chưa đủ ngưỡng
        if abs(score) < self.threshold:
            return

        # Momentum filter: signal phải đang tăng tốc (không giảm)
        if not self._momentum_ok(symbol, score):
            console.print(
                f"  [dim]{symbol}: score {score:+d} nhưng momentum yếu — skip[/dim]"
            )
            return

        # Multi-TF confirmation
        if AGENT_REQUIRE_CONFIRM and self.confirm_tf != self.timeframe:
            if not self._confirm_tf_agrees(symbol, score):
                console.print(
                    f"  [yellow]  {symbol}: TF xác nhận ({self.confirm_tf}) ngược chiều — skip[/yellow]"
                )
                return

        # Risk calculation
        calc = RiskCalculator()
        plan = calc.calculate(ind, ticker, self.capital, self.risk_pct, score)

        if plan.side == "WAIT" or plan.position_usdt < 1:
            return

        self._enter_position(symbol, plan, score)

    def _momentum_ok(self, symbol: str, current_score: int) -> bool:
        """
        Momentum filter: chỉ vào lệnh nếu signal đang tăng tốc hoặc duy trì.

        Logic:
          - Cần ít nhất 2 điểm lịch sử
          - Nếu LONG (score > 0): score hiện tại >= trung bình lịch sử
          - Nếu SHORT (score < 0): score hiện tại <= trung bình lịch sử
          - Nghĩa là trend đang duy trì hoặc mạnh hơn, không đảo chiều

        Tắt bộ lọc nếu AGENT_MOMENTUM_BARS = 0.
        """
        if AGENT_MOMENTUM_BARS <= 1:
            return True  # Tắt filter

        history = self._score_history[symbol]
        if len(history) < 2:
            return True  # Chưa đủ data → không chặn

        prev_scores = history[:-1]  # Không tính điểm hiện tại
        avg_prev    = sum(prev_scores) / len(prev_scores)

        if current_score > 0:
            # LONG: score không được giảm mạnh so với trước
            return current_score >= avg_prev * 0.7
        else:
            # SHORT: score không được tăng (giảm âm)
            return current_score <= avg_prev * 0.7

    def _confirm_tf_agrees(self, symbol: str, main_score: int) -> bool:
        """TF cao hơn cùng chiều không."""
        try:
            df2 = self.fetcher.fetch_ohlcv(symbol, self.confirm_tf, limit=100)
            if df2 is None or len(df2) < 50:
                return True

            ind2  = TechnicalAnalyzer(df2).compute_all()
            score2, _ = SignalEngine().score(ind2)

            return (main_score > 0 and score2 > 0) or (main_score < 0 and score2 < 0)
        except Exception:
            return True

    def _enter_position(self, symbol: str, plan, score: int) -> None:
        direction = plan.side.lower()
        arrow     = "↑ LONG" if direction == "long" else "↓ SHORT"

        console.print(
            f"\n  [bold]→ SIGNAL [{arrow}] {symbol}[/bold]  "
            f"score=[bold]{score:+d}[/bold]  "
            f"SL=${plan.stop_loss:,.4f}  "
            f"TP1=${plan.take_profits[0]:,.4f}  "
            f"TP3=${plan.take_profits[-1]:,.4f}  "
            f"size=${plan.position_usdt:.2f}\n"
        )

        record = self.executor.enter(symbol, plan, score)

        with self.lock:
            self.positions[symbol] = {
                "symbol":         symbol,
                "direction":      direction,
                "side":           plan.side,
                "entry_price":    plan.entry_price,
                "stop_loss":      plan.stop_loss,
                "tp1":            plan.take_profits[0] if plan.take_profits else None,
                "tp2":            plan.take_profits[1] if len(plan.take_profits) > 1 else None,
                "tp3":            plan.take_profits[2] if len(plan.take_profits) > 2 else None,
                "tp_allocation":  list(plan.tp_allocation),
                "position_usdt":  plan.position_usdt,
                "position_coins": plan.position_coins,
                "capital":        plan.capital,
                "peak_price":     plan.entry_price,
                "sl_moved":       False,
                "tp1_hit":        False,
                "tp2_hit":        False,
                "entered_at":     _now_iso(),
                "score":          score,
                "sl_order_id":    record.get("sl_order_id"),
                "tp1_order_id":   record.get("tp1_order_id"),
                "tp2_order_id":   record.get("tp2_order_id"),
                "tp3_order_id":   record.get("tp3_order_id"),
                "id":             record.get("id"),
            }

        # Telegram entry alert
        self.notifier.entry(
            symbol, plan.side, plan.entry_price,
            plan.stop_loss, plan.take_profits[0],
            plan.position_usdt, score, self.mode,
        )

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_position_exit(
        self, symbol: str, reason: str, pnl_pct: float, pnl_usdt: float
    ) -> None:
        """Được gọi bởi PositionGuardian sau mỗi lần thoát lệnh."""
        # Ghi exit log
        self._exits.append({
            "symbol":   symbol,
            "reason":   reason,
            "pnl_pct":  pnl_pct,
            "pnl_usdt": pnl_usdt,
            "time":     _now_iso(),
        })
        self._save_exits()

        # Circuit breaker: ghi nhận kết quả
        self.circuit.record_trade(pnl_usdt=pnl_usdt, reason=reason)

        # Notify Telegram nếu circuit vừa trip
        if self.circuit.is_open():
            stats = self.circuit.stats()
            self.notifier.circuit_break(
                self.circuit.trip_reason(),
                stats["daily_pnl"],
                stats["daily_limit"],
            )

        # Cooldown
        bar_seconds  = self._tf_to_seconds(self.timeframe)
        cooldown_sec = AGENT_COOLDOWN_BARS * bar_seconds
        self._cooldown[symbol] = time.time() + cooldown_sec

        # Reset momentum history
        self._score_history.pop(symbol, None)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _restore_positions(self) -> None:
        open_pos = self.executor.load_open_positions()
        if open_pos:
            with self.lock:
                self.positions.update(open_pos)
            console.print(
                f"[yellow]Đã khôi phục {len(open_pos)} position: "
                f"{list(open_pos.keys())}[/yellow]"
            )

    def _in_cooldown(self, symbol: str) -> bool:
        return time.time() < self._cooldown.get(symbol, 0)

    def _print_banner(self) -> None:
        wl_str  = "  ".join(self.watchlist)
        ws_info = "OKX WebSocket ⚡" if (self.ws and self.ws.is_ready()) else "REST polling"
        console.print(
            Panel(
                f"[bold cyan]🤖 CRYPTO AGENT v2 — {self.mode.upper()}[/bold cyan]\n\n"
                f"  Exchange  : [bold]OKX[/bold]\n"
                f"  Mode      : [bold {'green' if self.mode == 'paper' else 'red'}]{self.mode.upper()}[/bold {'green' if self.mode == 'paper' else 'red'}]\n"
                f"  Giá       : [bold]{ws_info}[/bold]\n"
                f"  TF Scan   : [bold]{self.timeframe}[/bold]  (confirm: {self.confirm_tf})\n"
                f"  Watchlist : [cyan]{wl_str}[/cyan]\n"
                f"  Capital   : ${self.capital:.2f}/lệnh  |  Risk: {self.risk_pct*100:.1f}%\n"
                f"  Threshold : ±{self.threshold}/100  |  Max: {AGENT_MAX_POSITIONS} lệnh\n"
                f"  Scan mỗi  : {AGENT_SCAN_INTERVAL}s  |  Guardian: {AGENT_GUARDIAN_INTERVAL}s\n"
                f"  Circuit   : Daily -${abs(self.circuit.stats()['daily_limit']):.0f} / "
                f"Session -${abs(self.circuit.stats()['session_limit']):.0f}\n\n"
                f"  [dim]Ctrl+C để dừng[/dim]",
                title="[bold]CryptoRadar Auto Agent v2[/bold]",
                border_style="cyan",
                padding=(0, 2),
            )
        )

    def _print_positions_summary(self) -> None:
        with self.lock:
            pos_copy = dict(self.positions)

        if not pos_copy:
            return

        tbl = Table(box=box.SIMPLE, show_header=True, pad_edge=False)
        tbl.add_column("Symbol",  style="cyan", width=12)
        tbl.add_column("Side",    width=7)
        tbl.add_column("Entry",   width=14)
        tbl.add_column("SL",      width=14)
        tbl.add_column("TP1",     width=14)
        tbl.add_column("TP2",     width=14)
        tbl.add_column("Flags",   width=12)

        for sym, p in pos_copy.items():
            sc = "green" if p["direction"] == "long" else "red"
            flags = ""
            if p.get("sl_moved"):   flags += "T"   # trailing
            if p.get("tp1_hit"):    flags += "1"
            if p.get("tp2_hit"):    flags += "2"
            tbl.add_row(
                sym,
                f"[{sc}]{p['direction'].upper()}[/{sc}]",
                f"${p['entry_price']:,.4f}",
                f"[red]${p['stop_loss']:,.4f}[/red]",
                f"[green]${p['tp1']:,.4f}[/green]" if p.get("tp1") else "—",
                f"[green]${p['tp2']:,.4f}[/green]" if p.get("tp2") else "—",
                flags or "—",
            )
        console.print(tbl)

    def _print_circuit_stats(self) -> None:
        stats = self.circuit.stats()
        if stats["total_trades"] == 0:
            return
        color = "green" if stats["daily_pnl"] >= 0 else "red"
        console.print(
            f"  [dim]Circuit — Daily PnL: [{color}]${stats['daily_pnl']:+.2f}[/{color}]  "
            f"Session: ${stats['session_pnl']:+.2f}  "
            f"WR: {stats['win_rate']:.0f}%  "
            f"Consec losses: {stats['consec_losses']}[/dim]"
        )

    def _print_final_stats(self) -> None:
        if not self._exits:
            return
        wins      = [e for e in self._exits if e["pnl_pct"] >= 0]
        losses    = [e for e in self._exits if e["pnl_pct"] < 0]
        total_pnl = sum(e["pnl_usdt"] for e in self._exits)
        wr = len(wins) / len(self._exits) * 100 if self._exits else 0

        console.print(
            Panel(
                f"  Tổng lệnh : {len(self._exits)}\n"
                f"  Win Rate  : {wr:.1f}%  ({len(wins)}W / {len(losses)}L)\n"
                f"  PnL USDT  : [{'green' if total_pnl >= 0 else 'red'}]{total_pnl:+.2f}[/]\n"
                f"  SL hits   : {sum(1 for e in self._exits if e['reason']=='STOP_LOSS')}\n"
                f"  TP hits   : {sum(1 for e in self._exits if 'TP' in e['reason'])}",
                title="Session Stats",
                border_style="yellow",
            )
        )

        # Telegram summary
        self.notifier.session_summary(
            len(self._exits), len(wins), len(losses), total_pnl, self.mode
        )

    def _sleep_with_countdown(self, seconds: int) -> None:
        for remaining in range(seconds, 0, -5):
            if not self._running:
                return
            console.print(
                f"[dim]  Scan tiếp theo trong {remaining}s...[/dim]",
                end="\r",
            )
            time.sleep(min(5, remaining))
        console.print(" " * 40, end="\r")

    def _shutdown(self) -> None:
        self.guardian.stop()
        if self.ws:
            self.ws.stop()
        console.print("\n[yellow]Agent đã dừng.[/yellow]")
        self._print_final_stats()
        # Paper mode: hiển thị slippage report
        if self.mode == "paper":
            self.executor.print_paper_stats()

    def _handle_interrupt(self, sig, frame) -> None:
        console.print("\n[yellow]Đang dừng agent...[/yellow]")
        self._running = False

    def _save_exits(self) -> None:
        try:
            EXIT_LOG_FILE.write_text(
                json.dumps(self._exits, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    @staticmethod
    def _resolve_tf(tf: str) -> str:
        profile = TIMEFRAME_PROFILES.get(tf.lower())
        return profile[0] if profile else tf

    @staticmethod
    def _tf_to_seconds(tf: str) -> int:
        mapping = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600,
            "1d": 86400, "1w": 604800,
        }
        return mapping.get(tf, 900)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
