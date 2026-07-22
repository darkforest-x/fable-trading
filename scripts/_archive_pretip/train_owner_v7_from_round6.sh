#!/bin/bash
# Round-6 labels are done in LS → export → golden_pool → dense_owner_v7 → train dual base → promote.
# Usage: bash scripts/train_owner_v7_from_round6.sh
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/owner_v7_train.log 2>&1
PY="${PY:-.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi
export PYTHONPATH=.
echo "=== owner_v7 pipeline start $(date) ==="

echo "--- 1) export all finished round6 LS projects"
$PY - <<'PY'
from __future__ import annotations
import http.cookiejar, json, re, urllib.parse, urllib.request
from pathlib import Path

PROJECT = Path(".").resolve()
BASE = "http://127.0.0.1:8081"
OUT_DIR = PROJECT / "output/label_studio"
OUT_DIR.mkdir(parents=True, exist_ok=True)

env = {}
for line in (PROJECT / "scripts/.label_studio.env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
user = env.get("LABEL_STUDIO_USERNAME") or "fable-review@example.com"
pw = env.get("LABEL_STUDIO_PASSWORD") or "fable-review-local"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login_page = opener.open(f"{BASE}/user/login/", timeout=30).read().decode()
m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', login_page)
csrf = m.group(1) if m else next((c.value for c in jar if c.name == "csrftoken"), "")
body = urllib.parse.urlencode({"email": user, "password": pw, "csrfmiddlewaretoken": csrf}).encode()
req = urllib.request.Request(f"{BASE}/user/login/", data=body, headers={"Referer": f"{BASE}/user/login/"})
opener.open(req, timeout=30)
projects = json.loads(opener.open(f"{BASE}/api/projects?page_size=100", timeout=30).read())
results = projects.get("results", projects)
r6 = [p for p in results if str(p.get("title", "")).startswith("round6_halfhalf")]
assert r6, "no round6 projects in Label Studio"
print(f"found {len(r6)} round6 projects", flush=True)

all_tasks = []
for p in sorted(r6, key=lambda x: x["id"]):
    pid, title = p["id"], p["title"]
    data = opener.open(
        f"{BASE}/api/projects/{pid}/export?exportType=JSON&download_all_tasks=true",
        timeout=180,
    ).read()
    path = OUT_DIR / f"export_{title}.json"
    path.write_bytes(data)
    tasks = json.loads(data)
    n_ann = sum(1 for t in tasks if t.get("annotations"))
    print(f"  {title} id={pid}: {len(tasks)} tasks, {n_ann} annotated -> {path.name}", flush=True)
    all_tasks.extend(tasks)

merged_path = OUT_DIR / "export_round6_all.json"
merged_path.write_text(json.dumps(all_tasks, ensure_ascii=False), encoding="utf-8")
print(f"merged export: {len(all_tasks)} tasks -> {merged_path}", flush=True)
PY

echo "--- 2) merge round6 into golden_pool (newer stems win)"
$PY - <<'PY'
from __future__ import annotations
import json
from pathlib import Path
import sys
sys.path.insert(0, "scripts")
from golden_disagreement import rects  # noqa: E402

PROJECT = Path(".").resolve()
POOL_PATH = PROJECT / "data/golden_pool.json"
EXPORT = PROJECT / "output/label_studio/export_round6_all.json"
pool = json.loads(POOL_PATH.read_text())
before = len(pool)
added = updated = boxes = backgrounds = 0
for t in json.loads(EXPORT.read_text()):
    stem = t.get("data", {}).get("stem")
    if not stem or not t.get("annotations"):
        continue
    # latest annotation (LS may keep history)
    ann = sorted(t["annotations"], key=lambda a: a.get("updated_at") or a.get("created_at") or "")[-1]
    owner = rects(ann)
    if stem in pool:
        updated += 1
    else:
        added += 1
    pool[stem] = owner
    boxes += len(owner)
    if not owner:
        backgrounds += 1
POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
print(json.dumps({
    "pool_before": before,
    "pool_after": len(pool),
    "round6_added": added,
    "round6_updated": updated,
    "round6_boxes": boxes,
    "round6_backgrounds": backgrounds,
}, indent=2), flush=True)
PY

echo "--- 3) build datasets/dense_owner_v7 (exclude frozen-eval symbols)"
$PY - <<'PY'
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path

PROJECT = Path(".").resolve()
POOL = json.loads((PROJECT / "data/golden_pool.json").read_text())
DST = PROJECT / "datasets/dense_owner_v7"
SRC_DIRS = [
    PROJECT / "datasets/dense_15m_full/images/val",
    PROJECT / "datasets/dense_15m_full/images/train",
    PROJECT / "datasets/dense_swap_v1/images/train",
    PROJECT / "datasets/dense_swap_v1/images/val",
    PROJECT / "datasets/round6_scout/images/train",
    PROJECT / "datasets/dense_owner_v6/images/train",
    PROJECT / "datasets/dense_owner_v6/images/val",
]

from src.detection.owner_eval import is_eval_stem as is_eval_symbol, split_of  # one source of truth

def find_image(stem: str) -> Path | None:
    for d in SRC_DIRS:
        for ext in (".png", ".jpg"):
            p = d / f"{stem}{ext}"
            if p.exists():
                return p
    return None

# clean rebuild
if DST.exists():
    shutil.rmtree(DST)
for sub in ("images/train", "images/val", "labels/train", "labels/val"):
    (DST / sub).mkdir(parents=True, exist_ok=True)

counts = {"train": [0, 0], "val": [0, 0]}
skipped_eval = skipped_missing = 0
for stem, boxes in POOL.items():
    if is_eval_symbol(stem):
        skipped_eval += 1
        continue
    src = find_image(stem)
    if src is None:
        skipped_missing += 1
        continue
    split = split_of(stem)
    shutil.copy2(src, DST / "images" / split / src.name)
    (DST / "labels" / split / f"{stem}.txt").write_text(
        "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes)
    )
    counts[split][0] += 1
    counts[split][1] += len(boxes)

(DST / "data.yaml").write_text(
    f"path: {DST}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n"
)
print(json.dumps({
    "dataset": str(DST),
    "train": {"images": counts["train"][0], "boxes": counts["train"][1]},
    "val": {"images": counts["val"][0], "boxes": counts["val"][1]},
    "skipped_eval": skipped_eval,
    "skipped_missing_image": skipped_missing,
}, indent=2), flush=True)
assert counts["train"][0] > 1000, "train set too small"
PY

CHAIN_BASE="runs/detect/runs/detect/owner_v6_chain/weights/best.pt"
if [ ! -f "$CHAIN_BASE" ]; then
  CHAIN_BASE="runs/detect/runs/detect/owner_v6_coco/weights/best.pt"
fi
if [ ! -f "$CHAIN_BASE" ]; then
  CHAIN_BASE="models/owner_best.pt"
fi

echo "--- 4) train owner_v7_chain (fine-tune from $CHAIN_BASE)"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v7/data.yaml \
  --model "$CHAIN_BASE" \
  --epochs 40 --patience 10 --name owner_v7_chain

echo "--- 5) train owner_v7_coco (cold start)"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v7/data.yaml \
  --model yolo11s.pt \
  --epochs 100 --patience 20 --name owner_v7_coco

echo "--- 6) frozen-eval + promote"
$PY - <<'PY'
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1

prev_path = Path("analysis/output/frozen_eval_comparison.json")
prev = json.loads(prev_path.read_text()) if prev_path.exists() else {}
for run in ("owner_v7_chain", "owner_v7_coco"):
    w = Path(f"runs/detect/runs/detect/{run}/weights/best.pt")
    if w.exists():
        best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
        prev[run.replace("owner_", "")] = best
        print(run, "frozen-F1", best["f1"], "P", best["p"], "R", best["r"], flush=True)
prev_path.write_text(json.dumps(prev, indent=2), encoding="utf-8")
PY
$PY scripts/promote_owner_best.py

$PY - <<'PY'
from src.notify import send
import json
from pathlib import Path
b = json.loads(Path("models/owner_best.json").read_text()) if Path("models/owner_best.json").exists() else {}
d = json.loads(Path("analysis/output/frozen_eval_comparison.json").read_text())
v7 = {k: v for k, v in d.items() if "v7" in k}
send(
    "🧠 <b>owner_v7 训练完成</b>\n"
    f"promoted: <code>{b.get('source_run','?')}</code> frozen-F1 {b.get('frozen_eval_f1','?')}\n"
    f"v7: {json.dumps(v7, ensure_ascii=False)[:800]}"
)
print("tg notify done", flush=True)
PY

echo "=== owner_v7 pipeline done $(date) ==="
