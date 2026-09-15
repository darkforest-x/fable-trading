# 时间块 bootstrap 必须先核对分组行数守恒

- **问题**：ETH V9/YOLO 统计集成检查发现，按时间戳生成的 ISO 周标签带 DatetimeIndex，而交易表带整数索引。`assign` 会按索引对齐，可能把全部周标签变为缺失。
- **死胡同**：仅检查函数能运行、输出有 JSON 或用零样本测试，不会发现这类静默丢行；零块也可能被错误解释成样本不足。
- **有效路径**：周标签显式继承输入 Series 的行索引，以不连续整数索引和跨周日期做合成反例；检查生成的块数与输入日期一致。首次真实统计前修复，不重采样选结果。
- **通用规则**：任何时间分组统计先检查分组前后行数守恒、缺失标签数与实际时间块数，再解释置信区间。
- **牵连**：`yoyo/evaluation/spike_eth_yolo_statistics.py`、`tests/evaluation/test_spike_eth_yolo_statistics.py`；原始交易和检测配置保持冻结。
