"""Causality and reproducibility tests for the 15m L2 global-context experiment."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research_15m_ma_launch_l2_global_context import (
    BAR_DELTA,
    EXPERIMENT_ID,
    L2ExperimentError,
    assign_dependency_blocks,
    causal_atr_quintile,
    cluster_symbol_episodes,
    deterministic_control_rows,
    feature_outcome_row,
    load_preregistration,
    matched_control_metrics,
    overlaps_any_interval,
    split_name,
    utc,
)
from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
PREREG = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "preregistration.json"
)


def _prereg() -> dict:
    return load_preregistration(PREREG)


def _featured_frame(rows: int = 3_000) -> pd.DataFrame:
    times = pd.date_range("2026-03-25", periods=rows, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": np.full(rows, 100.0),
            "high": np.full(rows, 100.1),
            "low": np.full(rows, 99.9),
            "close": np.full(rows, 100.0),
            "volume": np.full(rows, 1_000.0),
            "atr14": np.full(rows, 0.2),
            "atr_pct": np.full(rows, 0.002),
            "atr_quintile": pd.Series(np.full(rows, 3), dtype="Int64"),
        }
    )
    for column in FEATURE_COLUMNS:
        if column not in frame:
            frame[column] = 0.1
    return frame


def test_preregistration_freezes_no_holdout_and_owner_safety() -> None:
    prereg = _prereg()
    assert prereg["owner_authorization"]["holdout_read_authorized"] is False
    assert prereg["owner_authorization"]["p0_p1_training_gate_override_for_this_research_run"] is True
    assert all(value is False for value in prereg["safety"].values())
    assert utc(prereg["source"]["candidate_available_at_end_exclusive"]) + pd.Timedelta(
        hours=18
    ) <= utc(prereg["source"]["holdout_start"])
    assert prereg["splits"]["purge_train_tune"]["duration_hours"] == 60
    assert prereg["splits"]["purge_tune_validation"]["duration_hours"] == 60


def test_split_boundaries_leave_exact_18_hour_purges() -> None:
    prereg = _prereg()
    assert split_name(utc("2026-02-26T11:45:00Z"), prereg) == "train"
    assert split_name(utc("2026-02-26T12:00:00Z"), prereg) == "purge"
    assert split_name(utc("2026-02-28T23:45:00Z"), prereg) == "purge"
    assert split_name(utc("2026-03-01T00:00:00Z"), prereg) == "tune"
    assert split_name(utc("2026-03-29T12:00:00Z"), prereg) == "purge"
    assert split_name(utc("2026-04-01T00:00:00Z"), prereg) == "final_validation"


def test_feature_row_ends_one_bar_before_available_at_and_label_path() -> None:
    prereg = _prereg()
    featured = _featured_frame(400)
    signal_i = 200
    feature_time = utc(featured.loc[signal_i, "open_time"])
    episode = {
        "episode_id": "e1",
        "symbol": "BTC_USDT_SWAP",
        "side": "long",
        "class_id": 0,
        "confidence": 0.8,
        "episode_max_confidence": 0.9,
        "window_len": 18,
        "window_start_i": signal_i - 17,
        "window_end_i": signal_i,
        "core_start_i": signal_i - 8,
        "core_end_i": signal_i - 5,
        "confirmation_bars": 5,
        "prediction_cx_norm": 0.5,
        "prediction_cy_norm": 0.5,
        "prediction_w_norm": 0.2,
        "prediction_h_norm": 0.2,
        "input_pixel_sha256": "a" * 64,
        "available_at": (feature_time + BAR_DELTA).isoformat(),
    }
    row = feature_outcome_row(episode, featured, prereg=prereg)
    assert row is not None
    assert utc(row["feature_bar_time"]) == feature_time
    assert utc(row["available_at"]) == feature_time + BAR_DELTA
    assert utc(row["signal_time"]) == feature_time + BAR_DELTA
    assert row["outcome"] == "timeout"
    assert row["exit_offset"] == 72


def test_causal_atr_bucket_prefix_does_not_change_when_future_is_appended() -> None:
    prefix = pd.Series(np.linspace(0.0015, 0.004, 800))
    future = pd.Series(np.linspace(0.02, 0.03, 200))
    before = causal_atr_quintile(prefix)
    after = causal_atr_quintile(pd.concat([prefix, future], ignore_index=True)).iloc[: len(prefix)]
    pd.testing.assert_series_equal(before.reset_index(drop=True), after.reset_index(drop=True))


def test_training_interval_overlap_is_closed_and_exact() -> None:
    intervals = [
        (utc("2026-01-01T01:00:00Z"), utc("2026-01-01T05:00:00Z")),
        (utc("2026-01-02T01:00:00Z"), utc("2026-01-02T05:00:00Z")),
    ]
    assert overlaps_any_interval(
        utc("2026-01-01T05:00:00Z"), utc("2026-01-01T05:15:00Z"), intervals
    )
    assert not overlaps_any_interval(
        utc("2026-01-01T05:15:00Z"), utc("2026-01-01T05:30:00Z"), intervals
    )


def test_episode_clustering_merges_across_midnight_without_class_suppression() -> None:
    base = {
        "model_key": "m",
        "symbol": "BTC_USDT_SWAP",
        "inst_id": "BTC-USDT-SWAP",
        "window_len": 18,
        "window_start_i": 80,
        "core_start_i": 90,
        "core_end_i": 93,
        "core_length_bars": 4,
        "confirmation_bars": 3,
        "core_start_local": 10,
        "core_end_local": 13,
        "confidence": 0.8,
        "prediction_cx_norm": 0.5,
        "prediction_cy_norm": 0.5,
        "prediction_w_norm": 0.2,
        "prediction_h_norm": 0.2,
        "input_width": 1280,
        "input_height": 742,
        "input_n_bars": 18,
        "input_pixel_sha256": "a" * 64,
        "core_start_time": "2026-01-01T23:15:00Z",
        "core_end_time": "2026-01-02T00:00:00Z",
        "core_high": 1.0,
        "core_low": 0.9,
    }
    first = {
        **base,
        "window_end_i": 96,
        "window_start_time": "2026-01-01T19:45:00Z",
        "window_end_time": "2026-01-02T00:45:00Z",
        "available_at": "2026-01-02T01:00:00Z",
        "class_id": 0,
        "class_name": "dense_long",
        "side": "long",
    }
    second = {
        **base,
        "window_start_i": 83,
        "core_start_i": 94,
        "core_end_i": 97,
        "window_end_i": 100,
        "window_start_time": "2026-01-01T20:30:00Z",
        "window_end_time": "2026-01-02T01:45:00Z",
        "available_at": "2026-01-02T02:00:00Z",
        "class_id": 1,
        "class_name": "dense_short",
        "side": "short",
    }
    annotated, episodes = cluster_symbol_episodes("m", "BTC_USDT_SWAP", [first, second])
    assert len(annotated) == 2
    assert len(episodes) == 1
    assert episodes[0]["episode_mixed_class"] is True
    assert episodes[0]["side"] == "long"  # earliest model-available representative


def test_control_assignments_are_deterministic_and_no_replacement() -> None:
    prereg = _prereg()
    featured = _featured_frame(3_000)
    events = pd.DataFrame(
        [
            {
                "episode_id": f"e{n}",
                "symbol": "BTC_USDT_SWAP",
                "side": "long" if n % 2 == 0 else "short",
                "split": "final_validation",
                "dependency_representative": True,
                "available_at": (utc(featured.loc[index, "open_time"]) + BAR_DELTA).isoformat(),
                "atr_quintile": 3,
            }
            for n, index in enumerate((800, 900, 1000, 1100))
        ]
    )
    args = (
        events,
        {"BTC_USDT_SWAP": featured},
        {"BTC_USDT_SWAP": [800, 900, 1000, 1100]},
    )
    first = deterministic_control_rows(*args, prereg=prereg)
    second = deterministic_control_rows(*args, prereg=prereg)
    assert first == second
    assert first
    table = pd.DataFrame(first)
    for _, group in table.groupby("assignment"):
        assert not group[["symbol", "control_feature_bar_i"]].duplicated().any()
        assert (group["month"] == "2026-04").all()


def test_dependency_blocks_merge_transitively_and_keep_only_earliest_event() -> None:
    base = utc("2026-04-01T00:00:00Z")
    events = pd.DataFrame(
        [
            {
                "symbol": "BTC_USDT_SWAP",
                "episode_id": episode_id,
                "available_at": (base + pd.Timedelta(hours=offset)).isoformat(),
                "exposure_start_time": (
                    base + pd.Timedelta(hours=offset - 42)
                ).isoformat(),
                "exposure_end_exclusive": (
                    base + pd.Timedelta(hours=offset + 18)
                ).isoformat(),
                "split": "final_validation",
            }
            for episode_id, offset in (("e1", 0), ("e2", 50), ("e3", 100), ("e4", 170))
        ]
    )
    blocked = assign_dependency_blocks(events)
    by_id = blocked.set_index("episode_id")
    assert by_id.loc["e1", "dependency_block_id"] == by_id.loc["e3", "dependency_block_id"]
    assert by_id.loc["e4", "dependency_block_id"] != by_id.loc["e3", "dependency_block_id"]
    assert bool(by_id.loc["e1", "dependency_representative"])
    assert not bool(by_id.loc["e2", "dependency_representative"])
    assert not bool(by_id.loc["e3", "dependency_representative"])
    assert bool(by_id.loc["e4", "dependency_representative"])


def test_dependency_blocks_reject_cross_split_full_exposure_overlap() -> None:
    events = pd.DataFrame(
        [
            {
                "symbol": "BTC_USDT_SWAP",
                "episode_id": "train_event",
                "available_at": "2026-02-28T12:00:00Z",
                "exposure_start_time": "2026-02-26T18:00:00Z",
                "exposure_end_exclusive": "2026-03-01T06:00:00Z",
                "split": "train",
            },
            {
                "symbol": "BTC_USDT_SWAP",
                "episode_id": "tune_event",
                "available_at": "2026-03-01T00:00:00Z",
                "exposure_start_time": "2026-02-27T06:00:00Z",
                "exposure_end_exclusive": "2026-03-01T18:00:00Z",
                "split": "tune",
            },
        ]
    )
    with pytest.raises(L2ExperimentError, match="full exposure crosses splits"):
        assign_dependency_blocks(events)


def test_matched_control_gate_fails_when_one_required_assignment_is_empty() -> None:
    validation = pd.DataFrame(
        [
            {"episode_id": "e1", "net_ret": 0.02},
            {"episode_id": "e2", "net_ret": 0.03},
        ]
    )
    controls = pd.DataFrame(
        [
            {"assignment": 0, "episode_id": "e1", "control_net_ret": -0.01},
            {"assignment": 0, "episode_id": "e2", "control_net_ret": -0.01},
        ]
    )
    metrics = matched_control_metrics(
        validation,
        controls,
        {"e1", "e2"},
        required_assignments=2,
    )
    assert metrics["missing_assignments"] == [1]
    assert metrics["complete_assignment_coverage"] is False
    assert metrics["all_assignments_positive"] is False
