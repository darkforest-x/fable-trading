# H13 BTC 大盘状态共享特征（SWAP 池, train/val）

**日期**：2026-07-15  
**纪律**：发现级 val-only；holdout 未碰；未改 `features.py` / 冻结模型。  
**特征**：BTC_USDT_SWAP **1h** EMA55 斜率（4 根 pct_change）+ 1h ATR% 分位（168 根滚动秩）。  
因果：1h bar 仅在收盘后可用（`available_at = open_time + 1h`），`merge_asof` 后向对齐 15m 信号。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/h13_btc_regime.py
```

## 数据统计

- 候选池（含特征对齐前）：23683 / 币种 256
- 可用（28 维 + BTC 特征非空）：23683
- train/val：18755 / 4705（load_splits 标准 purge）
- BTC 1h bars：9699

## IC（因子 vs 72-bar 前向 close ret）

| 因子 | n | IC | IR | 月数 | 符号稳定 | 分类 |
|---|---:|---:|---:|---:|---|---|
| btc_ema55_slope | 23683 | +0.0187 | -0.19 | 12 | ✗ | dead |
| btc_atr_pctile | 23683 | +0.0436 | +0.06 | 12 | ✗ | reversed |

IC 存活（|IC|≥0.03 且符号稳定）：（无）

## 单变量净增益（val，top-decile 净@maker 0.06%）

基线 28 维 top 净@maker：+0.00138（AUC 0.5515，p=0.001）

| 特征集 | val AUC | p | top 净@maker | 相对基线增益 | top 胜率 |
|---|---:|---:|---:|---:|---:|
| baseline_28 | 0.5515 | 0.001 | +0.00138 | — | 0.3723 |
| +btc_ema55_slope | 0.5665 | 0.001 | +0.00179 | +0.00041 | 0.3872 |
| +btc_atr_pctile | 0.5485 | 0.001 | +0.00149 | +0.00011 | 0.3766 |
| +both_btc | 0.5599 | 0.001 | +0.00181 | +0.00043 | 0.3894 |

## 判定

| 门槛 | 结果 |
|---|---|
| IC 存活（\|IC\|≥0.03 且符号稳定） | **0 个**（`btc_atr_pctile` 有 IC+0.044 但符号不稳 → reversed） |
| 单变量 top 净@maker 增益 | 最大 +0.041%/笔（`+btc_ema55_slope`），**远低于** 研究议程常用 +0.02% 实质线的经济意义（此处增益≈噪声量级） |
| 置换 p | 基线与各变体均为 0.001（模型整体可分，不证明新增特征贡献） |

### 总判定

**未通过（不并入主线）**。  
IC 无稳定存活；单变量净增益虽数值略正，但仅 +0.01%~+0.04%/笔，相对 top 本身 ~0.14% 的水平属于 LightGBM 抖动区，**不构成发现级证据**。禁止据此改 `features.py`。

## 解读

- `btc_ema55_slope` 略抬 AUC（0.551→0.567）与 top 净，方向符合「大盘向上有助山寨突破」直觉，但幅度不足以过线。
- `btc_atr_pctile` 单独 IC 较高却月度反号（高波动月有时助涨有时助跌），reversed 分类合理；并入后 AUC 反而略降。
- 两特征同加（+both）增益 ≈ 单加 slope，无协同。
- 池内本地 `atr_pct` / `slow_slope_12` / `ret_*` 已吸收大部分可交易的「环境」信息，BTC 共享状态边际有限。

## 风险与诚实声明

- val 已多次用于选型，数字只排序不宣称绩效；
- train 18755 / val 4705 来自本脚本重建池（TP5/SL2），与冻结数据集 SHA 不同，仅作相对比较；
- ATR 分位 rolling apply 完全因果；1h 收盘后才可用，已用 `available_at` 防前视；
- 未使用 funding / OI；未碰 holdout、冻结模型、`forward_log`。

## 下一步

1. 默认 **不** 把 BTC 特征写入 `features.py`。
2. * 若 owner 仍想要大盘状态，优先试更慢时钟（4h/1D 趋势 regime 离散标签）或 BTC-alt 相对强度，而非连续 1h 斜率。
3. 进入出场结构族任务（H3/H4/H5）。