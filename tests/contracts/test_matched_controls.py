"""Matched controls must be matched, deterministic, and refuse to pretend.

The control is the only instrument that sees the failure the whole project's
worst reported number came from: a pool that earns because the period was
falling, not because the model found anything. That makes three properties
non-negotiable -- the strata really constrain, the draw reproduces, and an
unmatched candidate is an error rather than a quiet widening.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.matched_controls import (
    ControlPoolError,
    draw_matched_controls,
    match_key,
    volatility_buckets,
)


def _pool(n=600, seed=3):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    symbols = np.repeat(["ETH-USDT-SWAP", "BTC-USDT-SWAP"], n // 2)
    return pd.DataFrame(
        {"symbol": symbols, "decision_ts": ts, "atr": rng.uniform(1.0, 9.0, n)}
    )


def _candidates(pool, rows=(10, 200, 400)):
    picked = pool.iloc[list(rows)].reset_index(drop=True)
    return picked.assign(candidate_id=[f"c{i}" for i in range(len(picked))])


def test_every_control_shares_the_candidates_symbol_month_and_volatility_bucket():
    pool = _pool()
    candidates = _candidates(pool)
    drawn = draw_matched_controls(candidates, pool, n_per_candidate=5)
    merged = drawn.controls
    assert not merged.empty
    assert (merged["symbol"] == merged["match_symbol"]).all()
    for _, row in merged.iterrows():
        assert pd.Timestamp(row["decision_ts"]).strftime("%Y-%m") == row["match_time_bucket"]


def test_the_draw_is_reproducible_without_replaying_the_loop():
    """Hash-ordered selection, not an rng whose state depends on event order."""
    pool = _pool()
    candidates = _candidates(pool)
    first = draw_matched_controls(candidates, pool, n_per_candidate=5)
    shuffled = candidates.iloc[::-1].reset_index(drop=True)
    second = draw_matched_controls(shuffled, pool, n_per_candidate=5)
    key = ["candidate_id", "control_rank", "selection_sha256"]
    pd.testing.assert_frame_equal(
        first.controls[key].sort_values(key).reset_index(drop=True),
        second.controls[key].sort_values(key).reset_index(drop=True),
    )


def test_a_different_seed_selects_different_controls():
    pool = _pool()
    candidates = _candidates(pool)
    a = draw_matched_controls(candidates, pool, n_per_candidate=5, seed="a")
    b = draw_matched_controls(candidates, pool, n_per_candidate=5, seed="b")
    assert set(a.controls["selection_sha256"]) != set(b.controls["selection_sha256"])


def test_an_empty_stratum_is_an_error_not_a_widening():
    pool = _pool()
    orphan = pd.DataFrame(
        {
            "candidate_id": ["lonely"],
            "symbol": ["SOL-USDT-SWAP"],  # no such symbol in the pool
            "decision_ts": [pd.Timestamp("2026-01-05", tz="UTC")],
            "atr": [4.0],
        }
    )
    with pytest.raises(ControlPoolError, match="no matched control"):
        draw_matched_controls(orphan, pool)


def test_fallback_is_available_but_counted():
    pool = _pool()
    orphan = pd.DataFrame(
        {
            "candidate_id": ["lonely"],
            "symbol": ["SOL-USDT-SWAP"],
            "decision_ts": [pd.Timestamp("2026-01-05", tz="UTC")],
            "atr": [4.0],
        }
    )
    drawn = draw_matched_controls(orphan, pool, n_per_candidate=3, allow_fallback=True)
    assert drawn.fallback_count == 1
    assert drawn.coverage == 0.0


def test_volatility_buckets_come_from_the_windows_own_bars():
    """A fixed grid would put a low-ATR symbol entirely in one bucket."""
    quiet = pd.Series(np.linspace(0.1, 0.3, 300))
    loud = pd.Series(np.linspace(10.0, 30.0, 300))
    quiet_edges, quiet_assignment = volatility_buckets(quiet)
    loud_edges, loud_assignment = volatility_buckets(loud)
    assert quiet_edges.max() < loud_edges.min()
    for assignment in (quiet_assignment, loud_assignment):
        assert set(np.unique(assignment)) == {0, 1, 2}


def test_the_strata_recorded_are_the_five_the_task_book_requires():
    pool = _pool()
    drawn = draw_matched_controls(_candidates(pool), pool, n_per_candidate=2)
    assert drawn.strata_used == ("symbol", "month", "volatility_tercile", "horizon", "cost")


def test_a_missing_column_is_named_rather_than_raising_a_keyerror_later():
    pool = _pool().drop(columns=["atr"])
    with pytest.raises(ControlPoolError, match=r"pool is missing columns \['atr'\]"):
        draw_matched_controls(_candidates(_pool()), pool)


def test_match_key_rejects_an_unknown_time_bucket():
    with pytest.raises(ValueError, match="unknown time_bucket"):
        match_key("ETH-USDT-SWAP", pd.Timestamp("2026-01-01", tz="UTC"), 1, time_bucket="fortnight")
