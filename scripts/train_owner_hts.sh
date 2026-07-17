#!/bin/bash
# H-TS: train detector on pre-2026-05-04 images only (3060 overnight).
# Does NOT promote owner_best. Writes frozen-eval + analysis/p2a_hts_report.md.
#
# Usage:
#   bash scripts/train_owner_hts.sh
#   bash scripts/train_owner_hts.sh --force
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs analysis/output
exec > >(tee -a logs/owner_hts_train.log) 2>&1

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
export PYTHONPATH=.
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

DONE_MARKER="logs/.owner_hts_done"
if [ -f "$DONE_MARKER" ] && [ "$FORCE" = "0" ]; then
  echo "already done ($DONE_MARKER); use --force to rerun"
  exit 0
fi

HOST="${FABLE_3060_HOST:-zzc@192.168.1.5}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20"
REMOTE="C:/fable"
RRUN="$REMOTE/runs/detect/runs/detect"
NAME="owner_hts_chain"
DATASET="datasets/dense_owner_hts"
BASE="models/owner_best.pt"
EPOCHS=40
PATIENCE=10

echo "=== H-TS pipeline start $(date) ==="

echo "--- 1) build time-cut dataset (window end < 2026-05-04)"
if [ ! -f datasets/dense_owner_hts/hts_meta.json ] || [ "$FORCE" = "1" ]; then
  $PY scripts/build_hts_dataset.py --src dense_owner_v9 --dst dense_owner_hts
