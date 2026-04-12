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
CANDLE_LIMIT     = int(os.getenv("CANDLE_LIMIT", 300))   # Số nến tải về

# ── Khung thời gian ──────────────────────────────────────────────────────────
# (candle_interval, mô_tả, phù_hợp_với)
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
ATR_SL_MULTIPLIER  = 1.5    # SL = entry ± ATR * multiplier
TP_RATIOS          = (1.5, 2.5, 4.0)   # RR cho TP1, TP2, TP3
TP_ALLOCATION      = (0.40, 0.35, 0.25) # % vị thế chốt tại mỗi TP

# ── Giao dịch ─────────────────────────────────────────────────────────────────
TRADING_MODE  = os.getenv("TRADING_MODE", "paper")   # paper | live
EXCHANGE_ID   = os.getenv("EXCHANGE", "binance")
API_KEY       = os.getenv("BINANCE_API_KEY", "")
API_SECRET    = os.getenv("BINANCE_API_SECRET", "")

# ── Alias coin phổ biến → symbol Binance ────────────────────────────────────
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
