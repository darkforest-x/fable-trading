"""Synthetic contracts for the read-only frozen-interval retest diagnostic."""
import ast
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_retest_diagnostic as diagnostic


def tables(close=11.0, status="accepted", reason=None, next_hours=1, low=8.0):
    time = pd.Timestamp("2026-08-01T12:00Z")
    clock = time + pd.Timedelta(hours=next_hours)
    reason = reason or ("close_above_frozen_high" if status == "accepted" else "close_at_or_below_frozen_high")
    row = dict(instrument="OKX:TEST", asset="TEST", symbol="TEST/USDT:USDT", venue="okx", minutes=60,
        candidate_i=0, candidate_time=time, candidate_close=10.5, frozen_parent_high=10.,
        candidate_status=status, reason=reason, decision_i=1, decision_time=clock, decision_close=close,
        expected_decision_time=time+diagnostic.HOUR)
    state = pd.DataFrame(dict(decision_i=[0, 1], decision_time=[time, clock],
        v3_early=[True, False], v3_parent_i=[0, 0], v3_frozen_parent_high=[10., 10.],
        v3_prog_prior_low=[low, 9.5], v3_prog_prior_high=[10., 10.5],
        price_acceptance_candidate_i=[0, 0], price_acceptance_candidate_time=[time, time],
        price_acceptance_candidate_close=[10.5, 10.5], price_acceptance_frozen_parent_high=[10., 10.],
        price_acceptance_candidate_status=["pending", status],
        price_acceptance_reason=["awaiting_next_close", reason],
        price_acceptance_decision_i=[np.nan, 1], price_acceptance_decision_time=[pd.NaT, clock],
        price_acceptance_decision_close=[np.nan, close]))
    return pd.DataFrame([row]), state


@pytest.mark.parametrize("close,expected", [(11., "above"), (10., "inside"), (9., "inside"), (8., "inside"), (7., "below")])
def test_boundaries_are_inclusive_and_partition_decision_close(close, expected):
    status = "accepted" if close > 10 else "rejected"
    registry, state = tables(close, status)
    row = diagnostic.classify_rows(registry, state).iloc[0]
    assert row.location == expected
    assert row.classification_time == row.candidate_time + diagnostic.HOUR
    assert not row.available_at_candidate


def test_original_low_is_frozen_and_next_bar_rolling_low_is_irrelevant():
    registry, state = tables(9., "rejected")
    first = diagnostic.classify_rows(registry, state)
    assert first.iloc[0].location == "inside"  # 9 is below new rolling low9.5, but above original low8.
    state.loc[1, ["v3_prog_prior_low", "v3_prog_prior_high"]] = [1000., 2000.]
    pd.testing.assert_frame_equal(first, diagnostic.classify_rows(registry, state))
    assert first.iloc[0].interval_source_i == first.iloc[0].candidate_i == 0


@pytest.mark.parametrize("status,reason,delay,close", [
    ("unknown", "missing_next_hour", 2, 100.),
    ("unknown", "invalid_next_ohlc", 1, 100.),
    ("unknown", "invalid_next_ohlc", 1, np.nan),
])
def test_unavailable_adjudication_never_counts_as_accepted_or_rejected(status, reason, delay, close):
    registry, state = tables(close, status, reason, delay)
    row = diagnostic.classify_rows(registry, state).iloc[0]
    assert row.location == "unknown" and row.f_status == "unknown"
    assert row.location_reason == reason
    assert row.classification_time == row.candidate_time + pd.Timedelta(hours=delay)


def test_missing_end_bar_has_no_fabricated_decision_clock():
    registry, state = tables()
    state = state.iloc[:1].copy()
    registry.loc[0, ["candidate_status", "reason"]] = ["unknown", "no_next_bar_at_sample_end"]
    registry.loc[0, ["decision_i", "decision_close"]] = np.nan
    registry.loc[0, "decision_time"] = pd.NaT
    row = diagnostic.classify_rows(registry, state).iloc[0]
    assert row.location == "unknown" and pd.isna(row.classification_time)
    assert pd.notna(row.expected_decision_time)
    registry.loc[0, "decision_time"] = registry.loc[0, "expected_decision_time"]
    with pytest.raises(ValueError, match="fabricated clock"):
        diagnostic.classify_rows(registry, state)


