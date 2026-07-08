# 阶段 2b-v2 报告：宽障碍 + 新数据 + 双池对比

**日期**：2026-07-08
**代码**：`src/judgment/{candidates,labeling,features,build_dataset,train}.py`（v2 默认：TP 4×ATR / SL 2×ATR、`atr_pct ≥ 0.0015`、strict/expanded 双阈值预设）
**数据**：`data/kline_fetched/`（56 币种 × 400 天 OKX 15m，2026-07-07/08 拉取）+ 旧缓存合并，共 328 序列
**产出**：`analysis/output/p2b_v2_{strict,expanded}_metrics.json` 等（train.py --tag 前缀输出）
**复现**：

```bash
python3 -m src.judgment.build_dataset --mode strict   --out data/judgment_dataset_v2_strict.csv
python3 -m src.judgment.build_dataset --mode expanded --out data/judgment_dataset_v2_expanded.csv
python3 -m src.judgment.train --data data/judgment_dataset_v2_strict.csv   --tag p2b_v2_strict
python3 -m src.judgment.train --data data/judgment_dataset_v2_expanded.csv --tag p2b_v2_expanded
# 注意：两次训练均未加 --eval-holdout。holdout 未被触碰。
```

## 一句话结论

**阶段 2b 验收通过（2026-07-08 holdout 一次性评估，项目所有者批准）：
expanded × v2 在 holdout 上 AUC 0.602（p=0.001）、top-decile 扣成本净收益 +0.083%，
两项验收标准全部满足，进入阶段 3。**
v2 的核心修复是标签经济性：两池 top-decile 净收益均由负转正
（v1 为 -0.07%/-0.11%）；strict 池模型学不过单特征基线，扩池假设成立，
expanded 为唯一主线配置。holdout 明细见 6.5 节。

## 1. 数据变化

- 新拉取 56/57 币种（TON_USDT 在 OKX 无此交易对，跳过），每币 400 天完整 15m；
- 候选时间范围从 v1 的"高度集中于 2025-12 之后"扩展为 **2025-06 ~ 2026-07 共 13 个月**；
- strict 候选 1 371 → **2 898**，expanded 候选 **10 255**（3.5×），有候选币种 106 / 190 个。

## 2. 实验矩阵结果（val，均未触碰 holdout）

| 指标 | v1 参照（strict×窄障碍） | strict × v2 | expanded × v2 |
|---|---|---|---|
| 候选数 | 1 902 | 2 898 | 10 255 |
| 正类率（TP 先触） | 45.2%（val） | 36.9% | 35.5% |
| val 样本数 | 270 | 464 | 1 598 |
| val AUC | 0.565 | 0.543 | **0.565** |
| 置换检验 p | 0.033 | 0.045 | **0.001** |
| 单特征基线 AUC | — | **0.556（高于模型）** | 0.462（模型完胜） |
| top-decile 毛收益 | +0.13% | +0.29% | +0.30% |
| **top-decile 净收益（扣 0.2%）** | **-0.07%** | **+0.091%** | **+0.101%** |
| top-decile 胜率 | 51.9% | 54.3% | 50.9% |
| 全池平均净收益 | 负 | +0.039% | +0.036% |

（v1 holdout 参照：AUC 0.591、p=0.002、top-decile 净 -0.109%——即"有信号没利润"的原始证据。）

## 3. 解读

1. **障碍加宽达到了设计目的**。top-decile 毛收益从 +0.13% 提到 +0.29%/+0.30%，
   成本占比从 154% 降到 ~66%，净收益因此转正。这验证了 v2 决策的核心假设：
   v1 的问题在标签的收益尺度，不在模型。
2. **expanded 是两池中唯一"模型有增量价值"的配置**。
   - 显著性：p=0.001（strict 仅 0.045，不满足 PROJECT_PLAN 的 p<0.01）；
   - 对照基线：单特征 logreg 在 expanded 池 AUC 只有 0.462、top-decile 净收益 -0.196%，
     而 LightGBM +0.101%——扩池后"哪些候选值得做"不再是 ma_spread 一个维度能回答的，
     模型学到了额外结构（top 特征：close_vs_ema200、ext_up、atr_pct、drawdown24）；
   - strict 池上模型反而学不过基线（0.543 vs 0.556），样本少（train 1 829）是主因。
