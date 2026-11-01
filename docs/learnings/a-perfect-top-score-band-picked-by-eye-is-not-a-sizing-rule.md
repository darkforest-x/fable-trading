# A perfect top score band picked by eye is not a sizing rule

- **问题**：Owner 在 live-wick 回测列表里看到评分 >0.76 的单子全部赢了，问能不能按高评分动态加大仓位（`exp-monday-limit-close-score-boost-20261101-v1`），之前也问过按排列分加仓（`exp-monday-limit-close-order-boost-20261101-v1`）。
- **死胡同**：直接看结果，评分 ≥0.76 一共 12 笔、12 笔全赢，每笔平均 +6.6%，按 1.5 倍仓位加仓后，2025 年起 6 份 3 倍账户从 5.34x 升到 6.46x，回撤不变。但这 12 笔全在 2025–26 年，2020–24 年一笔都没有，0.76 这条线就是看着这 12 笔画出来的。整体看，评分和盈亏、胜率的等级相关只有 0.06（打乱评分 2000 次，单侧 p≈0.10–0.11）。分档曲线也不单调：0.55–0.60 最差（2025 年以来胜率 52%），0.70–0.76 有 3 次止损。
- **有效路径**：加仓要和“随机挑同样数量的单子加同样倍数”比，因为对平均为正的单子，多下任何仓位都会提高倍数、拉大回撤。事先定的 0.6 线加 1.5 倍：2025 年起 7.79x 对随机中位 6.88x，只赢 80% 的随机挑法；2020–24 年反而比不加差。排列分 ≥3 加仓也一样，只赢 78%。只有事后画的 0.76 线赢 100%，但它在 2020–24 年没有样本，无法检验。
- **通用规则**：看到某个分数段“全赢”，先查三件事：这条线是不是看着结果画的；这段在选出期以外有没有样本；整条分数和收益的曲线是否单调。再用随机加仓对照，把“选得准”和“多下仓位”分开。三项都不过，就只能当前向观察的候选，不能直接改仓位。
- **牵连**：`yoyo/evaluation/monday_limit_close_score_boost.py`、`monday_limit_close_order_boost.py`（`weighted_frac`）、`survivors.csv.gz` 的 `score`（触价收盘评分）。相关：[max-profit-under-a-drawdown-cap-overshoots-out-of-sample.md](max-profit-under-a-drawdown-cap-overshoots-out-of-sample.md)。
