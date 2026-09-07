# 诊断依赖也必须在读取实验结果前预检

- **问题**：V30 runner 原本先计算真实收益，再加载冻结的统计诊断工具；项目虚拟环境缺少该工具导入需要的 seaborn。
- **死胡同**：先完成收益计算再补诊断依赖，会留下已看结果但运行失败的记录，且诱使人为选择重跑路径。直接往主环境安装又可能改变契约版本。
- **有效路径**：审核者用合成四值重现导入失败，确认系统 python3 的 NumPy 2.0.2 / pandas 2.3.3 符合现有合同且诊断调用成功；runner 将真实工具加载及合成调用移至 timestamp/outcome materialization 之前，不安装依赖。
- **通用规则**：实验预检必须覆盖最终必交付的诊断与审计依赖，不能只检查计算核心能否 import；先用合成值跑通完整 API，再消耗数据。
- **牵连**：`yoyo/evaluation/hourly_impulse_classifier_economic_research.py`、固定哈希的 assumption_checks.py；本次在首次真实 V30 运行前修复，没有因此额外读取实际收益。