@pytest.mark.parametrize("low", [np.nan, np.inf, -1., 12.])
def test_unavailable_original_interval_is_unknown_without_reinterpreting_f(low):
    registry, state = tables(low=low)
    row = diagnostic.classify_rows(registry, state).iloc[0]
    assert row.location == "unknown" and row.f_status == "accepted"
    assert row.location_reason == "unavailable_original_interval"


def test_candidate_high_must_match_original_row_exactly():
    registry, state = tables()
    registry.loc[0, "frozen_parent_high"] = 10.000001
    with pytest.raises(ValueError, match="frozen high differs"):
        diagnostic.classify_rows(registry, state)


@pytest.mark.parametrize("field,value", [("decision_time", pd.Timestamp("2026-08-01T12:00Z")),
    ("decision_i", 0), ("decision_close", 12.)])
def test_adjudication_must_match_actual_current_state_not_backdated_or_fabricated(field, value):
    registry, state = tables()
    registry.loc[0, field] = value
    with pytest.raises(ValueError, match="current state row|next stored bar"):
        diagnostic.classify_rows(registry, state)


def test_known_outcome_cannot_skip_missing_hour():
    registry, state = tables(next_hours=2)
    with pytest.raises(ValueError, match="missing hour"):
        diagnostic.classify_rows(registry, state)


def test_f_status_must_agree_with_its_existing_price_high_rule():
    registry, state = tables(close=9., status="accepted")
    with pytest.raises(ValueError, match="contradicts"):
        diagnostic.classify_rows(registry, state)


def test_candidate_partition_conserves_unknown_separately():
    frames = []
    for n, (close, status, reason) in enumerate([(11., "accepted", None), (9., "rejected", None),
                                               (7., "rejected", None), (12., "unknown", "invalid_next_ohlc")]):
        registry, state = tables(close, status, reason)
        frame = diagnostic.classify_rows(registry, state)
        frame["instrument"] = "asset" + str(n)
        frames.append(frame)
    classified = pd.concat(frames, ignore_index=True)
    result = diagnostic.position_summary(classified)
    assert result.candidates.tolist() == [1, 1, 1, 1]
    assert result.candidate_denominator.tolist() == [4]*4
    assert result.candidate_fraction.sum() == 1
    with pytest.raises(ValueError, match="Duplicate classified"):
        diagnostic.position_summary(pd.concat([classified, classified.iloc[[0]]]))


def test_classification_keeps_the_exact_original_signal_identity_time_and_price():
    registry, state = tables()
    classified = diagnostic.classify_rows(registry, state)
    signal = pd.DataFrame(dict(instrument=["OKX:TEST"], decision_i=[0],
        decision_time=[registry.candidate_time.iloc[0]], signal_close=[10.5], arm=["v3"], stage=["early"]))
    diagnostic.check_raw_candidates(classified, signal)
    signal.loc[0, "decision_time"] += diagnostic.HOUR
    with pytest.raises(ValueError, match="exact frozen original V3 event stream"):
        diagnostic.check_raw_candidates(classified, signal)
    signal.loc[0, "decision_time"] -= diagnostic.HOUR
    signal.loc[0, "decision_i"] = 1
    with pytest.raises(ValueError, match="exact frozen original V3 event stream"):
        diagnostic.check_raw_candidates(classified, signal)


def test_candidate_t_cannot_already_contain_next_close_information():
    registry, state = tables()
    state.loc[0, "price_acceptance_candidate_status"] = "accepted"
    with pytest.raises(ValueError, match="pending clock"):
        diagnostic.classify_rows(registry, state)


def anchor_tables():
    time = pd.Timestamp("2026-08-01T12:00Z")
    labels = pd.DataFrame(dict(instrument=["x", "x", "x", "x", "x"], event_i=[0, 4, 8, 12, 16],
        decision_time=[time+pd.Timedelta(hours=i) for i in (0, 4, 8, 12, 16)],
        label=["positive", "positive", "positive", "negative", "unknown"],
        large_peak=[True, True, True, False, False]))
    detections = pd.DataFrame(dict(instrument=["x"]*5, event_i=[0, 4, 8, 12, 16], arm=["v3"]*5,
        stage=["early"]*5, first_signal_lag=[0., 1., np.nan, np.nan, np.nan], hit_1=[True, True, False, False, False]))
    classified = pd.DataFrame(dict(instrument=["x", "x"], candidate_i=[0, 5],
        candidate_time=[time, time+5*diagnostic.HOUR], classification_time=[time+diagnostic.HOUR, time+6*diagnostic.HOUR],
        location=["inside", "above"], f_status=["rejected", "accepted"]))
    return labels, detections, classified


