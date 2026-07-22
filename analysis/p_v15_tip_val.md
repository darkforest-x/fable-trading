# v15 tip-val（Hypothesis B）中期裁决 — 2026-07-23

**纪律**：未 promote `owner_best` / ACTIVE / frozen；未评 holdout；未清 forward_log。

单变量：相对 v14，**仅 val 也 pad200 tip 对齐**（测「中段 val early-stop 忘 tip」是否主因）。

## 复现命令

```bash
# Windows 3060 nested path → Mac（已做；训仍可能在跑）
scp zzc@192.168.1.3:C:/fable/runs/detect/runs/detect/owner_v15_tipval_oomfix/weights/best.pt \
  models/owner_v15_tipval.pt

bash scripts/eval_v15_vs_v12_tip.sh
```

## 训练快照（Windows RTX 3060，拉权重时）

| 项 | 值 |
|---|---|
| 数据 | `dense_owner_v15_tipval`（train=v14 pad200；**val 也 tip-align pad200**） |
| 底座 | v12_htip |
| 计划 | 40 ep，patience=10 |
| 拉权重时 | results.csv 已到 **ep32**；best≈**ep22**（mAP50≈0.722 / mAP50-95≈0.351） |
| 权重 | `models/owner_v15_tipval.pt`（从 nested `runs/detect/runs/detect/.../best.pt`） |
| 日志 | `C:\fable\logs\owner_v15_tipval_oomfix.log` |

注：本报告用的是**训练未必要终局**时的 best；tip 主表已足够否决 Hypothesis B。若 ep40 终局 best 换代，可重跑同脚本覆盖 JSON，不自动 promote。

## H-DET-1 发现级（主表）

| 指标 | v12 (`owner_best`) | v14 pad200 | **v15 tipval** | 判定 |
|---|---:|---:|---:|---|
| true_tip tip_hit（val 重渲 tip，conf=0.3，n=120） | **0.925** (111/120) | **0.033** (4/120) | **0.0167** (2/120) | **更差**；未回 v12 |
| tip-smoke 贴边开火 | **0/27** | **0/27** | **0/27** | **未过线** |

产物：`analysis/output/tip_rate_v15_tipval.json`、`analysis/output/diag_tip_smoke_v15.json`。

## 解读

1. **Hypothesis B 否决（发现级）**：把 val 也改成 tip-align pad200 后，true_tip **没有**向 v12 的 0.925 恢复，反而略差于 v14（0.0167 vs 0.033）。说明「只怪中段 val early-stop」**解释不了** tip 崩盘。  
2. **tip-smoke 仍 0/27**：过线标准是 ≫ v12；本轮无改善。  
3. **val mAP 虚高**：tip-align val 上 mAP50≈0.72 远高于 v14 官方中段 val，**不可当 tip 裁决**——与根因报告「C 语义≠盘口 tip」一致。  
4. **不 promote**。主线仍 v12。勿再同构 pad200 微调当银弹。

## 风险与诚实声明

- tip-smoke 用 `forward_log_vps_20260721.csv`，与 v12/v14 同口径。  
- 权重取自训中 best（ep≈22 / 已跑到 ep32）；终局若换 best 应重评，但当前幅度已足够否定 B。  
- 未跑 frozen-F1 / holdout / 目视叠框。  
- tip_hit 2/120 量级噪声；不宣称「比 v14 显著更差」，只宣称「未恢复」。

## 下一步（需 owner 决策）

1. 等 v15 训完再 scp 终局 best 重跑同表（可选；预期不变）→ **否**自动 promote。  
2. 按 `p_v14_failure_rootcause.md` 主因 **C**：真实 tip 成败金标小样（`p_v13_real_tip_collect_plan.md`）——需 owner 点头。  
3. 停掉同构 pad200 迭代；主线继续 v12。
