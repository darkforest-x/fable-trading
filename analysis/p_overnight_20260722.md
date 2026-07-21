# 夜间工作纪要 — 2026-07-22

Owner 授权：A 档能做的做掉 + 少许系统整理；**不杀 v13、不 promote、不动真金/VPS 装机**。

## 1. 做了什么

### A 档落地（wuzao）

- **LWC 加深**：10 窗 hardneg 批量 HTML，图层开关（密集窗 / 后文 / tip 带 / MA）  
  → `analysis/output/wuzao_lwc_hardneg_batch/` · `scripts/build_hardneg_lwc_batch.py`
- **叠框画廊**：matplotlib（未装 supervision）  
  → `analysis/output/hardneg_overlay_gallery/` · `scripts/overlay_hardneg_boxes.py`
- **LS 发现级小包**：24 tasks  
  → `output/label_studio/tasks_hardneg_discovery.json` · `scripts/hardneg_to_labelstudio.py`
- **Protections 规格**（B 档只抄规格）→ `docs/EXEC_PROTECTIONS_SPEC.md`
- **nvitop/netron** 说明 → `docs/LOCAL_DEBUG_TOOLS.md`
- **VPS 待批清单** → `docs/ops/VPS_OBSERVABILITY_PENDING.md`
- 短报 → `analysis/p_wuzao_a_tier_done.md`；扫描 A 档状态已改 → `analysis/p_wuzao_topics_scan.md`

### 系统优化（同约束）

- `scripts/v13_train_status.sh` — CPU 看训进度  
- `scripts/eval_v13_vs_v12_tip.sh` — 补「明早路径」头注释  
- hardneg 目录索引 → `analysis/output/hardneg_mid_cluster/README.md`  
- `docs/DOC_MAP.md` / `HANDOFF.md` 顶部指针 / `RESEARCH_AGENDA.md` §E 状态互链  

## 2. 假设状态变化

| ID | 前 | 后 |
|----|----|----|
| H-FE-1 | 🟡 CSV 3 窗 | 🟡 批量图层加深 |
| H-TOOL-2 | ⚪ | 🟢 发现级画廊 |
| H-EXEC-WUZAO-1 | ⚪ | 🟡 规格文档 |
| H-TOOL-3 / VPS | ⚪ | ⚪ + 待批清单 |
| H-TOOL-4 | ⚪ | ⚪（LS 小包代替 FO） |
| H-TOOL-5 / netron | ⚪ | ⚪ + 一键命令（未 export） |

## 3. 故意没做

- 杀/抢 v13、ONNX export、大 YOLO 推理  
- holdout / promote / ACTIVE / owner_best / 清 forward_log / 真金  
- VPS 装 Kuma/Grafana/exporter  
- 硬负加训、判断层新实验、大前端重构  

## 4. 明早第一件事（v13）

```bash
bash scripts/v13_train_status.sh
# 若 train DEAD 且有稳定权重：
ls -lh models/owner_v13_pad200.pt \
  || ls -lh runs/detect/runs/detect/owner_v13_pad200/weights/best.pt
bash scripts/eval_v13_vs_v12_tip.sh
# 看 tip-smoke / tip_hit；默认不 promote — 等 owner
```

训中可先：`bash scripts/eval_v13_vs_v12_tip.sh --dry-run`

## 5. 仍需 Owner 拍板

1. VPS 可观测是否装（清单已写）  
2. H-DET-2 硬负加训时机与样本量  
3. v13 是否 promote 检测主线（脚本永不自动）  
4. Protections 日损/回撤具体数字（规格有了，默认值未改）
