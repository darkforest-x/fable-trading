from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from yoyo.datasets.ma_launch_owner_autofill_review import (
    FEATURE_NAMES,
    Profile,
    morphology_profile,
    passes_gate,
    profile_distance,
    select_diverse,
)
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS


GATES = {
    "max_ma_envelope_atr": 1.5,
    "max_ma_spread_end_atr": 1.1,
    "max_core_body_atr": 1.5,
    "min_core_progress_atr": -0.6,
    "max_core_progress_atr": 2.5,
    "min_post3_progress_atr": 1.25,
    "min_post5_progress_atr": 1.75,
    "min_aligned_ma_slope_atr": 0.03,
    "max_minimum_close_to_ma_atr": 1.0,
}


def synthetic_arrays(direction: str) -> dict[str, np.ndarray]:
    n = 40
    sign = 1.0 if direction == "LONG" else -1.0
    base = 100.0 + sign * np.r_[np.zeros(20), np.linspace(0.0, 8.0, 20)]
    result = {
        "open": base - sign * 0.1,
        "close": base,
        "high": np.maximum(base, base - sign * 0.1) + 0.2,
        "low": np.minimum(base, base - sign * 0.1) - 0.2,
        "atr": np.ones(n),
    }
    for index, column in enumerate(SIX_MA_COLUMNS):
        result[column] = 100.0 + sign * np.r_[
            np.zeros(20), np.linspace(0.0, 2.0 + index * 0.05, 20)
        ]
    return result


def test_direction_normalization_mirrors_long_and_short() -> None:
    long = morphology_profile(
        synthetic_arrays("LONG"),
        anchor_i=24,
        direction="LONG",
        core_start_offset=-4,
        core_end_offset=-1,
    )
    short = morphology_profile(
        synthetic_arrays("SHORT"),
        anchor_i=24,
        direction="SHORT",
        core_start_offset=-4,
        core_end_offset=-1,
    )
    assert long is not None and short is not None
    np.testing.assert_allclose(long.features, short.features, atol=1e-9)
    np.testing.assert_allclose(long.sequence, short.sequence, atol=1e-9)


def test_gate_requires_fresh_post_core_release() -> None:
    values = np.asarray([1.0, 0.8, 2.0, 1.0, 0.5, 1.5, 2.0, 0.1, 0.1, 0.2])
    profile = Profile(features=values, sequence=np.zeros((4, 10)))
    assert passes_gate(profile, GATES)
    stale = values.copy()
    stale[FEATURE_NAMES.index("post3_progress_atr")] = 0.2
    assert not passes_gate(Profile(stale, profile.sequence), GATES)


def test_profile_distance_is_zero_for_reference_identity() -> None:
    profile = Profile(np.ones(10), np.ones((4, 10)))
    assert profile_distance(
        profile,
        [profile],
        feature_scales=np.ones(10),
        feature_weight=0.45,
        sequence_weight=0.55,
    ) == 0.0


def test_diverse_selection_fills_25_per_side_without_repeated_symbols() -> None:
    candidates = []
    for side_index, direction in enumerate(("LONG", "SHORT")):
        for index in range(100):
            stamp = pd.Timestamp("2022-01-01T00:00:00Z") + pd.Timedelta(days=index * 7 + side_index)
            candidates.append(
                {
                    "event_id": f"{direction}-{index:03d}",
                    "direction": direction,
                    "symbol": f"{direction}_COIN_{index:03d}",
                    "anchor_time": stamp.isoformat(),
                    "similarity_distance": index / 1000.0,
                }
            )
    chosen = select_diverse(
        candidates,
        {
            "target_per_side": 25,
            "time_bins_per_side": 5,
            "max_per_utc_day": 2,
            "max_per_utc_hour": 1,
        },
    )
    assert Counter(row["direction"] for row in chosen) == {"LONG": 25, "SHORT": 25}
    assert len({row["symbol"] for row in chosen}) == 50
