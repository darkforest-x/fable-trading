# 门放行率异常，先查分值并列，再查阈值

- **问题**：接管计划记的是「q90 阈值在 val 放行约 91.2%，门太松」。本机复核确认放行率
  （池 89.0% / val 91.1%），但病因不是阈值：`models/ACTIVE` 指向的 frozen artifact
  `best_iteration = 1`、文件里只有 **1 棵树**、18,379 个候选只有 **15 个不同分值**，
  其中 **83.9% 完全同分**，而 `threshold_val_q90` 恰好等于那个众数分值。
  q90 是分位数，当 84% 的行共享一个分数时分位数就落在并列块上，`score >= threshold` 放行整块。
- **死胡同**：把它当阈值问题处理——下调/上调 `threshold_val_q90`、重扫阈值网格、
  在报告里加 `pass_rate` 指标。这些**全都不会改变任何一笔的排序**：84% 的候选对这个模型
  是无差别的，任何阈值都只能在"全放"和"全拒"之间跳，中间不存在"顶十分位"这个东西。
  按项目纪律阈值还是 owner 决策，于是这条路既无效又要占用 owner 的决策额度。
- **有效路径**：先算**分值基数**再算放行率——`np.unique(scores)` 的长度、众数覆盖率、
  众数是否等于阈值。三个数出来病因就是自明的：模型没有分辨率，不是门没关紧。
  确认后再看 `best_iteration` 与 `booster.num_trees()`，直接坐实退化产物。
- **通用规则**：**放行率是「模型 × 阈值」的联合性质，不是阈值的性质。**
  任何"门太松/太紧"的报告，第一步查分值分布的并列质量，不是查阈值。
  推论：冻结 artifact 前必须打印分值唯一率与放行率，
  放行率偏离目标分位数超过 ±2% 就不许冻结——本项目唯一的正结果是"挑顶十分位"，
  而一个 84% 同分的模型**在定义上**做不到这件事，AUC 和元数据都看不出来。
- **牵连**：`scripts/diag_active_artifact_resolution.py`（本条的复现脚本）、
  `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json`、`src/judgment/frozen.py`；
  病根是早停指标不是业务指标，见
  [checkpoint 选优必须对齐真实验收门](checkpoint-selection-must-optimize-the-real-acceptance-gate.md)；
  阈值方向的姊妹条 [下调阈值不会创造边](lower-threshold-does-not-create-edge.md)；
  被它挡住的那个结论 [顶十分位是极端而非密集](top-decile-is-extreme-not-dense.md)。
