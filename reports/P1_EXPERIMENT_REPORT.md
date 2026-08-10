# P1 Experiment Report — Local Signal V2

**Decision**：P1 局部化假设仅按历史发现级 `accepted`；后续密度审计判定 B2 当前 operating
point `failed_on_enriched_proposal_pool`，生产级 `not eligible`，P3 判断层阻断。

> **2026-08-11 口径纠正**：3,880 是 B2 在已预筛 v10 proposal ledger 上的 L1 fire rows，
> 不是订单。conf=0.35 命中 56/357 easy-negative endpoints（15.69%）和 3,880/7,795
> proposal rows（49.78%，88.27 fires/ledger-day）。这不是重复/edge/PNG 传输 bug；把 conf
> 抬到0.45会让召回从73.46%塌到6.98%。下一步必须先做P2 hard-negative mining与连续
> causal-tip密度回放，不能直接进入P3判断层。

| Arm | Point | Event P | Event R | Event F1 | FP/1000 | Gate |
|---|---|---:|---:|---:|---:|---|
| A legacy 200 | conf=0.05 | 2.96% | 7.54% | 4.25% | 1,239.16 | FAIL |
| B1 fixed 24 | conf=0.10 | 35.43% | 99.16% | 52.21% | 904.90 | FAIL |
| B2 fixed 30 | conf=0.35 | 81.93% | 73.46% | 77.47% | 81.12 | PASS / selected |
| C3 range 20–30 | conf=0.45 | 74.71% | 70.95% | 72.78% | 120.28 | PASS |

冻结发现门为 Precision≥50%、Recall≥50%、FP/1000≤250。共同尺 715 endpoints，其中 358 正事件、357 easy negatives；最大时间 2026-05-03 10:45 UTC。holdout 消耗 0，未 promote、未部署。

这里的 FP/1000 分母是平衡抽样 endpoints 的窗口 bars，只能裁决 P1 局部化对照，不能代表
连续市场触发密度。后验密度审计见 `analysis/output/p1_b2_density_diagnostic_20260811.json`。

完整方法、数据门、训练诊断、风险、复现命令与下一步选项见：

- `analysis/p1_local_signal_v2_report_20260811.md`
- `analysis/html/p1_local_signal_v2_report_20260811.html`
- `analysis/output/p1_local_signal_v2/comparison.json`
- `reports/ACCEPTANCE_DECISION.json`
