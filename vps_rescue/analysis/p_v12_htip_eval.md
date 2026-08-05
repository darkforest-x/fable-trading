# H-TIP v12 评测（D1）— 2026-07-20

**纪律**：未 promote `owner_best`；未改 `models/ACTIVE` / frozen 默认；未评 holdout；
未写主线 `data/forward_log.csv`。

## 复现命令

```bash
# 训练链（已完成）
bash scripts/train_owner_v12_htip.sh
# 产物
# runs/detect/runs/detect/owner_v12_htip/weights/best.pt
# analysis/output/tip_rate_v12.json
# analysis/output/owner_v12_htip_frozen.json
```

## 数据 / 训练

| 项 | 值 |
|---|---|
| 底座 | `models/owner_best.pt`（v11 chain） |
| 数据 | `dense_owner_v12_htip` = v11 ∪ tip 重渲克隆（train only） |
| 训练 | 40 ep patience 10；**ep 32 early stop** |
| 稳定权重路径 | `models/owner_v12_htip.pt`（从 run best 拷贝） |

## 门槛对照（周计划 D1）

| 指标 | v11 基线 | v12 | 门槛 | 判定 |
|---|---:|---:|---|---|
| tip_hit_rate (`true_tip_rerender`, val, conf=0.3) | **0.009** (1/111) | **0.925** (111/120) | ≥ 0.20 | **通过** |
| frozen-F1 (`owner_eval_frozen` MANIFEST) | **0.658** | **0.650** | 回撤 ≤ 0.03 | **通过**（Δ −0.008） |
| frozen P / R | — | 0.615 / 0.690 | — | 参考 |

原始 JSON：`analysis/output/tip_rate_v11_baseline.json`、`tip_rate_v12.json`、
`owner_v12_htip_frozen.json`。

## 解读

- tip 指标数量级跃迁（0.9% → 92.5%）说明 **H-TIP 右缘重渲单变量有效**（至少在
  true_tip 评测协议下）：模型学会在「无后文」几何上开火。
- frozen-F1 几乎持平：中图尺子未塌，符合「只加 tip 克隆、不毁原分布」的预期。
- **确认级仍未开始**：本结果是发现级/训练侧评测；live tip 能否进 30min 新鲜门，
  要靠 D2–D3 影子 48h。

## 风险与诚实声明

1. `tip_detectability --true-tip` 用 **重渲 tip 图** 测检出，与 live 脉冲共享渲染语义，
   但仍是 **离线 val 重渲**，不是实盘时钟上的 100 笔裁决。
2. v11 基线 n=111（skipped 9）与 v12 n=120 略不齐；不影响 0.009 vs 0.925 量级结论。
3. early stop 在 ep32；best 以 fitness=mAP50-95 为准，F1 尺子另算。
4. **禁止**把 tip_hit 0.925 解读为「策略已可赚钱」——判断层/阈值/成本未改，且主线
   前向 0/100 仍空。
5. 若影子 48h 无新鲜 tip 入账，优先查：权重是否部署、`FABLE_V12_SHADOW=1`、
   K 线新鲜度、tip 窗是否真在盘口 bar。

## 下一步（D2–D3，本报告配套已落地代码）

1. `models/owner_v12_htip.pt` 部署到 VPS  
2. 脉冲设 `FABLE_V12_SHADOW=1` → 写 `data/forward_log_v12_shadow.csv`（tip-only）  
3. 48h 后写 `analysis/p_v12_shadow_48h.md`  
4. **切流 / holdout 第 6 次 / promote = owner 决策**（见 `week_plan_20260720.md` D4–D5）

## 总判定

**D1 双门通过。** 进入影子验证；主线保持 v11。
