#!/bin/bash
# round7 labels done in LS -> export -> golden_pool -> dense_owner_v8 -> train on
# the 3060 -> frozen-eval -> promote. The Mac owns every decision; the 3060 only
# fits weights.
#
# Two arms, and the second one is the point:
#   v9_chain : fine-tune from v8_chain (0.650) with the fixed lr -> production candidate
#   v9_coco  : cold start, extends the label-value curve to a 4th point
#
# The curve measured 2026-07-16 (nested subsets, one machine, identical val) is
#     F1 ≈ 0.067*log2(train_images) - 0.265
#     1061 -> 0.415   2122 -> 0.463   4245 -> 0.549
# and it PREDICTS v9_coco at ~6500 train images scores ~0.584 (+0.035 over 4245).
# That prediction is registered here, before the run, on purpose: if v9_coco lands
# far off it, the curve is wrong and the labelling ROI has to be re-derived rather
# than explained away.
#
# Usage:
#   bash scripts/train_owner_v9_from_round7.sh          # refuses if round7 unfinished
#   bash scripts/train_owner_v9_from_round7.sh --force  # run on whatever is labelled
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/owner_v9_train.log 2>&1

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# This is polled from cron every 30 min so it fires the moment round7 is done.
# Without a marker a successful run would retrain v9 on every subsequent tick,
# burning the GPU and rewriting owner_best in a loop. Delete the marker to rerun.
DONE_MARKER="logs/.owner_v9_done"
if [ -f "$DONE_MARKER" ] && [ "$FORCE" = "0" ]; then
  exit 0
fi

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
REMOTE="C:/fable"
RRUN="$REMOTE/runs/detect/runs/detect"

echo "=== owner_v9 pipeline start $(date) ==="

echo "--- 1) export round7 projects from Label Studio"
$PY - "$FORCE" <<'PY'
from __future__ import annotations
import http.cookiejar, json, re, sys, urllib.parse, urllib.request
from pathlib import Path

FORCE = sys.argv[1] == "1"
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
page = opener.open(f"{BASE}/user/login/", timeout=30).read().decode()
m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page)
csrf = m.group(1) if m else next((c.value for c in jar if c.name == "csrftoken"), "")
body = urllib.parse.urlencode({"email": user, "password": pw, "csrfmiddlewaretoken": csrf}).encode()
opener.open(urllib.request.Request(f"{BASE}/user/login/", data=body,
                                   headers={"Referer": f"{BASE}/user/login/"}), timeout=30)
projects = json.loads(opener.open(f"{BASE}/api/projects?page_size=100", timeout=30).read())
r7 = [p for p in projects.get("results", projects) if str(p.get("title", "")).startswith("round7_chunk")]
assert r7, "Label Studio 里没有 round7 项目"

all_tasks, total, done = [], 0, 0
for p in sorted(r7, key=lambda x: x["id"]):
    data = opener.open(
        f"{BASE}/api/projects/{p['id']}/export?exportType=JSON&download_all_tasks=true",
        timeout=300).read()
    (OUT_DIR / f"export_{p['title']}.json").write_bytes(data)
    tasks = json.loads(data)
    n_ann = sum(1 for t in tasks if t.get("annotations"))
    print(f"  {p['title']}: {len(tasks)} 任务, {n_ann} 已标", flush=True)
    total += len(tasks); done += n_ann
    all_tasks.extend(tasks)

(OUT_DIR / "export_round7_all.json").write_text(json.dumps(all_tasks, ensure_ascii=False),
                                                encoding="utf-8")
pct = done / total * 100 if total else 0
print(f"  合计 {done}/{total} 已标 ({pct:.0f}%)", flush=True)
# Training on a half-labelled round would put a number on the curve that is not
# the number that round is worth; better to stop than to log a misleading point.
if pct < 90 and not FORCE:
    raise SystemExit(f"round7 只标了 {pct:.0f}% (<90%) —— 先标完，或用 --force")
PY
[ $? -ne 0 ] && { echo "=== 中止: round7 未标完 $(date) ==="; exit 1; }

echo "--- 2) merge round7 into golden_pool"
$PY - <<'PY'
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from golden_disagreement import rects  # noqa: E402

