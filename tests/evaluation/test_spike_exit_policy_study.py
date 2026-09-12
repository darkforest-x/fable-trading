"""Focused state-machine tests for the receipt-bound exit-policy engine."""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as study


def test_partial_request_conserves_original_quantity_on_one_bar_jump() -> None:
    assert study._partial_request("partial_1_25_3_35", 3.1, False, False) == (.60, "partial_1r_25_and_3r_35_next_open", True, True)
    assert study._partial_request("partial_1_25_3_35", 3.1, True, False) == (.35, "partial_3r_35_next_open", True, True)


def test_cost_breakeven_is_not_entry_price() -> None:
    assert study._be_level("be1_cost", 100.0, 1, 1.0) == (100.2, "be1_cost")
    assert study._be_level("be1_cost", 100.0, -1, 1.0) == (99.8, "be1_cost")


def test_fast_ma_and_md_cross_require_a_close_confirmation() -> None:
    index = pd.date_range("2024-09-10", periods=2, freq="h", tz="UTC")
    frame = pd.DataFrame({"close": [101., 99.], "s20": [100., 100.], "e20": [100., 100.],
                          "md": [2., 0.], "sb": [1., 1.]}, index=index)
    assert not study._fast_ma_wrong_side(frame, 0, 1)
    assert study._fast_ma_wrong_side(frame, 1, 1)
    assert study._md_cross_wrong_side(frame, 1, 1)


def test_frozen_representative_baseline_matches_legacy_closed_ledger() -> None:
    root = study.SOURCE_STREAMS
    folder = next(path for path in sorted(root.iterdir()) if path.is_dir())
    context = study.load_verified_stream(folder)
    new, fills, _ = study.replay_policy(context, cohort="v1_common_long", policy="baseline")
    old = pd.read_csv(folder / "trades.csv.gz")
    old = old.loc[old.variant.eq("v1_common_execution_long") & ~old.censored.astype(bool)].copy()
    new = new.loc[~new.censored.astype(bool)].copy()
    columns = ["signal_i", "entry_i", "side", "exit_i", "exit_reason"]
    assert new[columns].sort_values("signal_i").reset_index(drop=True).equals(old[columns].sort_values("signal_i").reset_index(drop=True))
    assert len(fills.loc[fills.kind.eq("entry")]) == len(new)
    assert (fills.groupby("trade_id").cost_return.sum() <= .002 + 1e-12).all()
    assert math.isclose(float(fills.groupby("trade_id").cost_return.sum().max()), .002, rel_tol=0, abs_tol=1e-12)
