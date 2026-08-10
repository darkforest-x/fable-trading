"""Unit tests for Stage-B causal local-signal V2 sampling arithmetic."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.audit_local_signal_v2 import refine_split_audit
from scripts.audit_w20_midbox_causality import (
    box_inside_decision,
    decision_bar,
    future_bars,
    is_causal,
    visible_end_bar,
)
from scripts.build_local_signal_v2_stageb import (
    BOX_LEFT,
    derive_negative_time_bounds,
    event_id_of,
    negative_window_allowed,
    select_diverse_preview_events,
)


def test_stage_b_window_ends_on_decision():
    anchor, delay, win_len = 1000, 2, 24
    dec = decision_bar(anchor, delay)
    win_start = dec - win_len + 1
    assert visible_end_bar(win_start, win_len) == dec
    assert future_bars(win_start, win_len, anchor, delay) == 0
    assert is_causal(win_start, win_len, anchor, delay)


def test_box_never_past_decision_mode_c():
    anchor, delay = 500, 1
    dec = decision_bar(anchor, delay)
    s0, s1 = anchor - BOX_LEFT, dec
    assert box_inside_decision(s1, anchor, delay)
    assert s1 == dec
    assert s0 == anchor - 2


def test_event_id_stable():
    a = event_id_of("ETH_USDT_SWAP", 123, "ETH_USDT_SWAP_0001_pad200")
    b = event_id_of("ETH_USDT_SWAP", 123, "ETH_USDT_SWAP_0001_pad200")
    c = event_id_of("ETH_USDT_SWAP", 124, "ETH_USDT_SWAP_0001_pad200")
    assert a == b
    assert a != c
    assert len(a) == 16


def test_fixed_window_cli_is_available_without_changing_default_protocol():
    project = Path(__file__).resolve().parents[1]
    strict_builder = (
        project / "scripts" / "build_local_signal_v2_stageb_strictneg_v2.py"
    ).read_text()
    assert '"--fixed-window-len"' in strict_builder
    assert "STRICT_NEG_PROTOCOL" in strict_builder
    assert 'f"{STRICT_NEG_PROTOCOL}_w{args.fixed_window_len}"' in strict_builder


def _pos(split: str, end: str, stem: str) -> dict:
    return {
        "split": split,
        "end_time": end,
        "win_len": 24,
        "event_id": stem,
        "symbol": "ETH_USDT_SWAP",
        "stem": stem,
    }


def _neg(split: str, start: str, end: str, stem: str) -> dict:
    return {
        "split": split,
        "start_time": start,
        "end_time": end,
        "win_len": 24,
        "symbol": "ETH_USDT_SWAP",
        "stem": stem,
    }


def test_strict_negative_bounds_keep_windows_inside_their_time_blocks():
    pos = [
        _pos("train", "2026-03-01T00:00:00Z", "tr"),
        _pos("val", "2026-03-03T00:00:00Z", "va0"),
        _pos("val", "2026-03-04T00:00:00Z", "va1"),
    ]
    bounds = derive_negative_time_bounds(pos)
    assert negative_window_allowed(
        "train",
        start_time=pd.Timestamp("2026-02-28T18:00:00Z"),
        end_time=pd.Timestamp("2026-03-01T00:00:00Z"),
        bounds=bounds,
    )
    assert not negative_window_allowed(
        "train",
        start_time=pd.Timestamp("2026-03-02T00:00:00Z"),
        end_time=pd.Timestamp("2026-03-02T06:00:00Z"),
        bounds=bounds,
    )
    assert negative_window_allowed(
        "val",
        start_time=pd.Timestamp("2026-03-03T00:00:00Z"),
        end_time=pd.Timestamp("2026-03-03T06:00:00Z"),
        bounds=bounds,
    )
    assert not negative_window_allowed(
        "val",
        start_time=pd.Timestamp("2026-03-02T23:45:00Z"),
        end_time=pd.Timestamp("2026-03-03T06:00:00Z"),
        bounds=bounds,
    )


def test_split_audit_rejects_cross_time_negatives():
    pos = [
        _pos("train", "2026-03-01T00:00:00Z", "tr"),
        _pos("val", "2026-03-03T00:00:00Z", "va0"),
        _pos("val", "2026-03-04T00:00:00Z", "va1"),
    ]
    neg = [
        _neg(
            "train",
            "2026-03-02T00:00:00Z",
            "2026-03-02T06:00:00Z",
            "future_train_neg",
        ),
        _neg(
            "val",
            "2026-02-20T00:00:00Z",
            "2026-02-20T06:00:00Z",
            "past_val_neg",
        ),
    ]
    result = refine_split_audit(pos, neg, {"purge_bars": 150, "is_time_split": True})
    assert not result["is_time_split"]
    assert not result["negative_time_split"]["pass"]
    assert result["negative_time_split"]["n_train_after_train_end"] == 1
    assert result["negative_time_split"]["n_val_before_val_start"] == 1


def test_split_audit_accepts_strict_negative_blocks():
    pos = [
        _pos("train", "2026-03-01T00:00:00Z", "tr"),
        _pos("val", "2026-03-03T00:00:00Z", "va0"),
        _pos("val", "2026-03-04T00:00:00Z", "va1"),
    ]
    neg = [
        _neg("train", "2026-02-28T18:00:00Z", "2026-03-01T00:00:00Z", "ntr"),
        _neg("val", "2026-03-03T00:00:00Z", "2026-03-03T06:00:00Z", "nva"),
    ]
    result = refine_split_audit(pos, neg, {"purge_bars": 150, "is_time_split": True})
    assert result["is_time_split"]
    assert result["negative_time_split"]["pass"]
    assert result["negatives_have_timestamps"]


def test_preview_selection_prefers_distinct_symbols_and_is_deterministic():
    events = [
        {"stem": "a1", "symbol": "A"},
        {"stem": "a2", "symbol": "A"},
        {"stem": "b1", "symbol": "B"},
        {"stem": "c1", "symbol": "C"},
    ]
    splits = {event["stem"]: "train" for event in events}
    selected = select_diverse_preview_events(events, splits, n=3, seed=7)
    assert len({event["symbol"] for event in selected}) == 3
    assert selected == select_diverse_preview_events(events, splits, n=3, seed=7)


def test_3060_wrapper_ships_repository_safe_trainer_and_uses_strict_dataset():
    project = Path(__file__).resolve().parents[1]
    generic = (project / "scripts" / "train_w20_midbox_on_3060.sh").read_text()
    stageb = (project / "scripts" / "train_local_signal_v2_stageb_on_3060.sh").read_text()
    p0_gate = (project / "scripts" / "run_local_signal_v2_after_stageb.sh").read_text()
    assert "src/detection/train.py" in generic
    assert "train_safe.py" in generic
    assert "train_dense.py" not in generic
    assert "-replace '^path:.*$'" in generic
    assert "PipelineReader" in generic
    assert "Set-Content -Path C:/fable/run_" not in generic
    assert "CommandLine='$REMOTE_CMD'" in generic
    assert 'RUNS="$REMOTE/runs/detect/runs/detect"' in generic
    assert "$HOST:$RUNS/$NAME/weights/best.pt" in generic
    assert "--status --host $HOST --name $NAME" in generic
    assert "--seed $SEED" in generic
    assert "local_signal_v2_stageb_strictneg_v2" in stageb
    assert "local_signal_v2_stageb_strictneg_v2" in p0_gate
    assert "bash scripts/train_local_signal_v2_stageb_on_3060.sh" not in p0_gate
