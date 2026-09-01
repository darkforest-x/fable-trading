"""Causality and contract tests for the owner-corrected short-window L2."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.research_15m_ma_launch_l2_short_window_side_split import (
    EXPERIMENT_ID,
    ShortWindowL2Error,
    assign_short_dependency_blocks,
    build_side_homogeneous_episodes,
    load_preregistration,
)
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart
from yoyo.layers.l2_judgment.short_window_features import (
    SHORT_WINDOW_FEATURE_COLUMNS,
    extract_short_window_features,
)


ROOT = Path(__file__).resolve().parents[1]
PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"


def ohlcv(rows: int = 320) -> pd.DataFrame:
    close = 100 + np.linspace(0, 3, rows) + np.sin(np.arange(rows) / 9) * 0.3
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close - 0.04,
            "high": close + 0.22,
            "low": close - 0.24,
            "close": close,
            "volume": 1000 + np.arange(rows),
        }
    )


def detection(*, start: int, end: int, core_start: int, core_end: int) -> dict:
    return {
        "l1_confidence": 0.75,
        "prediction_cx_norm": 0.72,
        "prediction_cy_norm": 0.48,
        "prediction_w_norm": 0.22,
        "prediction_h_norm": 0.16,
        "window_len": end - start + 1,
        "window_start_i": start,
        "core_start_i": core_start,
        "core_end_i": core_end,
        "confirmation_bars": end - core_end,
    }


def test_preregistration_freezes_exact_short_window_without_holdout() -> None:
    prereg = load_preregistration(PREREG)
    assert prereg["owner_authorization"]["training_authorized"] is True
    assert prereg["owner_authorization"]["holdout_read_authorized"] is False
    assert prereg["short_window_contract"]["window_lengths"] == [18, 19]
    assert prereg["short_window_contract"]["feature_count"] == 238
    assert prereg["model"]["sides"] == ["long", "short"]
    assert prereg["model"]["mixed_model"] is False
    assert prereg["splits"]["random_split"] is False
    assert all(value is False for value in prereg["safety"].values())


def test_feature_contract_has_no_old_global_context_names() -> None:
    forbidden = (
        "pre_range",
        "spread_pos96",
        "dense_frac48",
        "ema200",
        "atr_pct_ratio96",
        "ret_48",
        "episode_max_confidence",
        "volume",
    )
    assert len(SHORT_WINDOW_FEATURE_COLUMNS) == 238
    assert not any(token in column for token in forbidden for column in SHORT_WINDOW_FEATURE_COLUMNS)


def test_mixed_source_episode_is_reclustered_independently_by_side() -> None:
    common = {
        "symbol": "BTC_USDT_SWAP",
        "episode_id": "old_mixed_episode",
        "window_start_i": 80,
        "window_end_i": 97,
        "window_len": 18,
        "core_start_i": 90,
        "core_end_i": 94,
        "confirmation_bars": 3,
        "confidence": 0.7,
        "available_at": "2026-01-02T00:30:00Z",
    }
    candidates = pd.DataFrame(
        [
            {**common, "candidate_id": "long", "side": "long"},
            {**common, "candidate_id": "short", "side": "short"},
        ]
    )
    episodes = build_side_homogeneous_episodes(candidates)
    assert len(episodes) == 2
    assert set(episodes["side"]) == {"long", "short"}
    assert episodes["episode_id"].nunique() == 2
    assert set(episodes["source_cross_side_episode_id"]) == {"old_mixed_episode"}


@pytest.mark.parametrize("window_len", [18, 19])
def test_sequence_is_right_aligned_to_decision_bar(window_len: int) -> None:
    enriched = add_mas(ohlcv())
    end = 250
    start = end - window_len + 1
    window = enriched.iloc[start : end + 1]
    _, transform = render_chart(window)
    row = extract_short_window_features(
        window,
        detection(start=start, end=end, core_start=end - 7, core_end=end - 4),
        price_min=transform.price_min,
        price_max=transform.price_max,
    )
    assert tuple(row) == SHORT_WINDOW_FEATURE_COLUMNS
    assert row["t00_valid"] == 1.0
    expected_last_y = (transform.price_max - float(window.iloc[-1]["close"])) / (
        transform.price_max - transform.price_min
    )
    assert row["t00_close_y"] == pytest.approx(expected_last_y)
    if window_len == 18:
        assert row["t18_valid"] == 0.0
        assert np.isnan(row["t18_close_y"])
    else:
        assert row["t18_valid"] == 1.0
        assert np.isfinite(row["t18_close_y"])


def test_short_features_do_not_change_when_future_bars_are_mutated() -> None:
    signal_i = 250
    base = ohlcv(360)
    mutated = base.copy()
    future = mutated.index > signal_i
    scale = np.linspace(3.0, 20.0, int(future.sum()))
    for column in ("open", "high", "low", "close"):
        mutated.loc[future, column] = mutated.loc[future, column].to_numpy() * scale
    mutated.loc[future, "volume"] = mutated.loc[future, "volume"] * 1000

    observed = []
    for frame in (base, mutated):
        enriched = add_mas(frame)
        start = signal_i - 18
        window = enriched.iloc[start : signal_i + 1]
        image, transform = render_chart(window)
        features = extract_short_window_features(
            window,
            detection(
                start=start,
                end=signal_i,
                core_start=signal_i - 7,
                core_end=signal_i - 4,
            ),
            price_min=transform.price_min,
            price_max=transform.price_max,
        )
        observed.append((image, features))
    np.testing.assert_array_equal(observed[0][0], observed[1][0])
    for column in SHORT_WINDOW_FEATURE_COLUMNS:
        left, right = observed[0][1][column], observed[1][1][column]
        if np.isnan(left):
            assert np.isnan(right)
        else:
            assert left == right


def test_feature_builder_rejects_core_outside_visible_window() -> None:
    enriched = add_mas(ohlcv())
    start, end = 232, 250
    window = enriched.iloc[start : end + 1]
    _, transform = render_chart(window)
    with pytest.raises(ValueError, match="escapes"):
        extract_short_window_features(
            window,
            detection(start=start, end=end, core_start=start - 1, core_end=start + 2),
            price_min=transform.price_min,
            price_max=transform.price_max,
        )


def test_dependency_blocks_use_actual_short_input_exposure() -> None:
    base = pd.Timestamp("2026-04-01T00:00:00Z")
    records = []
    for name, offset in (("e1", 0), ("e2", 10), ("e3", 35)):
        available = base + pd.Timedelta(hours=offset)
        records.append(
            {
                "episode_id": name,
                "symbol": "BTC_USDT_SWAP",
                "split": "final_validation",
                "available_at": available.isoformat(),
                "exposure_start_time": (available - pd.Timedelta(hours=4.5)).isoformat(),
                "exposure_end_exclusive": (available + pd.Timedelta(hours=18)).isoformat(),
            }
        )
    blocked = assign_short_dependency_blocks(pd.DataFrame(records)).set_index("episode_id")
    assert blocked.loc["e1", "dependency_block_id"] == blocked.loc["e2", "dependency_block_id"]
    assert blocked.loc["e1", "dependency_block_id"] != blocked.loc["e3", "dependency_block_id"]
    assert bool(blocked.loc["e1", "dependency_representative"]) is True
    assert bool(blocked.loc["e2", "dependency_representative"]) is False
    assert bool(blocked.loc["e3", "dependency_representative"]) is True


def test_dependency_blocks_refuse_cross_split_exposure() -> None:
    frame = pd.DataFrame(
        [
            {
                "episode_id": "train",
                "symbol": "BTC_USDT_SWAP",
                "split": "train",
                "available_at": "2026-02-26T11:45:00Z",
                "exposure_start_time": "2026-02-26T07:15:00Z",
                "exposure_end_exclusive": "2026-02-27T05:45:00Z",
            },
            {
                "episode_id": "tune",
                "symbol": "BTC_USDT_SWAP",
                "split": "tune",
                "available_at": "2026-02-27T00:00:00Z",
                "exposure_start_time": "2026-02-26T19:30:00Z",
                "exposure_end_exclusive": "2026-02-27T18:00:00Z",
            },
        ]
    )
    with pytest.raises(ShortWindowL2Error, match="crosses splits"):
        assign_short_dependency_blocks(frame)
