#!/usr/bin/env python3
"""
CryptoRadar — Framework phân tích đầu tư tiền điện tử tự động.

Cách dùng:
  python main.py                         # Chế độ tương tác (Binance data)
  python main.py BTC 10000 swing         # Inline args
  python main.py --positions             # Xem lệnh paper đang mở

  # Auto-trading Agent (OKX):
  python main.py --agent                 # Paper agent, TF=15m, watchlist mặc định
  python main.py --agent live            # Live agent trên OKX (cần .env)
  python main.py --agent paper --tf 1h --watchlist BTC/USDT,ETH/USDT --capital 200
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.text import Text

from src.config import TIMEFRAME_PROFILES, COIN_ALIASES, DEFAULT_RISK_PCT
from src.fetcher import CryptoFetcher
from src.indicators import TechnicalAnalyzer
from src.signals import SignalEngine, build_signal_result
from src.risk import RiskCalculator
from src.reporter import Reporter
from src.trader import Trader

console = Console()

BANNER = """[bold cyan]
  ██████╗██████╗ ██╗   ██╗██████╗ ████████╗ ██████╗     ██████╗  █████╗ ██████╗  █████╗ ██████╗
 ██╔════╝██╔══██╗╚██╗ ██╔╝██╔══██╗╚══██╔══╝██╔═══██╗    ██╔══██╗██╔══██╗██╔══██╗██╔══██╗██╔══██╗
 ██║     ██████╔╝ ╚████╔╝ ██████╔╝   ██║   ██║   ██║    ██████╔╝███████║██║  ██║███████║██████╔╝
 ██║     ██╔══██╗  ╚██╔╝  ██╔═══╝    ██║   ██║   ██║    ██╔══██╗██╔══██║██║  ██║██╔══██║██╔══██╗
 ╚██████╗██║  ██║   ██║   ██║        ██║   ╚██████╔╝    ██║  ██║██║  ██║██████╔╝██║  ██║██║  ██║
  ╚═════╝╚═╝  ╚═╝   ╚═╝   ╚═╝        ╚═╝    ╚═════╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝[/bold cyan]
