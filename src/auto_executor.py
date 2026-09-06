"""
AutoExecutor: Thực thi lệnh paper hoặc live trên OKX.

Paper mode (v2 — realistic):
  - Slippage simulation (VolumeShareSlippageModel)
  - Partial fill simulation (~40% limit orders)
  - Latency simulation (50-200ms)
  - SQLite persistence thay JSON
  - PaperEngine ghi log mọi fill event

Live mode (OKX):
  - Đặt market order entry
  - Đặt server-side algo SL (tồn tại ngay cả khi agent crash)
  - Đặt limit TP1/TP2/TP3 đầy đủ lên sàn
  - Guardian theo dõi SL trailing và fill events
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from rich.console import Console

from .config import (
    OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE,
    OKX_SANDBOX, OKX_MARKET_TYPE,
    TP_ALLOCATION,
)
from .risk import TradePlan
from .paper_engine import PaperEngine

console = Console()

AGENT_DB    = Path(__file__).parent.parent / "data" / "agent_trades.json"
AGENT_STATE = Path(__file__).parent.parent / "data" / "agent_state.json"


class AutoExecutor:
    """Thực thi entry / exit / cancel cho agent."""

    def __init__(self, mode: str = "paper"):
        self.mode = mode.lower()
        AGENT_DB.parent.mkdir(exist_ok=True)

        self._exchange = None
        if self.mode == "live":
            self._exchange = self._init_exchange()

        # Paper engine với realistic simulation
        self._paper = PaperEngine() if self.mode == "paper" else None

    # ── Public API ────────────────────────────────────────────────────────────

    def enter(self, symbol: str, plan: TradePlan, score: int) -> dict:
        """Vào lệnh. Trả về dict mô tả vị thế đã mở."""
        if self.mode == "live":
            return self._live_enter(symbol, plan, score)
        return self._paper_enter(symbol, plan, score)

    def exit(
        self,
        symbol: str,
        direction: str,
        amount_coins: float,
        reason: str = "manual",
        current_price: Optional[float] = None,
    ) -> dict:
        """Thoát lệnh. Trả về dict kết quả."""
        if self.mode == "live":
            return self._live_exit(symbol, direction, amount_coins, reason)
        return self._paper_exit(symbol, direction, amount_coins, reason, current_price)

    def cancel_symbol_orders(self, symbol: str) -> None:
        """Hủy tất cả lệnh pending của symbol (trước khi thoát)."""
        if self.mode != "live" or not self._exchange:
            return
        try:
            orders = self._exchange.fetch_open_orders(symbol)
            for o in orders:
                try:
                    self._exchange.cancel_order(o["id"], symbol)
                except Exception:
                    pass
        except Exception as e:
            console.print(f"[yellow]Không hủy được lệnh {symbol}: {e}[/yellow]")

    def cancel_order_id(self, symbol: str, order_id: Optional[str]) -> bool:
        """Hủy một lệnh cụ thể theo ID. Trả về True nếu thành công."""
        if self.mode != "live" or not self._exchange or not order_id:
            return False
        try:
            self._exchange.cancel_order(order_id, symbol)
            return True
        except Exception:
            return False

    def update_sl_order(
        self,
        symbol: str,
        old_sl_id: Optional[str],
        new_sl_price: float,
        amount: float,
        direction: str,
    ) -> Optional[str]:
        """
        Cập nhật SL order trên sàn:
          1. Hủy SL cũ
          2. Đặt SL mới tại new_sl_price
        Trả về order_id mới, hoặc None nếu thất bại.
        """
        if self.mode != "live" or not self._exchange:
            return None

        ex = self._exchange
        opp_side = "sell" if direction == "long" else "buy"

        # Hủy SL cũ
        if old_sl_id:
            try:
                ex.cancel_order(old_sl_id, symbol)
            except Exception:
                pass

        # Đặt SL mới
        try:
            sl_order = ex.create_order(
                symbol, "stop_market", opp_side,
                amount, None,
                {"stopPrice": new_sl_price, "reduceOnly": True},
            )
            return sl_order.get("id")
        except Exception as e:
            console.print(f"[yellow]Update SL thất bại {symbol}: {e}[/yellow]")
            return None

    # ── Paper (Realistic v2) ──────────────────────────────────────────────────

    def _paper_enter(self, symbol: str, plan: TradePlan, score: int) -> dict:
        """
        Paper entry với realistic slippage + partial fill simulation.
        Slippage được tính từ VolumeShareSlippageModel.
        Entry price thực tế = mid_price + slippage → ghi vào record.
        """
        pe       = self._paper
        trade_id = f"P{int(datetime.now().timestamp())}"

        side_str    = "buy" if plan.side == "LONG" else "sell"
        mid_price   = plan.entry_price
        qty         = plan.position_coins or (plan.position_usdt / mid_price)

        # Ước tính bar_volume cho slippage calculation.
        # Typical 15m bar volume:
        #   BTC/USDT : ~$10M   → ~140 BTC @ $70k
        #   ETH/USDT : ~$5M    → ~2000 ETH
        #   SOL/USDT : ~$2M    → ~24k SOL
        # Conservative minimum: $2M USD per bar
        min_bar_vol_usd = 2_000_000
        est_bar_vol = max(qty * 300, min_bar_vol_usd / max(mid_price, 0.001))

        fill = pe.simulate_market_entry(
            symbol=symbol,
            side=side_str,
            quantity=qty,
            mid_price=mid_price,
            bar_volume=est_bar_vol,
            volatility=0.02,
            score=score,
            plan=plan,
        )

        actual_price  = fill.fill_price
        actual_qty    = fill.filled_qty
        slippage_bps  = fill.slippage_bps
        actual_usdt   = actual_price * actual_qty

        record = {
            "id":                  trade_id,
            "symbol":              symbol,
            "side":                plan.side,
            "direction":           plan.side.lower(),
            "status":              "open",
            "score":               score,
            "entry_price":         actual_price,    # fill với slippage
            "entry_price_ideal":   mid_price,        # mid price không slippage
            "slippage_entry_bps":  slippage_bps,
            "stop_loss":           plan.stop_loss,
            "tp1":                 plan.take_profits[0] if plan.take_profits else None,
            "tp2":                 plan.take_profits[1] if len(plan.take_profits) > 1 else None,
            "tp3":                 plan.take_profits[2] if len(plan.take_profits) > 2 else None,
            "tp_allocation":       list(plan.tp_allocation),
            "position_usdt":       actual_usdt,
            "position_coins":      actual_qty,
            "capital":             plan.capital,
            "max_loss_usdt":       plan.max_loss_usdt,
            "rr_ratio":            plan.rr_ratio,
            "peak_price":          actual_price,
            "sl_moved":            False,
            "tp1_hit":             False,
            "tp2_hit":             False,
            "entered_at":          _now_iso(),
            "exited_at":           None,
            "exit_price":          None,
            "exit_reason":         None,
            "pnl_pct":             None,
            "pnl_usdt":            None,
            "mode":                "paper",
            "sl_order_id":         None,
            "tp1_order_id":        None,
            "tp2_order_id":        None,
            "tp3_order_id":        None,
        }

        trades = self._load_db()
        trades.append(record)
        self._save_db(trades)

        # Log vào SQLite
        pe.log_trade_event(
            event_type="entry", trade_id=trade_id, symbol=symbol,
            direction=plan.side.lower(), price=actual_price,
            quantity=actual_qty, slippage_bps=slippage_bps,
        )

        # Hiển thị với slippage info
        slippage_color = "green" if slippage_bps < 3 else "yellow"
        console.print(
            f"[bold green]✅ PAPER ENTRY #{trade_id}[/bold green]  "
            f"{plan.side} [cyan]{symbol}[/cyan] @ "
            f"[white]${actual_price:,.4f}[/white]  "
            f"[{slippage_color}](slip: {slippage_bps:.2f}bps)[/{slippage_color}]  "
            f"SL:[red]${plan.stop_loss:,.4f}[/red]  "
            f"TP1:[green]${plan.take_profits[0]:,.4f}[/green]  "
            f"Size:[yellow]${actual_usdt:,.2f}[/yellow]"
        )
        return record

    def _paper_exit(
        self,
        symbol: str,
        direction: str,
        amount_coins: float,
        reason: str,
        current_price: Optional[float],
    ) -> dict:
        """
        Paper exit với realistic slippage.
        SL exits có slippage xấu hơn (volatile market condition).
        """
        pe     = self._paper
        trades = self._load_db()
        result = {}

        for t in trades:
            if t["symbol"] == symbol and t["status"] == "open":
                mid_price = current_price or t["entry_price"]
                qty       = amount_coins or t["position_coins"]

                # Simulate exit fill với slippage
                fill = pe.simulate_market_exit(
                    symbol=symbol,
                    direction=direction,
                    quantity=qty,
                    current_price=mid_price,
                    bar_volume=qty * 50,
                    volatility=0.025,
                    reason=reason,
                )

                exit_price   = fill.fill_price
                slippage_bps = fill.slippage_bps

                # PnL thực tế: tính từ actual entry và actual exit (cả 2 có slippage)
                entry_price = t.get("entry_price", t.get("entry_price_ideal", mid_price))
                pnl_data    = pe.calculate_pnl(
                    direction=direction,
                    entry_fill=entry_price,
                    exit_fill=exit_price,
                    quantity=qty,
                    position_usdt=t["position_usdt"],
                )

                t.update({
                    "status":              "closed",
                    "exit_price":          exit_price,
                    "exit_reason":         reason,
                    "pnl_pct":             pnl_data["pnl_pct"],
                    "pnl_usdt":            pnl_data["pnl_usdt"],
                    "slippage_exit_bps":   slippage_bps,
                    "exited_at":           _now_iso(),
                })
                result = t

                # Log vào SQLite
                pe.log_trade_event(
                    event_type="exit", trade_id=t["id"], symbol=symbol,
                    direction=direction, price=exit_price,
                    quantity=qty,
                    pnl_pct=pnl_data["pnl_pct"],
                    pnl_usdt=pnl_data["pnl_usdt"],
                    slippage_bps=slippage_bps,
                    reason=reason,
                )
                break

        self._save_db(trades)
        return result

    def print_paper_stats(self) -> None:
        """Hiển thị slippage stats từ paper engine."""
        if self._paper:
            self._paper.print_slippage_report()

    # ── Live (OKX) ────────────────────────────────────────────────────────────

    def _live_enter(self, symbol: str, plan: TradePlan, score: int) -> dict:
        ex        = self._exchange
        side_ccxt = "buy" if plan.side == "LONG" else "sell"
        opp_side  = "sell" if plan.side == "LONG" else "buy"

        # 1. Lấy giá thực tế ngay trước khi đặt lệnh
        ticker = ex.fetch_ticker(symbol)
        price  = float(ticker["last"])
        amount = plan.position_usdt / price

        console.print(
            f"[bold yellow]→ OKX ENTER {plan.side} {symbol} "
            f"${plan.position_usdt:.2f} (~{amount:.6f} coins)[/bold yellow]"
        )

        # 2. Market entry
        entry_order  = ex.create_market_order(symbol, side_ccxt, amount)
        actual_price = float(entry_order.get("average") or entry_order.get("price") or price)
        filled_amt   = float(entry_order.get("filled") or amount)

        # 3. Server-side SL algo order (sống sót khi agent crash)
        sl_order_id = None
        try:
            sl_params = {"stopPrice": plan.stop_loss, "reduceOnly": True}
            if OKX_MARKET_TYPE == "swap":
                sl_params["tdMode"] = "isolated"
            sl_order    = ex.create_order(
                symbol, "stop_market", opp_side,
                filled_amt, None, sl_params,
            )
            sl_order_id = sl_order.get("id")
            console.print(f"  [green]✓ SL  #{sl_order_id} @ ${plan.stop_loss:,.4f}[/green]")
        except Exception as e:
            console.print(f"  [yellow]⚠ SL order thất bại ({e}) — guardian theo dõi thủ công[/yellow]")

        tps = plan.take_profits
        allocs = plan.tp_allocation
        tp_ids: dict[str, Optional[str]] = {"tp1": None, "tp2": None, "tp3": None}

        # 4. TP1, TP2, TP3 — đặt tất cả lên sàn
        tp_labels = ["tp1", "tp2", "tp3"]
        for i, (tp_key, tp_price, alloc) in enumerate(zip(tp_labels, tps, allocs)):
            try:
                tp_amount = filled_amt * alloc
                tp_order  = ex.create_limit_order(
                    symbol, opp_side, tp_amount, tp_price,
                    {"reduceOnly": True},
                )
                tp_ids[tp_key] = tp_order.get("id")
                console.print(
                    f"  [green]✓ TP{i+1} #{tp_ids[tp_key]} @ "
                    f"${tp_price:,.4f} ({alloc*100:.0f}%)[/green]"
                )
            except Exception as e:
                console.print(f"  [yellow]⚠ TP{i+1} thất bại ({e})[/yellow]")

        record = {
            "id":             entry_order.get("id", _now_iso()),
            "symbol":         symbol,
            "side":           plan.side,
            "direction":      plan.side.lower(),
            "status":         "open",
            "score":          score,
            "entry_price":    actual_price,
            "stop_loss":      plan.stop_loss,
            "tp1":            tps[0] if tps else None,
            "tp2":            tps[1] if len(tps) > 1 else None,
            "tp3":            tps[2] if len(tps) > 2 else None,
            "tp_allocation":  list(allocs),
            "position_usdt":  plan.position_usdt,
            "position_coins": filled_amt,
            "capital":        plan.capital,
            "max_loss_usdt":  plan.max_loss_usdt,
            "rr_ratio":       plan.rr_ratio,
            "peak_price":     actual_price,
            "sl_moved":       False,
            "tp1_hit":        False,
            "tp2_hit":        False,
            "entered_at":     _now_iso(),
            "exited_at":      None,
            "exit_price":     None,
            "exit_reason":    None,
            "pnl_pct":        None,
            "pnl_usdt":       None,
            "mode":           "live",
            "sl_order_id":    sl_order_id,
            "tp1_order_id":   tp_ids["tp1"],
            "tp2_order_id":   tp_ids["tp2"],
            "tp3_order_id":   tp_ids["tp3"],
        }

        trades = self._load_db()
        trades.append(record)
        self._save_db(trades)

        console.print(
            f"[bold green]✅ OKX LIVE #{record['id']}[/bold green]  "
            f"{plan.side} [cyan]{symbol}[/cyan] @ ${actual_price:,.4f}"
        )
        return record

    def _live_exit(
        self,
        symbol: str,
        direction: str,
        amount_coins: float,
        reason: str,
    ) -> dict:
        ex       = self._exchange
        opp_side = "sell" if direction.lower() == "long" else "buy"

        # Hủy SL/TP pending trước
        self.cancel_symbol_orders(symbol)

        # Market exit
        try:
            exit_order = ex.create_market_order(symbol, opp_side, amount_coins)
            exit_price = float(exit_order.get("average") or exit_order.get("price") or 0)
        except Exception as e:
            console.print(f"[red]Lỗi thoát lệnh {symbol}: {e}[/red]")
            return {}

        # Cập nhật DB
        trades = self._load_db()
        result = {}
        for t in trades:
            if t["symbol"] == symbol and t["status"] == "open":
                if direction.lower() == "long":
                    pnl_pct = (exit_price - t["entry_price"]) / t["entry_price"] * 100
                else:
                    pnl_pct = (t["entry_price"] - exit_price) / t["entry_price"] * 100
                pnl_usdt = pnl_pct / 100 * t["position_usdt"]

                t.update({
                    "status":      "closed",
                    "exit_price":  exit_price,
                    "exit_reason": reason,
                    "pnl_pct":     round(pnl_pct, 4),
                    "pnl_usdt":    round(pnl_usdt, 4),
                    "exited_at":   _now_iso(),
                })
                result = t
                break
        self._save_db(trades)
        return result

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _load_db(self) -> list:
        if AGENT_DB.exists():
            try:
                return json.loads(AGENT_DB.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_db(self, trades: list) -> None:
        AGENT_DB.write_text(
            json.dumps(trades, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_open_positions(self) -> dict[str, dict]:
        """Trả về dict {symbol: trade_record} của các lệnh đang mở."""
        trades = self._load_db()
        return {t["symbol"]: t for t in trades if t["status"] == "open"}

    # ── Exchange init ─────────────────────────────────────────────────────────

    def _init_exchange(self):
        try:
            import ccxt
        except ImportError:
            raise ImportError("Thiếu ccxt. Chạy: pip install ccxt")

        if not OKX_API_KEY:
            raise ValueError(
                "Chưa có OKX_API_KEY. Thêm vào .env:\n"
                "  OKX_API_KEY=...\n  OKX_SECRET=...\n  OKX_PASSPHRASE=..."
            )

        ex = ccxt.okx({
            "apiKey":   OKX_API_KEY,
            "secret":   OKX_SECRET,
            "password": OKX_PASSPHRASE,
            "enableRateLimit": True,
            "options":  {"defaultType": OKX_MARKET_TYPE},
        })

        if OKX_SANDBOX:
            ex.set_sandbox_mode(True)
            console.print("[yellow]⚠  OKX SANDBOX MODE[/yellow]")

        return ex


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
