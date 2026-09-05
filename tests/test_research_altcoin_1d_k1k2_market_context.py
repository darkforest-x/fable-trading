"""Causality and frozen-partition tests for the altcoin daily V3 context gate."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import research_altcoin_1d_k1k2_episode_runner as signal_parent
from scripts import research_altcoin_1d_k1k2_market_context as subject


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = (
    ROOT
    / "experiments/active"
    / "exp-altcoin-1d-k1k2-market-context-preholdout-20260905-v3"
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _daily(symbol_offset: float, *, days: int = 110) -> pd.DataFrame:
    index = np.arange(days, dtype=float)
    close = 100.0 + symbol_offset + 0.18 * index + 1.3 * np.sin(index / 7.0)
    open_ = close - 0.15 * np.cos(index / 5.0)
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2023-01-01", periods=days, freq="D", tz="UTC"
            ),
            "open": open_,
            "high": np.maximum(open_, close) + 1.0,
            "low": np.minimum(open_, close) - 1.0,
            "close": close,
            "volume": 1000.0 + symbol_offset * 5.0 + index,
            "source_rows": 96,
            "segment_id": 1,
        }
    )
    return frame


def _profile_map(dailies: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    signal_config = _json(
        ROOT
        / "experiments/active"
        / "exp-altcoin-1d-k1k2-episode-runner-preholdout-20260905-v1"
        / "config.json"
    )
    return {
        symbol: signal_parent.build_profile(frame, signal_config, "ema13_sma34")
        for symbol, frame in dailies.items()
    }


def test_frozen_universe_partitions_are_disjoint_and_b_is_sealed() -> None:
    manifest = _json(EXPERIMENT / "universe_manifest.json")
    config = _json(EXPERIMENT / "config.json")
    confirmation_a = set(manifest["confirmation_a"])
    confirmation_b = set(manifest["sealed_confirmation_b"])
    excluded = set(manifest["development_symbols_excluded_from_holdbacks"])
    a_paths = {
        path
        for record in manifest["confirmation_a"].values()
        for path in record["sources"]
    }
    b_paths = {
        path
        for record in manifest["sealed_confirmation_b"].values()
        for path in record["sources"]
    }

    assert len(confirmation_a) == 109
    assert len(confirmation_b) == 108
    assert not confirmation_a & confirmation_b
    assert not confirmation_a & excluded
    assert not confirmation_b & excluded
    assert not a_paths & b_paths
    assert config["safety"]["sealed_confirmation_b_must_not_be_read"] is True
    assert pd.Timestamp(config["source_contract"]["safe_end_exclusive"]) < pd.Timestamp(
        config["source_contract"]["holdout_start"]
    )


def test_trailing_features_reset_at_every_source_gap() -> None:
    frame = pd.DataFrame(
        {
            "close": [1.0, 2.0, 3.0, 4.0, 20.0, 22.0, 24.0, 26.0],
            "segment_id": [1, 1, 1, 1, 2, 2, 2, 2],
        }
    )
    returns, efficiency = subject._trailing_return_and_efficiency(frame, window=3)

    assert returns.iloc[:3].isna().all()
    assert returns.iloc[4:7].isna().all()
    assert returns.iloc[3] == 3.0
    assert np.isclose(returns.iloc[7], 0.3)
    assert efficiency.iloc[3] == 1.0
    assert efficiency.iloc[7] == 1.0


def test_context_panel_is_invariant_to_future_ohlcv_changes() -> None:
    config = _json(EXPERIMENT / "config.json")
    target_daily = {f"S{i:02d}": _daily(float(i)) for i in range(12)}
    reference_daily = {"BTC": _daily(40.0), "ETH": _daily(20.0)}
    target_profiles = _profile_map(target_daily)
    original, original_major = subject.build_context_panel(
        target_profiles, reference_daily, config
    )

    changed_targets = deepcopy(target_daily)
    changed_references = deepcopy(reference_daily)
    for frame in [*changed_targets.values(), *changed_references.values()]:
        future = frame.index > 75
        frame.loc[future, ["open", "high", "low", "close"]] *= np.linspace(
            0.4, 2.5, int(future.sum())
        )[:, None]
        frame.loc[future, "volume"] *= 7.0
    changed_profiles = _profile_map(changed_targets)
    altered, altered_major = subject.build_context_panel(
        changed_profiles, changed_references, config
    )
    cutoff = pd.Timestamp("2023-03-17", tz="UTC")

    pd.testing.assert_frame_equal(
        original.loc[original["open_time"].le(cutoff)].reset_index(drop=True),
        altered.loc[altered["open_time"].le(cutoff)].reset_index(drop=True),
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        original_major.loc[original_major["open_time"].le(cutoff)].reset_index(
            drop=True
        ),
        altered_major.loc[altered_major["open_time"].le(cutoff)].reset_index(
            drop=True
        ),
        check_exact=True,
    )


def test_directional_context_mirrors_cross_sectional_rank() -> None:
    config = _json(EXPERIMENT / "config.json")
    row = {
        "up_breadth": 0.8,
        "down_breadth": 0.2,
        "up_breadth_change5": 0.1,
        "down_breadth_change5": -0.1,
        "major_up_alignment": 1.0,
        "major_down_alignment": 0.0,
        "major_momentum_atr": 2.0,
        "return_rank": 0.9,
        "efficiency_rank": 0.8,
        "cross_section_count": 10,
        "breadth_constituents": 12,
    }
    long_context = subject.directional_context(row, 1, config)
    short_context = subject.directional_context(row, -1, config)

    assert long_context["context_available"] is True
    assert long_context["context_relative_return_rank"] == 0.9
    assert long_context["context_relative_efficiency_rank"] == 0.8
    assert np.isclose(short_context["context_relative_return_rank"], 0.2)
    assert np.isclose(short_context["context_relative_efficiency_rank"], 0.3)
    assert long_context["context_mean"] > short_context["context_mean"]


def test_context_gate_is_fail_closed_only_when_a_gate_is_active() -> None:
    config = _json(EXPERIMENT / "config.json")
    baseline = config["selection"]["initial"]
    assert subject.context_passes({"context_available": False}, baseline) == (
        True,
        "pass",
    )

    active = {**baseline, "breadth_level_min": 0.6}
    assert subject.context_passes({"context_available": False}, active) == (
        False,
        "context_unavailable",
    )
    row = {
        "context_available": True,
        "context_breadth_level": 0.59,
        "context_breadth_change5": 0.2,
        "context_major_score": 1.0,
        "context_relative_score": 1.0,
        "context_mean": 1.0,
    }
    assert subject.context_passes(row, active) == (False, "breadth_level_min")


def test_execution_adapter_preserves_v2_entry_exit_and_cost_contract() -> None:
    config = _json(EXPERIMENT / "config.json")
    execution = _json(
        ROOT
        / config["parents"]["execution_config_path"]
    )
    adapted = subject._execution_adapter(config, execution)

    assert adapted["execution"]["entry"] == "next_complete_UTC_day_open"
    assert adapted["execution"]["round_trip_cost_fraction"] == 0.002
    assert adapted["execution"]["maximum_horizon_bars"] == 90
    assert config["parents"]["fixed_execution_params"] == {
        "stop_policy": "two_close_all_hard_2_0",
        "bank_schedule": "v1_tail80",
        "trail_reference": "slow",
        "runner_buffer_atr": 1.25,
    }