def test_old_timely_hit_joins_actual_original_t_not_later_acceptance():
    joined = diagnostic.join_anchors(*anchor_tables())
    assert len(joined) == 5
    assert joined.original_signal_i.iloc[:2].tolist() == [0., 5.]
    assert joined.timely_location.tolist() == ["inside", "above", "not_caught", "not_caught", "not_caught"]
    summary = diagnostic.label_position_summary(joined).set_index("location")
    assert summary.original_timely_denominator.eq(2).all()
    assert summary.original_large_denominator.eq(3).all()
    assert summary.loc["inside", "timely_positive"] == 1
    assert summary.loc["not_caught", "large_positive_anchors"] == 1
    assert summary.timely_positive.sum() == 2 and summary.large_positive_anchors.sum() == 3
    # No trade outcomes or wins are inferred from an old positive label.
    assert not any("win" in key or "return" in key or "profit" in key for key in summary)


def test_missing_detection_cannot_reduce_old_label_denominator():
    labels, detections, classified = anchor_tables()
    with pytest.raises(ValueError, match="fixed denominator"):
        diagnostic.join_anchors(labels, detections.iloc[:-1], classified)


def test_lag_two_cannot_be_presented_as_original_timely_hit():
    labels, detections, classified = anchor_tables()
    detections.loc[1, "first_signal_lag"] = 2
    with pytest.raises(ValueError, match="zero or one"):
        diagnostic.join_anchors(labels, detections, classified)


def test_missing_classification_cannot_reduce_1463_partition():
    labels, detections, classified = anchor_tables()
    with pytest.raises(ValueError, match="no original candidate"):
        diagnostic.join_anchors(labels, detections, classified.iloc[:1])


def test_old_positive_membership_does_not_follow_shifted_f_clock():
    labels, detections, classified = anchor_tables()
    classified["candidate_time"] += diagnostic.HOUR
    with pytest.raises(ValueError, match="original signal clock"):
        diagnostic.join_anchors(labels, detections, classified)


def test_source_hash_tampering_fails_before_use(tmp_path):
    path = tmp_path / "artifact.csv"
    path.write_text("frozen\n")
    digest, refs = diagnostic.sha(path), {}
    assert diagnostic.checked(path, digest, refs) == path.resolve()
    path.write_text("changed\n")
    with pytest.raises(ValueError, match="Changed authenticated input"):
        diagnostic.checked(path, digest, refs)


def test_exact_f_receipt_linkage_and_tampering(tmp_path, monkeypatch):
    root, source = tmp_path, tmp_path / "frozen"
    source.mkdir()
    ref = root / "source.py"
    ref.write_text("# frozen source\n")
    artifacts = []
    for name in ("matching.json", "candidate_registry.csv.gz", "signals.csv.gz", "labels.csv.gz", "detections.csv.gz"):
        path = source / name
        path.write_bytes(b"not parsed by authentication\n")
        artifacts.append(diagnostic.artifact(path))
    prepared = dict(status="complete", config=dict(arms=["v3", "price_acceptance"]),
        source_pins={"source.py": diagnostic.sha(ref)}, sources={}, artifacts=artifacts)
    pp = source / "prepared_manifest.json"
    diagnostic.write_json(pp, prepared)
    validation = dict(status="complete", config=prepared["config"], source_pins=prepared["source_pins"],
        prepared_sha=diagnostic.sha(pp), artifacts=[])
    vp = source / "validation_manifest.json"
    diagnostic.write_json(vp, validation)
    monkeypatch.setattr(diagnostic, "ROOT", root)
    monkeypatch.setattr(diagnostic, "F_SOURCE", source)
    monkeypatch.setattr(diagnostic, "PREPARED_SHA", diagnostic.sha(pp))
    monkeypatch.setattr(diagnostic, "VALIDATION_SHA", diagnostic.sha(vp))
    # Invalid CSV bytes deliberately prove authentication does not parse labels/outcomes.
    p, v, refs = diagnostic.authenticate()
    assert p == prepared and v == validation and len(refs) == 8
    (source / "labels.csv.gz").write_bytes(b"tampered\n")
    with pytest.raises(ValueError, match="Changed authenticated"):
        diagnostic.authenticate()


