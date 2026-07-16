#!/bin/bash
# Partial round8 (chunk1+2 finished) + existing golden (r7…) → dense_owner_v9
# → train owner_v10 on 3060 → frozen-eval → promote.
#
# Owner decision 2026-07-17: do NOT wait for chunk3/4; train on the ~1000
# labelled 2026-SWAP windows now. Chunk3/4 v2 can retrain later.
#
# Arms:
#   v10_chain : fine-tune from owner_v9_chain (current owner_best, F1 0.627)
#   v10_coco  : cold start on the enlarged pool (curve check)
#
# Usage:
#   bash scripts/train_owner_v10_from_round8_c12.sh
#   bash scripts/train_owner_v10_from_round8_c12.sh --force
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec > >(tee -a logs/owner_v10_train.log) 2>&1

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

DONE_MARKER="logs/.owner_v10_c12_done"
if [ -f "$DONE_MARKER" ] && [ "$FORCE" = "0" ]; then
  echo "already done ($DONE_MARKER); use --force to rerun"
  exit 0
fi

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
REMOTE="C:/fable"
RRUN="$REMOTE/runs/detect/runs/detect"

echo "=== owner_v10 (r8 c1+c2) pipeline start $(date) ==="

echo "--- 1) export round8_chunk1/2 from Label Studio"
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
opener.open(urllib.request.Request(
    f"{BASE}/user/login/", data=body, headers={"Referer": f"{BASE}/user/login/"}), timeout=30)
projects = json.loads(opener.open(f"{BASE}/api/projects?page_size=100", timeout=30).read())
want = {"round8_chunk1", "round8_chunk2"}
r8 = [p for p in projects.get("results", projects) if str(p.get("title", "")) in want]
assert len(r8) == 2, f"need round8_chunk1+2 in LS, found {[p.get('title') for p in r8]}"

all_tasks, total, done = [], 0, 0
for p in sorted(r8, key=lambda x: x["title"]):
    data = opener.open(
        f"{BASE}/api/projects/{p['id']}/export?exportType=JSON&download_all_tasks=true",
        timeout=300).read()
    (OUT_DIR / f"export_{p['title']}.json").write_bytes(data)
    tasks = json.loads(data)
    n_ann = sum(1 for t in tasks if t.get("annotations"))
    print(f"  {p['title']}: {len(tasks)} 任务, {n_ann} 已标", flush=True)
    total += len(tasks)
    done += n_ann
    all_tasks.extend(tasks)

(OUT_DIR / "export_round8_c12.json").write_text(
    json.dumps(all_tasks, ensure_ascii=False), encoding="utf-8")
pct = done / total * 100 if total else 0
print(f"  合计 {done}/{total} 已标 ({pct:.0f}%)", flush=True)
if pct < 90 and not FORCE:
    raise SystemExit(f"round8 c1+c2 只标了 {pct:.0f}% (<90%) —— 先标完，或用 --force")
PY
[ $? -ne 0 ] && { echo "=== 中止: round8 c1+c2 未标完 $(date) ==="; exit 1; }

echo "--- 2) merge round8 c1+c2 into golden_pool"
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
for t in json.loads(Path("output/label_studio/export_round8_c12.json").read_text()):
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
print(json.dumps({
    "pool_before": before, "pool_after": len(pool), "added": added,
    "updated": updated, "boxes": boxes, "backgrounds": bg,
}, indent=2), flush=True)
PY

echo "--- 3) build datasets/dense_owner_v9 (includes dense_2026h1; eval excluded)"
$PY - <<'PY'
from __future__ import annotations
import json, shutil
from pathlib import Path

PROJECT = Path(".").resolve()
POOL = json.loads((PROJECT / "data/golden_pool.json").read_text())
DST = PROJECT / "datasets/dense_owner_v9"
# Image roots: historical packs + 2026 H1 renders for round8 stems
SRC_DIRS = [
    PROJECT / "datasets" / d / "images" / s
    for d in (
        "dense_15m_full", "dense_swap_v1", "round6_scout",
        "dense_owner_v6", "dense_owner_v7", "dense_owner_v8",
        "dense_2026h1",
    )
    for s in ("train", "val")
]
# dense_2026h1 layout is images/train only
SRC_DIRS.append(PROJECT / "datasets/dense_2026h1/images/train")

from src.detection.owner_eval import is_eval_stem as is_eval, split_of

