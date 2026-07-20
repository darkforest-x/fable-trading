"""Config for multi-symbol 1m/5m tip rules channel."""
from __future__ import annotations

from pathlib import Path
from typing import Final

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
CHANNEL_DIR: Final = PROJECT_DIR / "data" / "short_tf"
SIGNAL_LOG: Final = CHANNEL_DIR / "signal_log.csv"
STATUS_JSON: Final = CHANNEL_DIR / "status.json"
LATEST_JSON: Final = CHANNEL_DIR / "latest.json"

SOURCE: Final = "okx"
BARS: Final = ("1m", "5m")

# Liquid majors only — volume + maker liquidity for short TF.
SYMBOLS: Final = (
    "BTC_USDT_SWAP",
    "ETH_USDT_SWAP",
    "SOL_USDT_SWAP",
    "BNB_USDT_SWAP",
    "XRP_USDT_SWAP",
    "DOGE_USDT_SWAP",
    "ADA_USDT_SWAP",
    "LINK_USDT_SWAP",
    "AVAX_USDT_SWAP",
    "LTC_USDT_SWAP",
    "DOT_USDT_SWAP",
    "ARB_USDT_SWAP",
    "OP_USDT_SWAP",
    "NEAR_USDT_SWAP",
    "SUI_USDT_SWAP",
)

BAR_MINUTES: Final = {"1m": 1, "5m": 5, "15m": 15}
# Only keep candidates in the last N closed bars (tip-fresh).
TIP_BARS: Final = {"1m": 8, "5m": 4}
# Max age from *bar close* (signal open + bar minutes) to now — tip pulse target.
FRESH_MIN: Final = {"1m": 6, "5m": 18}
# Live lookback for indicators (EMA200 needs ~250+)
LIVE_LOOKBACK: Final = 400
# Min shape_score rank within this scan to notify (0=top only … 1=all tip hits)
SCORE_TOP_FRAC: Final = 0.35

SIGNAL_COLUMNS: Final = (
    "source",
    "symbol",
    "bar",
    "signal_time",
    "entry_time",
    "entry_price",
    "score",
    "threshold",
    "tp_price",
    "sl_price",
    "atr14",
    "atr_pct",
    "lag_min",
    "status",
    "notified_at",
    "channel",
)


def ensure_dirs() -> None:
    CHANNEL_DIR.mkdir(parents=True, exist_ok=True)
