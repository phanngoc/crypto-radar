"""
Reporter: Hiển thị kết quả phân tích dạng bảng đẹp bằng Rich.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.rule import Rule
from rich import box

from .indicators import IndicatorSet
from .signals import SignalDetail, build_signal_result
from .risk import TradePlan
from typing import Dict


class Reporter:

    def __init__(self, console: Console):
        self.c = console

    # ── Entry point ───────────────────────────────────────────────────────────

    def display_full_report(
        self,
        asset: str,
        interval: str,
        tf_desc: str,
        ticker: dict,
        indicators: IndicatorSet,
        score: int,
        signals: Dict[str, SignalDetail],
        trade_plan: TradePlan,
        fear_greed: dict,
        capital: float,
    ) -> None:
        sig = build_signal_result(score, signals)
        self.c.print()

        # ── Header ─────────────────────────────────────────────────────────────
        self._print_header(asset, interval, tf_desc, ticker, fear_greed)

        # ── Signal summary ─────────────────────────────────────────────────────
        self._print_signal_summary(score, sig, indicators)

        # ── Technical indicators breakdown ─────────────────────────────────────
        self._print_indicator_table(signals)

        # ── Trade plan ─────────────────────────────────────────────────────────
        if trade_plan.side != "WAIT":
            self._print_trade_plan(trade_plan, asset, ticker["price"])
        else:
            self._print_wait_message(score, indicators)

        # ── Footer ─────────────────────────────────────────────────────────────
        self.c.print()
        self.c.print(
            "[dim]⚠  Chỉ mang tính tham khảo. Luôn quản lý rủi ro trước khi giao dịch.[/dim]",
            justify="center",
        )

    # ── Sections ──────────────────────────────────────────────────────────────

    def _print_header(
        self,
        asset: str,
        interval: str,
        tf_desc: str,
        ticker: dict,
        fear_greed: dict,
    ) -> None:
        price     = ticker["price"]
        change    = ticker["change_pct"]
        fg_val    = fear_greed["value"]
        fg_label  = fear_greed["classification"]
        fg_color  = self._fg_color(fg_val)

        change_str = (
            f"[green]+{change:.2f}%[/green]"
            if change >= 0
            else f"[red]{change:.2f}%[/red]"
        )

        header_text = (
            f"[bold cyan]{asset}/USDT[/bold cyan]  "
            f"[bold white]${price:,.4f}[/bold white]  {change_str}  "
            f"[dim]|  Khung: {tf_desc}  |  "
            f"Fear & Greed: [{fg_color}]{fg_val} {fg_label}[/{fg_color}][/dim]"
        )
        self.c.print(Panel(header_text, border_style="cyan", padding=(0, 1)))

    def _print_signal_summary(
        self,
        score: int,
        sig,
        ind: IndicatorSet,
    ) -> None:
        bar   = self._score_bar(score)
        trend_icons = {
            "UPTREND":   "[green]↑ UPTREND[/green]",
            "DOWNTREND": "[red]↓ DOWNTREND[/red]",
            "NEUTRAL":   "[yellow]→ NEUTRAL[/yellow]",
        }
        trend_str = trend_icons.get(ind.trend, ind.trend)

        self.c.print(
            Panel(
                f"  {sig.emoji}  [{sig.color}]{sig.label}[/{sig.color}]"
                f"   Điểm: [{sig.color}]{score:+d}[/{sig.color}]/100"
                f"   Độ tin cậy: [bold]{sig.confidence}%[/bold]"
                f"   Xu hướng: {trend_str}\n"
                f"  {bar}",
                title="[bold]TÍN HIỆU TỔNG HỢP[/bold]",
                border_style=sig.color.replace("bold ", ""),
                padding=(0, 1),
            )
        )

    def _print_indicator_table(self, signals: Dict[str, SignalDetail]) -> None:
        tbl = Table(
            title="Phân tích chỉ báo kỹ thuật",
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style="bold cyan",
            min_width=60,
        )
        tbl.add_column("Chỉ báo", style="bold", width=10)
        tbl.add_column("Tín hiệu",  width=14)
        tbl.add_column("Sức mạnh",  width=10)
        tbl.add_column("Giá trị",   width=24)
        tbl.add_column("Điểm",   justify="right", width=8)

        dir_color = {
            "MUA": "green",
            "BÁN": "red",
            "TRUNG LẬP": "white",
        }
        str_style = {
            "MẠNH": "bold",
            "VỪA":  None,
            "YẾU":  "dim",
            "—":    "dim",
        }

        for key, det in signals.items():
            col   = dir_color.get(det.direction, "white")
            style = str_style.get(det.strength)
            sc_str = (
                f"[green]+{det.score}[/green]"
                if det.score > 0
                else (f"[red]{det.score}[/red]" if det.score < 0 else "[dim]0[/dim]")
            )
            strength_str = (
                f"[{style}]{det.strength}[/{style}]" if style else det.strength
            )
            tbl.add_row(
                det.name or key,
                f"[{col}]{det.direction}[/{col}]",
                strength_str,
                det.value,
                sc_str,
            )
        self.c.print(tbl)

    def _print_trade_plan(self, plan: TradePlan, asset: str, price: float) -> None:
        is_long = plan.side == "LONG"
        side_color = "green" if is_long else "red"
        side_icon  = "↑ LONG" if is_long else "↓ SHORT"

        # ── Entry zone ─────────────────────────────────────────────────────────
        entry_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        entry_table.add_column("key",   style="dim",        width=20)
        entry_table.add_column("value", style="bold white", width=22)
        entry_table.add_row("Vị thế",    f"[{side_color}]{side_icon}[/{side_color}]")
        entry_table.add_row("Vùng vào",
            f"[cyan]${plan.entry_low:,.4f}[/cyan] – [cyan]${plan.entry_high:,.4f}[/cyan]")
        entry_table.add_row("Giá vào đề xuất",  f"[bold cyan]${plan.entry_price:,.4f}[/bold cyan]")

        # ── TP / SL ────────────────────────────────────────────────────────────
        exit_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        exit_table.add_column("key",   style="dim",  width=10)
        exit_table.add_column("price", style="bold", width=16)
        exit_table.add_column("pct",   style="bold", width=10)
        exit_table.add_column("alloc", style="dim",  width=10)

        if plan.take_profits:
            alloc_pct = [int(a * 100) for a in plan.tp_allocation]
            labels    = ["TP1", "TP2", "TP3"]
            rewards   = [plan.reward_tp1, plan.reward_tp2, plan.reward_tp3]
            for i, (tp, rwd, alc) in enumerate(
                zip(plan.take_profits, rewards, alloc_pct)
            ):
                exit_table.add_row(
                    labels[i],
                    f"[green]${tp:,.4f}[/green]",
                    f"[green]+{rwd:.2f}%[/green]",
                    f"({alc}% vốn)",
                )
        sl_pct = plan.risk_pct_price
        exit_table.add_row(
            "STOP LOSS",
            f"[red]${plan.stop_loss:,.4f}[/red]",
            f"[red]-{sl_pct:.2f}%[/red]",
            "(cắt lỗ)",
        )

        # ── Position sizing ────────────────────────────────────────────────────
        size_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        size_table.add_column("key",   style="dim",        width=22)
        size_table.add_column("value", style="bold white", width=20)
        size_table.add_row("Vốn đầu tư",      f"${plan.capital:,.2f} USDT")
        size_table.add_row("Rủi ro tối đa",   f"[red]${plan.max_loss_usdt:,.2f} USDT[/red]")
        size_table.add_row("Kích thước lệnh",
            f"[bold]${plan.position_usdt:,.2f}[/bold] ({plan.position_coins:.6g} {asset})")
        size_table.add_row("Risk/Reward",      f"[bold]1 : {plan.rr_ratio:.2f}[/bold]")

        # ── Print panels ────────────────────────────────────────────────────────
        self.c.print(Panel(entry_table, title="[bold]VÀO LỆNH[/bold]", border_style="cyan", padding=(0,1)))
        self.c.print(Panel(exit_table,  title="[bold]CHỐT LỜI / CẮT LỖ[/bold]", border_style="yellow", padding=(0,1)))
        self.c.print(Panel(size_table,  title="[bold]QUẢN LÝ VỐN[/bold]", border_style="blue", padding=(0,1)))

    def _print_wait_message(self, score: int, ind: IndicatorSet) -> None:
        msg = (
            "Tín hiệu chưa đủ rõ ràng để vào lệnh.\n"
            f"  Điểm hiện tại: [bold]{score:+d}[/bold] (cần ≥ +25 để MUA hoặc ≤ -25 để BÁN)\n"
            f"  RSI: {ind.rsi:.1f}  |  Xu hướng: {ind.trend}\n"
            "  → Chờ breakout hoặc tín hiệu xác nhận thêm."
        )
        self.c.print(Panel(msg, title="[bold yellow]KHUYẾN NGHỊ: CHỜ[/bold yellow]",
                           border_style="yellow", padding=(0, 1)))

    # ── Utils ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _score_bar(score: int) -> str:
        """Tạo thanh tiến trình trực quan -100 → +100."""
        filled = abs(score) // 5   # 0-20 ký tự
        empty  = 20 - filled
        if score >= 0:
            bar = "[dim]" + "·" * 20 + "[/dim][green]" + "█" * filled + "[/green]"
        else:
            bar = "[red]" + "█" * filled + "[/red][dim]" + "·" * 20 + "[/dim]"

        label_left  = f"[red]-100[/red]"
        label_right = f"[green]+100[/green]"
        center_mark = "[dim]0[/dim]"
        return f"  {label_left}  {bar}  {label_right}   ({score:+d})"

    @staticmethod
    def _fg_color(value: int) -> str:
        if value < 25:
            return "red"
        if value < 45:
            return "orange3"
        if value < 55:
            return "white"
        if value < 75:
            return "green"
        return "bold green"
