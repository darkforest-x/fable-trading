# 连亏长度主要由胜率决定，要先和「打乱顺序」比一比

- **问题**：SPIKE V12.9 ETH 5m 普通信号最长连亏 16 笔，币安早期样本里是 20 笔。很容易把它解读成「某段行情不适合」「连亏之后该反弹了」，进而想加过滤或倍投。
- **死胡同**：按连亏发生的日期去找行情原因，任何一段都能事后编出解释；看「连亏 N 笔后下一笔胜率」时，单看某一档（例如 5 连亏后 39%，样本 33 笔）就会以为找到了规律。
- **有效路径**：把同一组盈亏结果随机打乱 20000 次。OKX 普通信号打乱后的最长连亏平均 17.1 笔，观测值 16 笔（p=0.62）；游程检验 z=0.18；币安 20 笔对打乱后平均 19.2 笔。连亏之后下一笔的胜率在各档之间随机起伏（22%~39%），没有随连亏变长而上升。结论：这些连亏就是胜率约 27% 的系统在几百笔交易里本来会出现的长度，不是行情聚集造成的。
- **通用规则**：看到长连亏，先算胜率 p、笔数 n 下打乱顺序的最长连亏分布（经验上约为 log(n·p)/log(1/(1-p))）。只有观测值明显超过打乱后的 95% 分位，才值得去找行情原因。连亏后胜率不变，倍投就没有依据。
- **牵连**：`experiments/active/exp-spike-v129-eth5m-20261010-v1/analyze_patterns.py`（`streaks`）、`run_v1/patterns_v1/streaks.json`。相关：[breakeven-breaks-streaks-without-clearing-recovery-debt.md](breakeven-breaks-streaks-without-clearing-recovery-debt.md)、[post-hoc-subgroup-edges-need-a-non-overlapping-window.md](post-hoc-subgroup-edges-need-a-non-overlapping-window.md)
