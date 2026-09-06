"""
Cấu hình toàn cục cho CryptoRadar framework.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── API Endpoints ─────────────────────────────────────────────────────────────
BINANCE_BASE     = "https://api.binance.com"
COINGECKO_BASE   = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL   = "https://api.alternative.me/fng/"

# ── Cài đặt phân tích ────────────────────────────────────────────────────────
CANDLE_LIMIT     = int(os.getenv("CANDLE_LIMIT", 300))

# ── Khung thời gian ──────────────────────────────────────────────────────────
TIMEFRAME_PROFILES = {
    "scalp":    ("15m", "Scalp (15 phút)",         "< 24 giờ"),
    "1h":       ("1h",  "Ngắn hạn (1 giờ)",        "1-3 ngày"),
    "swing":    ("4h",  "Swing (4 giờ)",            "3-14 ngày"),
    "4h":       ("4h",  "Swing (4 giờ)",            "3-14 ngày"),
    "position": ("1d",  "Position (1 ngày)",        "2-8 tuần"),
    "1d":       ("1d",  "Position (1 ngày)",        "2-8 tuần"),
    "1w":       ("1w",  "Dài hạn (1 tuần)",         "1-6 tháng"),
}

# ── Ngưỡng điểm tín hiệu ─────────────────────────────────────────────────────
SIGNAL_THRESHOLDS = {
    "strong_buy":   60,
    "buy":          25,
    "neutral_high": 25,
    "neutral_low":  -25,
    "sell":         -25,
    "strong_sell":  -60,
}

# ── Risk management mặc định ─────────────────────────────────────────────────
DEFAULT_RISK_PCT   = float(os.getenv("DEFAULT_RISK_PCT", 2.0)) / 100
ATR_SL_MULTIPLIER  = 1.5
TP_RATIOS          = (1.5, 2.5, 4.0)
TP_ALLOCATION      = (0.40, 0.35, 0.25)

# ── Giao dịch (legacy Binance) ────────────────────────────────────────────────
TRADING_MODE  = os.getenv("TRADING_MODE", "paper")
EXCHANGE_ID   = os.getenv("EXCHANGE", "binance")
API_KEY       = os.getenv("BINANCE_API_KEY", "")
API_SECRET    = os.getenv("BINANCE_API_SECRET", "")

# ── OKX API ───────────────────────────────────────────────────────────────────
OKX_API_KEY     = os.getenv("OKX_API_KEY", "")
OKX_SECRET      = os.getenv("OKX_SECRET", "")
OKX_PASSPHRASE  = os.getenv("OKX_PASSPHRASE", "")
OKX_SANDBOX     = os.getenv("OKX_SANDBOX", "false").lower() == "true"
OKX_MARKET_TYPE = os.getenv("OKX_MARKET_TYPE", "spot")  # spot | swap

# ── Telegram Notifier ─────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Circuit Breaker ───────────────────────────────────────────────────────────
# Ngưỡng thua lỗ (USDT, số dương) — vượt qua sẽ dừng entry mới
CIRCUIT_DAILY_LOSS_LIMIT   = float(os.getenv("CIRCUIT_DAILY_LOSS_LIMIT", "50"))
CIRCUIT_SESSION_LOSS_LIMIT = float(os.getenv("CIRCUIT_SESSION_LOSS_LIMIT", "100"))
# Số lệnh thua liên tiếp → dừng tạm trong ngày
CIRCUIT_MAX_CONSEC_LOSSES  = int(os.getenv("CIRCUIT_MAX_CONSEC_LOSSES", "4"))

# ── Agent — cài đặt giám sát tự động ─────────────────────────────────────────
_watchlist_raw  = os.getenv("AGENT_WATCHLIST", "BTC/USDT,ETH/USDT,SOL/USDT,SUI/USDT,BNB/USDT")
AGENT_WATCHLIST = [s.strip() for s in _watchlist_raw.split(",")]

AGENT_TIMEFRAME     = os.getenv("AGENT_TIMEFRAME", "15m")
AGENT_CONFIRM_TF    = os.getenv("AGENT_CONFIRM_TF", "1h")

# Khoảng thời gian quét (giây)
AGENT_SCAN_INTERVAL     = int(os.getenv("AGENT_SCAN_INTERVAL", "60"))
# Guardian: 1s với WebSocket, 5s nếu dùng REST-only
AGENT_GUARDIAN_INTERVAL = int(os.getenv("AGENT_GUARDIAN_INTERVAL", "1"))

# Ngưỡng signal
AGENT_SIGNAL_THRESHOLD  = int(os.getenv("AGENT_SIGNAL_THRESHOLD", "30"))
AGENT_REQUIRE_CONFIRM   = os.getenv("AGENT_REQUIRE_CONFIRM", "true").lower() == "true"

AGENT_MAX_POSITIONS     = int(os.getenv("AGENT_MAX_POSITIONS", "3"))
AGENT_CAPITAL_PER_TRADE = float(os.getenv("AGENT_CAPITAL_PER_TRADE", "100.0"))
AGENT_RISK_PCT          = float(os.getenv("AGENT_RISK_PCT", "1.5")) / 100

# Trailing stop
AGENT_TRAILING_STOP       = os.getenv("AGENT_TRAILING_STOP", "true").lower() == "true"
AGENT_TRAILING_ACTIVATION = float(os.getenv("AGENT_TRAILING_ACTIVATION", "1.5"))
AGENT_TRAILING_DISTANCE   = float(os.getenv("AGENT_TRAILING_DISTANCE", "0.8"))

# Cooldown sau exit
AGENT_COOLDOWN_BARS = int(os.getenv("AGENT_COOLDOWN_BARS", "3"))

# Momentum filter: số scan lưu lịch sử để kiểm tra trend tăng tốc
# 0 hoặc 1 = tắt filter; 2-5 = hợp lý
AGENT_MOMENTUM_BARS = int(os.getenv("AGENT_MOMENTUM_BARS", "3"))

# WebSocket realtime price feed (tắt nếu cần debug hoặc môi trường hạn chế)
AGENT_USE_WEBSOCKET = os.getenv("AGENT_USE_WEBSOCKET", "true").lower() == "true"

# ── Alias coin phổ biến ──────────────────────────────────────────────────────
COIN_ALIASES = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "DOGE": "DOGE",
    "DOGECOIN": "DOGE",
    "XRP": "XRP",
    "RIPPLE": "XRP",
    "BNB": "BNB",
    "ADA": "ADA",
    "CARDANO": "ADA",
    "AVAX": "AVAX",
    "LINK": "LINK",
    "DOT": "DOT",
    "MATIC": "POL",
    "POL": "POL",
    "SUI": "SUI",
    "NEAR": "NEAR",
    "ARB": "ARB",
    "OP": "OP",
    "INJ": "INJ",
    "FET": "FET",
    "RENDER": "RENDER",
    "TAO": "TAO",
}
