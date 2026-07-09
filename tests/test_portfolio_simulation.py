from __future__ import annotations

import pandas as pd

from src.backtest.run import MAX_CONCURRENT, simulate


def _signal(
    *,
    symbol: str,
    entry_time: pd.Timestamp,
    exit_time: pd.Timestamp,
    score: float = 0.9,
) -> dict[str, object]:
    return {
        "source": "okx",
        "symbol": symbol,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "score": score,
        "outcome": "tp",
        "realized_ret": 0.01,
    }


def test_simulate_skips_overlapping_positions_for_same_symbol() -> None:
    start = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    signals = pd.DataFrame(
        [
            _signal(symbol="BTC_USDT_SWAP", entry_time=start, exit_time=start + pd.Timedelta(hours=1)),
            _signal(
                symbol="BTC_USDT_SWAP",
                entry_time=start + pd.Timedelta(minutes=15),
                exit_time=start + pd.Timedelta(hours=2),
                score=0.95,
            ),
            _signal(
                symbol="BTC_USDT_SWAP",
                entry_time=start + pd.Timedelta(hours=1),
                exit_time=start + pd.Timedelta(hours=3),
            ),
        ]
    )

    trades = simulate(signals, threshold=0.5)

    assert len(trades) == 2
    assert trades["entry_time"].tolist() == [start, start + pd.Timedelta(hours=1)]


def test_simulate_respects_global_concurrency_cap() -> None:
    start = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    signals = pd.DataFrame(
        [
            _signal(
                symbol=f"SYM{i}_USDT_SWAP",
                entry_time=start,
                exit_time=start + pd.Timedelta(hours=1),
                score=1.0 - i * 0.01,
            )
            for i in range(MAX_CONCURRENT + 3)
        ]
    )

    trades = simulate(signals, threshold=0.5)

    assert len(trades) == MAX_CONCURRENT
    assert trades["symbol"].tolist() == [f"SYM{i}_USDT_SWAP" for i in range(MAX_CONCURRENT)]