POOL = Path("data/golden_pool.json")
pool = json.loads(POOL.read_text())
before = len(pool)
added = updated = boxes = bg = 0
for t in json.loads(Path("output/label_studio/export_round7_all.json").read_text()):
    stem = t.get("data", {}).get("stem")
    if not stem or not t.get("annotations"):
        continue
    ann = sorted(t["annotations"], key=lambda a: a.get("updated_at") or a.get("created_at") or "")[-1]
    owner = rects(ann)
    updated += stem in pool
    added += stem not in pool
    pool[stem] = owner
    boxes += len(owner)
    bg += not owner
POOL.write_text(json.dumps(pool, ensure_ascii=False), encoding="utf-8")
print(json.dumps({"pool_before": before, "pool_after": len(pool), "added": added,
                  "updated": updated, "boxes": boxes, "backgrounds": bg}, indent=2), flush=True)
PY

echo "--- 3) build datasets/dense_owner_v8 (eval symbols excluded)"
$PY - <<'PY'
from __future__ import annotations
import hashlib, json, shutil
from pathlib import Path

PROJECT = Path(".").resolve()
POOL = json.loads((PROJECT / "data/golden_pool.json").read_text())
DST = PROJECT / "datasets/dense_owner_v8"
SRC_DIRS = [PROJECT / "datasets" / d / "images" / s
            for d in ("dense_15m_full", "dense_swap_v1", "round6_scout",
                      "dense_owner_v6", "dense_owner_v7")
            for s in ("train", "val")]

from src.detection.owner_eval import is_eval_stem as is_eval, split_of  # one source of truth

def find(stem):
    for d in SRC_DIRS:
        for ext in (".png", ".jpg"):
            p = d / f"{stem}{ext}"
            if p.exists():
                return p
    return None

if DST.exists():
    shutil.rmtree(DST)
for sub in ("images/train", "images/val", "labels/train", "labels/val"):
    (DST / sub).mkdir(parents=True, exist_ok=True)

