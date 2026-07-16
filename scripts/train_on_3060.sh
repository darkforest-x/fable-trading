#!/bin/bash
# Fit a detector on the LAN RTX 3060, then bring the weights home.
#
# Division of labour is deliberate and load-bearing:
#   Mac  (here)  = single source of truth. golden_pool.json, dataset build,
#                  frozen-eval, promote, deploy. Every decision happens here.
#   3060 (there) = a GPU that fits weights. Holds no pool, no git, no owner_best.
#                  Wipe it any time; nothing is lost.
#
# This split is why there is no split-brain: the 3060 never writes project state,
# so the two machines can never disagree about which labels produced which F1.
#
# Version parity is enforced, not assumed: the remote runs the same torch 2.8.0
# and ultralytics 8.4.89 as this Mac, so a 3060 number is directly comparable to
# the v6 0.595 -> v7 0.625 clean curve. Every run re-checks it before training.
#
# Usage:
#   bash scripts/train_on_3060.sh --check
#   bash scripts/train_on_3060.sh --name owner_v8_chain --dataset datasets/dense_owner_v7
set -uo pipefail
cd "$(dirname "$0")/.."

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
REMOTE="C:/fable"
RPY="$REMOTE/.venv/Scripts/python.exe"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15"

NAME="owner_v8_chain"
DATASET="datasets/dense_owner_v7"
BASE="runs/detect/runs/detect/owner_v7_chain/weights/best.pt"
EPOCHS=40
PATIENCE=10
CHECK_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --name)     NAME="$2"; shift 2 ;;
    --dataset)  DATASET="$2"; shift 2 ;;
    --base)     BASE="$2"; shift 2 ;;
    --epochs)   EPOCHS="$2"; shift 2 ;;
    --patience) PATIENCE="$2"; shift 2 ;;
    --check)    CHECK_ONLY=1; shift ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

say() { echo -e "\n\033[1;36m=== $* ===\033[0m"; }
die() { echo -e "\033[1;31m[X] $*\033[0m"; exit 1; }

say "0) 连通性 + 版本对齐检查"
$SSH "$HOST" "echo ok" >/dev/null 2>&1 || die "SSH 不通: $HOST"

# A mismatched remote silently makes results incomparable, which is worse than
# a crash: the number still looks plausible. Fail loudly instead.
LOCAL_V=$(.venv/bin/python -c "import torch,ultralytics,numpy;print(f'{torch.__version__.split(\"+\")[0]}|{ultralytics.__version__}|{numpy.__version__}')" 2>/dev/null)
REMOTE_V=$($SSH "$HOST" "$RPY -c \"import torch,ultralytics,numpy;print(f'{torch.__version__.split(chr(43))[0]}|{ultralytics.__version__}|{numpy.__version__}')\"" 2>/dev/null | tr -d '\r')
echo "  Mac : $LOCAL_V"
echo "  3060: $REMOTE_V"
[ "$LOCAL_V" = "$REMOTE_V" ] || die "版本不一致 -> 结果无法与历史曲线对照。先对齐再训。"

CUDA_OK=$($SSH "$HOST" "$RPY -c \"import torch;print(torch.cuda.is_available())\"" 2>/dev/null | tr -d '\r')
[ "$CUDA_OK" = "True" ] || die "远端 CUDA 不可用 (torch.cuda.is_available()=$CUDA_OK)"
$SSH "$HOST" "$RPY -c \"import torch;print('  GPU:',torch.cuda.get_device_name(0),f'{torch.cuda.get_device_properties(0).total_memory/1024**3:.0f}GB')\"" 2>/dev/null | tr -d '\r'

if [ "$CHECK_ONLY" = "1" ]; then echo -e "\n✅ 检查通过,可以训练。"; exit 0; fi

[ -d "$DATASET" ] || die "数据集不存在: $DATASET"
[ -f "$BASE" ]    || die "基础权重不存在: $BASE"

say "1) 同步数据集 -> 3060 (排除 .npy 缓存,远端自己重建)"
# scp per-file would spend more time on round-trips than on bytes; one tar wins.
# COPYFILE_DISABLE=1 stops macOS tar from emitting a ._x AppleDouble beside every
# file: on Windows those land as real files, *.png globs match them, and the image
# count silently doubles with binary junk that ultralytics tries to decode.
TAR=$(mktemp -t fable_ds).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C "$(dirname "$DATASET")" "$(basename "$DATASET")"
echo "  包: $(du -h "$TAR" | cut -f1)"
scp -o BatchMode=yes -q "$TAR" "$HOST:$REMOTE/ds.tar" || die "scp 数据集失败"
scp -o BatchMode=yes -q "$BASE" "$HOST:$REMOTE/base.pt" || die "scp 基础权重失败"
$SSH "$HOST" "cd $REMOTE; Remove-Item -Recurse -Force datasets/$(basename "$DATASET") -ErrorAction SilentlyContinue; tar xf ds.tar -C .; Remove-Item ds.tar" || die "远端解包失败"
rm -f "$TAR"