def find(stem: str):
    for d in SRC_DIRS:
        if not d.exists():
            continue
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
missing = []
for stem, boxes in POOL.items():
    if is_eval(stem):
        skip_eval += 1
        continue
    src = find(stem)
    if src is None:
        skip_missing += 1
        if len(missing) < 12:
            missing.append(stem)
        continue
    sp = split_of(stem)
    shutil.copy2(src, DST / "images" / sp / src.name)
    (DST / "labels" / sp / f"{stem}.txt").write_text(
        "".join(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n" for cx, cy, w, h in boxes)
    )
    counts[sp][0] += 1
    counts[sp][1] += len(boxes)

(DST / "data.yaml").write_text(
    f"path: {DST}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n"
)
print(json.dumps({
    "train": {"images": counts["train"][0], "boxes": counts["train"][1]},
    "val": {"images": counts["val"][0], "boxes": counts["val"][1]},
    "skipped_eval": skip_eval,
    "skipped_missing": skip_missing,
    "missing_sample": missing,
}, indent=2), flush=True)
assert counts["train"][0] > 1000, f"训练集太小 ({counts['train'][0]})，中止"
assert skip_missing < counts["train"][0] * 0.15, (
    f"缺图过多 missing={skip_missing} train={counts['train'][0]} — 检查 SRC_DIRS / dense_2026h1"
)
import math
n = counts["train"][0]
print(f"\n  曲线粗预测 coco @ {n} train 图 -> F1 {0.067*math.log2(n)-0.2647:.3f}", flush=True)
PY
[ $? -ne 0 ] && { echo "=== 中止: 数据集构建失败 $(date) ==="; exit 1; }

echo "--- 4) ship dense_owner_v9 + v9 base weights to 3060"
TAR=$(mktemp -t fable_v9ds).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C datasets dense_owner_v9
scp -o BatchMode=yes -q "$TAR" "$HOST:$REMOTE/ds9.tar" || { echo "scp dataset 失败"; exit 1; }
scp -o BatchMode=yes -q runs/detect/runs/detect/owner_v9_chain/weights/best.pt \
  "$HOST:$REMOTE/base_v9.pt" || { echo "scp base_v9 失败"; exit 1; }
$SSH "$HOST" "cd $REMOTE; Remove-Item -Recurse -Force datasets/dense_owner_v9 -EA SilentlyContinue; tar xf ds9.tar -C datasets; Remove-Item ds9.tar; Get-ChildItem -Path datasets\dense_owner_v9 -Recurse -Force -Filter '._*' -EA SilentlyContinue | Remove-Item -Force"
rm -f "$TAR"
$SSH "$HOST" '$d="C:\fable\datasets\dense_owner_v9"; foreach ($s in @("train","val")) { "  {0}: {1} images / {2} labels" -f $s, (Get-ChildItem "$d\images\$s\*.png" -EA SilentlyContinue).Count, (Get-ChildItem "$d\labels\$s\*.txt" -EA SilentlyContinue).Count }'

run_arm() {
  name="$1"; model="$2"; epochs="$3"; patience="$4"
  echo "--- 训练 $name $(date '+%H:%M:%S')"
  $SSH "$HOST" "
\$cmd = 'cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $name --model $model --dataset C:/fable/datasets/dense_owner_v9 --epochs $epochs --patience $patience --cache false --workers 4 > C:\fable\$name.log 2>&1'
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null"
  sleep 90
  for i in $(seq 1 500); do
    a=$($SSH "$HOST" 'if (Get-Process python* -EA SilentlyContinue) { "y" } else { "n" }' 2>/dev/null | tr -d '\r')
    [ "$a" != "y" ] && break
    if [ $((i % 4)) -eq 0 ]; then
      echo "  … still training $name $(date '+%H:%M:%S')"
    fi
    sleep 45
  done
  mkdir -p "runs/detect/runs/detect/$name/weights"
  for f in weights/best.pt results.csv args.yaml; do
    scp -o BatchMode=yes -q "$HOST:$RRUN/$name/$f" "runs/detect/runs/detect/$name/$f" 2>/dev/null
  done
  echo "  取回完成 $name $(date '+%H:%M:%S')"
}

echo "--- 5) two arms on the 3060"
run_arm owner_v10_chain "C:\\fable\\base_v9.pt" 40 10
run_arm owner_v10_coco  "C:\\fable\\yolo11s.pt" 100 20

echo "--- 6) frozen-eval + promote"
$PY - <<'PY'
import csv, json, math
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1

lines = []
for run in ("owner_v10_chain", "owner_v10_coco"):
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
        try:
            import importlib.util as _il
            spec = _il.spec_from_file_location("bc", "scripts/benchmark_check.py")
            bc = _il.module_from_spec(spec); spec.loader.exec_module(bc)
            gate = bc.run(w)
            g = "⭐通过" if gate["passed"] else "⭐❌不通过"
            extra = f"  {g}(训{gate['train']['recall']}/评{gate['eval']['recall']})"
        except Exception as e:
            extra = f"  ⭐check_skip({e})"
        lines.append(
            f"{run}  F1 {best['f1']:.3f}  P {best['p']:.3f}  R {best['r']:.3f}  {shape}{extra}"
        )
        print("  " + lines[-1], flush=True)

n = len(list(Path("datasets/dense_owner_v9/images/train").glob("*.png")))
pred = 0.067 * math.log2(max(n, 2)) - 0.2647
print(f"\n  曲线粗预测 coco @ {n} 图 = {pred:.3f}", flush=True)
print(f"  baseline owner_best = v9_chain F1 0.627", flush=True)

try:
    from src.notify import send
    send(
        "🧠 <b>owner_v10 (r8 c1+c2) 训练完成</b>\n<code>"
        + "\n".join(lines)
        + f"\n\npool train≈{n}  粗预测 coco F1≈{pred:.3f}</code>\n"
        "相对 v9_chain 0.627；chunk3/4 v2 标完可再训一刀"
    )
except Exception as e:
    print(f"  notify skip: {e}", flush=True)
PY

$PY scripts/promote_owner_best.py 2>&1 | tail -20

date > "$DONE_MARKER"
echo "=== owner_v10 pipeline done $(date) ==="
echo "（要重跑先删 $DONE_MARKER 或加 --force）"
