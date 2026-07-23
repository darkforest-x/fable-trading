# Owner 扩特征分边裁决 — 2026-07-23

**纪律**：纯离线，`<2026-05-04`（**未碰 holdout**）；long→`label_candidate`、
short→`label_short_candidate`；TP5/SL2/72bar；成本同时报 swap maker 0.06% 与 legacy
0.2%。不 promote、不改 ACTIVE、不改执行器。

**实验主题（owner 批准为单变量）**：针对已标 long/short 框，在 cut_global 及之前
窗口**尽量因子化**本地 OHLCV 可算量（不设窄特征天花板），再做因果规则裁决。

回答：上轮窄特征（`add_indicators`/`add_features` ≈30 列）分边未过 1.3；**扩到
116 列市场因子后，能否救出可部署边（因果 PF@maker ≥ 1.3）？**

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python scripts/owner_side_rich_features_verdict.py \
  --sheet analysis/output/owner_side_review/review_sheet.csv \
  --n-symbols 0 --tag owner_side_rich_features_verdict

# 输出:
#   analysis/output/owner_side_rich_features_verdict.json
#   analysis/output/owner_side_rich_features_verdict_main.csv
#   analysis/output/owner_side_rich_features_verdict.log
```

特征实现：`scripts/owner_side_rich_features.py`（在 judgment 窄特征链上叠加）。

## A. 标注与特征清单

| owner_side | n |
|---|---:|
| long | 1152 |
| short | 1361 |
| skip | 12 |
| 未标 | 0 |

| 项 | 值 |
|---|---|
| 市场特征数 | **116**（窄版 ≈29） |
| 组 | ma_family / dense_spread / momentum_structure / volatility / volume / structure / time |
| 框几何 | `box_width_bars` / `box_height_pct` / `box_right_frac` — **仅披露，不进因果规则** |
| holdout | 未触碰 |

覆盖示例（能写都写，限本机 OHLCV）：

- 多周期均线：SMA/EMA 相对位置、间距、斜率、交叉、纠缠度、带宽、BB 宽
- 密集/离散：spread 水平/变化/分位代理、扩张·收缩 run、离密扩张标记
- 动量/结构：多窗 ret/ROC、距高低、突破距、区间位置、HH/HL/LH/LL 粗标签
- 波动：ATR7/28、realized vol、影线/实体比
- 量能：相对均量、量 z、价量相关近似、突破根量比、上涨量占比
- 时间：hour/dow 的 sin/cos

## B. 主表（裁决 = 因果规则 PF，不看 oracle / AUC）

成功线：**分边因果规则 PF @ SWAP maker ≥ 1.3**。

| side | n_boxes | n_feat | LGBM AUC | oracle n | oracle PF@maker | 规则 n | **规则 PF@maker** | 规则 PF@0.2% | 窄版 PF@maker | Δ vs 窄 | ≥1.3? |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **long** | 1152 | 116 | 0.980 | 1090 | 5.620 | 53466 | **0.938** | 0.841 | 0.917 | +0.021 | **否** |
| **short** | 1361 | 116 | 0.972 | 1286 | 7.417 | 25718 | **1.227** | 1.094 | 1.127 | +0.100 | **否** |

对照：

| 口径 | PF@maker |
|---|---:|
| Emergence（已发表） | 0.874 |
| 窄特征分边 long / short | 0.917 / 1.127 |
| **本轮扩特征 long / short** | **0.938 / 1.227** |

**裁决：扩特征仍未救出可部署边。** short 从 1.127→1.227，仍差 1.3；long 仍 <1。

## C. LGBM gain 披露（不当终审）

### long — top gain

| 特征 | group | gain |
|---|---|---:|
| order_score | ma_family | 7594 |
| close_vs_ema20 | ma_family | 2475 |
| fast_spread_chg4 | dense_spread | 2001 |
| spread_chg8 | dense_spread | 1903 |
| ext_up | ma_family | 1234 |

组合计：ma_family ≫ dense_spread ≫ momentum；volume/time/structure 几乎无贡献。

因果 AND：
`order_score≥4` ∧ `close_vs_ema20≥0.0085` ∧ `fast_spread_chg4≥0.0028` ∧
`spread_chg8≥0.0038` ∧ `ext_up≥0.0040`

### short — top gain

| 特征 | group | gain |
|---|---|---:|
| close_vs_sma20 | ma_family | 7961 |
| order_score | ma_family | 5020 |
| fast_spread_chg4 | dense_spread | 3783 |
| spread_chg8 | dense_spread | 2261 |
| gap_sma60_120 | ma_family | 2014 |

因果 AND：
`close_vs_sma20≤−0.0102` ∧ `order_score≤0` ∧ `fast_spread_chg4≥0.0030` ∧
`spread_chg8≥0.0040` ∧ `gap_sma60_120≥−0.0019`

读法：扩特征后 top 仍是「方向结构（均线排序/相对均线）+ 已经在散开（spread_chg）」——
与窄版同一语义；新因子（结构标签、量能、时间、多窗 ATR）**没有改写手法画像**。

### long vs short（正样本内分类，披露）

AUC≈0.998；top = `close_vs_ema55` / `order_score` / `gap_ema8_21`。说明 owner
多空标注在均线方向上高度可分——**测量正确性**，不是新 alpha。

## D. Walk-forward 分数阈值（标明不可部署）

在已标样本时间切分上，用 LGBM 分数分位过滤正样本再结算：

| side | 最佳分位 | n | PF@maker | 可部署? |
|---|---:|---:|---:|---|
| long | q=0.6 | 381 | 5.05 | **否** |
| short | q=0.5 | 457 | 7.38 | **否** |

高 PF 来自「确认态选点 + 样本内阈值」，**未做全市场因果扫描**，不得当作可部署过滤。

## E. 风险与诚实声明

1. **特征再多也可能复制不了事后选点**——本轮 116 因子 vs 窄 29，规则 PF 只抬
   +0.02/+0.10，oracle 仍 5–7，gap 证明增量在 hindsight 选点，不在可规则化因子。
2. AUC≈0.97–0.98 仅说明「标框时刻 vs 随机 bar」可分，不作交易证据。
3. 框几何未进规则；与随机负样本的 box-LGBM gain 因负样本无框（中位填充）**不可信**，
   不作结论。
4. short 1.227 接近但未过 1.3；再抠阈值易过拟合，本轮不宣称「差一点就能上」。
5. 未消耗 holdout；即便过 1.3 也只是发现级。
6. 未改 ACTIVE / 执行器 / 新鲜度门。

## F. 下一步（需 owner 决策）

1. **停在「扩特征救不出边」**，回到 tip 金标 / 检测器路径（与纪律 12 一致）——推荐默认。
2. 若仍要挖：换问题（入场时机、持有规则），而不是继续堆 OHLCV 因子。
3. 不要把 walk-forward 分数门或 oracle PF 写进实盘。

## 产物

- 脚本：`scripts/owner_side_rich_features.py`、`scripts/owner_side_rich_features_verdict.py`
- 数值：`analysis/output/owner_side_rich_features_verdict.json`
- 主表：`analysis/output/owner_side_rich_features_verdict_main.csv`
- 窄版对照：`analysis/p_owner_side_feature_verdict.md`
