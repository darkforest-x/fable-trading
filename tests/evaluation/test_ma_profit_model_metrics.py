"""Focused full-pool and no-leakage checks for MA-profit model metrics."""
from __future__ import annotations

import pytest

from yoyo.evaluation.ma_profit_model_metrics import ProfitMetricError, evaluate_events, join_events


BOX = {"cx_norm": .5, "cy_norm": .5, "w_norm": .2, "h_norm": .2}


def ledger(event_id, retained, direction="LONG", quality=.5):
    return {"event_id": event_id, "split": "val", "direction": direction, "bar_minutes": 15, "canonical_asset": "BTC", "core_end_time": "2026-02-01T00:00:00+00:00", "quality_score": quality, "profit": {"retained": retained, "outcome": "TP" if retained else "SL", "gross_r": 3. if retained else -1., "net_r": 2.9 if retained else -1.1, "risk_price": 2., "entry_price": 100.}}


def manifest(event_id, retained):
    return {"event_id": event_id, "split": "val", "direction": "LONG", "bar_minutes": 15, "canonical_asset": "BTC", "core_end_time": "2026-02-01T00:00:00+00:00", "class_id": 0 if retained else None, "arms": ["A", "B"], "variant": "A", "image_path": f"images/val/{event_id}.png", "image_sha256": event_id * 8, "box": BOX if retained else None}


def prediction(event_id, boxes):
    return {"event_id": event_id, "split": "val", "image_path": f"images/val/{event_id}.png", "image_sha256": event_id * 8, "boxes": boxes}


def box(class_id, confidence, **geometry):
    return {"class_id": class_id, "confidence": confidence, **(BOX | geometry)}


def test_full_pool_failure_fire_and_box_level_duplicates_are_event_metrics():
    rows = [ledger("winner", True, quality=.9), ledger("failure", False, quality=.1)]
    manifests = [manifest("winner", True), manifest("failure", False)]
    predictions = [prediction("winner", [box(0, .4), box(0, .9)]), prediction("failure", [box(0, .6)])]
    events = join_events(rows, manifests, predictions, split="val")
    report = evaluate_events(events)["model"]
    assert len(events) == 2 and next(event for event in events if event["event_id"] == "winner")["score"] == .9
    d = report["detection"]
    assert (d["tp"], d["fp"], d["fn"]) == (1, 2, 0)
    assert d["false_positive_boxes"] == 2 and d["event_precision"] == pytest.approx(1/3)
    assert d["event_recall"] == d["failure_trigger_rate"] == d["failure_same_direction_trigger_rate"] == 1.
    assert report["roc_auc"] == 1. and report["top10"]["events"] == 1


def test_missing_image_and_wrong_direction_are_not_silently_scored_as_hits():
    rows, manifests = [ledger("winner", True)], [manifest("winner", True)]
    with pytest.raises(ProfitMetricError, match="missing prediction"):
        join_events(rows, manifests, [], split="val")
    events = join_events(rows, manifests, [prediction("winner", [box(1, .99)])], split="val")
    metric = evaluate_events(events)["model"]
    assert events[0]["score"] == 0. and not events[0]["hit"]
    assert metric["detection"]["fp"] == 1 and metric["detection"]["fn"] == 1
    assert metric["roc_auc"] is None and metric["ranking_permutation"]["status"] == "not_identifiable"


def test_wrong_location_box_is_an_event_miss_and_false_positive():
    events = join_events([ledger("winner", True)], [manifest("winner", True)], [prediction("winner", [box(0, .9, cx_norm=.1)])], split="val")
    detection = evaluate_events(events)["model"]["detection"]
    assert not events[0]["hit"] and detection["tp"] == 0 and detection["fp"] == 1 and detection["fn"] == 1


def test_missing_quality_score_marks_baseline_incomplete_but_keeps_model_metrics():
    rows = [ledger("one", True, quality=None), ledger("two", False, quality=.2)]
    manifests = [manifest("one", True), manifest("two", False)]
    predictions = [prediction("one", [box(0, .8)]), prediction("two", [])]
    report = evaluate_events(join_events(rows, manifests, predictions, split="val"))
    assert report["model"]["status"] == "ok"
    assert report["quality_score_baseline"] == {"status": "not_computable_missing_score", "missing_event_ids": ["one"]}


def test_bad_geometry_and_identity_fail_even_for_a_negative_event():
    with pytest.raises(ProfitMetricError, match="invalid normalized box"):
        join_events([ledger("x", False)], [manifest("x", False)], [prediction("x", [box(0, .01, w_norm=-1)])], split="val")
    bad = manifest("x", False) | {"direction": "SHORT"}
    with pytest.raises(ProfitMetricError, match="direction mismatch"):
        join_events([ledger("x", False)], [bad], [prediction("x", [])], split="val")


def test_constant_score_top10_is_marked_arbitrary_and_mixed_splits_refused():
    events = join_events([ledger("a", True), ledger("b", False)], [manifest("a", True), manifest("b", False)], [prediction("a", []), prediction("b", [])], split="val")
    report = evaluate_events(events)["model"]
    assert report["top10"]["selection_status"] == "constant_score_arbitrary_id_tiebreak"
    assert report["ranking_permutation"]["status"] == "not_identifiable"
    with pytest.raises(ProfitMetricError, match="separately"):
        evaluate_events([events[0], events[1] | {"split": "test"}])