3. **"扩池收益 > 稀释代价"成立**：expanded 正类率仅比 strict 低 1.4 个百分点，
   信号数量 3.4×，净收益反而更高。

## 4. 验收对照（PROJECT_PLAN 阶段 2b 标准，在 val 上）

| 标准 | strict × v2 | expanded × v2 |
|---|---|---|
| 时间外 AUC 显著 > 0.5（p < 0.01） | ❌ p=0.045 | ✅ p=0.001 |
| top-decile 扣 0.2% 成本为正 | ✅ +0.091% | ✅ +0.101% |

**expanded × v2 在验证集上通过全部 2b 验收标准**；正式判定需 holdout 一次性评估确认。

## 5. 风险与诚实声明

- **holdout 本轮未触碰**（两次训练均未加 --eval-holdout）。v1 已消耗过一次 holdout；
  PROJECT_PLAN 规定 v2 仅允许最终对照时各评一次，何时动用由项目所有者决定；
- **净期望仍然薄**（~0.10%/笔）：滑点若比 0.2% 假设差，利润可被吃掉。
  阶段 3 带完整成本的回测才是试金石；
- ~~标签窗口重叠泄漏未处理~~ **勘误（2026-07-08 核实）**：purge/embargo 已在
  `train.py` 实现（`PURGE_WINDOW` = 73 根 outcome 窗口，dev/holdout 与 train/val
  两个边界均清除，与 `labeling.py` 的 entry=i+1、HORIZON_BARS=72 精确对应）。
  本报告最初版本误称泄漏未处理——上表全部指标本来就是泄漏修正后的数字；
- 本轮同时改了障碍、数据、池三个变量，是项目所有者 2026-07-07 批准的打包决策
  （记录于 PROJECT_PLAN 2b-v2 节），此后恢复单变量纪律。

## 6.5 holdout 一次性评估（2026-07-08，项目所有者在对话中批准后执行）

**配置**：expanded × v2，tag `p2b_v2_expanded_final`（train/val 与上文完全一致，仅追加 holdout 评估）。
**这是该配置对 holdout 的第一次、也是计划内唯一一次消耗**（时间窗与 v1 消耗的是同一段
2026-05-04 之后，标签结构不同，风险已在 PROJECT_PLAN 记录）。

| 指标 | val（参照） | **holdout（2 214 样本，2026-05-04 ~ 07-06）** |
|---|---|---|
| AUC | 0.565 | **0.602** |
| 置换检验 p | 0.001 | **0.001** |
| top-decile 净收益（扣 0.2%） | +0.101% | **+0.083%** |
| top-decile 胜率 | 50.9% | 48.0% |
| 全池平均净收益 | +0.036% | **-0.148%** |
| 基线 AUC / top-decile 净 | 0.462 / -0.196% | 0.528 / **-0.05%** |

**判定：阶段 2b 验收通过**——AUC 显著 > 0.5（p=0.001 < 0.01）✅，
top-decile 扣成本净收益为正（+0.083%）✅，且样本外继续跑赢基线。

值得如实记录的三点：

1. holdout 期（2026-05 ~ 07）全池平均净收益 -0.148%，比 val 期恶劣得多——
   模型的选择价值（+0.083% vs -0.148%，选择增益 ~0.23%/笔）在更差的行情里依然成立；
2. top-decile 胜率 48% < 50%：盈利来自障碍不对称（TP 距离是 SL 的 2 倍），
   靠赔率不靠胜率，这与设计一致，但意味着连亏容忍度要在阶段 3 用回撤指标检验;
3. 净期望 +0.083%/笔 仍然薄，阶段 3 带真实成本模型的回测是最终裁决。

## 6. 下一步选项（待项目所有者决策）

（原选项 A"先做 purged CV"作废——核实后确认 purge 已实现，见第 5 节勘误。）

- **A（推荐）**：用 expanded × v2 做 holdout 一次性评估——该配置已通过 val 全部验收
  且泄漏修正已确认在位；通过则进阶段 3 回测框架；
- **B**：继续在 val 上做特征/参数单变量迭代，暂不动 holdout（更保守，但 val 只有
  1 598 样本，继续迭代的过拟合风险随次数上升）。
