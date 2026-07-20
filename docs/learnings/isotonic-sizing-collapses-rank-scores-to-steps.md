# isotonic 校准做仓位映射会把排序分压成台阶，阈值附近的交易被静默弃单

- **问题**：weight-centric 实验里，isotonic 校准概率映射 w=(p−p_min)/scale 的变体
  在 val 窗表现反而弱于简单分位分档——净收益 +155% vs 分档 +192%。
- **死胡同**：直觉认为「校准概率比原始排序分更适合做连续仓位」。实际 isotonic 是
  分段常数函数：阈值（val-q90）附近的一整段分数落在同一平台上，p 恰好 ≤ p_min，
  w=(p−p_min) 映射把 412 笔合格交易中的 82 笔（19%）静默映射到 w=0 弃单——
  而这批交易实际均净 +2.26%、胜率 82.9%。丢的不是噪声，是盈利腿。
- **有效路径**：直接用分数分位分档（q90-95/q95-99/q99+ → 1x/1.5x/2x）。关键判断：
  模型是回归排序分，档间收益单调（val 2.6%/3.7%/8.1% 净/笔，train 窗迁移同样单调），
  分位映射保留全部排序信息且每笔合格交易权重 ≥1x，不产生隐性弃单。
- **通用规则**：把「分数→仓位」映射接到任何过滤阈值后面时，先检查映射在阈值处的取值——
  若 w(threshold)=0（proportional-above-minimum 类映射天然如此），映射就偷偷收紧了
  入场过滤，实验不再是单变量。要么给 w 设正下限，要么用分位档。
- **牵连**：`scripts/weight_centric_backtest.py`、`analysis/p_weight_centric_val.md`、
  sklearn `IsotonicRegression`（out_of_bounds="clip"，分段常数）、阈值 val-q90=0.02022。
