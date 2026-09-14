"""Synthetic contracts for the read-only Owner-gold indicator bridge."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.evaluation import owner_gold_indicator_bridge as bridge


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _source(tmp_path):
    times = pd.date_range("2025-01-01T00:00:00Z", periods=131, freq="15min")
    close = 100.0 + np.arange(len(times)) * 0.01
    raw = pd.DataFrame({"open_time": times, "open": close - 0.1, "high": close + 0.2,
                        "low": close - 0.2, "close": close, "volume": 1.0})
    csv = tmp_path / "source.csv"
    pd.DataFrame({"ts": times.astype("int64") // 1_000_000, "open": raw["open"], "high": raw["high"],
                  "low": raw["low"], "close": raw["close"], "volume": raw["volume"]}).to_csv(csv, index=False)
    return raw, csv


def _manifest(raw, *, review_id="review-1", source_path="source.csv", start=120, end=124):
    window = add_six_mas(raw).iloc[start:end + 1]
    return {"review_id": review_id, "source_path": source_path, "main_start_i": start, "main_end_i": end,
            "main_start_time": raw.iloc[start]["open_time"].isoformat(),
            "main_end_time": raw.iloc[end]["open_time"].isoformat(),
            "chart_transform": {"width": 1280, "height": 742, "left": 12, "plot_w": 1256, "n_bars": 5},
            "main_window_ohlcv_ma_sha256": bridge.window_sha256(window)}


def _answer(*, review_id="review-1", status="owner_boxes", kind="annotation", boxes=None):
    return {"annotation_id": 7, "task_id": 9, "review_id": review_id, "record_kind": kind,
            "effective_answer": True, "event_conflict": False, "status": status,
            "effective_boxes": boxes if boxes is not None else [], "raw_answer": {"was_cancelled": False}}


def _rectangle(x, width):
    return {"label": "多头", "x": x, "width": width, "y": 10.0, "height": 20.0,
            "rotation": 0, "image_rotation": 0, "original_width": 1280, "original_height": 742}


def test_geometry_round_trip_selects_inclusive_candle_centers_and_preserves_rectangle(tmp_path):
    raw, _ = _source(tmp_path)
    manifest = _manifest(raw)
    # The fixed transform centers are 12, 326, 640, 954, 1268.  The exact
    # endpoints must select both centers 326 and 640.
    rectangle = _rectangle(326 / 1280 * 100, (640 - 326) / 1280 * 100)
    answers, manifests = tmp_path / "answers.jsonl", tmp_path / "manifest.jsonl"
    _write_jsonl(answers, [_answer(boxes=[rectangle])])
    _write_jsonl(manifests, [manifest])

    cases, exclusions = bridge.load_cases(answers, manifests)

    assert exclusions == []
    assert len(cases) == 1
    case = cases[0]
    assert (case["core_start_i"], case["core_end_i"]) == (121, 122)
    assert (case["primary_start_i"], case["primary_end_i"]) == (121, 122)
    assert case["side"] == "long"
    assert case["original_rectangle"] == rectangle


def test_predictions_are_excluded_and_no_target_ignores_inherited_boxes(tmp_path):
    raw, _ = _source(tmp_path)
    answers, manifests = tmp_path / "answers.jsonl", tmp_path / "manifest.jsonl"
    _write_jsonl(manifests, [_manifest(raw, review_id="prediction"), _manifest(raw, review_id="no-target")])
    inherited = _rectangle(50, 1)
    prediction = _answer(review_id="prediction", kind="prediction", boxes=[inherited])
    no_target = _answer(review_id="no-target", status=bridge.NO_TARGET_STATUS, boxes=[])
    no_target["raw_boxes"] = [inherited]
    _write_jsonl(answers, [prediction, no_target])

    cases, exclusions = bridge.load_cases(answers, manifests)

    assert [row["reason"] for row in exclusions] == ["not_annotation"]
    assert len(cases) == 1
    case = cases[0]
    assert case["case_kind"] == "owner_no_target"
    assert case["core_start_i"] is None and case["core_end_i"] is None
    assert (case["primary_start_i"], case["primary_end_i"]) == (120, 124)


def test_invalid_second_rectangle_rejects_the_entire_annotation(tmp_path):
    raw, _ = _source(tmp_path)
    answers, manifests = tmp_path / "answers.jsonl", tmp_path / "manifest.jsonl"
    _write_jsonl(manifests, [_manifest(raw)])
    valid = _rectangle(50, 1)
    invalid = {**_rectangle(50, 1), "rotation": 1}
    _write_jsonl(answers, [_answer(boxes=[valid, invalid])])

    cases, exclusions = bridge.load_cases(answers, manifests)

    assert cases == []
    assert exclusions[0]["reason"] == "invalid_annotation_or_manifest"


def test_load_source_rejects_holdout_case_before_opening_source(tmp_path, monkeypatch):
    raw, _ = _source(tmp_path)
    case = {**_manifest(raw), "main_end_time": "2026-05-04T00:00:00+00:00"}
    opened = False

    def forbidden(*args, **kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("source reader must not be reached")

    monkeypatch.setattr(bridge, "read_preholdout_prefix", forbidden)
    with pytest.raises(bridge.OwnerGoldBridgeError, match="before source load"):
        bridge.load_source([case], root=tmp_path)
    assert not opened


def test_load_source_fails_closed_on_main_window_digest_mismatch(tmp_path):
    raw, _ = _source(tmp_path)
    case = _manifest(raw)
    case["main_window_ohlcv_ma_sha256"] = "0" * 64

    with pytest.raises(bridge.OwnerGoldBridgeError, match="digest mismatch"):
        bridge.load_source([case], root=tmp_path)
