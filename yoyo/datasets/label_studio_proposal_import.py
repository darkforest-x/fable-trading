"""Import versioned geometry suggestions without inventing human annotations.

Label Studio's documented predictions format stores proposals separately from
human answers (https://labelstud.io/guide/predictions.html). The local 1.13.1 API
supports fields=all with an explicit include list to inspect predictions without
retrieving annotation bodies. The existing blank-only importer stays strict.
Owner authorized historical box refinement on 2026-09-07; no training data is
rewritten here. Re-entry validates every data/prediction identity and never
replaces existing annotations, drafts or proposals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from yoyo.datasets import label_studio_import as base

PROTOCOL = "owner_geometry_refinement_v1"
ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
EXPERIMENT = ROOT / "experiments/active/exp-owner-box-refinement-20260907-v1"
CONFIG = ROOT / "configs/labelstudio/owner_box_refinement_future40.xml"
TITLE = "YOLO 历史人工标注 · 2513框细化建议 · 未来40根"


def canonical_prediction(value: dict) -> dict:
    """Validate one proposal in percentage coordinates, never a human answer."""
    if not isinstance(value, dict) or value.get("model_version") != PROTOCOL:
        raise ValueError("Unknown proposal model_version")
    results = value.get("result")
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("Exactly one proposed rectangle is required")
    result = results[0]
    if (not isinstance(result, dict) or not result.get("id")
            or result.get("type") != "rectanglelabels"
            or result.get("from_name") != "pattern" or result.get("to_name") != "image"
            or result.get("original_width") != 1280 or result.get("original_height") != 742
            or result.get("image_rotation", 0) != 0):
        raise ValueError("Proposal rectangle must target the original 1280x742 image")
    coords = result.get("value", {})
    if not isinstance(coords, dict) or coords.get("rectanglelabels") not in (["多头"], ["空头"]):
        raise ValueError("Proposal must preserve an explicit LONG or SHORT class")
    numbers = [coords.get(k) for k in ("x", "y", "width", "height")]
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in numbers):
        raise ValueError("Rectangle coordinates must be finite numbers")
    x, y, width, height = numbers
    if (x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 100 + 1e-7
            or y + height > 100 + 1e-7 or coords.get("rotation", 0) != 0):
        raise ValueError("Rectangle is outside the image or has unsupported rotation")
    return {"model_version": PROTOCOL, "result": results}


def import_proposal_project(title: str, tasks_file: str | Path, config_file: str | Path,
                            output: str | Path, document_root_path: str | Path | None = None,
                            batch_size: int = 500) -> dict:
    """Add missing tasks, validating every existing proposal before any import."""
    if base.BASE != "http://127.0.0.1:8081" or not title or batch_size < 1:
        raise ValueError("Only the authorized local server and positive batches are supported")
    tasks = json.loads(Path(tasks_file).read_text())
    config = Path(config_file).read_text()
    xml = ET.fromstring(config)
    fields = sorted({node.attrib["value"][1:] for node in xml.iter()
                     if node.tag == "Image" and node.attrib.get("value", "").startswith("$")})
    if not isinstance(tasks, list) or not tasks or "image" not in fields:
        raise ValueError("Nonempty tasks and explicit Image fields are required")
    expected = {}
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"data", "predictions"}:
            raise ValueError("Proposal imports require only data and predictions, never annotations")
        data = task["data"]
        if (not isinstance(data, dict) or data.get("protocol_id") != PROTOCOL
                or not isinstance(data.get("review_id"), str) or not data["review_id"]
                or data["review_id"] in expected):
            raise ValueError("Missing, duplicate or foreign proposal review identity")
        if not isinstance(task["predictions"], list) or len(task["predictions"]) != 1:
            raise ValueError("Exactly one prediction per task is required")
        canonical_prediction(task["predictions"][0])
        expected[data["review_id"]] = task

    sess = base.session()
    base.api(sess, "POST", "/api/projects/validate/", {"label_config": config})
    projects = base._pages(sess, "/api/projects")
    hits = [p for p in projects if p.get("title") == title]
    if len(hits) > 1:
        raise ValueError("Duplicate project title")
    if hits and ET.canonicalize(hits[0]["label_config"], strip_text=True) != ET.canonicalize(config, strip_text=True):
        raise ValueError("Existing project configuration changed")
    if hits and hits[0].get("show_collab_predictions") is not True:
        raise ValueError("Existing project no longer displays predictions; preserve its settings")
    if hits and hits[0].get("model_version") != PROTOCOL:
        raise ValueError("Existing project selected model_version changed; preserve its settings")
    probe = hits[0]["id"] if hits else projects[0]["id"] if projects else None
    if probe is None:
        raise ValueError("A pre-existing project is needed for storage validation")
    root = Path(document_root_path).absolute() if document_root_path else base.document_root(sess, probe)
    resources, directories = {}, set()
    for rid, task in expected.items():
        data = task["data"]
        for field in fields:
            url = urllib.parse.urlsplit(data.get(field, ""))
            parts = urllib.parse.parse_qs(url.query).get("d", [])
            if url.scheme or url.netloc or url.path != "/data/local-files/" or len(parts) != 1:
                raise ValueError("Only local-files image resources are allowed")
            relative = PurePosixPath(parts[0])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("Image resource escapes the document root")
            path, digest = root / relative, data.get(field + "_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not path.is_file():
                raise ValueError(f"{rid}: missing image or SHA-256 for {field}")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"{rid}: image SHA-256 mismatch for {field}")
            resources[rid, field] = path, digest
            directories.add(str(path.parent))
    for directory in sorted(directories):
        base.api(sess, "POST", "/api/storages/localfiles/validate", {
            "project": probe, "path": directory, "use_blob_urls": False})
    pid = hits[0]["id"] if hits else base.api(sess, "POST", "/api/projects", {
        "title": title, "label_config": config, "show_collab_predictions": True,
        "model_version": PROTOCOL})["id"]
    task_path = (f"/api/tasks?project={pid}&fields=all&resolve_uri=false"
                 "&include=id,data,predictions,total_predictions,total_annotations")

    def inspect() -> dict:
        seen, task_ids = {}, set()
        for row in base._pages(sess, task_path):
            tid = row.get("id")
            if isinstance(tid, bool) or not isinstance(tid, int) or tid < 1 or tid in task_ids:
                raise ValueError("Task IDs must be positive and unique")
            task_ids.add(tid)
            rid = row.get("data", {}).get("review_id")
            if rid not in expected or rid in seen or row["data"] != expected[rid]["data"]:
                raise ValueError("Existing project has duplicate, foreign or changed task data")
            predictions = row.get("predictions")
            if (not isinstance(row.get("total_annotations"), int) or row.get("total_predictions") != 1
                    or not isinstance(predictions, list) or len(predictions) != 1):
                raise ValueError("Existing task prediction or annotation counters differ")
            if canonical_prediction(predictions[0]) != canonical_prediction(expected[rid]["predictions"][0]):
                raise ValueError("Existing prediction changed; never overwrite it")
            seen[rid] = row
        return seen

    before = inspect()
    stores = base._pages(sess, f"/api/storages/localfiles?project={pid}")
    for directory in sorted(directories):
        matches = [s for s in stores if s.get("path") == directory]
        if any(s.get("use_blob_urls") is not False for s in matches):
            raise ValueError("Existing storage configuration changed")
        if not matches:
            base.api(sess, "POST", "/api/storages/localfiles", {
                "project": pid, "title": Path(directory).name, "path": directory, "use_blob_urls": False})
    missing = [task for rid, task in expected.items() if rid not in before]
    created_ids = []
    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        result = base.api(sess, "POST", f"/api/projects/{pid}/import?return_task_ids=true", batch)
        if result.get("task_count") != len(batch) or len(result.get("task_ids", [])) != len(batch):
            raise ValueError("Import did not confirm every task; rerun to reconcile safely")
        created_ids.extend(result["task_ids"])
    after = inspect()
    if set(after) != set(expected) or {r["id"] for k, r in after.items() if k not in before} != set(created_ids):
        raise ValueError("Post-import task identities differ")
    if any(r["total_annotations"] for k, r in after.items() if k not in before):
        raise ValueError("New proposal tasks unexpectedly contain human annotations")
    checks = []
    for position in sorted({0, len(tasks) // 2, len(tasks) - 1}):
        data = tasks[position]["data"]
        for field in fields:
            _, digest = resources[data["review_id"], field]
            with sess[0].open(base.BASE + data[field], timeout=30) as response:
                body, status = response.read(), response.status
            if status != 200 or hashlib.sha256(body).hexdigest() != digest:
                raise ValueError("HTTP image SHA-256 differs from the frozen local file")
            checks.append({"review_id": data["review_id"], "field": field, "sha256": digest, "status": status})
    receipt = {"generated_at": datetime.now(timezone.utc).isoformat(), "protocol_id": PROTOCOL,
               "project_id": pid, "title": title, "url": f"{base.BASE}/projects/{pid}/data",
               "document_root": str(root), "total": len(after), "imported": len(missing), "reused": len(before),
               "predictions": sum(row["total_predictions"] for row in after.values()),
               "existing_annotations_preserved": sum(row["total_annotations"] for row in before.values()),
               "task_ids": {k: row["id"] for k, row in after.items()}, "image_checks": checks}
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import"])
    parser.add_argument("--receipt", type=Path, default=EXPERIMENT / "results/import_receipt.json")
    args = parser.parse_args()
    from yoyo.datasets.grade_a_label_studio import link_pack
    link_pack(PACK)
    receipt = import_proposal_project(TITLE, PACK / "tasks.json", CONFIG, args.receipt)
    print(json.dumps({k: receipt[k] for k in ("url", "total", "imported", "reused", "predictions")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
