# 分类准确率必须先和多数类常数预测比较

- **问题**：ETH 3m 分类器的 val top1 为 80.95%，表面不错，但验证集恰有 34/42 个 `no_start`；固定阈值混淆矩阵实际是 TP0/FP0/TN34/FN8。
- **死胡同**：只读训练器汇总的 top1，或把 early-stop 保存的 `best.pt` 自动当成“学到了信号”。在不平衡数据上，全部预测多数类也能成为 top1 最优 checkpoint。
- **有效路径**：用预注册的 0.50 阈值逐图导出正类概率，强制报告 TP/FP/TN/FN、recall 与 balanced accuracy，并和常数分类器及简单因果规则同表比较。本例 train 完美而 val 全负，直接暴露了时间外泛化崩溃。
- **通用规则**：任何不平衡二分类的第一条验收都不是 accuracy，而是固定阈值混淆矩阵；若 accuracy 等于多数类占比，默认判为退化，除非逐样本证据证明不是常数预测。
- **牵连**：ETH 3m v2 classifier；val 8 正/34 负；固定 p=0.50；Ultralytics top1；预注册 TP≥6/8、FP≤2/34。
