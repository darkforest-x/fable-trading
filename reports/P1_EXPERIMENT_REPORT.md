# P1 Experiment Report — Local Signal V2

**Decision**：历史发现级 `accepted`；候选 `B2_local_fixed_w30_causal`；生产级 `not eligible`。

| Arm | Point | Event P | Event R | Event F1 | FP/1000 | Gate |
|---|---|---:|---:|---:|---:|---|
| A legacy 200 | conf=0.05 | 2.96% | 7.54% | 4.25% | 1,239.16 | FAIL |
| B1 fixed 24 | conf=0.10 | 35.43% | 99.16% | 52.21% | 904.90 | FAIL |
| B2 fixed 30 | conf=0.35 | 81.93% | 73.46% | 77.47% | 81.12 | PASS / selected |
| C3 range 20–30 | conf=0.45 | 74.71% | 70.95% | 72.78% | 120.28 | PASS |

冻结发现门为 Precision≥50%、Recall≥50%、FP/1000≤250。共同尺 715 endpoints，其中 358 正事件、357 easy negatives；最大时间 2026-05-03 10:45 UTC。holdout 消耗 0，未 promote、未部署。

完整方法、数据门、训练诊断、风险、复现命令与下一步选项见：

- `analysis/p1_local_signal_v2_report_20260811.md`
- `analysis/html/p1_local_signal_v2_report_20260811.html`
- `analysis/output/p1_local_signal_v2/comparison.json`
- `reports/ACCEPTANCE_DECISION.json`
