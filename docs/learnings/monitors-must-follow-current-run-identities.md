# 长任务监控必须跟随当前 run identity

- **问题**：训练进程存活，但 pulse 固定读取已结束的 E2.1 CSV，因此当前 E2.1b 的 epoch、最佳值和正式报告状态长期错误。
- **死胡同**：只用宽泛 `pgrep src.detection.train` 能判断“有训练”，却不能证明读到的是同一个 run 的结果。
- **有效路径**：进程匹配、results.csv 目录名和正式报告文件统一使用 `dense_15m_full_s_e21b_hsv0`，同时提供显式路径覆盖以便测试和工作树切换。
- **通用规则**：长任务监控的 process、artifact、report 三个身份必须成组迁移；任一仍指旧 run，监控就不可信。
- **牵连**：`scripts/multi_day_pulse.sh`、`scripts/daily_digest.py`、对应回归测试、E2.1b 训练只读观察纪律。
