"""Export Label Studio project boxes to YOLO txt under a fingerprinted dry-run dir.

Never writes into datasets/. Prefer human annotations over predictions.
Reads VPS credentials from untracked LABEL_STUDIO_VPS_ACCESS.md (no secret print).

Exports ALL project tasks up to --limit (not only tasks with prelabels). Empty
stems get an empty YOLO txt with source=none so MANIFEST covers the review pack.

Example:
  .venv_label_studio_qa/bin/python scripts/export_ls_yolo_writeback.py --limit 80
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCESS = ROOT / "output/offline_tasks/LABEL_STUDIO_VPS_ACCESS.md"
DEFAULT_OUT = ROOT / "output/label_studio/writeback_dryrun"
CLASS_MAP = {"dense_cluster": 0}


def _val(text: str, key: str) -> str:
    m = re.search(rf"-\s*{key}:\s*\*?\*?`?([^\s*`]+)`?\*?\*?", text, re.I)
    if not m:
        raise SystemExit(f"missing credential key: {key}")
    return m.group(1).strip()


def _session(url: str, email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.get(f"{url}/user/login/", timeout=30)
    csrf = s.cookies.get("csrftoken")
    headers = {"X-CSRFToken": csrf, "Referer": f"{url}/user/login/"} if csrf else {}
    s.post(
        f"{url}/user/login/",
        data={"email": email, "password": password},
        headers=headers,
        allow_redirects=True,
        timeout=30,
    )
    token = s.get(f"{url}/api/current-user/token", timeout=30).json().get("token")
    if not token:
        raise SystemExit("no API token")
    s.headers["Authorization"] = f"Token {token}"
    return s


def fetch_all_tasks(s: requests.Session, url: str, project_id: int) -> list:
    """Page through project tasks until exhausted (page_size 50)."""
    tasks: list = []
    page = 1
    while True:
        r = s.get(
            f"{url}/api/projects/{project_id}/tasks/",
            params={"page": page, "page_size": 50},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            batch = data.get("tasks") or data.get("results") or []
            total = data.get("count") or data.get("total")
        else:
            batch = data
            total = None
        if not batch:
            break
        tasks.extend(batch)
        if total is not None and len(tasks) >= int(total):
            break
        if len(batch) < 50:
            break
        page += 1
    return tasks


def ls_rect_to_yolo(value: dict) -> tuple[float, float, float, float]:
    """Label Studio percent x,y,w,h → YOLO normalized cx,cy,w,h."""
    x, y, w, h = (
        float(value["x"]),
        float(value["y"]),
        float(value["width"]),
        float(value["height"]),
    )
    cx = min(1.0, max(0.0, (x + w / 2) / 100.0))
    cy = min(1.0, max(0.0, (y + h / 2) / 100.0))
    nw = min(1.0, max(0.0, w / 100.0))
    nh = min(1.0, max(0.0, h / 100.0))
    return cx, cy, nw, nh


def collect_boxes(detail: dict) -> tuple[list, str]:
    """Prefer human annotations over model predictions; else none."""
    boxes: list = []
    if detail.get("annotations"):
        for a in detail["annotations"]:
            for r in a.get("result") or []:
                if r.get("type") == "rectanglelabels":
                    boxes.append(r)
        if boxes:
            return boxes, "annotation"
    for p in detail.get("predictions") or []:
        for r in p.get("result") or []:
            if r.get("type") == "rectanglelabels":
                boxes.append(r)
    return boxes, ("prediction" if boxes else "none")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--access", type=Path, default=DEFAULT_ACCESS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--project-id", type=int, default=1)
    ap.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max tasks to export (default 5 dry-run; use 80 for full pack)",
    )
    args = ap.parse_args()

    out: Path = args.out
    # hard guard: never write under datasets/
    rel = out.resolve()
    if "datasets" in rel.parts:
        print("FAIL: output path must not be under datasets/", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    text = args.access.read_text(encoding="utf-8")
    url = _val(text, "URL").rstrip("/")
    email = _val(text, "Email")
    password = _val(text, "Password")
    s = _session(url, email, password)

    all_tasks = fetch_all_tasks(s, url, args.project_id)
    # Stable order by task id descending (newest first matches prior dry-runs).
    all_tasks = sorted(all_tasks, key=lambda t: int(t["id"]), reverse=True)
    cands = all_tasks[: max(0, args.limit)]
    print(
        f"EXPORT_N={len(cands)} project_tasks={len(all_tasks)} "
        f"project_id={args.project_id}"
    )

    # Clear prior dry-run txt so stale stems do not linger after re-export.
    for old in out.glob("*.txt"):
        old.unlink()

    manifest_items = []
    for t in cands:
        detail = s.get(f"{url}/api/tasks/{t['id']}/", timeout=30).json()
        boxes, source = collect_boxes(detail)
        lines = []
        for r in boxes:
            v = r["value"]
            cx, cy, nw, nh = ls_rect_to_yolo(v)
            labels = v.get("rectanglelabels") or ["dense_cluster"]
            cls = CLASS_MAP.get(labels[0], 0)
            lines.append(f"{cls} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")
        stem = (detail.get("data") or {}).get("stem") or f"task_{detail['id']}"
        yolo_path = out / f"{stem}.txt"
        body = "\n".join(lines) + ("\n" if lines else "")
        yolo_path.write_text(body, encoding="utf-8")
        item = {
            "stem": stem,
            "task_id": detail["id"],
            "source": source,
            "n_boxes": len(lines),
            "yolo_rel": str(yolo_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(body.encode()).hexdigest(),
            "image_ref": (detail.get("data") or {}).get("image"),
        }
        if item["yolo_rel"].startswith("datasets/"):
            raise SystemExit("refusing datasets path")
        manifest_items.append(item)
        print(f"{stem} source={source} boxes={len(lines)}")

    source_counts = dict(Counter(i["source"] for i in manifest_items))
    mf = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": "dense_15m_val_audit",
        "project_id": args.project_id,
        "n_stems": len(manifest_items),
        "project_task_count": len(all_tasks),
        "source_counts": source_counts,
        "class_map": CLASS_MAP,
        "note": "DRY-RUN only; does not overwrite datasets/dense_15m_full",
        "items": manifest_items,
    }
    blob = json.dumps(mf, sort_keys=True).encode()
    mf["manifest_sha256"] = hashlib.sha256(blob).hexdigest()
    (out / "MANIFEST.json").write_text(json.dumps(mf, indent=2), encoding="utf-8")
    print(f"SOURCE_COUNTS={json.dumps(source_counts, sort_keys=True)}")
    print(f"WROTE_MANIFEST sha256={mf['manifest_sha256']}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
