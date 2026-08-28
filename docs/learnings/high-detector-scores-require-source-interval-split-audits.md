# 高检测分数要审计源区间，不只是身份交集

- **问题**：时间切分的检测器在早期 epoch 就出现较高 `mAP50-95`。图像 SHA、样本 ID
  和事件 ID 零交集只能排除精确重复，不能排除同一 OHLCV 源中相邻渲染窗口或
  标签依赖区间跨 train/val 重叠。
- **死胡同**：把“32,000 张图哈希唯一”或“same-event single split”直接当成无泄漏证明；
  两张字节不同的图仍可以共享大部分 K 线。
- **有效路径**：对每张图构造同源索引区间；正例右端使用
  `max(window_end_i, source_core_end_i + 5)`，负例使用构建时冻结的
  `dependency_end_i`。然后只在 train/val 共用的 `source_path` 内扫描区间交叉，并独立检查
  图像、图像级 ID 和事件级 ID 交集。
- **通用规则**：结果高得反常时，先画出每个样本的“输入＋标签所依赖的完整时间区间”，
  再按原始数据源做 train/val 区间交集；哈希唯一性是必要但不充分的证据。
- **牵连**：`scripts/audit_15m_ma_launch_owner_grade_a8000_split.py`、Grade-A manifest
  的 `source_path / window_start_i / window_end_i / source_core_end_i / dependency_end_i`，
  chronological split 与 purge 合同。
