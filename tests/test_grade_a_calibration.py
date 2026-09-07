"""Safety and evidence tests for the event-level Owner calibration workflow."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from yoyo.datasets import grade_a_calibration as c


def candidate(i=0, **changes):
    start = c.instant("2023-01-01T00:00:00Z") + timedelta(days=i)
    return {"event_id": f"e{i}", "window_start_time": start.isoformat(),
            "window_end_time": (start+17*c.BAR).isoformat(), "timeframe": "15m",
            "window_len": "18", "window_start_i": "0", "window_end_i": "17",
            "exchange_symbol": "AAVEUSDT", "novelty_status": "new_event_review",
            "review_bucket": "candidate_positive", "model_direction": "LONG", **changes}


def test_cross_venue_join_preserves_token_multipliers():
    assert c.symbol("AAVE_USDT_SWAP") == c.symbol("AAVEUSDT")
    assert c.symbol("1000PEPEUSDT") != c.symbol("PEPE_USDT_SWAP")


def test_union_protects_negative_and_opposite_direction_members(tmp_path):
    start = "2023-01-01T00:00:00+00:00"
    row = {"window_start_time": start, "window_end_time": start,
           "dependency_end_time": "2023-01-01T01:00:00+00:00",
           "sample_kind": "negative", "exchange_symbol": "AAVE_USDT_SWAP"}
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(row)+"\n")
    intervals = c.membership_intervals(path, 150)
    eligible, audit = c.audit_population([candidate()], intervals, c.instant("2025-11-01T23:45:00Z"))
    assert eligible == []
    assert audit[0]["excluded_by"] == ["cross_venue_any_class_member_dependency_plus150"]


def test_interval_union_does_not_bridge_a_real_gap():
    t = c.instant("2023-01-01T00:00:00Z")
    intervals = [(t,t+c.BAR),(t+5*c.BAR,t+6*c.BAR)]
    assert not c.overlaps(intervals,t+2*c.BAR,t+3*c.BAR)
    assert c.overlaps(intervals,t+c.BAR,t+2*c.BAR)


@pytest.mark.parametrize("field,value",[("window_end_time","2026-05-04T00:00:00Z"),
    ("window_end_i","19"),("timeframe","4h"),("window_start_time","2023-01-01T00:00:00")])
def test_bad_time_and_index_contracts_fail_before_pixels(field,value):
    with pytest.raises(ValueError):
        c.audit_population([candidate(**{field:value})], {}, c.instant("2025-11-01T23:45:00Z"))


def test_sampling_is_order_invariant_and_never_uses_returns_or_scores():
    rows = []
    for b,bucket in enumerate(("candidate_positive","candidate_hard_negative")):
        for d,direction in enumerate(("LONG","SHORT")):
            for i in range(80):
                rows.append(candidate(i, event_id=f"{b}-{d}-{i}", exchange_symbol=f"TOKEN{b}{d}USDT",
                                      review_bucket=bucket, model_direction=direction))
    a,_ = c.select(rows,7)
    altered = [{**row,"confidence":"0.999","daily_return":"100000"} for row in reversed(rows)]
    b,_ = c.select(altered,7)
    assert [r["event_id"] for r in a] == [r["event_id"] for r in b]
    assert len(a) == len({r["event_id"] for r in a}) == 240
    order = c.blind_order(a,7)
    positions = {r["review_id"]:i for i,r in enumerate(order)}
    repeated = [r for r in order if not r["is_primary"]]
    assert len(order) == 276 and len(repeated) == 36
    assert min(positions[r["review_id"]]-positions[r["repeat_of_review_id"]] for r in repeated) >= 60


def answer(**changes):
    return {"review_id":"a", "label":"LONG", "core_start":3, "core_end":6,
        "box_top_norm":.2, "box_bottom_norm":.4, "box_semantics":"CORE_WICKS_MA",
        "answered_at":"2026-09-07T12:00:00+08:00", "reasons":[], "note":"", **changes}


@pytest.mark.parametrize("changes",[{"core_start":True},{"core_end":19},
    {"core_start":8},{"box_top_norm":float("nan")},{"box_top_norm":.6},
    {"box_semantics":None},{"label":"NO_SIGNAL"},{"reasons":["auto_gold"]}])
def test_saved_bad_answers_cannot_be_scored(changes):
    with pytest.raises(ValueError):
        c.validate_answer(answer(**changes),18)


def test_drafts_and_constant_label_kappa_are_not_fake_passes():
    assert c.validate_answer(answer(answered_at=None,core_start=None),18) is False
    assert c.kappa([]) is None
    assert c.kappa([("LONG","LONG")]*3) is None
    assert c.kappa([("LONG","LONG"),("NO_SIGNAL","NO_SIGNAL")]) == 1.0


def make_pack(tmp_path):
    items = [{"review_id":rid,"n_bars":18,"image":f"images/{rid}.png"} for rid in ("a","b")]
    c.dump(tmp_path/"public/manifest.json", {"schema_version":1,"pack_id":"test","items":items})
    c.jsonl(tmp_path/"admin/truth.jsonl", [
        {"review_id":"a","is_primary":True,"repeat_of_review_id":None},
        {"review_id":"b","is_primary":False,"repeat_of_review_id":"a"}])
    return {"schema_version":1,"pack_id":"test","manifest_sha256":c.sha(tmp_path/"public/manifest.json"),
            "answers":[answer(),answer(review_id="b")]}


def test_scoring_complete_and_partial_never_mutates_training_labels(tmp_path):
    export=make_pack(tmp_path)
    result=c.score(export,tmp_path)
    assert result["complete"] and result["repeat_agreement"] == 1
    assert result["training_eligible"] is False and result["label_mutation_performed"] is False
    assert result["same_direction_positive_repeat_geometry"][0]["core_equal"]
    export["answers"][1]["answered_at"]=None
    result=c.score(export,tmp_path)
    assert result["missing_or_draft"] == 1 and result["draft_answers"] == 1
    assert not result["complete"] and result["repeat_agreement"] is None


@pytest.mark.parametrize("kind",["duplicate","foreign","manifest"])
def test_answers_cannot_be_spliced_from_another_pack(tmp_path,kind):
    export=make_pack(tmp_path)
    if kind == "duplicate":export["answers"].append(answer())
    elif kind == "foreign":export["answers"][0]["review_id"]="foreign"
    else:export["manifest_sha256"]="0"*64
    with pytest.raises(ValueError):c.score(export,tmp_path)


def test_public_template_has_no_model_proposal_fields():
    template=(c.ROOT/"yoyo/datasets/templates/grade_a_calibration.html").read_text()
    assert template.count("__PACK_JSON__") == 1
    for field in ("source_direction","repeat_of_review_id","daily_return","event_peak_confidence","source_proposal_bucket"):
        assert field not in template


def test_local_snapshots_are_append_only_and_restore_actual_answers(tmp_path):
    export=make_pack(tmp_path)
    export["exported_at"]="2026-09-07T12:00:00+08:00"
    a=c.save_snapshot(export,tmp_path)
    b=c.save_snapshot(export,tmp_path)
    assert a["saved_answers"]==2 and a["total"]==2
    assert a["filename"]!=b["filename"]
    assert json.loads((tmp_path/"answers"/a["filename"]).read_text())==export
    assert len(list((tmp_path/"answers").glob("answers_*.json")))==2


@pytest.mark.parametrize("stamp",["2026-09-07T12:00:00","2026-02-30T12:00:00Z"])
def test_bad_import_dates_cannot_be_saved_as_completed(tmp_path,stamp):
    export=make_pack(tmp_path)
    export["exported_at"]="2026-09-07T12:00:00+08:00"
    export["answers"][0]["answered_at"]=stamp
    with pytest.raises(ValueError):c.save_snapshot(export,tmp_path)
    assert not (tmp_path/"answers").exists()