counts = {"train": [0, 0], "val": [0, 0]}
skip_eval = skip_missing = 0
for stem, boxes in POOL.items():
    if is_eval(stem):
        skip_eval += 1; continue
    src = find(stem)
    if src is None:
        skip_missing += 1; continue
    sp = split_of(stem)
    shutil.copy2(src, DST / "images" / sp / src.name)
    (DST / "labels" / sp / f"{stem}.txt").write_text(
        "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes))
    counts[sp][0] += 1; counts[sp][1] += len(boxes)

(DST / "data.yaml").write_text(
    f"path: {DST}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n")
print(json.dumps({"train": {"images": counts["train"][0], "boxes": counts["train"][1]},
                  "val": {"images": counts["val"][0], "boxes": counts["val"][1]},
                  "skipped_eval": skip_eval, "skipped_missing": skip_missing}, indent=2), flush=True)
assert counts["train"][0] > 1000, "训练集太小，中止"
# Register the curve's prediction before training, so it cannot be rationalised after.
import math
n = counts["train"][0]
print(f"\n  曲线预测 v9_coco @ {n} train 图 -> F1 {0.067*math.log2(n)-0.2647:.3f}", flush=True)
PY
[ $? -ne 0 ] && { echo "=== 中止: 数据集构建失败 $(date) ==="; exit 1; }

echo "--- 4) ship dataset to the 3060"
TAR=$(mktemp -t fable_v8).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C datasets dense_owner_v8
scp -o BatchMode=yes -q "$TAR" "$HOST:$REMOTE/ds8.tar" || { echo "scp 失败"; exit 1; }
scp -o BatchMode=yes -q runs/detect/runs/detect/owner_v8_chain/weights/best.pt \
  "$HOST:$REMOTE/base_v8.pt" || { echo "scp base 失败"; exit 1; }
$SSH "$HOST" "cd $REMOTE; Remove-Item -Recurse -Force datasets/dense_owner_v8 -EA SilentlyContinue; tar xf ds8.tar -C datasets; Remove-Item ds8.tar; Get-ChildItem -Path datasets\dense_owner_v8 -Recurse -Force -Filter '._*' -EA SilentlyContinue | Remove-Item -Force"
rm -f "$TAR"
$SSH "$HOST" '$d="C:\fable\datasets\dense_owner_v8"; foreach ($s in @("train","val")) { "  {0}: {1} images / {2} labels" -f $s, (Get-ChildItem "$d\images\$s\*.png" -EA SilentlyContinue).Count, (Get-ChildItem "$d\labels\$s\*.txt" -EA SilentlyContinue).Count }'

run_arm() {
  name="$1"; model="$2"; epochs="$3"; patience="$4"
  echo "--- 训练 $name $(date '+%H:%M:%S')"
  $SSH "$HOST" "
\$cmd = 'cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $name --model $model --dataset C:/fable/datasets/dense_owner_v8 --epochs $epochs --patience $patience --cache false --workers 4 > C:\fable\$name.log 2>&1'
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null"
  sleep 90
  for i in $(seq 1 400); do
    a=$($SSH "$HOST" 'if (Get-Process python* -EA SilentlyContinue) { "y" } else { "n" }' 2>/dev/null | tr -d '\r')
    [ "$a" != "y" ] && break
    sleep 45
  done
  mkdir -p "runs/detect/runs/detect/$name/weights"
  for f in weights/best.pt results.csv args.yaml; do
    scp -o BatchMode=yes -q "$HOST:$RRUN/$name/$f" "runs/detect/runs/detect/$name/$f" 2>/dev/null
  done
  echo "  取回完成 $(date '+%H:%M:%S')"
}

echo "--- 5) two arms on the 3060"
run_arm owner_v9_chain "C:\\fable\\base_v8.pt" 40 10
run_arm owner_v9_coco  "C:\\fable\\yolo11s.pt" 100 20

echo "--- 6) curve shape check, then frozen-eval + promote"
$PY - <<'PY'
import csv, json, math
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1

lines = []
for run in ("owner_v9_chain", "owner_v9_coco"):
    r = Path(f"runs/detect/runs/detect/{run}/results.csv")
    if not r.exists():
        print(f"  {run}: 没有 results.csv"); continue
    rows = list(csv.DictReader(open(r)))
    m = next(c for c in rows[0] if "mAP50(B)" in c)
    v = [float(x[m]) for x in rows]
    bi = max(range(len(v)), key=lambda i: v[i])
    n_col = sum(1 for x in v[bi:] if x < v[bi] * 0.2)
    shape = ("❌ best=预热轮，等于没训" if bi + 1 <= 2
             else "⚠️ 峰后剧烈震荡" if n_col > (len(v) - bi) * 0.25 else "✅ 曲线健康")
    print(f"  {run}: {len(v)}轮 best@{rows[bi]['epoch'].strip()} mAP50={v[bi]:.4f} {shape}", flush=True)

    w = Path(f"runs/detect/runs/detect/{run}/weights/best.pt")
    if w.exists():
        best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
        # Exemplar gate: a model that misses the owner's textbook cases is broken
        # regardless of its F1 -- this is what would have caught the lr bug on day 1.
        import importlib.util as _il
        spec = _il.spec_from_file_location("bc", "scripts/benchmark_check.py")
        bc = _il.module_from_spec(spec); spec.loader.exec_module(bc)
        gate = bc.run(w)
        g = "⭐通过" if gate["passed"] else "⭐❌不通过"
        lines.append(f"{run}  F1 {best['f1']:.3f}  P {best['p']:.3f}  R {best['r']:.3f}  "
                     f"{shape}  {g}(训{gate['train']['recall']}/评{gate['eval']['recall']})")
        print("  " + lines[-1], flush=True)

n = len(list(Path("datasets/dense_owner_v8/images/train").glob("*.png")))
pred = 0.067 * math.log2(n) - 0.2647
print(f"\n  曲线预测 v9_coco @ {n} 图 = {pred:.3f}（预测在训练前登记）", flush=True)

from src.notify import send
send("🧠 <b>owner_v9 训练完成</b>\n<code>" + "\n".join(lines) +
     f"\n\n曲线预测 v9_coco @ {n}图 = {pred:.3f}</code>\n预测偏差大 = 曲线本身要重估")
PY

$PY scripts/promote_owner_best.py 2>&1 | tail -6

date > "$DONE_MARKER"
echo "=== owner_v9 pipeline done $(date) ==="
echo "（cron 不会再触发；要重跑先删 $DONE_MARKER）"
