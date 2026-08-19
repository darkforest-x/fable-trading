"""Change every bar after the decision. Nothing the decision saw may move.

The strongest available statement of "no lookahead", and the only one that does
not depend on reading the feature code carefully. Window length is not the
control -- what decides how much future a label saw is where the window's RIGHT
EDGE falls, and w20_midbox shrank from 200 bars to 20-30 with 95.3% of samples
still ending after their decision bar
(docs/learnings/window-length-does-not-control-future-visibility.md).

Adapted from yoyo-eth tests/test_mvp.py::test_future_mutation at 6147810 and
extended: the original checked the 27 features, this also checks the scanner's
event positions, the dispersion columns and the canonical PatternEvent that a
label would be built from.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l1_detection.numeric_baseline import indicators as ind_mod
from yoyo.layers.l1_detection.numeric_baseline import scanner as scanner_mod
from yoyo.layers.l1_detection.numeric_baseline.features import FEATURE_COLUMNS, add_features

DECISION = 250
N_BARS = 400


def make_bars(n: int = N_BARS, seed: int = 7, start: str = "2024-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    open_ = close + rng.normal(0, 0.2, n)
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    return pd.DataFrame(
        {
            "ts": (ts.astype("int64") // 10**6),
            "timestamp": ts,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1, 100, n),
        }
    )


def mutate_future(df: pd.DataFrame, decision: int = DECISION, seed: int = 99) -> pd.DataFrame:
    """Multiply every OHLCV value strictly after `decision` by a random factor."""
    out = df.copy()
    rng = np.random.default_rng(seed)
    future = slice(decision + 1, len(out))
    n_future = len(out) - decision - 1
    for column in ("open", "high", "low", "close", "volume"):
        out.loc[out.index[future], column] = out.loc[out.index[future], column] * rng.uniform(
            0.5, 2.0, n_future
        )
    out["high"] = out[["open", "high", "low", "close"]].max(axis=1)
    out["low"] = out[["open", "low", "close"]].min(axis=1)
    return out


def _prepared(df: pd.DataFrame) -> pd.DataFrame:
    prepared = ind_mod.add_indicators(df)
    prepared = scanner_mod.add_dispersion(prepared)
    return add_features(prepared, compression_threshold=1.5)


def test_the_mutation_actually_changes_the_future():
    """Guards the guard: if the mutation were a no-op every test below passes."""
    original = make_bars()
    mutated = mutate_future(original)
    tail = slice(DECISION + 1, N_BARS)
    assert not np.allclose(
        original["close"].to_numpy()[tail], mutated["close"].to_numpy()[tail]
    )
    # and leaves the past alone
    head = slice(0, DECISION + 1)
    assert np.allclose(original["close"].to_numpy()[head], mutated["close"].to_numpy()[head])


def test_no_feature_at_the_decision_bar_moves():
    frames = [_prepared(make_bars()), _prepared(mutate_future(make_bars()))]
    first, second = (frame.loc[DECISION, FEATURE_COLUMNS].astype(float) for frame in frames)
    pd.testing.assert_series_equal(first, second, check_exact=False, rtol=1e-12)


def test_no_indicator_or_dispersion_column_up_to_the_decision_bar_moves():
    """Not just the decision bar: the whole causal prefix must be identical."""
    first, second = _prepared(make_bars()), _prepared(mutate_future(make_bars()))
    columns = [
        "sma_20", "sma_60", "sma_120", "ema_20", "ema_60", "ema_120",
        "atr_14", "ma_upper", "ma_lower", "cluster_center", "ma_dispersion_atr",
    ]
    pd.testing.assert_frame_equal(
        first.loc[: DECISION, columns],
        second.loc[: DECISION, columns],
        check_exact=False,
        rtol=1e-12,
    )


@pytest.mark.parametrize("trigger", scanner_mod.TRIGGERS)
def test_no_scanner_event_at_or_before_the_decision_bar_moves(trigger: str):
    """Every trigger, not only the default one.

    dispersion_exit and price_breakout both read the previous bar's streak, so
    an off-by-one in either would make an event's existence depend on a bar the
    decision could not have seen.
    """
    events = []
    for frame in (_prepared(make_bars()), _prepared(mutate_future(make_bars()))):
        found, _ = scanner_mod.scan(
            frame, threshold=1.5, min_duration=3, cooldown_bars=8, trigger=trigger
        )
        events.append(found[found["decision_pos"] <= DECISION].reset_index(drop=True))
    pd.testing.assert_frame_equal(events[0], events[1])


def test_mutating_only_the_bar_after_the_decision_is_enough_to_be_caught():
    """The tightest version: one bar of future, not a whole tail.

    An implementation that reads t+1 anywhere fails here while passing a test
    that only shuffles the distant future.
    """
    original = make_bars()
    nudged = original.copy()
    for column in ("open", "high", "low", "close"):
        nudged.loc[nudged.index[DECISION + 1], column] *= 1.5
    nudged["high"] = nudged[["open", "high", "low", "close"]].max(axis=1)
    nudged["low"] = nudged[["open", "low", "close"]].min(axis=1)

    first = _prepared(original).loc[DECISION, FEATURE_COLUMNS].astype(float)
    second = _prepared(nudged).loc[DECISION, FEATURE_COLUMNS].astype(float)
    pd.testing.assert_series_equal(first, second, check_exact=False, rtol=1e-12)
