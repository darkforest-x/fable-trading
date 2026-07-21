# wuzao A 档落地短报（2026-07-22 夜）

**约束遵守**：未杀 v13、未碰 MPS 大推理、未耗 holdout、未 promote、未改 LIVE/三门、未装 VPS。

## 做完（可演示）

| # | 项 | 产出 |
|---|----|------|
| 1 | LWC 加深（密集窗/后文/tip 图层开关） | `analysis/output/wuzao_lwc_hardneg_batch/index.html` · `scripts/build_hardneg_lwc_batch.py` |
| 2 | hardneg 叠框画廊（matplotlib） | `analysis/output/hardneg_overlay_gallery/` · `scripts/overlay_hardneg_boxes.py` |
| 3 | LS 预标小包（24） | `output/label_studio/tasks_hardneg_discovery.json` · `scripts/hardneg_to_labelstudio.py` |
| 4 | Protections→executor 规格 | `docs/EXEC_PROTECTIONS_SPEC.md` |
| 5 | nvitop/netron 说明 + v13 状态脚本 | `docs/LOCAL_DEBUG_TOOLS.md` · `scripts/v13_train_status.sh` |

## 故意没做 / 跳过

| 项 | 原因 |
|----|------|
| supervision pip 进训 .venv | 避污染；脚本保留 `--prefer-supervision` |
| FiftyOne App | 偏重；LS 小包覆盖策展入口 |
| netron export ONNX | 训中可能碰 MPS |
| VPS Kuma/Grafana/exporter | 需 owner 批 → `docs/ops/VPS_OBSERVABILITY_PENDING.md` |
| 硬负加训 / promote v13 | 等训完 + owner |

## 假设

见 `docs/RESEARCH_AGENDA.md` §E：H-FE-1 🟡加深 · H-TOOL-2 🟢 · H-EXEC-WUZAO-1 🟡规格 · 其余仍 ⚪/待批。

## 复现

```bash
PYTHONPATH=. .venv/bin/python scripts/build_hardneg_lwc_batch.py
PYTHONPATH=. .venv/bin/python scripts/overlay_hardneg_boxes.py
PYTHONPATH=. .venv/bin/python scripts/hardneg_to_labelstudio.py --limit 24
bash scripts/v13_train_status.sh
bash scripts/eval_v13_vs_v12_tip.sh --dry-run   # 训中安全
```

总纪要：`analysis/p_overnight_20260722.md`。
