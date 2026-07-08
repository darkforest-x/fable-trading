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

**v2 把经济性修正过来了：两池 top-decile 信号扣 0.2% 成本后均转正
（strict +0.091%、expanded +0.101%，v1 为 -0.07%/-0.11%）；
其中 expanded 池同时通过显著性检验（置换 p=0.001）并大幅跑赢单特征基线，
是进入阶段 3 的候选配置；strict 池模型与单特征基线无法区分，扩池假设成立。**

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
- **标签窗口重叠泄漏未处理**：triple-barrier 前向窗口在 train/val 边界附近存在重叠，
  purged CV + embargo 是下一个单变量改进候选（预期让指标更保守、更可信）；
- 本轮同时改了障碍、数据、池三个变量，是项目所有者 2026-07-07 批准的打包决策
  （记录于 PROJECT_PLAN 2b-v2 节），此后恢复单变量纪律。

## 6. 下一步选项（待项目所有者决策）

- **A（推荐）**：先做 purged CV/embargo 单变量改进，指标若仍达标，再动用 expanded 池的
  holdout 一次性评估；通过则进阶段 3 回测框架；
- **B**：直接用 expanded × v2 做 holdout 一次性评估（更快，但把最后一发子弹打在
  未做泄漏修正的配置上）；
- **C**：继续在 val 上做特征/参数单变量迭代，暂不动 holdout。
