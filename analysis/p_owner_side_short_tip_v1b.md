# owner_side_short_tip_v1b — tip-smoke 诚实评估（不 promote）

**日期**：2026-07-24  
**纪律**：未 promote / 未改 ACTIVE / 未动 holdout / 未清 forward_log。  
**裁决口径**：tip-smoke / 真 tip 金标；**自家 val mAP 永不作晋升裁决**（纪律 12）。

## 结论先行

| 项 | 结果 |
|---|---|
| 训练 | early-stop ≈ **57** epoch（patience 20）；进程已死；launchd 空 job 已 unload |
| 权重 | `runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt`（双重 `runs/detect`） |
| 训练 val（辅） | mAP50 ≈ **0.987–0.990**；P≈0.96 / R≈0.97；mAP50-95≈0.66（best fitness≈ep37） |
| tip-smoke tip | **19/27** 贴边开火 |
| tip-smoke live 对照 | **4/27** |
| vs 历史同口径 | v12/v13/v14/v15 tip-smoke 均为 **0/27** → 本轮发现级 **明显过线** |
| 晋升 | **否** — 仍研究口径；val mAP~0.99 **不作**晋升依据 |

## 复现

```bash
# tip-smoke（与 v12–v15 同账本快照）
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python \
  scripts/diag_forward_detect_lag.py --from-log --tip-smoke \
  --log analysis/output/forward_log_vps_20260721.csv \
  --weights runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt \
  --out analysis/output/diag_tip_smoke_owner_side_short_tip_v1b.json \
  > analysis/output/owner_side_short_tip_v1b_tip_smoke.log 2>&1
```

| 产物 | 路径 |
|---|---|
| 权重 | `runs/detect/runs/detect/owner_side_short_tip_v1b/weights/best.pt` |
| 训练日志 | `analysis/output/owner_side_short_tip_v1b_train.log` |
| tip-smoke 日志 | `analysis/output/owner_side_short_tip_v1b_tip_smoke.log` |
| tip-smoke JSON | `analysis/output/diag_tip_smoke_owner_side_short_tip_v1b.json` |

## 训练数字（辅表，非裁决）

| 指标 | 值 |
|---|---:|
| 数据 | `datasets/dense_owner_side_short_tip/`（train 1037 / val 324；holdout 0） |
| epochs | 57（early-stop） |
| best mAP50（results.csv max） | **0.99008** @ ep57 |
| Ultralytics best fitness≈ | ep37：mAP50 **0.98757** / mAP50-95 **0.66477** |
| 终局 val 摘要 | P **0.955** / R **0.972** / mAP50 **0.987** / mAP50-95 **0.665** |

## tip-smoke 主表

| 模式 | n_fired / n_symbols | 对照 |
|---|---:|---|
| tip @ conf=0.3 | **19/27** | v12–v15：**0/27** |
| live @ conf=0.3（对照） | **4/27** | 同快照历史多为 0 |

tip 开火币（19）：CAP, DOOD, EDEN, HOME, KAITO, KGEN, KITE, KORU, MUU, OL, OPG, PARTI, PEPE, PIEVERSE, RAM, RECALL, SPX, UB, YB。

## 解读

1. **发现级过线**：同口径账本 27 币上 tip 贴边开火从历史 0 拉到 **19/27**，说明 tip 对齐短金标（右缘≈窗末 + 时间切分）对盘口 tip 几何有效——与 pad200 长链失败（H-DET-1 0/27）形成对照。  
2. **val mAP~0.99 不作晋升**：训练/val 同 tip 几何时 mAP 虚高是预期；纪律 12 仍以 tip-smoke / 真 tip 金标为门。本轮 tip-smoke 已独立给出证据，mAP 只作辅。  
3. **live≪tip**：4/27 vs 19/27，符合 tip 窗调度语义；不自动改 VPS 主线为 tip-only（另批）。  
4. **不 promote**：下一闸是 Owner 明确的「真实 K 线框 ~1000 且排除训练集」，非 ACTIVE 切换。

## 风险与诚实声明

- tip-smoke 用 `forward_log_vps_20260721.csv` 快照（27 币），与 v12–v15 同口径；**非**新前向 100；本机 K 线未必覆盖账本信号日，但对照链一致。  
- 未跑 true_tip tip_hit 金标重渲、未跑 frozen-F1、未动 holdout。  
- 开火≠可交易；判断层 short 主链与前向新鲜仍待后续闸门。  
- launchd `com.fable.owner_side_short_tip_v1b` 已 bootout；`com.fable.local-webapp` 未动。

## 下一步（需 Owner）

1. **pending**：真实 K 线检出约 **1000** 框（排除 `dense_owner_side_short*` 训练集）— 命令草稿见 `analysis/todo_short_only_pipeline.md`；**未**自动开跑。  
2. tip-smoke 已通过发现级后，是否申请检测器晋升门（默认仍建议先 1000 目视 / 判断层接池，**不** promote）。  
3. 判断层 YOLO short 主链可接权重扫池（另批开跑）。
