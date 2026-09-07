"""Create exact task-ID review views in existing Label Studio project 77 only.

Grounded in local LS 1.13.1 backend and the packaged datamanager source map:
Tabs/tab_filter.js restricts operators to the frontend enum. Backend in_list is
unsupported there; Filters/types/Number.jsx supports scalar equal and Tab uses
OR conjunction. Empty sets use impossible task ID -1. Writes are only view POST
or explicit, guarded legacy-view PATCH; task data and Owner work are untouched.
Source must be committed before executing this delivery.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

from yoyo.datasets import label_studio_import as base

ROOT = Path(__file__).resolve().parents[2]
PROJECT = 77
PROTOCOL = "owner_box_curation_v1"
SOURCE_PROTOCOL = "owner_geometry_refinement_v1"
MANIFEST_SHA = "7588f9c62f26a66986f747f9dd27dff8f6c216a6b26f307e705cba2880c3a641"
TASKS_SHA = "bc89a4d5e41003f1bf191013d60ba76d84fafbd67a28e17c2dfd3f0c6bc6697a"
PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
PREVIOUS = ROOT / "experiments/active/exp-owner-box-refinement-20260907-v1/results/import_receipt.json"
RESULTS = ROOT / "experiments/active/exp-owner-box-curation-20260907-v1/results"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity() -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    hashes = {}
    for name in ("yoyo/datasets/label_studio_review_views.py", "tests/test_label_studio_review_views.py", "yoyo/datasets/label_studio_import.py"):
        committed = subprocess.check_output(["git", "show", f"{head}:{name}"], cwd=ROOT)
        hashes[name] = digest(ROOT/name)
        if hashlib.sha256(committed).hexdigest() != hashes[name]:
            raise ValueError(f"Commit source before view delivery: {name}")
    return {"source_commit": head, "code_sha256": hashes}


def read_inputs(queue_file: Path, receipt_file: Path, tasks_file: Path, manifest_file: Path) -> tuple[dict, dict, dict]:
    queue, receipt, tasks = (json.loads(p.read_text()) for p in (queue_file, receipt_file, tasks_file))
    if (not isinstance(queue, dict) or queue.get("schema_version") != 1 or queue.get("protocol_id") != PROTOCOL
            or queue.get("source_manifest_sha256") != MANIFEST_SHA or digest(manifest_file) != MANIFEST_SHA
            or digest(tasks_file) != TASKS_SHA):
        raise ValueError("Queue protocol or source manifest identity differs")
    if not isinstance(receipt, dict) or receipt.get("project_id") != PROJECT or receipt.get("protocol_id") != SOURCE_PROTOCOL:
        raise ValueError("Receipt belongs to another project or source protocol")
    mapping = receipt.get("task_ids")
    if (not isinstance(mapping, dict) or not mapping or any(not isinstance(r, str) or not r or type(t) is not int or t <= 0 for r, t in mapping.items())
            or len(set(mapping.values())) != len(mapping)):
        raise ValueError("Receipt must bind unique review IDs to unique task IDs")
    expected = {}
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Frozen tasks must be a nonempty list")
    for task in tasks:
        data = task.get("data", {})
        rid = data.get("review_id")
        if rid not in mapping or rid in expected or data.get("protocol_id") != SOURCE_PROTOCOL:
            raise ValueError("Frozen task identities differ from import receipt")
        expected[rid] = data
    if set(expected) != set(mapping):
        raise ValueError("Frozen task set differs from import receipt")
    if not isinstance(queue.get("views"), list) or not queue["views"]:
        raise ValueError("Queue needs at least one named view")
    keys, titles = set(), set()
    for view in queue["views"]:
        key, title, ids = view.get("key"), view.get("title"), view.get("review_ids")
        if (not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", key) or key in keys
                or not isinstance(title, str) or not title.strip() or title in titles or title == "Default"
                or not isinstance(ids, list) or any(not isinstance(r, str) or r not in mapping for r in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Invalid, duplicate or foreign view identity")
        keys.add(key); titles.add(title)
    return queue, mapping, expected


def view_data(view: dict, mapping: dict) -> dict:
    return {"title": view["title"], "type": "list", "target": "tasks", "ordering": ["tasks:id"],
        "filters": {"conjunction": "or", "items": [{"filter": "filter:tasks:id", "operator": "equal",
            "type": "Number", "value": task_id}
            for task_id in (sorted(mapping[rid] for rid in view["review_ids"]) or [-1])]},
        "curation": {"key": view["key"], "protocol_id": PROTOCOL, "source_manifest_sha256": MANIFEST_SHA}}


def legacy_view_data(view: dict, mapping: dict) -> dict:
    """The precise incompatible configuration emitted by the first delivery."""
    data = view_data(view, mapping)
    data["filters"] = {"conjunction": "and", "items": [{"filter": "filter:tasks:id",
        "operator": "in_list", "type": "Number", "value": sorted(mapping[rid] for rid in view["review_ids"])}]}
    return data


def verify_view(view: dict, expected: dict) -> None:
    data = view.get("data", {})
    if view.get("project") != PROJECT or any(data.get(k) != v for k, v in expected.items()):
        raise ValueError("Existing view configuration drifted; no overwrite is permitted")
    selected = data.get("selectedItems")
    if selected and selected != {"all": True, "excluded": []}:
        raise ValueError("Existing view has a task selection that changes its scope")


def task_identity(sess, expected: dict, mapping: dict) -> str:
    rows = base._pages(sess, f"/api/tasks?project={PROJECT}&fields=task_only&resolve_uri=false&include=id,data")
    found = {}
    for row in rows:
        rid = row.get("data", {}).get("review_id")
        if rid not in expected or rid in found or row.get("id") != mapping[rid] or row["data"] != expected[rid]:
            raise ValueError("Live task identity differs from frozen import")
        found[rid] = row["id"]
    if found != mapping:
        raise ValueError("Live project task set differs from frozen import")
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def filtered_ids(sess, expected_ids: set[int], *, data: dict | None = None, view_id: int | None = None) -> list[int]:
    path = f"/api/tasks?project={PROJECT}&fields=task_only&resolve_uri=false&include=id"
    path += f"&view={view_id}" if view_id is not None else "&query="+quote(json.dumps({"filters": data["filters"]}, separators=(",", ":")))
    rows = base._pages(sess, path)
    ids = [r.get("id") for r in rows]
    if any(type(i) is not int for i in ids) or len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise ValueError("Server filtered task IDs differ from the requested review set")
    return sorted(ids)


def create_review_views(queue_file: Path, output: Path, *, import_receipt: Path = PREVIOUS,
                        tasks_file: Path = PACK/"tasks.json", source_manifest: Path = PACK/"manifest.jsonl",
                        repair_legacy: bool = False) -> dict:
    if base.BASE != "http://127.0.0.1:8081":
        raise ValueError("Only the authorized local Label Studio instance is supported")
    queue_file, output = Path(queue_file), Path(output)
    if repair_legacy and output.resolve() in {(RESULTS/name).resolve() for name in
            ("label_studio_views_receipt.json", "label_studio_views_reentry.json")}:
        raise ValueError("Legacy repair must preserve the original delivery receipts")
    queue, mapping, expected = read_inputs(queue_file, Path(import_receipt), Path(tasks_file), Path(source_manifest))
    frozen = {str(p): digest(p) for p in (queue_file, Path(import_receipt), Path(tasks_file), Path(source_manifest))}
    source = source_identity()
    sess = base.session()
    project = base.api(sess, "GET", f"/api/projects/{PROJECT}")
    if project.get("id") != PROJECT or project.get("evaluate_predictions_automatically") is not False:
        raise ValueError("Task listing could trigger automatic predictions; project settings were not changed")
    before_identity = task_identity(sess, expected, mapping)
    views = base._pages(sess, f"/api/dm/views?project={PROJECT}")
    plans = []
    for requested in queue["views"]:
        data = view_data(requested, mapping)
        hits = [v for v in views if v.get("data", {}).get("title") == requested["title"]
                or v.get("data", {}).get("curation", {}).get("key") == requested["key"]]
        if len(hits) > 1:
            raise ValueError("Ambiguous existing review view")
        repair = False
        if hits:
            try:
                verify_view(hits[0], data)
            except ValueError:
                if not repair_legacy or hits[0].get("id") not in {45, 46, 47, 48}:
                    raise
                verify_view(hits[0], legacy_view_data(requested, mapping))
                repair = True
        elif repair_legacy:
            raise ValueError("Legacy repair cannot create missing views")
        ids = {mapping[rid] for rid in requested["review_ids"]}
        filtered_ids(sess, ids, data=data)
        plans.append((requested, data, hits[0] if hits else None, ids, repair))
    # Every queue/view/filter is validated before the first view-only mutation.
    next_order = max((v.get("order") or 0 for v in views), default=0)+1
    outcomes, writes = [], []
    for requested, data, existing, ids, repair in plans:
        if existing is None:
            payload = {"project": PROJECT, "order": next_order, "data": deepcopy(data)}
            existing = base.api(sess, "POST", "/api/dm/views/", payload)
            writes.append({"method": "POST", "path": "/api/dm/views/", "view_id": existing["id"]})
            next_order += 1
            created = True
        else:
            created = False
            if repair:
                path = f"/api/dm/views/{existing['id']}/"
                # Re-read immediately before PATCH so edits after preflight stop us.
                current = base.api(sess, "GET", path)
                if current != existing:
                    raise ValueError("Legacy view changed during repair; no overwrite is permitted")
                repaired_data = deepcopy(current["data"])
                repaired_data["filters"] = deepcopy(data["filters"])
                base.api(sess, "PATCH", path, {"data": repaired_data})
                writes.append({"method": "PATCH", "path": path, "view_id": existing["id"], "previous_view": current})
        actual = base.api(sess, "GET", f"/api/dm/views/{existing['id']}/")
        verify_view(actual, data)
        actual_ids = filtered_ids(sess, ids, view_id=actual["id"])
        outcomes.append({"key": requested["key"], "title": requested["title"], "view_id": actual["id"],
            "created": created, "repaired": repair, "task_ids": actual_ids, "count": len(actual_ids),
            "url": f"{base.BASE}/projects/{PROJECT}/data?tab={actual['id']}"})
    after_identity = task_identity(sess, expected, mapping)
    if any(digest(Path(p)) != value for p, value in frozen.items()):
        raise ValueError("Frozen inputs changed during view creation")
    receipt = {"schema_version": 1, "protocol_id": PROTOCOL, "source_manifest_sha256": MANIFEST_SHA,
        "generated_at": datetime.now(timezone.utc).isoformat(), **source, "input_sha256": frozen,
        "project_id": PROJECT, "task_count": len(mapping), "views": outcomes,
        "created": sum(v["created"] for v in outcomes), "repaired": sum(v["repaired"] for v in outcomes),
        "reused": sum(not v["created"] and not v["repaired"] for v in outcomes), "writes": writes,
        "task_data_identity_before": before_identity, "task_data_identity_after": after_identity,
        "task_data_predictions_annotations_drafts_written": False}
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(output.name+".pending")
    pending.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+"\n")
    pending.replace(output)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=RESULTS/"review_queue.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repair-legacy", action="store_true", help="Repair only unchanged legacy views 45–48")
    args = parser.parse_args()
    output = args.output or RESULTS/("label_studio_views_repair.json" if args.repair_legacy else "label_studio_views_receipt.json")
    result = create_review_views(args.queue, output, repair_legacy=args.repair_legacy)
    print(json.dumps({k: result[k] for k in ("project_id", "created", "repaired", "reused", "views")}, ensure_ascii=False, indent=2))