say "2) 远程训练: $NAME (epochs=$EPOCHS patience=$PATIENCE)"
echo "  开始: $(date '+%H:%M:%S')  —— 预计 1.5-2h,可以去睡觉"
$SSH "$HOST" "cd $REMOTE; $RPY train_dense.py --name $NAME --model base.pt --dataset $REMOTE/datasets/$(basename "$DATASET") --epochs $EPOCHS --patience $PATIENCE" 2>&1 \
  | grep -avE "it/s|Caching images" | tail -30
echo "  结束: $(date '+%H:%M:%S')"

say "3) 取回权重 + 训练记录"
# ultralytics resolves project="runs/detect" against its own runs_dir, so the run
# lands at runs/detect/runs/detect/<name> on BOTH hosts -- not the single level
# the path suggests. args.yaml must come home too: promote_owner_best.py proves a
# run's training set was eval-free by reading the dataset out of it, and a run
# without one is rejected as unverifiable no matter how good its weights are.
RUN_REMOTE="$REMOTE/runs/detect/runs/detect/$NAME"
mkdir -p "runs/detect/runs/detect/$NAME/weights"
scp -o BatchMode=yes -q "$HOST:$RUN_REMOTE/weights/best.pt" \
    "runs/detect/runs/detect/$NAME/weights/best.pt" || die "取回权重失败 —— 训练可能没成功"
for f in args.yaml results.csv; do
  scp -o BatchMode=yes -q "$HOST:$RUN_REMOTE/$f" "runs/detect/runs/detect/$NAME/$f" \
    || echo "  ⚠️ 没取到 $f（promote 会因此拒绝这个 run）"
done
ls -lh "runs/detect/runs/detect/$NAME/weights/best.pt" | awk '{print "  ✅ "$5"  "$9}'

say "3b) 曲线体检 —— 分数之前先看形状"
# A chain run whose best is epoch 1-2 did not train: it is the base plus warmup.
# That is exactly how the lr bug hid for months behind a plausible final score.
python3 - <<PY
import csv
from pathlib import Path
p = Path("runs/detect/runs/detect/$NAME/results.csv")
if not p.exists():
    print("  (无 results.csv，跳过)")
else:
    rows = list(csv.DictReader(open(p)))
    m = next(c for c in rows[0] if "mAP50(B)" in c)
    v = [float(x[m]) for x in rows]
    bi = max(range(len(v)), key=lambda i: v[i])
    post = v[bi:]
    n_col = sum(1 for x in post if x < v[bi] * 0.2)
    print(f"  {len(v)} 轮，最好 epoch {rows[bi]['epoch'].strip()}，峰值 mAP50={v[bi]:.4f}")
    print(f"  峰后崩溃 {n_col}/{len(post)}")
    if bi + 1 <= 2:
        print("  ❌ best=预热轮 —— 这个模型等于没训，别信它的 F1")
    elif n_col > len(post) * 0.25:
        print("  ⚠️ 峰后剧烈震荡 —— best 可能是侥幸")
    else:
        print("  ✅ 曲线健康")
PY

say "4) 本地 frozen-eval (尺子只在 Mac 上,这是唯一真相)"
PYTHONPATH=. .venv/bin/python - <<PY
import json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1

w = Path("runs/detect/runs/detect/$NAME/weights/best.pt")
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
print(f"  $NAME  frozen-F1 {best['f1']:.3f}  P {best['p']:.3f}  R {best['r']:.3f}")

p = Path("analysis/output/frozen_eval_comparison.json")
d = json.loads(p.read_text()) if p.exists() else {}
d["$NAME".replace("owner_", "")] = best
p.write_text(json.dumps(d, indent=2), encoding="utf-8")
PY

say "5) promote (只有赢过当前最好才会替换)"
PYTHONPATH=. .venv/bin/python scripts/promote_owner_best.py 2>&1 | tail -5

echo -e "\n\033[1;32m✅ 完成。owner_best.json:\033[0m"
python3 -c "import json;d=json.load(open('models/owner_best.json'));print(f\"  {d['source_run']}  frozen-F1 {d['frozen_eval_f1']}\")" 2>/dev/null
