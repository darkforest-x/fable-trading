"""Synthetic orchestration tests for V6; no price archive or outcomes read."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_realign_research import (
    assert_saved_columns, merge_wait_statuses, opportunity_changes,
    flat_path_diagnostics, verify_config,
)


def test_terminal_merge_keeps_original_nonentries_and_explicit_missing_values():
    stamp = pd.Timestamp("2024-01-01", tz="UTC")
    old = pd.DataFrame({"event_id": ["a", "b"], "status": ["request_emitted", "expired_no_k2"],
                        "terminal_time": [stamp, stamp], "other": [3., 4.]})
    new = pd.DataFrame({"event_id": ["a"], "status": ["expired_no_alignment"],
                        "terminal_time": [stamp+pd.Timedelta(hours=8)], "other": [np.nan]})
    merged = merge_wait_statuses(old, new).set_index("event_id")
    assert merged.loc["a", "status"] == "expired_no_alignment"
    assert pd.isna(merged.loc["a", "other"])
    assert merged.loc["b", "status"] == "expired_no_k2"
    assert merged.loc["b", "other"] == 4.
    pd.testing.assert_frame_equal(old, old.copy())


@pytest.mark.parametrize("ids", [[], ["b"], ["a", "a"], ["a", "c"]])
def test_terminal_merge_rejects_missing_foreign_and_duplicate_ids(ids):
    old = pd.DataFrame({"event_id": ["a", "b"], "status": ["request_emitted", "expired_no_k2"]})
    new = pd.DataFrame({"event_id": ids, "status": ["expired_no_alignment"]*len(ids)})
    with pytest.raises(ValueError):
        merge_wait_statuses(old, new)


def test_full_parity_normalizes_serialized_time_but_never_ignores_old_extra_column():
    a = pd.DataFrame({"event_id": ["a"], "armed_at": ["2024-01-01 00:00:00+00:00"],
                      "old_extra": [.005], "empty": [np.nan], "reason": [np.nan]})
    b = a.assign(armed_at=pd.to_datetime(a["armed_at"], utc=True), empty=None, reason="", new_column=True)
    assert_saved_columns(a, b)
    with pytest.raises(AssertionError):
        assert_saved_columns(a, b.assign(old_extra=.006))


def episodes(values, executed):
    n = len(values)
    return pd.DataFrame({"event_id": list("abcde")[:n],
                         "mother_decision_time": pd.date_range("2024-01-01", periods=n, freq="MS", tz="UTC"),
                         "episode_net_return": values, "executed": executed,
                         "episode_status": ["test"]*n, "direction": [1]*n})


def trades(ids, offset=0):
    return pd.DataFrame({"event_id": ids,
                         "entry_time": [pd.Timestamp("2024-01-01", tz="UTC")+pd.Timedelta(minutes=offset)]*len(ids),
                         "entry_price": [100.+offset]*len(ids), "initial_stop": [90.]*len(ids),
                         "risk_pct": [.1]*len(ids), "risk_atr": [1.]*len(ids),
                         "exit_time": [pd.Timestamp("2024-01-02", tz="UTC")]*len(ids),
                         "outcome": ["colour_exit"]*len(ids), "net_return": [.01]*len(ids),
                         "hold_minutes": [60]*len(ids)})


def test_opportunity_decomposition_keeps_zeros_unknowns_and_lost_winners():
    before = episodes([.01, -.01, 0, 0, .01], [True, True, False, False, True])
    after = episodes([0, -.02, .03, 0, np.nan], [False, True, True, False, False])
    pairs, info = opportunity_changes(before, after, trades(["a", "b", "e"]), trades(["b", "c"], 5))
    assert len(pairs) == 5
    assert info["unknown_pairs"] == 1
    assert info["same_executed"] == 1
    assert info["missed_net_winners"] == 1
    assert info["former_winners_now_nonpositive"] == 1
    assert info["same_executed_effect_per_mother_bp"] == pytest.approx(-25)
    assert info["participation_effect_per_mother_bp"] == pytest.approx(50)
    assert info["mean_bp"] == pytest.approx(25)
    assert info["same_executed_entry_delay_median_minutes"] == 5
    assert info["same_executed_directional_entry_change_median_bp"] == pytest.approx(500)


@pytest.mark.parametrize("direction", [1, -1])
def test_flat_diagnostics_never_reads_entry_bar_extrema(direction):
    times = pd.date_range("2024-01-01", periods=4, freq="5min", tz="UTC")
    raw = pd.DataFrame({"open_time": times, "low": [99., 98., 1., 1.], "high": [101., 102., 999., 999.]})
    request = pd.DataFrame({"event_id": ["a"], "base_decision_time": [times[0]], "decision_time": [times[2]],
                            "direction": [direction], "initial_stop": [97. if direction == 1 else 103.]})
    row = flat_path_diagnostics(raw, request).iloc[0]
    assert row["flat_bars"] == 2
    assert not row["flat_stop_touched"]
    source = raw.copy()
    source.loc[1, "low" if direction == 1 else "high"] = 97. if direction == 1 else 103.
    assert flat_path_diagnostics(source, request).iloc[0]["flat_stop_touched"]


def test_frozen_config_rejects_added_retry_or_exit():
    root = Path(__file__).resolve().parents[1]
    path = root/"experiments/active/exp-btcusdtp-1h-flat-realignment-preholdout-20260906-v6/config.json"
    config = json.loads(path.read_text())
    base = json.loads((root/config["base_config"]).read_text())
    verify_config(config, base)
    with pytest.raises(RuntimeError):
        verify_config({**config, "wait": {**config["wait"], "first_alignment_only": False}}, base)
    with pytest.raises(RuntimeError):
        verify_config({**config, "exit_mode": "colour"}, base)