fi
n_train=$(find "$DATASET/images/train" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
n_val=$(find "$DATASET/images/val" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
echo "  dense_owner_hts train=$n_train val=$n_val"
if [ "${n_train:-0}" -lt 500 ]; then
  echo "ABORT: too few train images ($n_train)"
  exit 1
fi

echo "--- 2) ship dataset + base weights to 3060"
$SSH "$HOST" "echo ok" >/dev/null || { echo "SSH fail $HOST"; exit 1; }
TAR=$(mktemp -t fable_hts).tar
COPYFILE_DISABLE=1 tar cf "$TAR" --exclude='*.npy' --exclude='*.cache' --exclude='._*' \
  -C datasets dense_owner_hts
echo "  pack $(du -h "$TAR" | cut -f1)"
scp -o BatchMode=yes -q "$TAR" "$HOST:$REMOTE/ds_hts.tar" || { echo "scp ds fail"; exit 1; }
scp -o BatchMode=yes -q "$BASE" "$HOST:$REMOTE/base_hts.pt" || { echo "scp base fail"; exit 1; }
rm -f "$TAR"
$SSH "$HOST" "cd $REMOTE; Remove-Item -Recurse -Force datasets/dense_owner_hts -EA SilentlyContinue; if (-not (Test-Path datasets)) { New-Item -ItemType Directory datasets | Out-Null }; tar xf ds_hts.tar -C datasets; Remove-Item ds_hts.tar -EA SilentlyContinue; Get-ChildItem -Path datasets\dense_owner_hts -Recurse -Force -Filter '._*' -EA SilentlyContinue | Remove-Item -Force"
$SSH "$HOST" '$d="C:\fable\datasets\dense_owner_hts"; foreach ($s in @("train","val")) { "  {0}: {1} images / {2} labels" -f $s, (Get-ChildItem "$d\images\$s\*.png" -EA SilentlyContinue).Count, (Get-ChildItem "$d\labels\$s\*.txt" -EA SilentlyContinue).Count }'

echo "--- 3) train $NAME on 3060 (WMI Create; SSH-safe) $(date '+%H:%M:%S')"
$SSH "$HOST" "
\$cmd = 'cmd.exe /c cd /d C:\fable && C:\fable\.venv\Scripts\python.exe -u C:\fable\train_dense.py --name $NAME --model C:/fable/base_hts.pt --dataset C:/fable/datasets/dense_owner_hts --epochs $EPOCHS --patience $PATIENCE --cache false --workers 4 > C:\fable\\$NAME.log 2>&1'
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=\$cmd} | Out-Null
Write-Output 'started'
"
sleep 90
for i in $(seq 1 500); do
  a=$($SSH "$HOST" 'if (Get-Process python* -EA SilentlyContinue) { "y" } else { "n" }' 2>/dev/null | tr -d '\r')
  [ "$a" != "y" ] && break
  if [ $((i % 4)) -eq 0 ]; then
    echo "  … still training $NAME $(date '+%H:%M:%S')"
    $SSH "$HOST" "if (Test-Path C:\\fable\\$NAME.log) { Get-Content C:\\fable\\$NAME.log -Tail 2 }" 2>/dev/null | tr -d '\r' | tail -3
  fi
  sleep 45
done

echo "--- 4) fetch weights $(date '+%H:%M:%S')"
mkdir -p "runs/detect/runs/detect/$NAME/weights"
for f in weights/best.pt results.csv args.yaml; do
  scp -o BatchMode=yes -q "$HOST:$RRUN/$NAME/$f" "runs/detect/runs/detect/$NAME/$f" 2>/dev/null \
    || echo "  missing $f"
done
ls -lh "runs/detect/runs/detect/$NAME/weights/best.pt" 2>/dev/null || { echo "ABORT: no best.pt"; exit 1; }

echo "--- 5) frozen-eval + report (NO promote)"
$PY - <<'PY'
import csv, json
from pathlib import Path
from src.detection.owner_eval import evaluate_owner_f1
from src.notify import send

name = "owner_hts_chain"
w = Path(f"runs/detect/runs/detect/{name}/weights/best.pt")
meta_ds = json.loads(Path("datasets/dense_owner_hts/hts_meta.json").read_text())
out = {
    "run": name,
    "hypothesis": "H-TS",
    "cutoff": "2026-05-04 exclusive (window end)",
    "dataset": meta_ds,
    "weights": str(w),
    "promoted": False,
    "note": "experiment only — owner_best not auto-switched",
}
best, _ = evaluate_owner_f1(w, "datasets/owner_eval_frozen")
out["frozen_eval"] = best
p = Path(f"runs/detect/runs/detect/{name}/results.csv")
if p.exists():
    rows = list(csv.DictReader(open(p)))
    m = next(c for c in rows[0] if "mAP50(B)" in c)
    v = [float(x[m]) for x in rows]
    bi = max(range(len(v)), key=lambda i: v[i])
    out["curve"] = {
        "epochs": len(v),
        "best_epoch": int(float(rows[bi]["epoch"])),
        "best_map50": round(v[bi], 4),
        "healthy": (bi + 1 > 2),
    }
ob = json.loads(Path("models/owner_best.json").read_text())
out["baseline_owner_best"] = {
    "source_run": ob.get("source_run"),
    "frozen_eval_f1": ob.get("frozen_eval_f1"),
}
delta = best["f1"] - float(ob.get("frozen_eval_f1") or 0)
out["f1_delta_vs_best"] = round(delta, 4)
Path("analysis/output/hts_experiment_summary.json").write_text(
    json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
lines = [
    "# H-TS — 检测层训练图时间切分实验",
    "",
    f"日期: 见 summary 时间戳 / logs/owner_hts_train.log",
    "一句话: 训练图 window-end 严格 < 2026-05-04，chain 续训自 owner_best，**不 promote**。",
    "",
    "## 复现",
    "```bash",
    "PYTHONPATH=. .venv/bin/python scripts/build_hts_dataset.py",
    "bash scripts/train_owner_hts.sh --force",
    "```",
    "",
    "## 数据",
    f"- src `dense_owner_v9` → `dense_owner_hts`",
    f"- kept={meta_ds.get('n_kept')} dropped={meta_ds.get('n_dropped')} "
    f"(post_cutoff={meta_ds.get('drop_reasons',{}).get('post_cutoff')}, "
    f"unresolved={meta_ds.get('drop_reasons',{}).get('unresolved')})",
    f"- stats: `{json.dumps(meta_ds.get('stats', {}), ensure_ascii=False)}`",
    "",
    "## frozen-eval（owner_eval_frozen 尺子）",
    f"- **H-TS** F1 **{best['f1']:.3f}**  P {best['p']:.3f}  R {best['r']:.3f}",
    f"- baseline **{ob.get('source_run')}** F1 **{ob.get('frozen_eval_f1')}**",
    f"- ΔF1 = {delta:+.3f}",
]
if out.get("curve"):
    c = out["curve"]
    lines.append(f"- 曲线: {c['epochs']} 轮 best@{c['best_epoch']} mAP50={c['best_map50']} "
                 f"{'✅' if c['healthy'] else '❌ 预热轮嫌疑'}")
lines += [
    "",
    "## 解读",
    "- 若 |ΔF1| 很小：detect 时间泄漏不是 frozen-F1 主因；PF 7.5 仍靠前向终审。",
    "- 若 H-TS F1 明显掉：原先分数部分吃了 accept 窗形态，回测数字应降权。",
    "- **下一步（需 owner）**：若 H-TS 可用，用该权重重扫 judgment 池再比 val（仍不动 holdout）。",
    "",
    "## 纪律",
    "- 未读 holdout 判断标签；未写 models/ACTIVE；未 promote owner_best。",
    "- 丢弃的 post_cutoff 图只用于本实验定义，不回灌训练。",
]
Path("analysis/p2a_hts_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps(out, indent=2, ensure_ascii=False))
try:
    send(
        "🧪 <b>H-TS 训练完成</b>（检测层时间切分）\n"
        f"F1 {best['f1']:.3f} vs best {ob.get('frozen_eval_f1')} (Δ{delta:+.3f})\n"
        f"池 kept={meta_ds.get('n_kept')} drop_post={meta_ds.get('drop_reasons',{}).get('post_cutoff')}\n"
        "未 promote · 报告 analysis/p2a_hts_report.md"
    )
except Exception as e:
    print(f"notify skip: {e}")
print("wrote analysis/p2a_hts_report.md")
PY

date > "$DONE_MARKER"
echo "=== H-TS pipeline done $(date) ==="
