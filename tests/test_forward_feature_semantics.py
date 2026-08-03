"""Feature semantics is a property of the model, never of the trade side.

The bug this pins down shipped and ran: forward chose its feature extractor by
trade side, so a short artifact got side-aligned features while the model behind
it had been trained on unaligned ones. align_short_feature_rows negates six
directional columns, so the model was reading +0.0047 where it had learned
-0.0047 -- not drift, an inverted coordinate system.

Nothing about "this is a short trade" tells you which coordinate system the model
was fitted in. Only the artifact knows, so only the artifact may decide.
See analysis/p0_baseline_audit_20260803.md.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows,
    extract_feature_rows_for_side,
    extract_feature_rows_for_semantics,
)
from src.judgment.forward_scan import (
    _artifact_feature_semantics,
    _extract_rows_for_artifact,
)


class _Artifact:
    """Minimal stand-in; only the attribute under test matters."""

    def __init__(self, feature_semantics=None):
        if feature_semantics is not None:
            self.feature_semantics = feature_semantics


def _frame(n: int = 320) -> pd.DataFrame:
    """A frame with enough history for the rolling windows in add_features."""
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.004, n)))
    hi = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    lo = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    d = pd.DataFrame({
        "open_time": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": hi, "low": lo, "close": close,
        "volume": rng.uniform(1e5, 1e6, n),
    })
    from src.judgment.candidates import add_indicators
    return add_features(add_indicators(d))


@pytest.fixture(scope="module")
def featured() -> pd.DataFrame:
    return _frame()


def test_missing_semantics_reads_as_legacy_unaligned() -> None:
    """Every artifact frozen before the field existed was trained unaligned.

    Defaulting the other way would silently feed a short model negated features,
    which is exactly the failure being prevented.
    """
    assert _artifact_feature_semantics(_Artifact()) == "legacy_unaligned"


def test_short_legacy_artifact_gets_unaligned_rows(featured: pd.DataFrame) -> None:
    idx = [300, 305]
    got = _extract_rows_for_artifact(featured, idx, _Artifact(), "short")
    expected = extract_feature_rows(featured, idx)
    pd.testing.assert_frame_equal(got, expected)


def test_short_aligned_artifact_gets_aligned_rows(featured: pd.DataFrame) -> None:
    idx = [300, 305]
    got = _extract_rows_for_artifact(
        featured, idx, _Artifact("side_aligned_v1"), "short"
    )
    expected = extract_feature_rows_for_side(featured, idx, "short")
    pd.testing.assert_frame_equal(got, expected)


def test_the_two_semantics_actually_differ(featured: pd.DataFrame) -> None:
    """Guards the guard: if these ever coincide the tests above prove nothing."""
    idx = [300, 305]
    plain = extract_feature_rows(featured, idx)
    aligned = extract_feature_rows_for_side(featured, idx, "short")
    flipped = [c for c in ("slow_slope_12", "ret_24", "close_vs_ema55")
               if c in plain.columns
               and not np.allclose(plain[c].to_numpy(dtype=float),
                                   aligned[c].to_numpy(dtype=float),
                                   equal_nan=True)]
    assert flipped, "short alignment is a no-op — the semantics split is pointless"


def test_side_does_not_leak_into_extractor_choice(featured: pd.DataFrame) -> None:
    """A long trade on a legacy artifact must take the same path as a short one.

    Both are unaligned, because the extractor follows the model rather than the
    direction. This is the assertion the shipped bug would have failed.
    """
    idx = [300, 305]
    as_long = _extract_rows_for_artifact(featured, idx, _Artifact(), "long")
    as_short = _extract_rows_for_artifact(featured, idx, _Artifact(), "short")
    pd.testing.assert_frame_equal(as_long, as_short)


def test_semantics_api_rejects_unknown_coordinate_system(featured: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="unknown feature_semantics"):
        extract_feature_rows_for_semantics(
            featured, [300], feature_semantics="guess_from_side", side="short"
        )


def test_feature_vector_is_asof_signal_bar(featured: pd.DataFrame) -> None:
    """D-06: changing future rows cannot alter a vector at the signal bar."""
    signal_i = 300
    original = featured.copy()
    changed = featured.copy()
    changed.loc[signal_i + 1 :, ["close", "high", "low", "volume"]] *= 1000.0

    for semantics in ("legacy_unaligned", "side_aligned_v1"):
        before = extract_feature_rows_for_semantics(
            original, [signal_i], feature_semantics=semantics, side="short"
        )
        after = extract_feature_rows_for_semantics(
            changed, [signal_i], feature_semantics=semantics, side="short"
        )
        pd.testing.assert_frame_equal(before, after)
        assert list(before.columns) == FEATURE_COLUMNS


def test_semantics_have_a_deterministic_28_column_snapshot(featured: pd.DataFrame) -> None:
    snapshot_path = Path(__file__).parent / "fixtures" / "feature_semantics_snapshot.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    for semantics in ("legacy_unaligned", "side_aligned_v1"):
        frame = extract_feature_rows_for_semantics(
            featured, [300, 305], feature_semantics=semantics, side="short"
        )
        rows = [
            [None if pd.isna(value) else round(float(value), 12) for value in row]
            for row in frame.to_numpy()
        ]
        payload = json.dumps(
            {"columns": list(frame.columns), "rows": rows},
            separators=(",", ":"),
            sort_keys=True,
        )
        assert len(frame.columns) == 28
        assert hashlib.sha256(payload.encode()).hexdigest() == expected[semantics]
