# H15 密集质量二阶特征 IC 筛选（SWAP 池, train/val）

**日期**：2026-07-15  
**纪律**：发现级 val-only；holdout 未碰；未改 `features.py` 主线（`order_score` / `ma_spread_pct` 等仍仅作对照）。  
**目的**：检验均线排列有序度、收敛加速度、束宽是否在密集启动候选池内仍有增量 IC。

## 复现命令

```bash
# 因子：src/factors/library.py → FACTORS[ma_order_score|convergence_speed|ma_bandwidth_pct]
PYTHONPATH=. python3 scripts/factor_ic_screen.py
# 本报告摘自 analysis/output/factor_ic_screen.json
```

## 因子定义（因果）

| 因子 | 定义 | 与主线特征关系 |
|---|---|---|
| `ma_order_score` | EMA8≥13≥21≥34≥55≥144 的 pairwise 计数，范围 0..5 | 主线 `order_score` 是 5 线 0..4；本因子多一条 55≥144 |
| `convergence_speed` | `ma_spread_pct` 的二阶差分（两段 4-bar 一阶差） | 主线有 `spread_chg8/24` 一阶；本因子为加速度 |
| `ma_bandwidth_pct` | 六均线 (max−min)/close | 接近 `full_spread`（七线含 200）；六线版略窄 |

## 数据统计

- 候选 24 179 / 币种 256 / 前向 72-bar close ret（同 H19 管道）
- 判定：`|IC|≥0.03` 且月度符号稳定 ≥70% → alive 候选

## 结果

| 因子 | IC | IR | 月数 | 分类 |
|---|---:|---:|---:|---|
| `ma_order_score` | +0.0129 | +0.27 | 12 | **dead** |
| `convergence_speed` | +0.0163 | +0.08 | 12 | **dead** |
| `ma_bandwidth_pct` | +0.0018 | −0.21 | 12 | **dead** |

三者均未达 alive 门。同次全库仍仅 `ret_skew`、`hl_pos` 存活。

## 判定

**H15 三个二阶质量因子全部不进入单变量验证队列。**

| 因子 | 候选？ | 备注 |
|---|---|---|
| `ma_order_score` | 否 | IC 弱；候选规则已要求 `order_score_min=3`，池内方差被截断 |
| `convergence_speed` | 否 | 二阶噪声更大，IR 接近 0 |
| `ma_bandwidth_pct` | 否 | 与入选门槛 `fast_spread/full_spread` 高度共线，池内几乎无排序力 |

## 解读

密集启动规则已经用 spread / order_score 做了硬截断，池内再构造「质量」变体时，边际信息被筛选器吃掉——这是 **selection-conditioned IC collapse**：特征在全样本可能有用，但在通过规则后的条件分布上 IC→0。主线 28 维里已有 `order_score`、`ma_spread_pct`、`spread_chg*`，H15 二阶增量未体现。

## 风险与诚实声明

- 未做 LightGBM 单变量增益（IC 未过线）；不排除非线性组合仍有用，但按纪律不跳级。
- `ma_order_score` 与主线 `order_score` 高度相关，即便 alive 也需确认增益非重复计数。
- 未碰 holdout / 冻结模型 / 主线特征表。

## 下一步

1. **默认不加** H15 进 `features.py`。
2. 若继续挖密集质量，优先 **规则外** 或 **放宽门槛后的条件 IC**，而非在 expanded 池内叠二阶。
3. 进入任务 3（BTC 大盘状态共享特征）。
