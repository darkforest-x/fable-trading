"""Tests for the pre-holdout 15m completed-shape candidate collector."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts.audit_15m_candidate_prelaunch import prelaunch_metrics, summarize
from scripts.collect_15m_ma_launch_candidates import (
    load_existing_candidate_rows,
    selection_audit,
    validate_preregistration,
)

from yoyo.datasets.fifteen_minute_launch_candidates import (
    CandidateCollectionError,
    CandidateSpec,
    audit_future_invariance,
    build_gallery,
    deduplicate_candidates,
    discover_universe,
    read_preholdout_prefix,
    render_review_chart,
    select_balanced_candidates,
)
from yoyo.layers.l2_judgment.pine_dense_start import DenseStartProfile


ROOT = Path(__file__).resolve().parents[1]
PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-candidate1000-v1"
    / "preregistration.json"
)
EXPANSION_PREREG = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-candidate9000-v1"
    / "preregistration.json"
)


def dense_l1() -> DenseStartProfile:
    return DenseStartProfile(
        profile_id="dense_l1",
        min_pre_pairwise_crosses=2,
        max_pre_bandwidth_atr_mean=3.0,
        min_current_alignment=6,
        min_pre_cross_imbalance=-1,
        min_slope_coherence=2.0 / 3.0,
        min_atr_release_ratio=1.0,
    )


def synthetic_frame(rows: int = 280) -> pd.DataFrame:
    index = np.arange(rows, dtype=float)
    close = 100.0 + 0.025 * index + 0.8 * np.sin(index / 5.0)
    open_price = close + 0.13 * np.sin(index / 3.0)
    high = np.maximum(open_price, close) + 0.35
    low = np.minimum(open_price, close) - 0.35
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2025-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + index,
            "_source_i": np.arange(rows, dtype=int),
            "_segment_id": np.ones(rows, dtype=int),
        }
    )


def candidate(
    event: str,
    side: str,
    symbol: str,
    stamp: str,
    score: float,
) -> dict[str, object]:
    return {
        "event_id": event,
        "direction": side,
        "symbol": symbol,
        "anchor_time": stamp,
        "completed_score": score,
    }


def test_preregistration_maps_to_exact_executable_spec() -> None:
    payload = json.loads(PREREG.read_text(encoding="utf-8"))
    spec = CandidateSpec.from_preregistration(payload)
    assert spec.scan_end_ts == pd.Timestamp("2026-05-04T00:00:00Z")
    assert spec.holdout_start_ts == spec.scan_end_ts
    assert spec.target_per_side == 500
    assert spec.review_bars == 48
    assert spec.release_bars - 1 == 11
    assert payload["scope"]["holdout_ohlcv_rows_allowed"] == 0
    assert payload["delivery"]["training_eligible"] is False


def test_expansion_preregistration_preserves_shape_and_seeds_prior_pool() -> None:
    payload, spec, profile, eval_symbols = validate_preregistration(
        EXPANSION_PREREG
    )
    prior = load_existing_candidate_rows(payload)
    assert spec.scan_start_ts == pd.Timestamp("2022-01-03T00:00:00Z")
    assert spec.scan_end_ts == pd.Timestamp("2026-05-04T00:00:00Z")
    assert spec.target_per_side == 4500
    assert spec.dedupe_bars == 224
    assert spec.review_marker_offset_bars == -3
    assert spec.max_per_symbol_per_side == 80
    assert spec.max_per_day_per_side == 80
    assert spec.review_bars == 48
    assert profile.profile_id == "dense_l1"
    assert len(eval_symbols) == 47
    assert len(prior) == 1000
    assert {row["owner_verdict"] for row in prior} == {"PENDING"}
    assert payload["authorized_multi_variable_bundle"]["training_authorization"] is False


def test_prefix_reader_never_converts_boundary_ohlcv(tmp_path: Path) -> None:
    path = tmp_path / "okx_TEST_USDT_SWAP_15m_4.csv"
    path.write_text(
        "ts,open,high,low,close,volume,open_time\n"
        "1777849200000,10,11,9,10.5,100,2026-05-03 23:00:00+00:00\n"
        "1777850100000,10.5,12,10,11,110,2026-05-03 23:15:00+00:00\n"
        "1777852800000,DO_NOT_PARSE,DO_NOT_PARSE,DO_NOT_PARSE,DO_NOT_PARSE,DO_NOT_PARSE,2026-05-04 00:00:00+00:00\n"
        "1777853700000,99,100,98,99.5,120,2026-05-04 00:15:00+00:00\n",
        encoding="utf-8",
    )
    frame, audit = read_preholdout_prefix(
        path, end_exclusive=pd.Timestamp("2026-05-04T00:00:00Z")
    )
    assert len(frame) == 2
    assert frame["open_time"].max() < pd.Timestamp("2026-05-04T00:00:00Z")
    assert audit["boundary_timestamp_rows_inspected"] == 1
    assert audit["holdout_ohlcv_rows_materialized"] == 0
    assert "DO_NOT_PARSE" not in frame.to_csv(index=False)


def test_universe_prefers_longest_then_deep_and_excludes_eval(tmp_path: Path) -> None:
    deep = tmp_path / "kline_deep"
    fetched = tmp_path / "kline_fetched"
    deep.mkdir()
    fetched.mkdir()
    (deep / "okx_AAA_USDT_SWAP_15m_90.csv").touch()
    (fetched / "okx_AAA_USDT_SWAP_15m_100.csv").touch()
    (deep / "okx_BBB_USDT_SWAP_15m_100.csv").touch()
    (fetched / "okx_BBB_USDT_SWAP_15m_100.csv").touch()
    (deep / "okx_CCC_USDT_SWAP_15m_120.csv").touch()

    universe, audit = discover_universe(
        [deep, fetched], eval_symbols={"CCC_USDT_SWAP"}
    )
    assert universe["AAA_USDT_SWAP"].parent == fetched
    assert universe["BBB_USDT_SWAP"].parent == deep
    assert "CCC_USDT_SWAP" not in universe
    assert audit["discovered_symbols"] == 3
    assert audit["eligible_filename_symbols"] == 2


def test_dedupe_and_balanced_selection_obey_frozen_quotas() -> None:
    spec = replace(
        CandidateSpec(),
        target_per_side=2,
        max_per_symbol_per_side=1,
        max_per_day_per_side=2,
    )
    rows = [
        candidate("l1", "LONG", "AAA", "2025-01-01T00:00:00Z", 0.95),
        candidate("l2-near", "LONG", "AAA", "2025-01-01T12:00:00Z", 0.90),
        candidate("l3", "LONG", "BBB", "2025-01-01T01:00:00Z", 0.85),
        candidate("s1", "SHORT", "AAA", "2025-01-02T00:00:00Z", 0.96),
        candidate("s2", "SHORT", "BBB", "2025-01-02T01:00:00Z", 0.86),
    ]
    deduplicated = deduplicate_candidates(rows, spec=spec)
    assert {row["event_id"] for row in deduplicated}.isdisjoint({"l2-near"})
    selected = select_balanced_candidates(deduplicated, spec=spec)
    assert [row["event_id"] for row in selected["LONG"]] == ["l1", "l3"]
    assert [row["rank"] for row in selected["SHORT"]] == [1, 2]


def test_per_symbol_streaming_dedupe_is_identical_to_one_global_batch() -> None:
    spec = replace(CandidateSpec(), dedupe_bars=4)
    rows = [
        candidate("a1", "LONG", "AAA", "2025-01-01T00:00:00Z", 0.90),
        candidate("a2", "LONG", "AAA", "2025-01-01T00:30:00Z", 0.80),
        candidate("a3", "LONG", "AAA", "2025-01-01T03:00:00Z", 0.70),
        candidate("b1", "LONG", "BBB", "2025-01-01T00:15:00Z", 0.95),
        candidate("b2", "LONG", "BBB", "2025-01-01T00:45:00Z", 0.85),
        candidate("b3", "SHORT", "BBB", "2025-01-01T00:45:00Z", 0.75),
    ]
    global_ids = {
        str(row["event_id"]) for row in deduplicate_candidates(rows, spec=spec)
    }
    streamed: list[dict[str, object]] = []
    for symbol in ("AAA", "BBB"):
        streamed.extend(
            deduplicate_candidates(
                [row for row in rows if row["symbol"] == symbol], spec=spec
            )
        )
    streamed_ids = {str(row["event_id"]) for row in streamed}
    assert streamed_ids == global_ids


def test_selection_fails_closed_when_one_side_is_short() -> None:
    spec = replace(CandidateSpec(), target_per_side=2)
    rows = [
        candidate("l1", "LONG", "AAA", "2025-01-01T00:00:00Z", 0.9),
        candidate("l2", "LONG", "BBB", "2025-01-02T00:00:00Z", 0.8),
        candidate("s1", "SHORT", "AAA", "2025-01-01T00:00:00Z", 0.9),
    ]
    with pytest.raises(CandidateCollectionError, match="SHORT has only 1"):
        select_balanced_candidates(rows, spec=spec)


def test_expansion_selection_seeds_prior_dedupe_and_union_quotas() -> None:
    spec = replace(
        CandidateSpec(),
        dedupe_bars=4,
        target_per_side=2,
        max_per_symbol_per_side=2,
        max_per_day_per_side=3,
    )
    existing = [
        candidate("old-l", "LONG", "AAA", "2025-01-01T00:00:00Z", 0.7),
        candidate("old-s", "SHORT", "AAA", "2025-01-02T00:00:00Z", 0.7),
    ]
    rows = [
        candidate("l-near", "LONG", "AAA", "2025-01-01T00:30:00Z", 0.99),
        candidate("l-far", "LONG", "AAA", "2025-01-01T03:00:00Z", 0.90),
        candidate("l-capped", "LONG", "AAA", "2025-01-01T06:00:00Z", 0.89),
        candidate("l-other", "LONG", "BBB", "2025-01-01T01:00:00Z", 0.80),
        candidate("s-near", "SHORT", "AAA", "2025-01-02T00:30:00Z", 0.99),
        candidate("s-other", "SHORT", "BBB", "2025-01-02T01:00:00Z", 0.90),
        candidate("s-third", "SHORT", "CCC", "2025-01-02T02:00:00Z", 0.80),
    ]
    selected = select_balanced_candidates(rows, spec=spec, existing_rows=existing)
    assert [row["event_id"] for row in selected["LONG"]] == ["l-far", "l-other"]
    assert [row["event_id"] for row in selected["SHORT"]] == ["s-other", "s-third"]
    audit = selection_audit(selected, spec=spec, existing_rows=existing)
    assert audit["sides"]["LONG"]["seeded_existing"] == 1
    assert audit["sides"]["LONG"]["combined_rows"] == 3
    assert audit["sides"]["LONG"]["max_per_symbol"] == 2


def test_future_mutation_leaves_every_causal_feature_exactly_unchanged() -> None:
    frame = synthetic_frame()
    row = {
        "event_id": "causal-proof",
        "anchor_time": frame["open_time"].iloc[230].isoformat(),
    }
    result = audit_future_invariance(frame.iloc[:250], row, profile=dense_l1())
    assert result["passed"] is True
    assert result["future_rows_mutated"] == 19
    assert result["max_abs_difference"] == 0.0
    assert result["gate_equal"] == {"long": True, "short": True}


def test_gallery_uses_path_relative_to_its_review_chart_sibling(tmp_path: Path) -> None:
    output = tmp_path / "results" / "index.html"
    rows = [
        {
            "review_path": "experiments/active/example/results/review_charts/one.png",
            "direction": "LONG",
            "symbol": "AAA_USDT_SWAP",
            "rank": 1,
            "anchor_time": "2025-01-01T00:00:00+00:00",
            "completed_score": 0.9,
            "release_close_signed_atr": 4.0,
            "release_favorable_atr": 6.0,
        }
    ]
    build_gallery(rows, output=output)
    document = output.read_text(encoding="utf-8")
    assert 'src="review_charts/one.png"' in document
    assert "candidate, not positive label" in document


def test_review_marker_can_move_without_changing_selection_anchor(tmp_path: Path) -> None:
    frame = synthetic_frame()
    anchor_i = 230
    row = {
        "event_id": "review-shift",
        "direction": "SHORT",
        "symbol": "AAA_USDT_SWAP",
        "rank": 1,
        "anchor_time": frame["open_time"].iloc[anchor_i].isoformat(),
        "source_anchor_i": anchor_i,
        "completed_score": 0.9,
        "formation_score": 0.8,
        "release_close_signed_atr": 4.0,
        "release_favorable_atr": 6.0,
        "ma_spread_before_pct": 0.5,
    }
    output = tmp_path / "shifted.png"
    meta = render_review_chart(
        frame,
        row,
        spec=replace(CandidateSpec(), review_marker_offset_bars=-3),
        output=output,
    )
    assert output.is_file()
    assert meta["review_marker_offset_bars"] == -3
    assert meta["review_marker_source_i"] == anchor_i - 3
    assert meta["review_marker_time"] == frame["open_time"].iloc[anchor_i - 3].isoformat()
    assert meta["review_marker_is_training_label"] is False


def test_prelaunch_audit_uses_only_fixed_prior_rows_and_anchor_body() -> None:
    frame = synthetic_frame(40)
    row = {
        "event_id": "prelaunch",
        "direction": "LONG",
        "symbol": "AAA_USDT_SWAP",
        "rank": 1,
        "anchor_time": frame["open_time"].iloc[20].isoformat(),
        "source_anchor_i": 20,
        "anchor_open": float(frame["open"].iloc[20]),
        "atr14_signal": 2.0,
    }
    metrics = prelaunch_metrics(frame, row)
    assert metrics["pre3_open_signed_atr"] == pytest.approx(
        (frame["open"].iloc[20] - frame["close"].iloc[17]) / 2.0
    )
    assert metrics["pre12_open_signed_atr"] == pytest.approx(
        (frame["open"].iloc[20] - frame["close"].iloc[8]) / 2.0
    )
    assert metrics["anchor_body_signed_atr"] == pytest.approx(
        (frame["close"].iloc[20] - frame["open"].iloc[20]) / 2.0
    )


def test_prelaunch_summary_requires_exact_balanced_thousand() -> None:
    with pytest.raises(CandidateCollectionError, match="expected 1000"):
        summarize([])


def test_prelaunch_summary_supports_a_preregistered_expansion_size() -> None:
    rows = [
        {
            "direction": side,
            "pre3_open_signed_atr": 0.5,
            "pre6_open_signed_atr": 1.5,
            "pre12_open_signed_atr": 2.5,
            "anchor_body_signed_atr": 1.2,
        }
        for side in ("LONG", "SHORT")
    ]
    result = summarize(rows, expected_total=2, expected_per_side=1)
    assert result["rows"] == 2
    assert result["sides"]["LONG"]["anchor_body_gt_1_atr"] == 1
