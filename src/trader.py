"""
Trader: Thực thi giao dịch.

Chế độ:
  paper — Mô phỏng, lưu vào JSON, không cần API key.
  live  — Kết nối Binance qua ccxt (cần API key trong .env).

Mặc định: paper mode để an toàn.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .config import TRADING_MODE, EXCHANGE_ID, API_KEY, API_SECRET
from .risk import TradePlan

PAPER_DB = Path(__file__).parent.parent / "data" / "paper_trades.json"


class Trader:
    """Thực thi lệnh paper hoặc live."""

    def __init__(self):
        self.console = Console()
        self.mode    = TRADING_MODE.lower()
        PAPER_DB.parent.mkdir(exist_ok=True)

    # ── Public ────────────────────────────────────────────────────────────────

    def execute(self, asset: str, plan: TradePlan, score: int) -> None:
        if plan.side == "WAIT":
            self.console.print("[yellow]Không vào lệnh — tín hiệu WAIT.[/yellow]")
            return

        if self.mode == "live":
            self._execute_live(asset, plan, score)
        else:
            self._execute_paper(asset, plan, score)

    def show_open_positions(self) -> None:
        """Hiển thị danh sách lệnh paper đang mở."""
        trades = self._load_paper_db()
        open_  = [t for t in trades if t["status"] == "open"]
        if not open_:
            self.console.print("[dim]Không có lệnh nào đang mở.[/dim]")
            return

        from rich.table import Table
        from rich import box
        tbl = Table(title="Lệnh đang mở (Paper)", box=box.SIMPLE_HEAVY)
        tbl.add_column("ID",      width=6)
        tbl.add_column("Asset",   width=8)
        tbl.add_column("Side",    width=7)
        tbl.add_column("Entry",   width=14)
        tbl.add_column("SL",      width=14)
        tbl.add_column("TP1",     width=14)
        tbl.add_column("Size $",  width=12)
        tbl.add_column("Thời gian", width=20)

        for t in open_:
            side_col = "green" if t["side"] == "LONG" else "red"
            tbl.add_row(
                str(t["id"]),
                t["asset"],
                f"[{side_col}]{t['side']}[/{side_col}]",
                f"${t['entry']:,.4f}",
                f"${t['stop_loss']:,.4f}",
                f"${t['tp1']:,.4f}" if t.get("tp1") else "—",
                f"${t['size_usdt']:,.2f}",
                t["time"],
            )
        self.console.print(tbl)

    # ── Paper ─────────────────────────────────────────────────────────────────

    def _execute_paper(self, asset: str, plan: TradePlan, score: int) -> None:
        trades = self._load_paper_db()
        trade_id = len(trades) + 1

        record = {
            "id":         trade_id,
            "asset":      asset,
            "side":       plan.side,
            "status":     "open",
            "score":      score,
            "entry":      plan.entry_price,
            "stop_loss":  plan.stop_loss,
            "tp1":        plan.take_profits[0] if plan.take_profits else None,
            "tp2":        plan.take_profits[1] if len(plan.take_profits) > 1 else None,
            "tp3":        plan.take_profits[2] if len(plan.take_profits) > 2 else None,
            "tp_alloc":   plan.tp_allocation,
            "size_usdt":  plan.position_usdt,
            "size_coins": plan.position_coins,
            "capital":    plan.capital,
            "risk_usdt":  plan.max_loss_usdt,
            "rr_ratio":   plan.rr_ratio,
            "time":       datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "mode":       "paper",
        }

        trades.append(record)
        self._save_paper_db(trades)

        self.console.print(
            Panel(
                f"[bold green]✅ LỆNH PAPER #{trade_id} ĐÃ GHI NHẬN[/bold green]\n\n"
                f"  Asset    : [bold]{asset}[/bold]\n"
                f"  Vị thế   : {'[green]LONG ↑[/green]' if plan.side=='LONG' else '[red]SHORT ↓[/red]'}\n"
                f"  Entry    : [cyan]${plan.entry_price:,.4f}[/cyan]\n"
                f"  SL       : [red]${plan.stop_loss:,.4f}[/red]\n"
                f"  TP1/2/3  : " + (
                    f"[green]${plan.take_profits[0]:,.4f}[/green] / "
                    f"${plan.take_profits[1]:,.4f} / ${plan.take_profits[2]:,.4f}"
                    if plan.take_profits else "—"
                ) + f"\n"
                f"  Kích thước: [bold]${plan.position_usdt:,.2f}[/bold]\n"
                f"  Lưu tại  : [dim]{PAPER_DB}[/dim]",
                title="[bold]PAPER TRADING[/bold]",
                border_style="green",
                padding=(0, 1),
            )
        )

    # ── Live (Binance via ccxt) ───────────────────────────────────────────────

    def _execute_live(self, asset: str, plan: TradePlan, score: int) -> None:
        if not API_KEY or not API_SECRET:
            self.console.print(
                "[red]❌ Chưa có API key. Thêm BINANCE_API_KEY và BINANCE_API_SECRET vào .env[/red]"
            )
            return

        self.console.print(
            Panel(
                f"[bold yellow]⚠  LIVE TRADING — XÁC NHẬN TRƯỚC KHI TIẾP TỤC[/bold yellow]\n\n"
                f"  Sẽ đặt lệnh [bold]THẬT[/bold] trên Binance:\n"
                f"  {asset}/USDT  |  {plan.side}  |  ${plan.position_usdt:,.2f}\n"
                f"  Entry: ${plan.entry_price:,.4f}  |  SL: ${plan.stop_loss:,.4f}",
                border_style="red",
            )
        )

        if not Confirm.ask("[red bold]Xác nhận đặt lệnh THẬT?[/red bold]", default=False):
            self.console.print("[dim]Đã huỷ.[/dim]")
            return

        try:
            import ccxt
            exchange = getattr(ccxt, EXCHANGE_ID)({
                "apiKey":    API_KEY,
                "secret":    API_SECRET,
                "enableRateLimit": True,
            })

            symbol    = f"{asset}/USDT"
            side_ccxt = "buy" if plan.side == "LONG" else "sell"
            amount    = plan.position_coins

            # Market order cho entry
            order = exchange.create_market_order(symbol, side_ccxt, amount)

            # OCO: TP1 + SL (Binance supports oco_order)
            if plan.take_profits:
                tp_price = plan.take_profits[0]
                sl_price = plan.stop_loss
                oco_side = "sell" if plan.side == "LONG" else "buy"
                try:
                    exchange.create_order(
                        symbol,
                        "oco",
                        oco_side,
                        amount * plan.tp_allocation[0],
                        tp_price,
                        {"stopPrice": sl_price, "stopLimitPrice": sl_price * 0.999},
                    )
                except Exception as oco_err:
                    self.console.print(f"[yellow]OCO không hỗ trợ: {oco_err}. Đặt SL thủ công.[/yellow]")

            self.console.print(
                f"[green]✅ Lệnh #{order['id']} đã được đặt thành công![/green]\n"
                f"  Giá thực hiện: ${float(order.get('price', plan.entry_price)):,.4f}"
            )

        except ImportError:
            self.console.print(
                "[red]Thiếu thư viện ccxt. Chạy: pip install ccxt[/red]"
            )
        except Exception as e:
            self.console.print(f"[red]Lỗi khi đặt lệnh: {e}[/red]")

    # ── Paper DB helpers ──────────────────────────────────────────────────────

    def _load_paper_db(self) -> list:
        if PAPER_DB.exists():
            try:
                return json.loads(PAPER_DB.read_text())
            except json.JSONDecodeError:
                return []
        return []

    def _save_paper_db(self, trades: list) -> None:
        PAPER_DB.write_text(json.dumps(trades, indent=2, ensure_ascii=False))
