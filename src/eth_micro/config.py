"""ETH-only micro-timeframe channel (1m/2m/3m/5m). Separate from 15m mainline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

PROJECT_DIR: Final = Path(__file__).resolve().parents[2]
CHANNEL_DIR: Final = PROJECT_DIR / "data" / "eth_micro"
SIGNAL_LOG: Final = CHANNEL_DIR / "signal_log.csv"
BACKTEST_JSON: Final = CHANNEL_DIR / "backtest_summary.json"
STATUS_JSON: Final = CHANNEL_DIR / "monitor_status.json"
MODELS_DIR: Final = CHANNEL_DIR / "models"
POOLS_DIR: Final = CHANNEL_DIR / "pools"

SYMBOL: Final = "ETH_USDT_SWAP"
SOURCE: Final = "okx"
BARS: Final = ("1m", "2m", "3m", "5m")

# Wall-clock match to mainline 15m×72 ≈ 18h
WALL_CLOCK_MIN: Final = 15 * 72
BAR_MINUTES: Final = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15}


@dataclass(frozen=True)
class BarConfig:
    bar: str
    horizon_bars: int

    @property
    def wall_hours(self) -> float:
        return self.horizon_bars * BAR_MINUTES[self.bar] / 60.0


def bar_configs() -> tuple[BarConfig, ...]:
    return tuple(
        BarConfig(bar=b, horizon_bars=max(12, WALL_CLOCK_MIN // BAR_MINUTES[b])) for b in BARS
    )


def ensure_dirs() -> None:
    for d in (CHANNEL_DIR, MODELS_DIR, POOLS_DIR):
        d.mkdir(parents=True, exist_ok=True)
