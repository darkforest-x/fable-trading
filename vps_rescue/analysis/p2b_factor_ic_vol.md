# H14/H17/H18 成交量因子三连 IC 筛选（SWAP 池, train/val）

**日期**：2026-07-15  
**纪律**：发现级 val-only；holdout（≥2026-05-04）未碰；未改 `features.py` 主线特征表。  
**目的**：在判断层量特征偏少（28 维里量只占 3）的背景下，筛 3 个因果无前视成交量因子是否具备进入单变量验证的资格。

## 复现命令

```bash
# 因子实现：src/factors/library.py → FACTORS[obv_slope|vol_dryup|taker_imbalance]
PYTHONPATH=. python3 scripts/factor_ic_screen.py
# 本报告摘录自 analysis/output/factor_ic_screen.json 中的三因子行
```

## 因子定义（因果，仅用 bar ≤ t）

| 因子 | 假设 | 定义 |
|---|---|---|
| `obv_slope` | H18 吸筹/派发 | 20 根 OBV 斜率，除以近 20 根 \|OBV\| 均值做跨币种尺度归一 |
| `vol_dryup` | H17 缩量深度 | 近 8 根密集期均量 / 更早 48 根均量；密集 = `ma_spread_pct ≤ 0.0028`（无该列时退化为近 8 根均量） |
| `taker_imbalance` | H14 买压近似 | 20 根均值：`(close-low)/(high-low)*volume`，再除以 20 根均量 → 近似买盘份额 |

## 数据统计

- 候选：24 179（expanded 规则，SWAP，`okx`，排除 stockish）
- 币种：256
- 前向收益代理：信号后 72 根 close 收益（与 H19 全库 IC 筛一致）
- IC = Spearman(因子, 72-bar 前向收益)；月度 IC 算 IR 与符号稳定（≥70% 月份同号）

## 结果

| 因子 | n | IC | IR | 月数 | 符号稳定 | 分类 |
|---|---:|---:|---:|---:|---|---|
| `obv_slope` | 24179 | +0.0246 | +0.14 | 12 | — | **dead** |
| `vol_dryup` | 24179 | +0.0206 | +0.31 | 12 | — | **dead** |
| `taker_imbalance` | 24179 | −0.0017 | −0.23 | 12 | — | **dead** |

判定阈值：`|IC| ≥ 0.03` 且符号稳定 → `alive`（候选）。三者均未达到。

对照（同次跑全库，H19 存活仍为 2 个）：`ret_skew` IC −0.0418 alive、`hl_pos` IC +0.0404 alive。本三因子 IC 绝对值均低于存活线。

## 判定

| 因子 | 是否候选 | 说明 |
|---|---|---|
| `obv_slope` | **否** | \|IC\|=0.025 < 0.03；方向为正（吸筹→后续偏多）符合直觉但偏弱 |
| `vol_dryup` | **否** | \|IC\|=0.021 < 0.03；IR +0.31 略稳但幅度不够；符号为正（缩量后偏涨）与 VSA 假说同向 |
| `taker_imbalance` | **否** | IC≈0，无预测力；OHLC 近似买压可能噪声过大 |

**结论**：三因子均 **不进入** 单变量增益验证队列。负结果保留：成交量方向假说在「密集启动候选池 + 72bar 前向收益」切片上尚未显现出可过线的线性秩相关。

## 解读

1. `obv_slope` 最接近阈值，说明 OBV 斜率有微弱同向信息，但不足以越过 H19 的 alive 门。
2. `vol_dryup` IR 相对好（月度更稳）但 IC 仍弱——可能「缩量」效应被规则扫描本身的 volume_ratio 门槛部分吸收。
3. `taker_imbalance` 用 bar 内位置近似 taker 买压，在 15m 上几乎无 IC，优先等 OKX 真 taker 字段（H14 原假设）再测，而非继续打磨代理。

## 风险与诚实声明

- IC 是线性秩相关，不保证加入 LightGBM 后的 top-decile 净收益；本轮甚至未达 IC 门，故未做单变量 retrain。
- `vol_dryup` 在候选 bar 上「近 8 根密集」与规则 `dense_run` 不完全同构；定义差异可能压低 IC。
- 前向收益代理是 72 根 close ret，不是 TP5/SL2 标签收益；与主线标签不完全对齐。
- 未碰 holdout、未改主线特征、未改冻结模型。

## 下一步（需 owner 决策的标 *）

1. 默认：**不**把三因子并入 `features.py`。
2. * 若 owner 仍想试 `obv_slope`（最接近线）：可单独做单变量 top 净增益，作为 IC 门的对照实验。
3. H14 真 taker 字段到位后再重跑 `taker_imbalance` 同类定义。
4. 继续任务 2（H15 密集质量二阶）与任务 3（BTC 大盘状态）。