def test_preflight_exact_sources_must_be_passed_reviewed_and_committed(tmp_path, monkeypatch):
    root = tmp_path
    builder = root / "yoyo/evaluation/builder.py"
    test = root / "tests/test_spike_burst_v3_retest_diagnostic.py"
    exp = root / "experiment"
    plan, qa = exp / "PROJECT_PLAN.md", exp / "qa/preflight_review.json"
    for path in (builder, test, plan, qa): path.parent.mkdir(parents=True, exist_ok=True)
    for path in (builder, test, plan): path.write_text("review me\n")
    pins = {str(path.relative_to(root)): diagnostic.sha(path) for path in (builder, test, plan)}
    qa.write_text(json.dumps(dict(status="passed", source_sha256=pins)))
    monkeypatch.setattr(diagnostic, "ROOT", root)
    monkeypatch.setattr(diagnostic, "EXPERIMENT", exp)
    monkeypatch.setattr(diagnostic, "__file__", str(builder))
    monkeypatch.setattr(diagnostic.subprocess, "check_output", lambda command, cwd: (root / command[-1][5:]).read_bytes())
    assert len(diagnostic.source_pins()) == 4
    test.write_text("changed after review\n")
    with pytest.raises(ValueError, match="did not review exact source"):
        diagnostic.source_pins()
    monkeypatch.setattr(diagnostic.subprocess, "check_output", lambda command, cwd: b"uncommitted")
    with pytest.raises(ValueError, match="Commit exact source"):
        diagnostic.source_pins()


def test_frozen_classification_is_required_before_labels_can_be_joined(tmp_path, monkeypatch):
    receipt_path = tmp_path / "classification_manifest.json"
    receipt = dict(status="complete", source_pins={}, config=diagnostic.CONFIG, labels_parsed=False,
        prepared_sha=diagnostic.PREPARED_SHA, validation_sha=diagnostic.VALIDATION_SHA)
    diagnostic.write_json(receipt_path, receipt)
    digest = diagnostic.sha(receipt_path)
    monkeypatch.setattr(diagnostic, "source_pins", lambda: {})
    monkeypatch.setattr(diagnostic, "authenticate", lambda: pytest.fail("must fail before reading F inputs"))
    receipt_path.write_text(json.dumps(dict(receipt, labels_parsed=True)))
    with pytest.raises(ValueError, match="Changed authenticated"):
        diagnostic.join(tmp_path, digest)
    with pytest.raises(ValueError, match="completed pre-label"):
        diagnostic.join(tmp_path, diagnostic.sha(receipt_path))


def test_products_refuse_overwrite_and_hash_all_written_bytes(tmp_path):
    frame = pd.DataFrame(dict(location=["inside"], candidates=[1]))
    output = diagnostic.save_products(tmp_path, {"classification.csv.gz": frame})
    assert len(output) == 1 and output[0]["sha256"] == diagnostic.sha(output[0]["path"])
    with pytest.raises(FileExistsError):
        diagnostic.save_products(tmp_path, {"classification.csv.gz": frame})


def test_static_builder_never_imports_or_invokes_market_detection_or_scoring():
    path = Path(diagnostic.__file__)
    tree = ast.parse(path.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any("yoyo" in (name or "") for name in imports)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not {"detect", "simulate_trade", "score_job", "load_feature", "fetch_ohlcv"} & calls
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    classify_text = ast.get_source_segment(path.read_text(), functions["classify"])
    assert '"labels.csv.gz"' not in classify_text and '"detections.csv.gz"' not in classify_text
    assert 'labels_parsed=False' in classify_text
    assert diagnostic.COUNTS == dict(candidates=10386, source_labels=14904, anchors=8046,
        positive=1660, large_positive=947, timely_positive=1463)
