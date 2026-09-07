"""Import blank manual-review tasks idempotently into native Label Studio 1.13.1.

Grounded in the installed LS projects/tasks/localfiles API and ls_auto_import's
authenticated session. Never sync storage, inject predictions, or fetch annotation
bodies. Document-root symlinks are intentional; local and HTTP SHA checks verify
their targets. Run serially for a given project; only the caller's receipt is written.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from scripts.ls_auto_import import BASE, PROJECT_DIR, api, session


def _pages(sess, path: str) -> list[dict]:
    rows, page = [], 1
    while True:
        result = api(sess, "GET", path + ("&" if "?" in path else "?") + f"page={page}&page_size=100")
        if isinstance(result, list):
            return rows + result
        batch = result.get("tasks", result.get("results"))
        total = result.get("total", result.get("count"))
        if not isinstance(batch, list) or not isinstance(total, int):
            raise ValueError("Unrecognized Label Studio pagination contract")
        rows.extend(batch)
        if len(rows) >= total:
            return rows
        if not batch:
            raise ValueError("Pagination stopped before the reported task count")
        page += 1


def document_root(sess, probe_project: int) -> Path:
    """Ask the running server to validate an existing ancestor; this does not save."""
    try:
        api(sess, "POST", "/api/storages/localfiles/validate", {
            "project": probe_project, "path": str(PROJECT_DIR), "use_blob_urls": False})
    except urllib.error.HTTPError as error:
        text = error.read().decode()
        match = re.search(r"LOCAL_FILES_DOCUMENT_ROOT=(.*?) and must be a child", text)
        if error.code == 400 and match:
            return Path(match.group(1)).absolute()
        raise ValueError("Running server did not disclose a usable document root") from None
    raise ValueError("Pass document_root_path explicitly; root discovery was inconclusive")


def import_blank_project(title: str, tasks_file: str | Path, config_file: str | Path,
                         output: str | Path, document_root_path: str | Path | None = None,
                         batch_size: int = 500) -> dict:
    """Reuse equal data by review_id; add only missing tasks and preserve owner work."""
    if BASE != "http://127.0.0.1:8081" or not title or batch_size < 1:
        raise ValueError("Only the authorized local instance and a positive batch size are supported")
    tasks = json.loads(Path(tasks_file).read_text())
    config = Path(config_file).read_text()
    fields = [node.attrib["value"][1:] for node in ET.fromstring(config).iter()
              if node.tag == "Image" and node.attrib.get("value", "").startswith("$")]
    if not isinstance(tasks, list) or not tasks or not fields:
        raise ValueError("Nonempty tasks and explicit Image data fields are required")
    expected = {}
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"data"} or not isinstance(task["data"], dict):
            raise ValueError("Blank imports must contain only data, never annotations or predictions")
        data = task["data"]; rid = data.get("review_id")
        if not isinstance(rid, str) or not rid or rid in expected:
            raise ValueError("Missing or duplicate review_id")
        expected[rid] = data
    fields = sorted(set(fields) | {key for data in expected.values() for key, value in data.items()
                                   if isinstance(value, str) and urllib.parse.urlsplit(value).path == "/data/local-files/"})
    sess = session()
    api(sess, "POST", "/api/projects/validate/", {"label_config": config})
    projects = _pages(sess, "/api/projects")
    hits = [p for p in projects if p.get("title") == title]
    if len(hits) > 1:
        raise ValueError("Ambiguous duplicate project title")
    if hits and ET.canonicalize(hits[0]["label_config"], strip_text=True) != ET.canonicalize(config, strip_text=True):
        raise ValueError("Existing project labeling configuration differs")
    probe = hits[0]["id"] if hits else projects[0]["id"] if projects else None
    if probe is None:
        raise ValueError("A pre-existing project is required for non-mutating storage preflight")
    root = Path(document_root_path).absolute() if document_root_path else document_root(sess, probe)
    resources, directories = {}, set()
    for rid, data in expected.items():
        for field in fields:
            url = urllib.parse.urlsplit(data.get(field, ""))
            paths = urllib.parse.parse_qs(url.query).get("d", [])
            if url.scheme or url.netloc or url.path != "/data/local-files/" or len(paths) != 1:
                raise ValueError(f"{rid}: {field} must be a relative local-files image URL")
            relative = PurePosixPath(paths[0])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError("Local image URL escapes the configured document root")
            path = root / relative
            digest = data.get(field + "_sha256") or (data.get("future_sha256") if field == "future_image" else None)
            if not path.is_file() or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{rid}: missing local image or SHA-256 for {field}")
            resources[rid, field] = (path, digest); directories.add(str(path.parent))
    for directory in sorted(directories):
        api(sess, "POST", "/api/storages/localfiles/validate", {"project": probe, "path": directory, "use_blob_urls": False})
    pid = hits[0]["id"] if hits else api(sess, "POST", "/api/projects", {"title": title, "label_config": config})["id"]
    task_path = f"/api/tasks?project={pid}&fields=task_only&resolve_uri=false&include=id,data,total_predictions,total_annotations"

    def inspect() -> dict:
        seen = {}
        for row in _pages(sess, task_path):
            rid = row.get("data", {}).get("review_id")
            if rid not in expected or rid in seen or row["data"] != expected[rid]:
                raise ValueError("Existing project has duplicate, foreign, or changed task data")
            if row.get("total_predictions") != 0 or not isinstance(row.get("total_annotations"), int):
                raise ValueError("Predictions are present or blank-import counters are unavailable")
            seen[rid] = row
        return seen

    before = inspect()
    stores = _pages(sess, f"/api/storages/localfiles?project={pid}")
    for directory in sorted(directories):
        matches = [s for s in stores if s.get("path") == directory]
        if any(s.get("use_blob_urls") is not False for s in matches):
            raise ValueError("Existing storage configuration differs")
        if not matches:
            api(sess, "POST", "/api/storages/localfiles", {"project": pid, "title": Path(directory).name, "path": directory, "use_blob_urls": False})
    missing = [{"data": data} for rid, data in expected.items() if rid not in before]
    created_ids = []
    for start in range(0, len(missing), batch_size):
        batch = missing[start:start + batch_size]
        result = api(sess, "POST", f"/api/projects/{pid}/import?return_task_ids=true", batch)
        if result.get("task_count") != len(batch) or len(result.get("task_ids", [])) != len(batch):
            raise ValueError("Import response did not confirm every task ID; rerun safely to reconcile")
        created_ids.extend(result["task_ids"])
    after = inspect()
    if set(after) != set(expected) or {r["id"] for k, r in after.items() if k not in before} != set(created_ids):
        raise ValueError("Post-import task identities do not match the requested pack")
    if len({row["id"] for row in after.values()}) != len(after):
        raise ValueError("Task IDs are not unique")
    if any(row["total_annotations"] for rid, row in after.items() if rid not in before):
        raise ValueError("New tasks unexpectedly contain annotations")
    probes = []
    for position in sorted({0, len(tasks) // 2, len(tasks) - 1}):
        data = tasks[position]["data"]
        for field in fields:
            path, digest = resources[data["review_id"], field]
            with sess[0].open(BASE + data[field], timeout=30) as response:
                body = response.read(); status = response.status
            if status != 200 or hashlib.sha256(body).hexdigest() != digest or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("HTTP image content differs from its declared/local SHA-256")
            probes.append({"review_id": data["review_id"], "field": field, "status": status, "sha256": digest})
    receipt = {"generated_at": datetime.now(timezone.utc).isoformat(), "project_id": pid, "title": title,
               "url": f"{BASE}/projects/{pid}/data", "document_root": str(root), "total": len(after),
               "imported": len(missing), "reused": len(before), "predictions": 0, "task_ids": {k: v["id"] for k, v in after.items()},
               "existing_annotations_preserved": sum(r["total_annotations"] for r in before.values()), "image_checks": probes}
    Path(output).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt
