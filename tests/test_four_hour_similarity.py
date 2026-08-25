"""Tests for the frozen retrospective 4h morphology retrieval helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l1_detection.four_hour_similarity import (
    ALL_MA_COLS,
    SimilaritySpec,
    build_reference_contract,
    candidate_anchor_indices,
    coarse_distance,
    deduplicate_candidates,
    enrich_4h,
    merge_with_api_suffix,
    multivariate_dtw_distance,
    passes_reference_contract,
    raw_window_tensor,
    resample_complete_4h,
)


def synthetic_4h() -> pd.DataFrame:
    n = 220
    close = np.full(n, 100.0)
    close[:160] += np.sin(np.arange(160) / 8.0) * 0.15
    release = np.array([103.0, 103.5, 104.2, 104.0, 105.0, 106.0,
                        106.5, 107.0, 107.6, 108.0, 108.7, 109.2])
    close[160:172] = release
    close[172:] = release[-1] + np.sin(np.arange(n - 172) / 5.0) * 0.2
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.25
    low = np.minimum(open_, close) - 0.25
    volume = np.full(n, 100.0)
    volume[160:172] = np.linspace(500.0, 180.0, 12)
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range(
                "2026-07-01", periods=n, freq="4h", tz="UTC"
            ),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return enrich_4h(frame)


def test_resample_retains_only_complete_sixteen_bar_buckets() -> None:
    times = pd.date_range("2026-01-01", periods=31, freq="15min", tz="UTC")
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": np.arange(31, dtype=float) + 100,
            "high": np.arange(31, dtype=float) + 101,
            "low": np.arange(31, dtype=float) + 99,
            "close": np.arange(31, dtype=float) + 100.5,
            "volume": 1.0,
        }
    )
    bars, audit = resample_complete_4h(frame)
    assert len(bars) == 1
    assert audit["four_hour_buckets_total"] == 2
    assert audit["four_hour_buckets_dropped_incomplete"] == 1
    assert bars.iloc[0]["open"] == 100
    assert bars.iloc[0]["close"] == 115.5


def test_local_api_overlap_must_be_exact() -> None:
    times = pd.date_range("2026-01-01", periods=10, freq="4h", tz="UTC")
    local = pd.DataFrame(
        {
            "open_time": times,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
        }
    )
    api = local.iloc[2:].copy()
    merged, audit = merge_with_api_suffix(
        local, api, scan_end=times[-1], min_overlap=6
    )
    assert len(merged) == len(local)
    assert audit["overlap_rows"] == 8
    bad = api.copy()
    bad.loc[bad.index[0], "high"] += 0.1
    with pytest.raises(ValueError, match="parity failed"):
        merge_with_api_suffix(local, bad, scan_end=times[-1], min_overlap=6)


def test_short_is_exact_price_axis_mirror() -> None:
    frame = synthetic_4h()
    spec = SimilaritySpec(
        scan_start="2026-07-01T00:00:00+00:00",
        scan_end="2026-08-31T00:00:00+00:00",
        reference_anchor="2026-07-27T16:00:00+00:00",
    )
    long_tensor, long_metrics = raw_window_tensor(frame, 160, 1, spec)
    short_tensor, short_metrics = raw_window_tensor(frame, 160, -1, spec)
    assert np.allclose(long_tensor[:, 0], -short_tensor[:, 0])
    assert np.allclose(long_tensor[:, 1], -short_tensor[:, 1])
    assert np.allclose(long_tensor[:, 2], short_tensor[:, 2])
    assert np.allclose(long_tensor[:, 3], -short_tensor[:, 3])
    assert np.allclose(long_tensor[:, 4], short_tensor[:, 4])
    assert long_metrics["release_close_signed_pct"] == pytest.approx(
        -short_metrics["release_close_signed_pct"]
    )


def test_vector_gate_recovers_reference_and_matches_scalar_gate() -> None:
    frame = synthetic_4h()
    anchor_time = frame.iloc[160]["open_time"].isoformat()
    spec = SimilaritySpec(
        scan_start="2026-07-01T00:00:00+00:00",
        scan_end="2026-08-31T00:00:00+00:00",
        reference_anchor=anchor_time,
    )
    _, metrics = raw_window_tensor(frame, 160, 1, spec)
    contract = build_reference_contract(metrics, spec)
    assert passes_reference_contract(metrics, contract)
    indices, counts = candidate_anchor_indices(
        frame, direction=1, contract=contract, spec=spec
    )
    assert 160 in indices
    assert counts["anchors_passing_broad_gate"] == len(indices)
    for index in indices:
        _, candidate_metrics = raw_window_tensor(frame, index, 1, spec)
        assert passes_reference_contract(candidate_metrics, contract)


def test_distances_and_deduplication_are_deterministic() -> None:
    query = np.arange(30, dtype=float).reshape(10, 3)
    assert coarse_distance(query, query) == 0.0
    assert multivariate_dtw_distance(query, query, radius=2) == 0.0
    shifted = np.roll(query, 1, axis=0)
    assert multivariate_dtw_distance(shifted, query, radius=2) > 0
    rows = [
        {"symbol": "BTC", "direction": "LONG", "anchor_i": 10, "d": 0.3},
        {"symbol": "BTC", "direction": "LONG", "anchor_i": 15, "d": 0.1},
        {"symbol": "BTC", "direction": "LONG", "anchor_i": 40, "d": 0.2},
        {"symbol": "BTC", "direction": "SHORT", "anchor_i": 12, "d": 0.05},
    ]
    kept = deduplicate_candidates(rows, distance_field="d", gap_bars=10)
    assert [(row["direction"], row["anchor_i"]) for row in kept] == [
        ("SHORT", 12),
        ("LONG", 15),
        ("LONG", 40),
    ]


def test_all_ma_columns_exist_after_enrichment() -> None:
    frame = synthetic_4h()
    assert set(ALL_MA_COLS).issubset(frame.columns)
    assert np.isfinite(frame.iloc[-1][list(ALL_MA_COLS)].to_numpy(dtype=float)).all()
