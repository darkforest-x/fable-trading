"""Tests for the P0 causal guard over the w20 mid-box dataset.

The 2026-08-07 handover spec makes ``visible_end_bar <= decision_bar`` a P0 pass
condition and requires it to be enforced by an automated test rather than by
review.  These tests pin the arithmetic on synthetic rows (so they hold whatever
the dataset on disk looks like) and then assert the audit's structural gates on
the real manifest when it is present.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.audit_w20_midbox_causality import (
    DATASET,
    audit_causality,
    audit_split,
    box_inside_decision,
    decision_bar,
    future_bars,
    is_causal,
    label_out_of_bounds,
    position_bucket,
    run_audit,
    visible_end_bar,
)


def row(*, mid=100, half=2, win_start=90, win_len=20, split="train",
        stem="S_USDT_SWAP_000100_pad200", symbol="S_USDT_SWAP",
        end_time="2025-07-01 00:00:00+00:00"):
    return {
        "stem": stem, "out_stem": stem.replace("_pad200", "_w20"), "symbol": symbol,
        "split": split, "mid_global": mid, "half": half,
        "small_bars": [mid - half, mid + half], "win_len": win_len,
        "win_start": win_start, "small_local": [mid - half - win_start, mid + half - win_start],
        "box_pos_frac": 0.5, "stored_mad": 0.0, "end_time": end_time,
    }


# --- pure arithmetic -------------------------------------------------------
def test_decision_bar_is_anchor_plus_confirm_delay():
    assert decision_bar(100, 0) == 100
    assert decision_bar(100, 2) == 102


def test_visible_end_is_last_drawn_bar():
    # a 20-bar window starting at 90 draws bars 90..109
    assert visible_end_bar(90, 20) == 109


def test_future_bars_counts_bars_after_decision():
    # anchor 100, confirm_delay 2 -> decision 102; window ends at 109 -> 7 future bars
    assert future_bars(90, 20, 100, 2) == 7
    assert not is_causal(90, 20, 100, 2)


def test_window_ending_on_decision_bar_is_causal():
    # window 83..102, decision 102 -> exactly zero future
    assert future_bars(83, 20, 100, 2) == 0
    assert is_causal(83, 20, 100, 2)


def test_window_ending_before_decision_is_causal():
    assert future_bars(82, 20, 100, 2) == -1
    assert is_causal(82, 20, 100, 2)


def test_symmetric_midbox_right_edge_equals_decision_bar():
    """half doubles as confirm_delay: box right edge is exactly the decision bar."""
    r = row(mid=100, half=3)
    assert r["small_bars"][1] == decision_bar(r["mid_global"], r["half"])
    assert box_inside_decision(r["small_bars"][1], r["mid_global"], r["half"])


def test_box_extending_past_decision_is_rejected():
    assert not box_inside_decision(105, 100, 2)


@pytest.mark.parametrize("frac,expected", [
    (0.0, "left_mid"), (0.34, "left_mid"), (0.35, "mid"),
    (0.54, "mid"), (0.55, "mid_right"), (0.74, "mid_right"),
    (0.75, "right"), (1.0, "right"),
])
def test_position_buckets(frac, expected):
    assert position_bucket(frac) == expected


@pytest.mark.parametrize("box,bad", [
    ((0.5, 0.5, 0.2, 0.2), False),
    ((0.5, 0.5, 1.0, 1.0), False),
    ((0.95, 0.5, 0.2, 0.2), True),   # right edge 1.05
    ((0.5, 0.02, 0.2, 0.2), True),   # top edge -0.08
    ((0.5, 0.5, 0.0, 0.2), True),    # zero width
])
def test_label_bounds(box, bad):
    assert label_out_of_bounds(box) is bad


# --- section aggregation ---------------------------------------------------
def test_audit_causality_flags_stage_a_dataset():
    rows = [row(win_start=90, win_len=20), row(win_start=83, win_len=20)]
    out = audit_causality(rows)
    assert out["n_future_gt0"] == 1
    assert out["verdict"] == "stage_a_only"


def test_audit_causality_accepts_all_causal_dataset():
    rows = [row(win_start=83, win_len=20), row(win_start=82, win_len=20)]
    out = audit_causality(rows)
    assert out["n_future_gt0"] == 0
    assert out["verdict"] == "causal"


def test_audit_split_detects_event_crossing_split():
    rows = [row(stem="A_000100_pad200", split="train"),
            row(stem="A_000100_pad200", split="val")]
    assert audit_split(rows, [])["n_events_crossing_split"] == 1


def test_audit_split_reports_symbol_split_is_not_time_split():
    rows = [row(symbol="A", stem="A_1", split="train", end_time="2025-07-01 00:00:00+00:00"),
            row(symbol="B", stem="B_1", split="val", end_time="2025-07-02 00:00:00+00:00")]
    out = audit_split(rows, [])
    assert out["is_time_split"] is False
    assert out["symbol_overlap_train_val"] == 0
    assert out["train_val_time_overlap_days"] == 0.0


# --- the dataset actually on disk -----------------------------------------
pytestmark_dataset = pytest.mark.skipif(
    not (DATASET / "w20_manifest.json").exists(), reason="w20 dataset not built here"
)


@pytestmark_dataset
def test_real_manifest_has_no_event_crossing_split():
    rows = json.loads((DATASET / "w20_manifest.json").read_text())
    assert audit_split(rows, [])["n_events_crossing_split"] == 0


@pytestmark_dataset
def test_real_manifest_box_never_extends_past_decision_bar():
    rows = json.loads((DATASET / "w20_manifest.json").read_text())
    assert audit_causality(rows)["box_end_le_decision"] is True


@pytestmark_dataset
def test_audit_runs_end_to_end_and_reports_every_gate():
    result = run_audit(DATASET)
    assert set(result["gates"]) == {
        "causal_dataset (visible_end <= decision)",
        "box_end <= decision",
        "no_event_crosses_split",
        "time_based_split",
        "no_holdout_in_training",
        "labels_in_bounds",
        "manifest_conserved",
    }
    assert result["p0_pass"] == all(result["gates"].values())
