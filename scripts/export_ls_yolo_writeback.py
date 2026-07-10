"""Export Label Studio project boxes to YOLO txt under a fingerprinted dry-run dir.

Never writes into datasets/. Prefer human annotations over predictions.
Reads VPS credentials from untracked LABEL_STUDIO_VPS_ACCESS.md (no secret print).

Example:
  .venv_label_studio_qa/bin/python scripts/export_ls_yolo_writeback.py --limit 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
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
    ap.add_argument("--limit", type=int, default=5)
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

    tasks = s.get(
        f"{url}/api/projects/{args.project_id}/tasks/?page_size=100",
        timeout=60,
    ).json()
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks") or tasks.get("results") or []
    cands = [t for t in tasks if (t.get("total_predictions") or 0) > 0]
    if not cands:
        cands = list(tasks)
    cands = cands[: max(0, args.limit)]
    print(f"EXPORT_N={len(cands)} project_id={args.project_id}")

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

    mf = {
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": args.project_id,
        "n_stems": len(manifest_items),
        "class_map": CLASS_MAP,
        "note": "Does not overwrite datasets/dense_15m_full; owner must approve promote",
        "items": manifest_items,
    }
    blob = json.dumps(mf, sort_keys=True).encode()
    mf["manifest_sha256"] = hashlib.sha256(blob).hexdigest()
    (out / "MANIFEST.json").write_text(json.dumps(mf, indent=2), encoding="utf-8")
    print(f"WROTE_MANIFEST sha256={mf['manifest_sha256']}")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
