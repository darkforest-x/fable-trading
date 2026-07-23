# Owner 分边标框 → 因果特征 → train base rate 裁决 — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；long→`label_candidate`、
short→`label_short_candidate`；TP5/SL2/72bar；成本同时报 swap maker 0.06% 与 legacy
0.2%。不 promote、不改 ACTIVE、不改执行器、不改新鲜度门。

回答：在无 side 裁决（oracle 有边、因果规则≈emergence）之后，owner 把同一批框标成
long/short/skip——**分边能否挖出可部署增量（因果规则 PF@maker ≥ 1.3）？**

## 复现命令

```bash
# 1) 审阅表已填（本轮 owner 完成）
#    analysis/output/owner_side_review/review_sheet.csv

# 2) 分边裁决（train only；默认 --n-symbols 0 = 全币）
PYTHONPATH=. .venv/bin/python scripts/owner_side_feature_verdict.py \
  --sheet analysis/output/owner_side_review/review_sheet.csv \
  --n-symbols 0 --tag owner_side_feature_verdict

# 输出:
#   analysis/output/owner_side_feature_verdict.json
#   analysis/output/owner_side_feature_verdict_main.csv
```

## A. 分边标注统计

| owner_side | n | 占比 |
|---|---:|---:|
| **long** | 1152 | 45.6% |
| **short** | 1361 | 53.9% |
| **skip** | 12 | 0.5% |
| **未标** | 0 | 0% |
| 合计 | 2525 | 100% |

未标 = 0，skip 仅 12——与 owner「都操作好啦」一致；全表进入下游（skip 不进任一边正样本）。

## B. 主表（裁决看因果规则，不看 oracle / AUC）

成功线（脚本硬编码）：**分边因果规则 PF @ SWAP maker ≥ 1.3**。

| side | n_boxes | LGBM val AUC | oracle n | oracle PF@maker | oracle PF@0.2% | 因果规则 n | 规则 PF@maker | 规则 PF@0.2% | ≥1.3? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **long** | 1152 | 0.974 | 1090 | **5.620** | 4.941 | 51172 | **0.917** | 0.823 | **否** |
| **short** | 1361 | 0.968 | 1286 | **7.379** | 6.470 | 66101 | **1.127** | 1.005 | **否** |

对照：

| 口径 | PF@maker | 相对本轮 |
|---|---:|---|
| Emergence（已发表） | **0.874** | 基线 |
| 无 side 因果规则（`p_owner_label_feature_verdict`） | **0.869** | ≈emergence |
| 无 side owner-cut oracle | **1.183** | 确认态选点 |
| 本轮 long 因果规则 | 0.917 | Δ vs em +0.04；仍亏钱 |
| 本轮 short 因果规则 | **1.127** | Δ vs em +0.25；**仍 <1.3** |

## C. 手法披露（LGBM gain，不当裁判）

### long（top）

| 特征 | gain | 读法 |
|---|---:|---|
| **order_score** | 9576 | 多头均线排序偏高（pos 中位 4 vs neg 2） |
| spread_chg8 | 3808 | spread **在变大**（启动/散开中） |
| ext_up | 3724 | 近窗上伸已打印 |
| slow_slope_12 | 993 | 慢锚斜率偏正 |

因果规则 AND：
`order_score≥4` ∧ `spread_chg8≥0.00377` ∧ `ext_up≥0.00400` ∧ `slow_slope_12≥-8.6e-5`

### short（top）

| 特征 | gain | 读法 |
|---|---:|---|
| **order_score** | 9903 | 空头排序偏低（pos 中位 0 vs neg 2） |
| spread_chg8 | 6368 | 同样在散开 |
| ret_12 | 2809 | 近 12bar 已跌（pos 中位 −2.2%） |
| slow_slope_12 | 2175 | 慢锚斜率偏负 |

因果规则 AND：
`order_score≤0` ∧ `spread_chg8≥0.00396` ∧ `ret_12≤-0.0122` ∧ `slow_slope_12≤-1.95e-4`

两边共性：**方向结构（order_score）+ 已经在动（spread_chg8 / ret）**——仍是确认态语义，不是 tip 出生。

## D. 裁决句

**分边后手法没有可部署增量（两边因果规则 PF@maker 均 < 1.3）。**

- short 是相对最好的一边（1.127），扣 0.2% 后仅≈1.005，距成功线仍差一截。
- long 因果规则 0.917，与 emergence / 无 side 同族，略亏。
- oracle PF 飙到 5.6 / 7.4：**不能当增量证据**——side 标签与匹配结算同向，且标框时点本就是确认态；相对无 side oracle 1.18 的暴涨，恰恰说明「看对方向再框」的 hindsight，而不是新的因果边。
- LGBM AUC≈0.97 仅披露可分性，**不作交易信号**。

## E. 下一步（需 owner 决策）

| 选项 | 建议 | 理由 |
|---|---|---|
| 用分边规则上线 / promote | **否** | 未过 1.3；且未耗 holdout |
| 把 short 1.127 当「接近了再调参」 | **慎** | 单变量纪律；阈值来自本轮正样本分位，再抠易过拟合 |
| 继续 YOLO tip 追这个手感 | **否** | 与无 side 报告同结论：手感 = 确认态，不是 tip |
| 显式「确认散开 + 方向」策略另测 | 可选，单变量 | 本轮 short 规则已是该语义的粗糙版，发现级 PF 1.13 |
| holdout 终审 | **未授权，不跑** | 两边都没过发现线，耗 holdout 无意义 |

## 风险与诚实声明

1. **side 标签可能含框后走势信息** → oracle PF 虚高；只信因果全市场扫描。
2. **AUC≈0.97 反常偏高** → 第一假设 hindsight 时机/方向，非特征泄漏；框几何未进 LGBM。
3. 规则阈值来自正样本分位，**train 内轻度自拟合**；发现级，非 holdout 终审。
4. sheet 子集相对全量 owner 框更小（2525 vs 此前有效 cut 3318 量级），币/时段覆盖可能偏。
5. **未消耗 holdout**；不得把 oracle 5–7 或 short 1.13 写成可上线证据。
6. 未改 ACTIVE / 执行器 / 新鲜度门；VPS 未动。

## 产物

- 脚本：`scripts/owner_side_feature_verdict.py`
- 数值：`analysis/output/owner_side_feature_verdict.json`
- 主表 CSV：`analysis/output/owner_side_feature_verdict_main.csv`
- 审阅表：`analysis/output/owner_side_review/review_sheet.csv`（未 commit，留给 owner）
- learning：`docs/learnings/owner-side-split-does-not-unlock-deployable-rule.md`
