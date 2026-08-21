"""Tests for the causal, scale-free six-line rope scorer."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import yoyo.datasets.ma_rope_filter as rope_module
from yoyo.datasets.ma_rope_filter import (
    InsufficientHistoryError,
    MissingOHLCError,
    RopeFilterConfig,
    SourceResolutionError,
    compute_rope_metrics,
    compute_rope_series,
    rank_inputs,
    read_review_sheet,
    resolve_symbol_source,
    score_rows,
)


def _ohlc(close: np.ndarray, *, start: str = "2025-01-01") -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open_time": pd.date_range(start, periods=len(close), freq="15min", tz="UTC"),
            "open": open_,
            "high": np.maximum(open_, close) + 0.05,
            "low": np.minimum(open_, close) - 0.05,
            "close": close,
        }
    )


def _tangled_frame(n: int = 260) -> pd.DataFrame:
    # Small alternating oscillations keep all six lines in one narrow price
    # band while repeatedly making fast and slow lines pass through each other.
    index = np.arange(n)
    close = 100.0 + 0.18 * np.sin(index * np.pi / 2.0)
    return _ohlc(close)


def _parallel_divergent_frame(n: int = 260) -> pd.DataFrame:
    # A monotone trend separates short from long averages and preserves their
    # order; it is intentionally not selected by a return or outcome label.
    index = np.arange(n)
    close = 100.0 * np.exp(0.006 * index)
    return _ohlc(close)


def test_canonical_metrics_are_causal_to_the_decision_bar() -> None:
    frame = _tangled_frame()
    decision = 220
    baseline = compute_rope_metrics(frame, decision_bar=decision)
    mutated = frame.copy()
    mutated.loc[decision + 1 :, ["open", "high", "low", "close"]] = 1_000_000.0
    after_future_mutation = compute_rope_metrics(mutated, decision_bar=decision)
    for key in (
        "six_ma_bandwidth",
        "pairwise_cross_density",
        "pairwise_crossing_score",
        "body_bundle_touch_rate",
        "body_bundle_cross_rate",
        "body_bundle_interaction_score",
        "rope_persistence_rate",
        "slope_consistency",
        "rope_score",
    ):
        assert after_future_mutation[key] == pytest.approx(baseline[key])
    assert baseline["future_bars_used"] == 0


def test_positive_price_rescaling_does_not_change_metrics() -> None:
    frame = _tangled_frame()
    scaled = frame.copy()
    for column in ("open", "high", "low", "close"):
        scaled[column] *= 37.0
    left = compute_rope_metrics(frame, decision_bar=220)
    right = compute_rope_metrics(scaled, decision_bar=220)
    for key in (
        "six_ma_bandwidth",
        "pairwise_cross_density",
        "pairwise_crossing_score",
        "rope_persistence_rate",
        "body_bundle_touch_rate",
        "body_bundle_cross_rate",
        "body_bundle_interaction_score",
        "startup_bandwidth_change",
        "slope_consistency",
        "rope_score",
    ):
        assert right[key] == pytest.approx(left[key], rel=1e-12, abs=1e-12)


def test_tangled_rope_scores_above_parallel_divergent_lines() -> None:
    config = RopeFilterConfig()
    tangled = compute_rope_metrics(_tangled_frame(), decision_bar=220, config=config)
    divergent = compute_rope_metrics(_parallel_divergent_frame(), decision_bar=220, config=config)
    assert tangled["six_ma_bandwidth"] < divergent["six_ma_bandwidth"]
    assert tangled["pairwise_cross_density"] > divergent["pairwise_cross_density"]
    assert tangled["rope_score"] > divergent["rope_score"]


def test_missing_ohlc_fails_closed() -> None:
    frame = _tangled_frame()
    frame.loc[150, "close"] = np.nan
    with pytest.raises(MissingOHLCError):
        compute_rope_metrics(frame, decision_bar=220)

    with pytest.raises(InsufficientHistoryError):
        compute_rope_metrics(_tangled_frame(125), decision_bar=120)


def test_series_exposes_required_interpretable_components() -> None:
    series = compute_rope_series(_tangled_frame())
    for column in (
        "six_ma_bandwidth",
        "pairwise_cross_density",
        "rank_flip_density",
        "pairwise_crossing_score",
        "rope_persistence_rate",
        "body_bundle_touch_rate",
        "body_bundle_cross_rate",
        "body_bundle_interaction_score",
        "body_touch_score",
        "body_cross_score",
        "slope_consistency",
        "startup_tightening",
        "rope_score",
    ):
        assert column in series.columns
    for column in (
        "pairwise_cross_density",
        "pairwise_crossing_score",
        "rope_persistence_rate",
        "body_bundle_touch_rate",
        "body_bundle_cross_rate",
        "body_bundle_interaction_score",
        "body_touch_score",
        "body_cross_score",
        "bandwidth_tightness_score",
        "startup_tightening_score",
        "rope_score",
    ):
        assert series[column].dropna().between(0.0, 1.0).all()


def test_body_bundle_interaction_beats_parallel_divergence() -> None:
    tangled = compute_rope_metrics(_tangled_frame(), decision_bar=220)
    divergent = compute_rope_metrics(_parallel_divergent_frame(), decision_bar=220)
    assert tangled["body_bundle_touch_rate"] > divergent["body_bundle_touch_rate"]
    assert tangled["body_bundle_cross_rate"] > divergent["body_bundle_cross_rate"]
    assert tangled["body_bundle_interaction_score"] > divergent["body_bundle_interaction_score"]
    # The divergent bundle is directionally consistent, but that diagnostic
    # is excluded from the score by design.
    assert divergent["slope_consistency"] > tangled["slope_consistency"]
    assert RopeFilterConfig().weight_slope_consistency == 0.0
    assert tangled["rope_score"] > divergent["rope_score"]


def test_score_series_is_computed_once_per_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "kline_fetched"
    data_root.mkdir()
    frame = _tangled_frame(230)
    frame.to_csv(data_root / "okx_TEST_USDT_SWAP_15m_230.csv", index=False)
    rows = [
        {
            "sample_id": f"TEST_{decision}",
            "symbol": "TEST_USDT_SWAP",
            "source_csv": "stale.csv",
            "source_owner_global": [decision - 10, decision],
            "source_owner_cut_time": frame.loc[decision, "open_time"].isoformat(),
        }
        for decision in (200, 220)
    ]
    calls = 0
    original = rope_module.compute_rope_series

    def counted(frame_arg, config=None):
        nonlocal calls
        calls += 1
        return original(frame_arg, config=config)

    monkeypatch.setattr(rope_module, "compute_rope_series", counted)
    report = score_rows(rows, population="multi_row", data_root=data_root)
    assert calls == 1
    assert report["series_computations"] == 1
    assert report["n_scored"] == 2


def test_short_tip_review_schema_preserves_review_labels_and_owner_side_field(
    tmp_path: Path,
) -> None:
    sheet = tmp_path / "short_tip_review.csv"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "i",
                "stem",
                "symbol",
                "tip_idx",
                "tip_time",
                "max_conf",
                "n_boxes",
                "tip_spread",
                "right_p50",
                "owner_keep",
                "owner_note",
                "image",
            ],
        )
        writer.writeheader()
        for i, label in enumerate(("keep", "drop", "skip", "")):
            writer.writerow(
                {
                    "i": i,
                    "stem": f"tip-{i}",
                    "symbol": "TEST_USDT_SWAP",
                    "tip_idx": 220,
                    "tip_time": "2025-01-03T07:00:00+00:00",
                    "owner_keep": label,
                    "owner_note": "note",
                }
            )
    parsed = read_review_sheet(sheet)
    assert len(parsed) == 4
    assert [row["review_status"] for row in parsed] == [
        "keep",
        "drop",
        "skip",
        "unreviewed",
    ]
    assert all("owner_side" in row for row in parsed)


def test_symbol_candidate_resolution_rejects_index_time_mismatch(tmp_path: Path) -> None:
    data_root = tmp_path / "kline_fetched"
    data_root.mkdir()
    frame = _tangled_frame(220)
    csv_path = data_root / "okx_TEST_USDT_SWAP_15m_999.csv"
    frame.to_csv(csv_path, index=False)
    row = {
        "sample_id": "TEST_000",
        "symbol": "TEST_USDT_SWAP",
        "source_csv": "data/kline_fetched/okx_TEST_USDT_SWAP_15m_123.csv",
        "source_owner_global": [200, 210],
        "source_owner_cut_time": "2025-01-03T00:00:00+00:00",
    }
    with pytest.raises(SourceResolutionError, match="exactly one OHLC source"):
        resolve_symbol_source([row], data_root=data_root)


def test_cli_report_keeps_missing_rows_and_resolves_stale_name(tmp_path: Path) -> None:
    data_root = tmp_path / "kline_fetched"
    data_root.mkdir()
    frame = _tangled_frame(230)
    current = data_root / "okx_TEST_USDT_SWAP_15m_230.csv"
    frame.to_csv(current, index=False)
    decision = 220
    row = {
        "sample_id": "TEST_000",
        "symbol": "TEST_USDT_SWAP",
        "source_csv": "data/kline_fetched/okx_TEST_USDT_SWAP_15m_111.csv",
        "source_owner_global": [210, decision],
        "source_owner_cut_time": frame.loc[decision, "open_time"].isoformat(),
        "class": "positive",
    }
    missing = {
        "sample_id": "MISSING_000",
        "symbol": "MISSING_USDT_SWAP",
        "source_csv": "data/kline_fetched/okx_MISSING_USDT_SWAP_15m_1.csv",
        "source_owner_global": [210, decision],
        "source_owner_cut_time": frame.loc[decision, "open_time"].isoformat(),
        "class": "positive",
    }
    report = score_rows(
        [row, missing],
        population="positive_manifest",
        data_root=data_root,
    )
    assert report["n_rows"] == 2
    assert report["n_scored"] == 1
    scored = report["rows"][0]
    assert scored["status"] == "scored"
    assert scored["recorded_source_path_stale"] is True
    assert scored["source_resolution"].startswith("symbol_candidate")
    assert report["rows"][1]["status"] == "missing_ohlc_source"
    assert len(report["missing_or_refused_rows"]) == 1


def test_review_sheet_preserves_all_owner_sides(tmp_path: Path) -> None:
    data_root = tmp_path / "kline_fetched"
    data_root.mkdir()
    frame = _tangled_frame(230)
    frame.to_csv(data_root / "okx_TEST_USDT_SWAP_15m_230.csv", index=False)
    sheet = tmp_path / "review_sheet.csv"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["box_id", "symbol", "cut_global", "cut_time", "owner_side"],
        )
        writer.writeheader()
        for i, side in enumerate(("long", "short", "skip")):
            writer.writerow(
                {
                    "box_id": f"box-{i}",
                    "symbol": "TEST_USDT_SWAP",
                    "cut_global": 220,
                    "cut_time": frame.loc[220, "open_time"].isoformat(),
                    "owner_side": side,
                }
            )
    manifest = tmp_path / "positive.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "sample_id": "pos-0",
                "symbol": "TEST_USDT_SWAP",
                "source_csv": "stale.csv",
                "source_owner_global": [210, 220],
                "source_owner_cut_time": frame.loc[220, "open_time"].isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = rank_inputs(
        positive_manifest=manifest,
        review_sheet=sheet,
        data_root=data_root,
    )
    review_rows = report["populations"]["review_sheet"]["rows"]
    assert [row["owner_side"] for row in review_rows] == ["long", "short", "skip"]
    assert report["n_rows"] == 4
    assert report["holdout_read"] is False


def test_holdout_row_is_refused_before_source_resolution(tmp_path: Path) -> None:
    row = {
        "sample_id": "HOLDOUT_000",
        "symbol": "TEST_USDT_SWAP",
        "source_csv": "does-not-matter.csv",
        "source_owner_global": [200, 220],
        "source_owner_cut_time": "2026-05-04T00:00:00+00:00",
    }
    report = score_rows([row], population="positive_manifest", data_root=tmp_path)
    assert report["rows"][0]["status"] == "holdout_refused"
    assert report["rows"][0]["resolved_source_csv"] is None
