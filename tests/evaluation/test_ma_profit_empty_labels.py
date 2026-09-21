"""A fully unmatched control pool is an empty result, not a labeler crash."""
import json

from yoyo.datasets import ma_profit_pipeline as pipeline


def test_empty_frozen_controls_produce_zero_count_honest_receipt(tmp_path, monkeypatch):
    plan, events, sources = (tmp_path/name for name in ("plan.json", "events.jsonl", "sources.json"))
    plan.write_text(json.dumps({"label_contract": {"target_r": 3., "round_trip_cost": .002}}))
    events.write_text("")
    sources.write_text('{"sources": []}')
    monkeypatch.setattr(pipeline, "committed", lambda paths: "test_fixture_only")
    monkeypatch.setattr(pipeline, "read_source", lambda *args: (_ for _ in ()).throw(AssertionError("empty controls must not read prices")))
    out = tmp_path/"out"
    result = pipeline.label(plan, events, sources, out)
    assert result["events"] == result["retained_before_purge"] == result["lineage_errors"] == 0
    assert result["outcomes"] == result["all_by_split"] == result["retained_by_split"] == {}
    assert result["gross_r_target"] == 3. and result["cost"] == .002
    assert (out/"outcomes.jsonl").read_text() == (out/"lineage_errors.jsonl").read_text() == ""
    assert result["outcomes_sha256"] == pipeline.digest(out/"outcomes.jsonl")
