"""Focused causal contracts for frozen V8 entry-evidence extraction."""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v8_entry_evidence_study import (
    attach_htf_evidence,
    bb_episode_evidence,
    ma_features,
    recent_bb_episode,
)


def _bars(n: int = 300) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    index = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    close = 100 + np.arange(n, dtype=float) * .01
    frame = pd.DataFrame({"open": close - .01, "high": close + .05, "low": close - .05, "close": close}, index=index)
    # Strict long cross-group order, with stable width after warmup.
    for name, value in (("s20", 100.06), ("e20", 100.05), ("s60", 100.03), ("e60", 100.02), ("s120", 100.00), ("e120", 99.99)):
        frame[name] = value
    bb = pd.DataFrame({"bb_compressed": False, "v7_ready": True}, index=index)
    return frame, bb, pd.Series(False, index=index)


def test_recent_episode_uses_latest_qualifying_run_only_within_memory() -> None:
    compressed = np.zeros(30, dtype=bool)
    valid = np.ones(30, dtype=bool)
    compressed[8:12] = True
    compressed[20:23] = True
    compressed[24:26] = True  # newer but not a qualifying run
    episode = recent_bb_episode(compressed, valid, 30)
    assert episode.found
    assert (episode.start_i, episode.end_i, episode.window_length, episode.age_bars) == (20, 22, 3, 8)


def test_truncated_recent_episode_reports_known_full_run_but_keeps_window_slice() -> None:
    compressed = np.zeros(40, dtype=bool)
    valid = np.ones(40, dtype=bool)
    compressed[15:30] = True
    episode = recent_bb_episode(compressed, valid, 30)
    assert episode.found
    assert (episode.start_i, episode.end_i) == (18, 29)
    assert episode.truncated_left
    assert (episode.true_start_i, episode.known_total_length) == (15, 15)


def test_gap_breaks_bb_episode_and_never_forms_a_cross_gap_run() -> None:
    compressed = np.zeros(20, dtype=bool)
    valid = np.ones(20, dtype=bool)
    compressed[8:11] = True
    valid[9] = False
    assert not recent_bb_episode(compressed, valid, 12).found


def test_bb_labels_are_prefix_causal_and_tight_overlap_is_known() -> None:
    bars, bb, gap = _bars()
    bb.iloc[285:288, bb.columns.get_loc("bb_compressed")] = True
    full = bb_episode_evidence(bars, bb, gap, 60, np.array([289]), np.array([1]))
    prefix = bb_episode_evidence(bars.iloc[:290], bb.iloc[:290], gap.iloc[:290], 60, np.array([289]), np.array([1]))
    columns = ["bb_episode_directional_order9_run3", "bb_episode_directional_order12_run3", "bb_ma_tight_overlap_run3", "bb_episode_start_i", "bb_episode_end_i"]
    assert full.loc[0, columns].to_dict() == prefix.loc[0, columns].to_dict()
    assert bool(full.bb_episode_directional_order9_run3.iloc[0])
    assert bool(full.bb_episode_directional_order12_run3.iloc[0])
    assert full.bb_ma_tight_overlap_status.iloc[0] == "eligible"


def test_ma_width_history_resets_at_gap_and_prefix_values_are_stable() -> None:
    bars, _, gap = _bars()
    full = ma_features(bars, gap, 60)
    prefix = ma_features(bars.iloc[:290], gap.iloc[:290], 60)
    for name in ("ma_order_long", "ma_order_short", "ma_width_close", "ma_width_p20_prior256", "ma_tight"):
        a, b = full.iloc[289][name], prefix.iloc[289][name]
        assert (pd.isna(a) and pd.isna(b)) or a == b
    gap.iloc[20] = True
    reset = ma_features(bars, gap, 60)
    assert not bool(reset.ma_tight_known.iloc[276])  # only 255 valid preceding rows after the break


def _htf_frame() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=4, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "close": [90.0, 90.0, 130.0, 130.0],
            "valid": [True, True, True, True],
            "ma_order_long": [1, 1, 12, 12],
            "ma_order_short": [11, 11, 0, 0],
            "fast_center": [100.0, 100.0, 100.0, 100.0],
            "fast_slope3": [-1.0, -1.0, 1.0, 1.0],
            "middle_slope3": [-1.0, -1.0, 1.0, 1.0],
            "slow_slope3": [-1.0, -1.0, 1.0, 1.0],
            "slope_vote": [-3, -3, 3, 3],
            "md": [1.0, 1.0, 1.0, 1.0],
            "sb": [2.0, 2.0, 2.0, 2.0],
        },
        index=index,
    )


def test_htf_uses_last_completed_bar_not_future_bar() -> None:
    events = pd.DataFrame({"signal_confirm_time": [pd.Timestamp("2025-01-01T02:00:00Z")], "side": [1]})
    got = attach_htf_evidence(events, _htf_frame(), 60, "same", "authenticated")
    assert got.htf_bar_open.iloc[0] == pd.Timestamp("2025-01-01T01:00:00Z")
    assert got.htf_bar_close_time.iloc[0] == pd.Timestamp("2025-01-01T02:00:00Z")
    assert bool(got.htf_opposed_completed.iloc[0])


def test_missing_htf_is_unknown_not_false() -> None:
    events = pd.DataFrame({"signal_confirm_time": [pd.Timestamp("2025-01-01T02:00:00Z")], "side": [1]})
    got = attach_htf_evidence(events, None, 60, None, "missing")
    assert got.htf_status.iloc[0] == "missing_source_stream"
    assert pd.isna(got.htf_opposed_completed.iloc[0])


def test_htf_bar_becomes_unknown_when_its_close_is_one_full_period_stale() -> None:
    events = pd.DataFrame({"signal_confirm_time": [pd.Timestamp("2025-01-01T03:00:00Z")], "side": [1]})
    htf = _htf_frame().iloc[:2]
    got = attach_htf_evidence(events, htf, 60, "same", "authenticated")
    assert got.htf_status.iloc[0] == "stale_htf_bar"
    assert pd.isna(got.htf_opposed_completed.iloc[0])
