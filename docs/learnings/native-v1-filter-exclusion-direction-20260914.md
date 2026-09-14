# 固定筛选条件先写“排除什么”，避免把排除表实现成白名单

- **问题**：SPIKE V1 triple-exit 的固定组合条件是排除 `volume_ratio > 20`、实际下一开盘风险
  `> 0.30`、USDC/PAXG 和 stock-linked 候选。初版引擎却把这些条件写成全部必须满足的
  admission 条件，恰好反转了研究池的方向。

- **死胡同**：把字段名叫作 `AdmissionFilters`，再用 `not_above_threshold` /
  `not_allowed` 作为拒绝理由，会让代码在局部看起来一致，却没有表达研究协议的集合运算。
  只测“异常值被处理”也检不出这个错误：正常 BTC（rv=5、风险=5%）没有作为正向样本，
  USDC/PAXG、rv=21、风险=31% 和美股关联标记没有作为反向样本成对钉住。

- **有效路径**：将契约改为 `ExclusionFilters`，用 `excluded_*` 原因明确记录已知排除事实；
  `filters_only` 和 `filtered_triple` 单独启用该 bundle，原始 baseline 不受污染。缺失的
  volume/base/stock metadata 不推断为拒绝，而是进入 `metadata_flags`，使统计层能如实报告
  未分类候选。实际风险仍在下一开盘和冻结 initial stop 确定后才判断。

- **通用规则**：筛选协议交接时，先把每个条件写成“这个具体样本应留下还是应排除”的
  双向例表；若条件意在降噪，默认怀疑它是排除集而不是白名单。测试至少覆盖一个正常保留
  样本、每个排除边界，以及缺失元数据的审计语义。

- **牵连**：`yoyo/evaluation/spike_v1_triple_exit.py` 的 `ExclusionFilters`、
  `_exclusion_reason` 和 admission audit；`tests/test_spike_v1_triple_exit.py` 的 BTC、
  高 rv、高风险、USDC/PAXG、stock-linked 与缺失 metadata 合成契约。此修正只改变
  eval-only triple-exit 研究臂，不改冻结 V1 信号、原生 baseline、实时执行或订单路径。
