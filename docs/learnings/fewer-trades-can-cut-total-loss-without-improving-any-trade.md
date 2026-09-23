# 总亏损减少可能只是少做了，不代表留下的单子变好

- **问题**：V12.7 候选（持线证据寿命 600→8 根）让全期总净R 从 −1716R 变成 −876R，
  胜率 28.68%→29.10%，PF 0.876→0.913。看上去像"过滤有效"。
- **死胡同**：直接看总额和胜率就宣布改善。这会把"少做了一批亏钱的"说成"每笔更好"，
  也会掩盖后段其实没变好。
- **有效路径**：把总额差拆成**数量项**和**质量项**（`paired_attribution.csv`）：
  共同事件 9,304 笔逐笔收益**完全相同，质量差 = 0.00**；差额全部来自删掉的 9,630 笔（合计 −1011R）
  和释放占仓后新增的 4,365 笔（合计 −171R）。再看后段：胜率 24.375%→24.384%、
  每笔净R −0.2325→−0.2354、每笔净bp −40.25→−47.91、相对匹配随机的超额 −0.019→−0.024R，四项预列检验 Holm p 全 1。
  结论因此写成"延迟解决了，收益没有改善"，默认值按运行前写死的非劣规则判为关闭。
- **通用规则**：任何会减少交易数的改动，验收必须同时给出：每笔净R/净bp、按同一时段的胜率、
  相对匹配随机的超额，以及**数量项/质量项分解**。总R、总bp、PF 都会随笔数变化，不能单独当证据。
  规则里若有"非劣才默认开启"，把阈值写在计划里、跑之前定死，避免事后按好看的那一栏挑结论。
- **牵连**：`yoyo/evaluation/spike_v127_held_age_report.py`（`default_rule`/`latency`）、
  `analysis/p1_spike_v127_held_age_20260923.md`；同族教训见
  [pool-internal-metrics-cannot-see-beta.md](pool-internal-metrics-cannot-see-beta.md) 与
  [breakeven-removal-can-raise-win-rate-and-lower-expectancy.md](breakeven-removal-can-raise-win-rate-and-lower-expectancy.md)。
