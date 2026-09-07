# 报告联表测试应保留真实表中的重名列

- **问题**：V29 支持计算和独立审计通过，但报告原因拆解 SQL 首次执行因 `classifier_center` 重名失败。请求上下文和完整小时轨迹都保存该字段。
- **死胡同**：只按查询需要为小时表准备 `open_time, close` 的简化测试，会漏掉生产表的重名风险；把报告出错说成策略结果错误也会混淆两条链。
- **有效路径**：保留真实的重叠列构建合成表，先复现失败，再显式限定 `c.` 上下文列与 `h.close` 原报价。测试刻意让小时表的同名特征矛盾，验证查询确实使用冻结上下文；同时覆盖多空、双条件四象限、严格边界和未知分母。
- **通用规则**：联表诊断先测试真实 schema 的冲突面，再测试数字；显示层失败单独保留收据，修复不得改上游策略、数据或结果。
- **牵连**：`exp-btcusdtp-1h-classifier-support-preholdout-20260907-v29/build_report.py`、`tests/test_hourly_impulse_classifier_report.py`、`report_prepare_attempt1_failed.json`；属于报告查询修复，不是第二次策略实验。
