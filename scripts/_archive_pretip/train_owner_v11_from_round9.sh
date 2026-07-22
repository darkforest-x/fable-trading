#!/bin/bash
# round9 (2025-H2 fresh windows) -> golden_pool -> dense_owner_v11 -> LOCAL Mac
# training (the 3060 is unreachable while the owner travels) -> frozen-eval ->
# exemplar gate -> promote. Chain arm only: on MPS a cold start would take ~18h,
# and the chain-vs-cold question was already settled (coco loses).
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
exec >> logs/owner_v11_train.log 2>&1
PY=".venv/bin/python"
export PYTHONPATH=.
echo "=== owner_v11 pipeline start $(date) ==="

echo "--- 1) export round9 from Label Studio"
$PY - <<'PYEOF'
import http.cookiejar, json, re, urllib.parse, urllib.request
from pathlib import Path
BASE = "http://127.0.0.1:8081"
env = dict(l.split("=",1) for l in Path("scripts/.label_studio.env").read_text().splitlines() if "=" in l and not l.startswith("#"))
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
pg = op.open(f"{BASE}/user/login/", timeout=30).read().decode()
m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', pg)
body = urllib.parse.urlencode({"email": env["LABEL_STUDIO_USERNAME"].strip(), "password": env["LABEL_STUDIO_PASSWORD"].strip(), "csrfmiddlewaretoken": m.group(1)}).encode()
op.open(urllib.request.Request(f"{BASE}/user/login/", data=body, headers={"Referer": f"{BASE}/user/login/"}), timeout=30)
projects = json.loads(op.open(f"{BASE}/api/projects?page_size=100", timeout=30).read())
r9 = [p for p in projects.get("results", projects) if re.match(r"round9_chunk\d$", str(p.get("title","")))]
assert r9, "no round9 projects"
all_tasks, done = [], 0
for p in sorted(r9, key=lambda x: x["id"]):
    data = op.open(f"{BASE}/api/projects/{p['id']}/export?exportType=JSON&download_all_tasks=true", timeout=300).read()
    Path(f"output/label_studio/export_{p['title']}.json").write_bytes(data)
    tasks = json.loads(data)
    n = sum(1 for t in tasks if t.get("annotations"))
    done += n; all_tasks.extend(tasks)
    print(f"  {p['title']}: {n}/{len(tasks)} 已标", flush=True)
Path("output/label_studio/export_round9_all.json").write_text(json.dumps(all_tasks, ensure_ascii=False))
print(f"  合计已标 {done}", flush=True)
assert done >= 2000, f"round9 只有 {done} 张,太少"
PYEOF

echo "--- 2) merge into golden_pool"
$PY - <<'PYEOF'
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from golden_disagreement import rects
POOL = Path("data/golden_pool.json")
pool = json.loads(POOL.read_text())
before = len(pool); boxes = bg = 0
for t in json.loads(Path("output/label_studio/export_round9_all.json").read_text()):
    stem = t.get("data", {}).get("stem")
    if not stem or not t.get("annotations"): continue
    ann = sorted(t["annotations"], key=lambda a: a.get("updated_at") or "")[-1]
    owner = rects(ann)
    pool[stem] = owner; boxes += len(owner); bg += not owner
POOL.write_text(json.dumps(pool, ensure_ascii=False))
print(f"  pool {before} -> {len(pool)}  (+框{boxes} 背景{bg})", flush=True)
PYEOF

echo "--- 3) build dense_owner_v11 (manifest eval-exclusion; all reservoirs)"
$PY - <<'PYEOF'
import json, shutil
from pathlib import Path
from src.detection.owner_eval import is_eval_stem, split_of
POOL = json.loads(Path("data/golden_pool.json").read_text())
DST = Path("datasets/dense_owner_v11")
SRC = [Path("datasets")/d/"images"/s for d in
       ("dense_15m_full","dense_swap_v1","round6_scout","dense_owner_v6",
        "dense_owner_v7","dense_2026h1","dense_2025h2") for s in ("train","val")]
def find(stem):
    for d in SRC:
        p = d/f"{stem}.png"
        if p.exists(): return p
if DST.exists(): shutil.rmtree(DST)
for sub in ("images/train","images/val","labels/train","labels/val"):
    (DST/sub).mkdir(parents=True, exist_ok=True)
n = {"train":0,"val":0}; skip_eval = miss = 0
for stem, bx in POOL.items():
    if is_eval_stem(stem): skip_eval += 1; continue
    src = find(stem)
    if src is None: miss += 1; continue
    sp = split_of(stem)
    shutil.copy2(src, DST/"images"/sp/src.name)
    (DST/"labels"/sp/f"{stem}.txt").write_text("".join(f"0 {a:.6f} {b:.6f} {c:.6f} {d:.6f}\n" for a,b,c,d in bx))
    n[sp] += 1
(DST/"data.yaml").write_text(f"path: {DST.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_cluster\n")
print(f"  train {n['train']} / val {n['val']}  (排除eval {skip_eval}, 缺图 {miss})", flush=True)
assert n["train"] > 5000
PYEOF

echo "--- 4) LOCAL train (MPS): chain from owner_best, fixed lr"
caffeinate -i $PY -m src.detection.train --data datasets/dense_owner_v11/data.yaml \
  --model models/owner_best.pt --epochs 40 --patience 10 --name owner_v11_chain \
  --workers 4 --cache disk

echo "--- 5) curve check + frozen-eval + exemplar gate + promote"
$PY - <<'PYEOF'
import csv, importlib.util as il, json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
r = Path("runs/detect/runs/detect/owner_v11_chain/results.csv")
rows = list(csv.DictReader(open(r)))
m = next(c for c in rows[0] if "mAP50(B)" in c)
v = [float(x[m]) for x in rows]
bi = max(range(len(v)), key=lambda i: v[i])
shape = "❌best=预热轮" if bi+1 <= 2 else ("⚠️峰后震荡" if sum(x < v[bi]*0.2 for x in v[bi:]) > (len(v)-bi)*0.25 else "✅曲线健康")
w = Path("runs/detect/runs/detect/owner_v11_chain/weights/best.pt")
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
spec = il.spec_from_file_location("bc", "scripts/benchmark_check.py")
bc = il.module_from_spec(spec); spec.loader.exec_module(bc)
gate = bc.run(w)
line = (f"owner_v11_chain  F1 {best['f1']:.3f} P {best['p']:.3f} R {best['r']:.3f}  "
        f"{shape}  ⭐{'过' if gate['passed'] else '不过'}(训{gate['train']['recall']}/评{gate['eval']['recall']})")
print("  " + line, flush=True)
from src.notify import send
send("🧠 <b>owner_v11 本机训练完成</b>\n<code>" + line + "</code>\n对照: v10_chain 0.645 / H-TS 0.658")
PYEOF
$PY scripts/promote_owner_best.py 2>&1 | tail -4
echo "=== owner_v11 pipeline done $(date) ==="
