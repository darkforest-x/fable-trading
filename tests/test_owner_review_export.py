"""Synthetic controls for preserving human intent and source identity on export."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace

import pytest

from yoyo.datasets import owner_review_export as mod


def box(**changes):
    result = {"id": "proposal", "type": "rectanglelabels", "from_name": "pattern", "to_name": "image",
        "original_width": 1280, "original_height": 742, "image_rotation": 0,
        "value": {"x": 20, "y": 30, "width": 15, "height": 20, "rotation": 0, "rectanglelabels": ["空头"]}}
    result["value"].update(changes)
    return result


def choice(value="无目标形态"):
    return {"type": "choices", "from_name": "no_box_reason", "to_name": "image", "value": {"choices": [value]}}


def prediction():
    return {"model_version": mod.OLD_PROTOCOL, "result": [box()]}


def answer(*results, aid=1, **extra):
    return {"id": aid, "completed_by": 4, "result": list(results), "was_cancelled": False, **extra}


@pytest.mark.parametrize("results,status", [
    ([box()], "owner_boxes"), ([choice()], "owner_no_target"),
    ([choice(), box()], "owner_no_target_with_inherited_proposal"),
    ([choice(), box(x=21)], "conflict_needs_review"),
    ([choice("拿不准")], "owner_uncertain"),
    ([choice("拿不准"), box()], "owner_uncertain"),
    ([choice("拿不准"), box(x=21)], "owner_uncertain"),
    ([], "missing_decision"),
])
def test_statuses_do_not_fall_back_to_prediction(results, status):
    original = answer(*results)
    before = deepcopy(original)
    result = mod.classify_answer(original, prediction())
    assert result["status"] == status
    assert bool(result["effective_boxes"]) == (status == "owner_boxes")
    assert result["training_eligible"] is False and result["new_gold"] is False
    assert original == before


def test_inherited_ignores_region_id_and_metadata_but_keeps_raw_geometry():
    inherited = box(x=20 + 0.9e-6)
    inherited.update(id="human-generated-region-id", origin="prediction", meta={"note": "unchanged"})
    result = mod.classify_answer(answer(choice(), inherited), prediction())
    assert result["status"] == "owner_no_target_with_inherited_proposal"
    assert result["raw_boxes"] == [inherited]
    assert result["effective_boxes"] == []


@pytest.mark.parametrize("change", ["x", "y", "width", "height", "class", "rotation", "size", "image_rotation"])
def test_every_semantic_geometry_dimension_matters(change):
    modified = box()
    if change == "class": modified["value"]["rectanglelabels"] = ["多头"]
    elif change == "size": modified["original_width"] = 1279
    elif change == "image_rotation": modified["image_rotation"] = 90
    else: modified["value"][change] += 2e-6
    result = mod.classify_answer(answer(choice(), modified), prediction())
    assert result["status"] == "conflict_needs_review"
    assert not result["effective_boxes"]


def test_cancelled_is_never_effective_even_with_boxes():
    assert mod.classify_answer(answer(box(), was_cancelled=True), prediction())["status"] == "cancelled"


@pytest.mark.parametrize("defect", ["future", "foreign_choice", "nan", "negative_width"])
def test_invalid_controls_or_geometry_are_preserved_as_conflicts(defect):
    result = box()
    if defect == "future": result["to_name"] = "future"
    elif defect == "foreign_choice": result = choice("unknown")
    elif defect == "nan": result["value"]["x"] = float("nan")
    else: result["value"]["width"] = -1
    classified = mod.classify_answer(answer(result), prediction())
    assert classified["status"] == "conflict_needs_review"
    assert classified["effective_boxes"] == []


def fixture_data(annotations=None, drafts=None):
    data = {"review_id": "old", "protocol_id": mod.OLD_PROTOCOL, "image": "frozen.png"}
    expected = {"old": {"data": data, "prediction": prediction(), "source_identity": {"box_id": "original-box"}}}
    annotations = annotations or []
    row = {"id": 12, "data": data, "annotations": annotations, "drafts": drafts or [], "total_annotations": len(annotations)}
    return expected, row


def test_multiple_human_answers_conflict_and_draft_never_overrides():
    expected, row = fixture_data([answer(box()), answer(choice(), aid=2)], [answer(box(x=21), aid=10)])
    answers, summary = mod.build_answers([row], expected)
    assert len(answers) == 3 and all(a["event_conflict"] for a in answers)
    assert summary["counts"]["event_conflicts"] == 1
    assert not answers[-1]["effective_answer"]
    assert answers[0]["raw_answer"]["completed_by"] == 4


def test_unchanged_inherited_no_target_and_boxless_no_target_agree():
    expected, row = fixture_data([answer(choice(), box()), answer(choice(), aid=2)])
    answers, summary = mod.build_answers([row], expected)
    assert summary["counts"]["event_conflicts"] == 0
    assert len(answers) == 2 and all(not a["effective_boxes"] for a in answers)


def test_geometry_edit_disagrees_even_for_same_class():
    expected, row = fixture_data([answer(box()), answer(box(x=21), aid=2)])
    assert mod.build_answers([row], expected)[1]["counts"]["event_conflicts"] == 1


@pytest.mark.parametrize("reason", ["无目标形态", "拿不准"])
def test_conflicting_or_uncertain_annotations_keep_geometry_disagreement(reason):
    expected, row = fixture_data([answer(choice(reason), box(x=21)), answer(choice(reason), box(x=22), aid=2)])
    assert mod.build_answers([row], expected)[1]["counts"]["event_conflicts"] == 1


def test_drafts_and_cancelled_do_not_form_consensus_or_conflict():
    expected, row = fixture_data([answer(choice(), was_cancelled=True)], [answer(box(x=21), aid=2)])
    answers, summary = mod.build_answers([row], expected)
    assert summary["counts"]["effective_annotations"] == 0
    assert summary["counts"]["cancelled"] == 1 and summary["counts"]["drafts"] == 1
    assert all(not a["effective_answer"] for a in answers)
    assert summary["events"][0]["status"] == "cancelled_only"


@pytest.mark.parametrize("defect", ["counter", "duplicate_id", "foreign_task", "missing_drafts"])
def test_incomplete_or_mislinked_answers_fail_closed(defect):
    expected, row = fixture_data([answer(box())])
    if defect == "counter": row["total_annotations"] = 2
    elif defect == "duplicate_id": row["annotations"].append(deepcopy(row["annotations"][0])); row["total_annotations"] = 2
    elif defect == "foreign_task": row["annotations"][0]["task"] = 13
    else: del row["drafts"]
    with pytest.raises(ValueError): mod.build_answers([row], expected)


@pytest.fixture
def server(tmp_path, monkeypatch):
    expected, row = fixture_data([answer(choice(), box())], [answer(box(x=21), aid=2)])
    live_prediction = {"id": 99, "task": 12, **prediction()}
    state = SimpleNamespace(row=row, prediction=live_prediction, calls=[], expected=expected)
    monkeypatch.setattr(mod, "source_identity", lambda: {"source_commit": "frozen", "source_sha256": {}})
    monkeypatch.setattr(mod, "read_sources", lambda: (expected, {"old": 12}, {"files_sha256": {}}))
    monkeypatch.setattr(mod.base, "session", lambda: None)
    def api(sess, method, path):
        state.calls.append((method, path))
        assert method == "GET" and path == "/api/projects/77/"
        return {"id": 77, "evaluate_predictions_automatically": False}
    def pages(sess, path):
        state.calls.append(("GET", path))
        if path.startswith("/api/predictions?"): return [deepcopy(state.prediction)]
        assert path.startswith("/api/tasks?")
        if "fields=task_only" in path: return [{"id": state.row["id"], "data": deepcopy(state.row["data"])}]
        return [deepcopy(state.row)]
    monkeypatch.setattr(mod.base, "api", api)
    monkeypatch.setattr(mod.base, "_pages", pages)
    state.output = tmp_path / "exports"
    return state


def test_export_get_only_raw_predictions_separate_and_new_run_preserves_old(server):
    original = deepcopy(server.row)
    first = mod.export(server.output)
    second = mod.export(server.output)
    assert first["output"] != second["output"]
    directory = mod.Path(first["output"])
    raw = json.loads((directory / "raw.json").read_text())
    assert raw["tasks"] == [original] and raw["predictions"] == [server.prediction]
    assert first["counts"]["annotations"] == 1 and first["counts"]["drafts"] == 1
    assert first["server_mutations"] == 0 and not first["training_eligible"]
    assert all(method == "GET" for method, _ in server.calls)
    assert first["snapshot_is_atomic"] is False
    for name, digest in first["files_sha256"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("defect", ["data", "foreign_id", "proposal"])
def test_identity_and_original_prediction_gate_precede_answer_fetch(server, defect):
    if defect == "data": server.row["data"] = {**server.row["data"], "caption": "changed"}
    elif defect == "foreign_id": server.row["id"] = 13
    else: server.prediction["result"][0]["value"]["x"] += 1
    with pytest.raises(ValueError): mod.export(server.output)
    assert not any("annotations,drafts" in path for _, path in server.calls)
    assert not server.output.exists()


def test_task_arrival_during_collection_rejected(server, monkeypatch):
    original = mod.base._pages
    def pages(sess, path):
        rows = original(sess, path)
        if "fields=all" in path: rows[0]["id"] += 1
        return rows
    monkeypatch.setattr(mod.base, "_pages", pages)
    with pytest.raises(ValueError): mod.export(server.output)


def test_source_gate_is_before_session(server, monkeypatch):
    def blocked(): raise ValueError("source not committed")
    monkeypatch.setattr(mod, "source_identity", blocked)
    with pytest.raises(ValueError): mod.export(server.output)
    assert server.calls == []


@pytest.fixture
def local_sources(tmp_path, monkeypatch):
    old = tmp_path / "old"; old.mkdir()
    expected, row = fixture_data()
    tasks = [{"data": row["data"], "predictions": [prediction()]}]
    (old / "tasks.json").write_text(json.dumps(tasks))
    (old / "manifest.jsonl").write_text(json.dumps({"review_id": "old", "main_end_time": "2025-01-01T00:00:00+00:00", "box_id": "box"}) + "\n")
    receipt = tmp_path / "import.json"
    receipt.write_text(json.dumps({"project_id": 77, "protocol_id": mod.OLD_PROTOCOL, "task_ids": {"old": 12}}))
    monkeypatch.setattr(mod, "OLD_PACK", old)
    monkeypatch.setattr(mod, "OLD_RECEIPT", receipt)
    monkeypatch.setattr(mod, "NEW_PACK", tmp_path / "does-not-exist")
    monkeypatch.setattr(mod, "OLD_COUNT", 1)
    monkeypatch.setattr(mod, "OLD_TASK_SHA", mod.sha(old / "tasks.json"))
    monkeypatch.setattr(mod, "OLD_MANIFEST_SHA", mod.sha(old / "manifest.jsonl"))
    return old


def test_only_old_pack_is_read_when_new_build_receipt_absent(local_sources):
    loaded, mapping, sources = mod.read_sources()
    assert set(loaded) == {"old"} and mapping == {"old": 12}
    assert sources["new_pack_status"] == "not_completed_or_absent"


@pytest.mark.parametrize("end,allowed", [("2026-05-03T23:45:00+00:00", True),
    ("2026-05-04T00:00:00+00:00", False), ("2025-01-01T00:00:00", False)])
def test_main_close_boundary_is_checked_before_server_reads(local_sources, monkeypatch, end, allowed):
    path = local_sources / "manifest.jsonl"
    row = json.loads(path.read_text())
    row["main_end_time"] = end
    path.write_text(json.dumps(row) + "\n")
    monkeypatch.setattr(mod, "OLD_MANIFEST_SHA", mod.sha(path))
    if allowed: assert set(mod.read_sources()[0]) == {"old"}
    else:
        with pytest.raises(ValueError): mod.read_sources()


def test_completed_new_pack_added_only_with_verified_receipt(local_sources, tmp_path, monkeypatch):
    pack = tmp_path / "new"; (pack / "admin").mkdir(parents=True)
    pred = prediction(); pred["model_version"] = mod.NEW_PROTOCOL
    tasks = [{"data": {"review_id": "new", "protocol_id": mod.NEW_PROTOCOL}, "predictions": [pred]}]
    (pack / "tasks.json").write_text(json.dumps(tasks))
    (pack / "manifest.jsonl").write_text(json.dumps({"review_id": "new", "source_event_id": "event",
        "main_end_time": "2025-01-01T00:00:00+00:00"}) + "\n")
    receipt = {"protocol_id": mod.NEW_PROTOCOL, "tasks": 1, "holdout_read": False, **mod.FALSE_FLAGS,
        "tasks_sha256": mod.sha(pack / "tasks.json"), "manifest_sha256": mod.sha(pack / "manifest.jsonl")}
    (pack / "admin/build_receipt.json").write_text(json.dumps(receipt))
    monkeypatch.setattr(mod, "NEW_PACK", pack)
    monkeypatch.setattr(mod, "NEW_COUNT", 1)
    expected, mapping, sources = mod.read_sources()
    assert set(expected) == {"old", "new"} and mapping == {"old": 12}
    assert expected["new"]["source_identity"]["source_event_id"] == "event"
    assert sources["new_pack_status"] == "completed"
    (pack / "tasks.json").write_text(json.dumps(tasks) + " ")
    with pytest.raises(ValueError): mod.read_sources()


def test_unordered_box_matching_handles_ambiguous_tolerance_neighbors():
    left = [mod.rectangle(box(x=20)), mod.rectangle(box(x=20 + 1.5e-6))]
    right = [mod.rectangle(box(x=20 + .75e-6)), mod.rectangle(box(x=20 - .75e-6))]
    assert mod.boxes_equal(left, right)
