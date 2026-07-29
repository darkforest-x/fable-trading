# 完美 train 分类会遮住时间/来源泛化塌陷

- **问题**：ETH3m v2 classifier 在 train 固定 p=0.50 下 TP22 FP0 TN73 FN0，但同一阈值在时间后段 val 上 TP0 FP0 TN34 FN8。
- **死胡同**：只看 Ultralytics top1 会误判为 34/42 尚可；但这个数等于 all-no 多数类基线，且完全没有召回正例。
- **有效路径**：用固定阈值同时评估 train 与 val，并报告概率范围；train 正负分离接近完美，而 val 全体 pmax 只有 0.43679，直接暴露来源/时间捷径。
- **通用规则**：小样本图像分类诊断先做 train/val 固定阈值混淆矩阵和概率摘要，再谈 AUC/AP；top1 必须和多数类基线同表。
- **牵连**：`scripts/evaluate_eth3m_short_pilot_v2_cls.py`、`analysis/output/eth3m_short_pilot_v2_cls_diag_20260730/summary.json`、固定阈值 p=0.50、禁止读取 holdout/weak/smoke。