[dim]           Framework phân tích đầu tư tiền điện tử tự động — RSI · MACD · BB · EMA · ATR[/dim]
"""


def parse_timeframe(user_input: str) -> tuple[str, str]:
    """Chuyển input user → (candle_interval, mô_tả)."""
    key = user_input.lower().strip()

    if key in TIMEFRAME_PROFILES:
        interval, desc, _ = TIMEFRAME_PROFILES[key]
        return interval, desc

    # Tự nhận biết theo ngôn ngữ tự nhiên
    if any(x in key for x in ["phút", "min", "scalp"]):
        return TIMEFRAME_PROFILES["scalp"][:2]
    if any(x in key for x in ["giờ", "hour", "1h"]):
        return TIMEFRAME_PROFILES["1h"][:2]
    if any(x in key for x in ["tuần", "week", "7d", "swing"]):
        return TIMEFRAME_PROFILES["swing"][:2]
    if any(x in key for x in ["tháng", "month", "30d", "position"]):
        return TIMEFRAME_PROFILES["position"][:2]
    if any(x in key for x in ["ngày", "day", "1d"]):
        return TIMEFRAME_PROFILES["1d"][:2]

    # Fallback: swing
    return TIMEFRAME_PROFILES["swing"][:2]


def normalize_symbol(raw: str) -> str:
    up = raw.upper().strip()
    return COIN_ALIASES.get(up, up)


def interactive_mode() -> tuple[str, float, str, float]:
    """Hỏi user các thông số đầu vào."""
    console.print()
    console.print("[bold cyan]Nhập thông tin vị thế:[/bold cyan]")
    console.print("[dim]Ví dụ: BTC | ETH | SOL | DOGE | SUI | ...[/dim]")
    console.print()

    asset_raw = Prompt.ask("  [yellow]Coin/Token[/yellow]", default="BTC")
    asset     = normalize_symbol(asset_raw)

    console.print()
    capital = float(Prompt.ask("  [yellow]Vốn đầu tư (USDT)[/yellow]", default="1000"))

    console.print()
    console.print("[dim]Lựa chọn: scalp | 1h | swing(4h) | 1d | position | 1w[/dim]")
    tf_raw = Prompt.ask("  [yellow]Thời gian đầu tư[/yellow]", default="swing")

    console.print()
    risk_pct = float(Prompt.ask("  [yellow]Rủi ro tối đa (%)[/yellow]", default=str(DEFAULT_RISK_PCT * 100)))

    return asset, capital, tf_raw, risk_pct


def run_analysis(
    asset: str,
    capital: float,
    tf_raw: str,
    risk_pct: float,
) -> None:
    interval, tf_desc = parse_timeframe(tf_raw)

    console.print(f"\n[dim]  Đang kết nối Binance và phân tích [bold]{asset}/USDT[/bold] ({tf_desc})...[/dim]\n")

    fetcher = CryptoFetcher()

    # Validate symbol trước
    if not fetcher.validate_symbol(asset):
        console.print(f"[red]Không tìm thấy {asset}/USDT trên Binance. Kiểm tra lại tên coin.[/red]")
        return

    # Fetch data
    ohlcv      = fetcher.get_ohlcv(asset, interval)
    ticker     = fetcher.get_ticker(asset)
    fear_greed = fetcher.get_fear_greed()

    # Phân tích kỹ thuật
    analyzer   = TechnicalAnalyzer(ohlcv)
    indicators = analyzer.compute_all()

    # Tính điểm tín hiệu
    engine        = SignalEngine()
    score, signals = engine.score(indicators)

    # Tính kế hoạch giao dịch
    risk_calc  = RiskCalculator()
    trade_plan = risk_calc.calculate(
        indicators=indicators,
        ticker=ticker,
        capital=capital,
        risk_pct=risk_pct / 100,
        score=score,
    )

    # Hiển thị kết quả
    reporter = Reporter(console)
    reporter.display_full_report(
        asset=asset,
        interval=interval,
        tf_desc=tf_desc,
        ticker=ticker,
        indicators=indicators,
        score=score,
        signals=signals,
        trade_plan=trade_plan,
        fear_greed=fear_greed,
        capital=capital,
    )

    # Auto-trading
    if trade_plan.side != "WAIT":
        console.print()
        sig = build_signal_result(score, signals)
        mode_str = "[dim](Paper — không cần API key)[/dim]"
        if Confirm.ask(f"  Ghi nhận lệnh tự động {mode_str}?", default=False):
            trader = Trader()
            trader.execute(asset, trade_plan, score)


def run_agent(args: list[str]) -> None:
    """Khởi động auto-trading agent."""
    from src.agent import CryptoAgent
    from src.config import (
        AGENT_WATCHLIST, AGENT_TIMEFRAME, AGENT_CONFIRM_TF,
        AGENT_CAPITAL_PER_TRADE, AGENT_RISK_PCT, AGENT_SIGNAL_THRESHOLD,
    )

    # Parse agent args: --agent [paper|live] [options]
    mode = "paper"
    if args and args[0] in ("paper", "live"):
        mode = args.pop(0)

    # Parse options: --tf 15m --watchlist BTC/USDT,ETH --capital 200 --threshold 30
    tf          = AGENT_TIMEFRAME
    confirm_tf  = AGENT_CONFIRM_TF
    watchlist   = list(AGENT_WATCHLIST)
    capital     = AGENT_CAPITAL_PER_TRADE
    risk_pct    = AGENT_RISK_PCT
    threshold   = AGENT_SIGNAL_THRESHOLD

    i = 0
    while i < len(args):
        opt = args[i]
        val = args[i + 1] if i + 1 < len(args) else ""
        if opt in ("--tf", "--timeframe"):
            tf = val; i += 2
        elif opt in ("--confirm-tf",):
            confirm_tf = val; i += 2
        elif opt in ("--watchlist", "--coins"):
            watchlist = [s.strip() for s in val.split(",")]
            # Normalise: BTC → BTC/USDT
            from src.okx_fetcher import OKXFetcher
            watchlist = [OKXFetcher.normalize_symbol(s) for s in watchlist]
            i += 2
        elif opt in ("--capital",):
            capital = float(val); i += 2
        elif opt in ("--risk",):
            risk_pct = float(val) / 100; i += 2
        elif opt in ("--threshold",):
            threshold = int(val); i += 2
        else:
            i += 1

    if mode == "live":
        from src.config import OKX_API_KEY
        if not OKX_API_KEY:
            console.print(
                "[red]❌ Live mode cần OKX_API_KEY trong .env\n"
                "   Xem .env.example để biết cách cấu hình.[/red]"
            )
            return
        console.print(
            "\n[bold red]⚠  LIVE TRADING MODE — lệnh THẬT sẽ được đặt trên OKX![/bold red]"
        )
        confirm = input("Nhập 'YES' để xác nhận: ").strip()
        if confirm != "YES":
            console.print("[dim]Đã huỷ.[/dim]")
            return

    agent = CryptoAgent(
        mode=mode,
        watchlist=watchlist,
        timeframe=tf,
        confirm_tf=confirm_tf,
        capital=capital,
        risk_pct=risk_pct,
        signal_threshold=threshold,
    )
    agent.start()


def main() -> None:
    console.print(BANNER)

    args = sys.argv[1:]

    # ── Agent mode ────────────────────────────────────────────────────────────
    if args and args[0] in ("--agent", "-a", "agent"):
        run_agent(args[1:])
        return

    # ── Xem positions ─────────────────────────────────────────────────────────
    if args and args[0] in ("--positions", "-p", "positions"):
        trader = Trader()
        trader.show_open_positions()
        return

    # ── Inline mode: python main.py BTC 10000 swing 2 ────────────────────────
    if len(args) >= 3:
        asset   = normalize_symbol(args[0])
        capital = float(args[1])
        tf_raw  = args[2]
        risk_pct = float(args[3]) if len(args) > 3 else DEFAULT_RISK_PCT * 100
        run_analysis(asset, capital, tf_raw, risk_pct)
        return

    # ── Interactive mode ──────────────────────────────────────────────────────
    try:
        asset, capital, tf_raw, risk_pct = interactive_mode()
        run_analysis(asset, capital, tf_raw, risk_pct)

        # Phân tích thêm?
        console.print()
        while Confirm.ask("  Phân tích coin khác?", default=False):
            asset, capital, tf_raw, risk_pct = interactive_mode()
            run_analysis(asset, capital, tf_raw, risk_pct)

    except KeyboardInterrupt:
        console.print("\n[dim]  Thoát.[/dim]")
    except ConnectionError as e:
        console.print(f"\n[red]Lỗi kết nối: {e}[/red]")
    except ValueError as e:
        console.print(f"\n[red]Dữ liệu không hợp lệ: {e}[/red]")


if __name__ == "__main__":
    main()
