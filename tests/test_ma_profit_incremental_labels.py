"""Regression coverage for byte-identical incremental MA-profit labelling."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

import yoyo.datasets.ma_profit_incremental_labels as incremental
import yoyo.datasets.ma_profit_pipeline as pipeline


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=incremental.ROOT, text=True).strip()


def source(tmp_path: Path, name: str = "TEST") -> Path:
    path = tmp_path / f"okx_{name}_USDT_SWAP_3m_1320.csv"
    times = pd.date_range("2025-01-01T00:00:00Z", periods=1320, freq="3min")
    with path.open("w") as handle:
        handle.write("open_time,open,high,low,close,volume\n")
        for index, stamp in enumerate(times):
            price = 100 + index * 0.01
            handle.write(f"{stamp.isoformat()},{price},{price + .4},{price - .4},{price + .1},1\n")
    return path


def event(path: Path, start: int, *, event_id: str, cluster: str, extra: str = "old") -> dict:
    times = pd.date_range("2025-01-01T00:00:00Z", periods=1320, freq="3min")
    return {"event_id": event_id, "cluster_id": cluster, "nms_note": extra, "source_path": str(path),
            "bar_minutes": 3, "direction": "LONG", "core_start_time": times[start].isoformat(),
            "core_end_time": times[start + 3].isoformat(), "core_bars": 4}


def files(tmp_path: Path, rows: list[dict], source_path: Path, *, target_r: float = 3.0, name: str = "") -> tuple[Path, Path, Path]:
    plan = tmp_path / f"plan{name}.json"
    plan.write_text(json.dumps({"discovery": {"data_end_exclusive": "2026-01-01T00:00:00Z"},
        "label_contract": {"confirmation_bars": 5, "horizon_hours": 1, "target_r": target_r, "round_trip_cost": .002},
        "splits": {"train_end_exclusive": "2026-01-01T00:00:00Z", "validation_end_exclusive": "2027-01-01T00:00:00Z", "test_end_exclusive": "2028-01-01T00:00:00Z"}}))
    events = tmp_path / f"events{name}.jsonl"
    events.write_text("".join(json.dumps(row) + "\n" for row in rows))
    sources = tmp_path / f"sources{name}.json"
    sources.write_text(json.dumps([{"source_path": str(source_path), "sha256": sha(source_path)}]))
    return plan, events, sources


@pytest.fixture(autouse=True)
def formal_guard(monkeypatch: pytest.MonkeyPatch):
    # Formal main/clean enforcement is exercised by the existing pipeline; the
    # fixtures deliberately live outside the repository.  Resolver/reader stay real.
    monkeypatch.setattr(incremental, "formal_guard", lambda _paths: head())
    monkeypatch.setattr(pipeline, "committed", lambda _paths: head())


def run(plan: Path, events: Path, sources: Path, output: Path, reuse: Path | None = None, **kwargs) -> dict:
    return incremental.label_incremental(plan, events, sources, output, reuse_label_dir=reuse, **kwargs)


def test_mixed_hits_are_byte_identical_and_keep_new_nms_metadata(tmp_path: Path):
    csv = source(tmp_path)
    first = event(csv, 1250, event_id="old-id", cluster="old-cluster")
    third = event(csv, 1270, event_id="old-third", cluster="old-third")
    plan, old_events, sources = files(tmp_path, [first, third], csv)
    cache = tmp_path / "cache"
    run(plan, old_events, sources, cache)

    changed = {**first, "event_id": "new-id", "cluster_id": "new-cluster", "nms_note": "new metadata"}
    second = event(csv, 1260, event_id="second", cluster="second-cluster", extra="new")
    changed_third = {**third, "event_id": "new-third", "cluster_id": "new-third", "nms_note": "new metadata"}
    plan, current_events, sources = files(tmp_path, [changed, second, changed_third], csv, name="-current")
    fresh, reused = tmp_path / "fresh", tmp_path / "reused"
    pipeline.label(plan, current_events, sources, fresh)
    summary = run(plan, current_events, sources, reused, cache)

    assert (fresh / "outcomes.jsonl").read_bytes() == (reused / "outcomes.jsonl").read_bytes()
    assert (fresh / "lineage_errors.jsonl").read_bytes() == (reused / "lineage_errors.jsonl").read_bytes()
    assert summary["reuse"]["status"] == "accepted"
    assert (summary["reuse"]["hits"], summary["reuse"]["misses"]) == (2, 1)
    assert json.loads((reused / "outcomes.jsonl").read_text().splitlines()[0])["cluster_id"] == "new-cluster"


def test_changed_source_bytes_reject_cache(tmp_path: Path):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "cache"
    run(plan, events, sources, cache)
    csv.write_text(csv.read_text().replace("100.0", "100.01", 1))
    plan, events, sources = files(tmp_path, [row], csv)
    summary = run(plan, events, sources, tmp_path / "out", cache)
    assert summary["reuse"]["status"] == "rejected"
    assert "frozen sources file drifted" in summary["reuse"]["reason"]


@pytest.mark.parametrize("change", ["plan", "implementation"])
def test_changed_plan_or_semantics_reject_cache(tmp_path: Path, change: str):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "cache"
    run(plan, events, sources, cache)
    if change == "plan":
        plan, events, sources = files(tmp_path, [row], csv, target_r=2.0)
    else:
        summary_path = cache / "summary.json"
        summary = json.loads(summary_path.read_text())
        summary["original_implementation"]["resolver"] = "0" * 64
        summary_path.write_text(json.dumps(summary))
    summary = run(plan, events, sources, tmp_path / f"out-{change}", cache)
    assert summary["reuse"]["status"] == "rejected"


def test_historical_pre_continuity_cache_is_rejected_with_receipt(tmp_path: Path):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "old-cache"
    cache.mkdir()
    (cache / "outcomes.jsonl").write_text("")
    (cache / "lineage_errors.jsonl").write_text("")
    (cache / "summary.json").write_text(json.dumps({"builder_commit": "627705ee88a2927ace6f5f1b1896915ca68def28",
        "outcomes_sha256": sha(cache / "outcomes.jsonl"), "input_events_sha256": sha(events),
        "source_manifest_sha256": sha(sources), "plan_sha256": sha(plan), "lineage_errors": 0}))
    summary = run(plan, events, sources, tmp_path / "out", cache)
    assert summary["reuse"]["status"] == "rejected"
    assert "pipeline implementation differs" in summary["reuse"]["reason"]
    assert json.loads((tmp_path / "out" / "reuse_receipt.json").read_text())["status"] == "rejected"


def test_current_original_labeler_output_is_a_safe_same_input_seed_cache(tmp_path: Path):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "pipeline-cache"
    pipeline.label(plan, events, sources, cache)
    result = run(plan, events, sources, tmp_path / "out", cache)
    assert result["reuse"]["status"] == "accepted"
    assert result["reuse"]["implementation_evidence"] == "builder_commit_derived"
    assert result["reuse"]["legacy_input_binding"] is True
    assert result["reuse"]["hits"] == 1 and result["reuse"]["misses"] == 0


def test_expanded_manifest_reuses_only_old_source_event_with_byte_parity(tmp_path: Path):
    old_csv, new_csv = source(tmp_path, "OLD"), source(tmp_path, "NEW")
    old = event(old_csv, 1250, event_id="old", cluster="old")
    plan, old_events, old_sources = files(tmp_path, [old], old_csv, name="-old")
    cache = tmp_path / "cache"
    run(plan, old_events, old_sources, cache)

    old_changed = {**old, "event_id": "old-expanded", "cluster_id": "old-expanded", "nms_note": "new cluster metadata"}
    new = event(new_csv, 1250, event_id="new", cluster="new")
    current_events = tmp_path / "events-expanded.jsonl"
    current_events.write_text("".join(json.dumps(row) + "\n" for row in (old_changed, new)))
    current_sources = tmp_path / "sources-expanded.json"
    current_sources.write_text(json.dumps([
        {"source_path": str(old_csv), "sha256": sha(old_csv)},
        {"source_path": str(new_csv), "sha256": sha(new_csv)},
    ]))
    fresh, reused = tmp_path / "fresh", tmp_path / "reused"
    pipeline.label(plan, current_events, current_sources, fresh)
    result = run(plan, current_events, current_sources, reused, cache)
    assert result["reuse"]["status"] == "accepted"
    assert (result["reuse"]["hits"], result["reuse"]["misses"]) == (1, 1)
    assert (fresh / "outcomes.jsonl").read_bytes() == (reused / "outcomes.jsonl").read_bytes()
    assert (fresh / "lineage_errors.jsonl").read_bytes() == (reused / "lineage_errors.jsonl").read_bytes()


def test_expanded_events_can_use_explicit_original_labeler_inputs(tmp_path: Path):
    csv = source(tmp_path)
    old = event(csv, 1250, event_id="old", cluster="old")
    plan, old_events, old_sources = files(tmp_path, [old], csv, name="-old")
    cache = tmp_path / "pipeline-cache"
    pipeline.label(plan, old_events, old_sources, cache)
    current = tmp_path / "events-expanded.jsonl"
    current.write_text("".join(json.dumps(row) + "\n" for row in (old, event(csv, 1260, event_id="new", cluster="new"))))
    result = run(plan, current, old_sources, tmp_path / "out", cache,
                 reuse_events_path=old_events, reuse_sources_path=old_sources)
    assert result["reuse"]["status"] == "accepted"
    assert (result["reuse"]["hits"], result["reuse"]["misses"]) == (1, 1)


def test_tampered_cached_positive_is_never_reused(tmp_path: Path):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "cache"
    run(plan, events, sources, cache)
    outcome = json.loads((cache / "outcomes.jsonl").read_text())
    outcome["profit"]["retained"] = True
    (cache / "outcomes.jsonl").write_text(json.dumps(outcome) + "\n")
    summary_path = cache / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["outcomes_sha256"] = sha(cache / "outcomes.jsonl")
    summary_path.write_text(json.dumps(summary))
    result = run(plan, events, sources, tmp_path / "out", cache)
    assert result["reuse"]["status"] == "rejected"
    assert "retained flag" in result["reuse"]["reason"]


@pytest.mark.parametrize("tamper, expected", [
    ("missing_outcome", "completely cover"), ("duplicate_outcome", "not unique"), ("lineage_error", "old lineage errors"),
])
def test_cache_rejects_incomplete_duplicate_or_lineage_error_history(tmp_path: Path, tamper: str, expected: str):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "cache"
    run(plan, events, sources, cache)
    outcomes_path, errors_path, summary_path = cache / "outcomes.jsonl", cache / "lineage_errors.jsonl", cache / "summary.json"
    summary = json.loads(summary_path.read_text())
    original = outcomes_path.read_text()
    if tamper == "missing_outcome":
        outcomes_path.write_text("")
        summary["outcomes_sha256"] = sha(outcomes_path)
    elif tamper == "duplicate_outcome":
        outcomes_path.write_text(original + original)
        summary["outcomes_sha256"] = sha(outcomes_path)
    else:
        errors_path.write_text(json.dumps({"event_id": "one", "error": "core_timestamp_lineage"}) + "\n")
        summary["lineage_errors"] = 1
    summary_path.write_text(json.dumps(summary))
    result = run(plan, events, sources, tmp_path / f"out-{tamper}", cache)
    assert result["reuse"]["status"] == "rejected"
    assert expected in result["reuse"]["reason"]


def test_full_cached_hit_still_hashes_source_without_reading_dataframe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    csv = source(tmp_path)
    row = event(csv, 1250, event_id="one", cluster="one")
    plan, events, sources = files(tmp_path, [row], csv)
    cache = tmp_path / "cache"
    run(plan, events, sources, cache)
    original_sha = incremental._sha
    hashed = []

    def counting_sha(path: Path) -> str:
        if Path(path) == csv:
            hashed.append(path)
        return original_sha(path)

    monkeypatch.setattr(incremental, "_sha", counting_sha)
    monkeypatch.setattr(incremental, "_read_source", lambda *_: pytest.fail("cached hit read CSV dataframe"))
    result = run(plan, events, sources, tmp_path / "out", cache)
    assert result["reuse"]["hits"] == 1 and result["reuse"]["misses"] == 0
    assert len(hashed) == 1
