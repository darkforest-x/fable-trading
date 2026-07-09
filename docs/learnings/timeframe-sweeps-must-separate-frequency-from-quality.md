# Timeframe sweeps must separate frequency from quality

- **问题**：R4 要验证 5m 是否扩大机会面、30m/1H 是否提升密集结构质量；这两个目标容易被同一张收益表混在一起。
- **死胡同**：只看 top-decile 净收益会误判 1H 和 30m，因为高周期样本少，少数极端盈利足以把均值拉高；也会误判 5m，因为 p 值显著但机会数没有增加。
- **有效路径**：同表报告净@maker、filled-only、p 值和 `n_val_vs_15m_baseline`。先问“频率有没有兑现”，再问“质量有没有变好”。5m 以机会数和 filled-only 判负，30m 以显著性和经济性记为低频高质量线索，1H 因样本过少不确认。
- **通用规则**：跨时间框架实验必须把“更多机会”和“更高质量”拆成两个判定轴；样本数不足时，收益均值只能生成线索，不能生成结论。
- **牵连**：`scripts/mtf_sweep.py`、`analysis/output/mtf_sweep.json`、`analysis/p2b_mtf_report.md`。
