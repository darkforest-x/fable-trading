# 统一移动审核标记只能改变视图，不能生成训练边界

- **问题**：候选检索锚点通常落在释放首根附近，Owner 希望把图上的竖线统一提前 3 根以便看启动前沿；但不同样本的真实启动首根并不固定相差 3 根。
- **死胡同**：直接把所有框或标签左移同一个 delta，看起来能快速得到整齐训练集，实际只是把旧位置 shortcut 换成新 shortcut。候选完成路径和统一偏移都没有提供逐样本类别确认或核心几何证据，详见 [逐图重框需要编号边界](per-image-reboxing-needs-indexed-boundaries-not-global-offsets.md)。
- **有效路径**：把偏移字段命名并约束为 `review_marker_offset_bars`；蓝线显示 `t-3`，同时用橙色虚线永久保留原始选择 `t`。manifest 对每行记录两者的时间和 source index，并强制 `review_marker_is_training_label=false`；验收器逐行检查恰好相差 3 根/45 分钟。训练边界仍只能来自逐样本 Owner 几何或 Owner 批准的派生规则。
- **通用规则**：只要变更是为了“更方便看”，产物层就必须把它标为 view annotation，并保留原坐标；未经逐样本证据，禁止把显示坐标复用为 label 坐标。
- **牵连**：`docs/protocol/local_signal_v2.md`、`experiments/active/exp-15m-ma-launch-candidate9000-v1/results/review_manifest.jsonl`、`scripts/verify_15m_candidate_pool.py`、4–7 根核心、约 14–22 根最短充分上下文、自然变化的框位置。
