"""Read-only, human-only export of project 77 without promoting proposals to Gold.

Source task identities are checked before fetching answer bodies. Explicit human
Choices take precedence over an unchanged inherited proposal; altered boxes plus
no-target remain conflicts. Drafts, cancellations and every returned annotation
are retained, never replaced by predictions or a last-answer-wins rule. This is
a paginated collection interval, not an atomic database snapshot. No media or
OHLCV is opened and no training input or Label Studio resource is changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets import label_studio_import as base

ROOT = Path(__file__).resolve().parents[2]
PROJECT = 77
OLD_PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
NEW_PACK = ROOT / "datasets/grade_a_hl2_review_20260908_v1"
OLD_RECEIPT = ROOT / "experiments/active/exp-owner-box-refinement-20260907-v1/results/import_receipt.json"
OUTPUT = ROOT / "output/offline_tasks/owner_review_exports"
OLD_PROTOCOL = "owner_geometry_refinement_v1"
NEW_PROTOCOL = "grade_a_hl2_training_label_review_v1"
OLD_TASK_SHA = "bc89a4d5e41003f1bf191013d60ba76d84fafbd67a28e17c2dfd3f0c6bc6697a"
OLD_MANIFEST_SHA = "7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641"
OLD_COUNT, NEW_COUNT = 2513, 1043
TOLERANCE = 1e-6
FALSE_FLAGS = {"training_eligible": False, "production_eligible": False, "new_gold": False}


def blob(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> dict:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ValueError("Export requires the shared main branch")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    names = ["yoyo/datasets/owner_review_export.py", "tests/test_owner_review_export.py",
        "yoyo/datasets/label_studio_import.py", "scripts/ls_auto_import.py",
        "scripts/export_owner_labels.py", "yoyo/contracts/holdout.py"]
    for name in names:
        if subprocess.check_output(["git", "show", head + ":" + name], cwd=ROOT) != (ROOT / name).read_bytes():
            raise ValueError("Commit export source before running: " + name)
    return {"source_commit": head, "source_sha256": {n: sha(ROOT / n) for n in names}}


def rectangle(result: dict) -> dict:
    """Semantic geometry only; IDs, origin and tool metadata are not geometry."""
    if (result.get("type") != "rectanglelabels" or result.get("from_name") != "pattern"
            or result.get("to_name") != "image"):
        raise ValueError("Rectangle must target the primary image")
    value = result.get("value", {})
    if value.get("rectanglelabels") not in (["多头"], ["空头"]):
        raise ValueError("Unknown rectangle class")
    normalized = {k: value.get(k) for k in ("x", "y", "width", "height")}
    normalized.update(rotation=value.get("rotation", 0), image_rotation=result.get("image_rotation", 0),
        original_width=result.get("original_width"), original_height=result.get("original_height"))
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in normalized.values()):
        raise ValueError("Non-finite or missing rectangle geometry")
    if (min(normalized[k] for k in ("width", "height", "original_width", "original_height")) <= 0
            or min(normalized["x"], normalized["y"]) < -TOLERANCE
            or normalized["x"] + normalized["width"] > 100 + TOLERANCE
            or normalized["y"] + normalized["height"] > 100 + TOLERANCE):
        raise ValueError("Rectangle is outside its canvas")
    return {"label": value["rectanglelabels"][0], **normalized}


def boxes_equal(left: list, right: list) -> bool:
    """Order-independent one-to-one matching; tolerance is in LS percentages."""
    if len(left) != len(right):
        return False
    candidates = [[j for j, b in enumerate(right) if a["label"] == b["label"] and all(
        math.isclose(a[k], b[k], rel_tol=0, abs_tol=TOLERANCE) for k in a if k != "label")]
        for a in left]
    matched = {}

    def assign(i, visited):
        for j in candidates[i]:
            if j not in visited:
                visited.add(j)
                if j not in matched or assign(matched[j], visited):
                    matched[j] = i
                    return True
        return False

    return all(assign(i, set()) for i in range(len(left)))


def classify_answer(answer: dict, prediction: dict) -> dict:
    """Classify a real answer only; never fall back to a model prediction."""
    results = answer.get("result")
    raw_boxes, boxes, choices, issues = [], [], [], []
    if not isinstance(results, list):
        results = []
        issues.append("missing_or_invalid_result")
    for result in results:
        if not isinstance(result, dict):
            issues.append("invalid_result")
        elif result.get("type") == "rectanglelabels":
            raw_boxes.append(deepcopy(result))
            try:
                boxes.append(rectangle(result))
            except (ValueError, TypeError, AttributeError) as error:
                issues.append(str(error))
        elif (result.get("type") == "choices" and result.get("from_name") == "no_box_reason"
                and result.get("to_name") == "image"):
            selected = result["value"].get("choices") if isinstance(result.get("value"), dict) else None
            if not isinstance(selected, list) or any(c not in ("无目标形态", "拿不准") for c in selected):
                issues.append("unknown_choice")
            else:
                choices.extend(selected)
        else:
            issues.append("unknown_result_control")
    inherited = bool(boxes) and not issues and boxes_equal(boxes, [rectangle(r) for r in prediction["result"]])
    if answer.get("was_cancelled") is True:
        status = "cancelled"
    elif "拿不准" in choices:
        status = "owner_uncertain"
    elif issues:
        status = "conflict_needs_review"
    elif "无目标形态" in choices:
        status = ("owner_no_target_with_inherited_proposal" if inherited else
                  "conflict_needs_review" if raw_boxes else "owner_no_target")
    else:
        status = "owner_boxes" if boxes else "missing_decision"
    return {"status": status, "raw_boxes": raw_boxes, "normalized_boxes": boxes,
        "effective_boxes": deepcopy(boxes) if status == "owner_boxes" else [],
        "matches_original_proposal": inherited, "choices": choices, "issues": issues, **FALSE_FLAGS}


def read_sources() -> tuple[dict, dict, dict]:
    """Load frozen metadata only. An unfinished new pack is not an input."""
    if sha(OLD_PACK / "tasks.json") != OLD_TASK_SHA or sha(OLD_PACK / "manifest.jsonl") != OLD_MANIFEST_SHA:
        raise ValueError("Historical source digest changed")
    imported = json.loads(OLD_RECEIPT.read_text())
    mapping = imported.get("task_ids", {})
    if (imported.get("project_id") != PROJECT or imported.get("protocol_id") != OLD_PROTOCOL
            or len(mapping) != OLD_COUNT or len(set(mapping.values())) != len(mapping)
            or any(type(v) is not int or v <= 0 for v in mapping.values())):
        raise ValueError("Historical import identity changed")
    inputs = [(OLD_PACK, OLD_PROTOCOL, OLD_COUNT)]
    receipt_path = NEW_PACK / "admin/build_receipt.json"
    sources = {"new_pack_status": "not_completed_or_absent", "files_sha256": {str(OLD_RECEIPT): sha(OLD_RECEIPT)}}
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get("protocol_id") != NEW_PROTOCOL or receipt.get("tasks") != NEW_COUNT
                or receipt.get("holdout_read") is not False
                or any(receipt.get(k) is not False for k in FALSE_FLAGS)):
            raise ValueError("New review build receipt is not eligible for read-only export")
        for name, key in (("tasks.json", "tasks_sha256"), ("manifest.jsonl", "manifest_sha256")):
            if sha(NEW_PACK / name) != receipt.get(key):
                raise ValueError("New review source digest changed")
        inputs.append((NEW_PACK, NEW_PROTOCOL, NEW_COUNT))
        sources["new_pack_status"] = "completed"
        sources["files_sha256"][str(receipt_path)] = sha(receipt_path)
    expected = {}
    for pack, protocol, count in inputs:
        tasks = json.loads((pack / "tasks.json").read_text())
        manifest = [json.loads(line) for line in (pack / "manifest.jsonl").read_text().splitlines() if line]
        metadata = {r["review_id"]: r for r in manifest}
        if len(tasks) != count or len(metadata) != count or len(manifest) != count:
            raise ValueError("Source membership changed")
        for task in tasks:
            data = task["data"]
            rid = data["review_id"]
            if rid not in metadata or rid in expected or data.get("protocol_id") != protocol:
                raise ValueError("Missing, duplicated or foreign source identity")
            row = metadata[rid]
            end = datetime.fromisoformat(row["main_end_time"])
            if end.tzinfo is None or end + timedelta(minutes=15) > HOLDOUT_START:
                raise ValueError("Source main canvas touches unauthorized holdout")
            predictions = task.get("predictions")
            if (not isinstance(predictions, list) or len(predictions) != 1
                    or predictions[0].get("model_version") != protocol
                    or not isinstance(predictions[0].get("result"), list) or len(predictions[0]["result"]) != 1):
                raise ValueError("Expected one frozen primary-image proposal")
            rectangle(predictions[0]["result"][0])
            expected[rid] = {"data": data, "prediction": predictions[0], "source_identity": {
                "pack": str(pack.relative_to(ROOT)) if pack.is_relative_to(ROOT) else str(pack),
                "review_id": rid, "protocol_id": protocol,
                **{k: row[k] for k in ("box_id", "source_event_id", "split", "original_split",
                    "main_start_time", "main_end_time", "alias_candidate_group", "alias_candidate_count") if k in row}}}
        sources["files_sha256"].update({str(pack / n): sha(pack / n) for n in ("tasks.json", "manifest.jsonl")})
    if {rid for rid, e in expected.items() if e["data"]["protocol_id"] == OLD_PROTOCOL} != set(mapping):
        raise ValueError("Historical task mapping is incomplete")
    return expected, mapping, sources


def verify_tasks(rows: list, expected: dict, old_mapping: dict) -> dict:
    seen, ids = {}, set()
    for row in rows:
        rid, tid = row.get("data", {}).get("review_id"), row.get("id")
        if (rid not in expected or rid in seen or type(tid) is not int or tid <= 0 or tid in ids
                or row["data"] != expected[rid]["data"] or (rid in old_mapping and old_mapping[rid] != tid)):
            raise ValueError("Live task identity differs from frozen input")
        seen[rid] = tid
        ids.add(tid)
    if not set(old_mapping) <= set(seen):
        raise ValueError("Historical tasks disappeared")
    return seen


def verify_predictions(predictions: list, expected: dict, mapping: dict) -> None:
    by_id = {tid: rid for rid, tid in mapping.items()}
    seen = set()
    for row in predictions:
        tid = row.get("task")
        if tid not in by_id or tid in seen:
            raise ValueError("Orphan or multiple live proposals")
        original = expected[by_id[tid]]["prediction"]
        if (row.get("model_version") != original["model_version"]
                or not isinstance(row.get("result"), list)
                or not boxes_equal([rectangle(r) for r in row["result"]], [rectangle(r) for r in original["result"]])):
            raise ValueError("Original proposal changed")
        seen.add(tid)
    if seen != set(by_id):
        raise ValueError("Original proposal missing; never infer its contents")


def equivalent_decision(left: dict, right: dict) -> bool:
    def decision(row):
        return "owner_no_target" if row["status"] == "owner_no_target_with_inherited_proposal" else row["status"]
    if decision(left) != decision(right):
        return False
    if decision(left) == "owner_no_target":
        return True  # A harmless inherited proposal is not the human decision.
    if not boxes_equal(left["normalized_boxes"], right["normalized_boxes"]):
        return False
    if left["issues"] or right["issues"]:
        fields = ("type", "from_name", "to_name", "value", "original_width", "original_height", "image_rotation")
        semantics = lambda row: [{k: b.get(k) for k in fields} for b in row["raw_boxes"]]
        return left["issues"] == right["issues"] and semantics(left) == semantics(right)
    return True


def build_answers(rows: list, expected: dict) -> tuple[list, dict]:
    answers, events = [], []
    used = {"annotation": set(), "draft": set()}
    for row in sorted(rows, key=lambda r: r["id"]):
        rid = row["data"]["review_id"]
        event_answers = []
        for field, kind in (("annotations", "annotation"), ("drafts", "draft")):
            if not isinstance(row.get(field), list):
                raise ValueError("Answer/draft list is absent; cannot assert completeness")
            for answer in row[field]:
                aid = answer.get("id")
                if type(aid) is not int or aid <= 0 or aid in used[kind] or answer.get("task", row["id"]) != row["id"]:
                    raise ValueError("Duplicate or foreign answer identity")
                used[kind].add(aid)
                classified = classify_answer(answer, expected[rid]["prediction"])
                event_answers.append({"review_id": rid, "task_id": row["id"],
                    "protocol_id": row["data"]["protocol_id"], "source_identity": expected[rid]["source_identity"],
                    "record_kind": kind, kind + "_id": aid, "raw_answer": deepcopy(answer), **classified,
                    "effective_answer": kind == "annotation" and classified["status"] != "cancelled"})
        declared = row.get("total_annotations")
        submitted = [a for a in event_answers if a["record_kind"] == "annotation"]
        if type(declared) is not int or declared < 0:
            raise ValueError("Missing server annotation counter")
        # LS counters may exclude skipped/cancelled annotations; raw count is authoritative.
        if declared not in (len(submitted), sum(a["effective_answer"] for a in submitted)):
            raise ValueError("Server annotation count and fetched bodies disagree")
        effective = [a for a in event_answers if a["effective_answer"]]
        conflict = any(not equivalent_decision(a, b) for i, a in enumerate(effective) for b in effective[i + 1:])
        status = ("event_conflict" if conflict else effective[0]["status"] if effective else
                  "cancelled_only" if submitted else "draft_only" if event_answers else "unanswered")
        for answer in event_answers:
            answer["event_conflict"] = conflict
        answers.extend(event_answers)
        events.append({"review_id": rid, "task_id": row["id"], "status": status,
            "annotation_count": len(submitted), "draft_count": len(row["drafts"]), "event_conflict": conflict,
            "server_total_annotations": declared, **FALSE_FLAGS})
    summary = {"counts": {"tasks": len(rows), "annotations": len(used["annotation"]), "drafts": len(used["draft"]),
        "cancelled": sum(a["record_kind"] == "annotation" and a["status"] == "cancelled" for a in answers),
        "effective_annotations": sum(a["effective_answer"] for a in answers),
        "event_conflicts": sum(e["event_conflict"] for e in events)},
        "annotation_status_counts": dict(Counter(a["status"] for a in answers if a["record_kind"] == "annotation")),
        "draft_status_counts": dict(Counter(a["status"] for a in answers if a["record_kind"] == "draft")),
        "event_status_counts": dict(Counter(e["status"] for e in events)),
        "source_task_counts": dict(Counter(r["data"]["protocol_id"] for r in rows)), "events": events, **FALSE_FLAGS}
    return answers, summary


def export(output: Path | None = None) -> dict:
    frozen = source_identity()
    expected, old_mapping, sources = read_sources()
    if base.BASE != "http://127.0.0.1:8081":
        raise ValueError("Only the authorized local Label Studio instance is supported")
    started = datetime.now(timezone.utc)
    sess = base.session()  # Authentication only; all project/data requests below are GET.
    project = base.api(sess, "GET", f"/api/projects/{PROJECT}/")
    if project.get("id") != PROJECT or project.get("evaluate_predictions_automatically") is not False:
        raise ValueError("Project identity or automatic-prediction read safety changed")
    prefix = f"/api/tasks?project={PROJECT}&resolve_uri=false"
    identities = base._pages(sess, prefix + "&fields=task_only&include=id,data")
    mapping = verify_tasks(identities, expected, old_mapping)
    predictions = base._pages(sess, f"/api/predictions?task__project={PROJECT}")
    verify_predictions(predictions, expected, mapping)
    rows = base._pages(sess, prefix + "&fields=all&include=id,data,annotations,drafts,total_annotations")
    if verify_tasks(rows, expected, old_mapping) != mapping:
        raise ValueError("Task membership changed while collecting; rerun a fresh export")
    answers, summary = build_answers(rows, expected)
    summary["counts"]["predictions"] = len(predictions)
    if source_identity()["source_sha256"] != frozen["source_sha256"] or any(
            sha(Path(p)) != digest for p, digest in sources["files_sha256"].items()):
        raise ValueError("Source changed during collection")
    finished = datetime.now(timezone.utc)
    directory = (Path(output) if output is not None else OUTPUT) / started.strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(parents=True, exist_ok=False)
    raw = {"project": project, "identity_snapshot": identities, "tasks": rows, "predictions": predictions,
        "collection_started_at": started.isoformat(), "collection_finished_at": finished.isoformat()}
    outputs = {"raw.json": blob(raw), "predictions.json": blob(predictions),
        "answers.jsonl": b"".join((json.dumps(a, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode() for a in answers)}
    for name, body in outputs.items():
        with (directory / name).open("xb") as file:
            file.write(body)
    summary.update(**frozen, sources=sources, project_id=PROJECT, output=str(directory),
        collection_started_at=started.isoformat(), collection_finished_at=finished.isoformat(),
        snapshot_is_atomic=False, tolerance=TOLERANCE, status="exported_for_review_not_training",
        effective_answer_definition="non_cancelled_annotation_record_not_training_eligibility",
        server_mutations=0, annotation_or_draft_writes=0, prediction_fallback_count=0,
        media_read=False, ohlcv_read=False, holdout_read=False, new_training=False,
        files_sha256={name: hashlib.sha256(body).hexdigest() for name, body in outputs.items()})
    with (directory / "summary.json").open("xb") as file:
        file.write(blob(summary))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export"])
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Parent for a new timestamped export directory")
    args = parser.parse_args()
    result = export(args.output)
    print(json.dumps({"output": result["output"], "counts": result["counts"]}, ensure_ascii=False))
