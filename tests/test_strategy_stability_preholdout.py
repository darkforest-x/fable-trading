"""Tests for pre-holdout strategy stability audit (no holdout access)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.judgment.features import FEATURE_COLUMNS
from src.judgment.train import HOLDOUT_START
from scripts.strategy_stability_preholdout import (
    CandidateSpec,
    HoldoutLeakError,
    assert_no_holdout,
    chronological_folds,
    load_preholdout,
    walk_forward,
)


def _toy_frame(n: int = 400, start: str = "2025-06-01") -> pd.DataFrame:
    rng = np.random.default_rng(0)
    times = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    assert times.max() < HOLDOUT_START
    data = {
        "source": ["okx"] * n,
        "symbol": ["BTC_USDT_SWAP"] * n,
        "signal_i": list(range(n)),
        "signal_time": times,
        "maker_filled": rng.random(n) > 0.3,
        "label": rng.integers(0, 2, n),
        "outcome": rng.choice(["tp", "sl", "timeout"], n),
        "exit_offset": rng.integers(5, 40, n),
        "entry_price": rng.uniform(1, 2, n),
        "realized_ret": rng.normal(0.001, 0.01, n),
    }
    for col in FEATURE_COLUMNS:
        data[col] = rng.normal(0, 1, n)
    return pd.DataFrame(data)


def test_assert_no_holdout_passes_before_boundary() -> None:
    times = pd.Series(pd.date_range("2025-01-01", periods=10, freq="D", tz="UTC"))
    assert_no_holdout(times, context="ok")


def test_assert_no_holdout_aborts_on_holdout_timestamp() -> None:
    times = pd.Series(
        [
            pd.Timestamp("2026-05-03", tz="UTC"),
            pd.Timestamp("2026-05-04", tz="UTC"),
        ]
    )
    with pytest.raises(HoldoutLeakError, match="holdout boundary"):
        assert_no_holdout(times, context="leak")


def test_chronological_folds_four_and_no_holdout() -> None:
    frame = _toy_frame(200)
    folds = chronological_folds(frame, 4)
    assert len(folds) == 4
    # chronological order preserved
    for i in range(3):
        assert folds[i]["signal_time"].max() <= folds[i + 1]["signal_time"].min()
    total = sum(len(f) for f in folds)
    assert total == 200


def test_chronological_folds_rejects_fewer_than_four() -> None:
    with pytest.raises(ValueError, match="at least 4"):
        chronological_folds(_toy_frame(200), 3)


def test_load_preholdout_filters_and_aborts_on_leaked_output(tmp_path: Path) -> None:
    frame = _toy_frame(100)
    # inject a holdout row that must be filtered out by cutoff
    bad = frame.iloc[[-1]].copy()
    bad["signal_time"] = HOLDOUT_START
    mixed = pd.concat([frame, bad], ignore_index=True)
    path = tmp_path / "cand.csv"
    mixed.to_csv(path, index=False)
    spec = CandidateSpec(
        name="toy",
        path=path,
        bar="15m",
        horizon_bars=72,
        role="test",
        notes="toy",
    )
    pre = load_preholdout(spec)
    assert (pre["signal_time"] < HOLDOUT_START).all()
    assert len(pre) == 100


def test_walk_forward_toy_reconciles_trade_counts(tmp_path: Path) -> None:
    frame = _toy_frame(500)
    path = tmp_path / "cand.csv"
    frame.to_csv(path, index=False)
    spec = CandidateSpec(
        name="toy_wf",
        path=path,
        bar="15m",
        horizon_bars=72,
        role="test",
        notes="toy walk-forward",
    )
    summary = walk_forward(spec, n_folds=4)
    assert summary["n_ok_folds"] >= 1
    assert summary["not_final_profitability_proof"] is True
    assert summary["evidence_label"] == "historical_candidate_evidence_preholdout_only"
    for fold in summary["folds"]:
        if fold.get("status") != "ok":
            continue
        # top-decile n reconciles to ~10% of test size
        expected_k = max(1, fold["n_test"] // 10)
        assert fold["top_decile"]["n"] == expected_k
        port_n = fold["portfolio_maker_filled"]["n_trades"]
        assert port_n >= 0
        assert port_n <= fold["n_test"]
