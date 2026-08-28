# 预览抽样必须使用图像级身份与契约类别

- **问题**：新数据集的一个形态事件有多个 `post_bars` 图像变体，它们共用事件级
  `sample_id`，但各自有 `dataset_sample_id`；同时类别由 `direction=LONG/SHORT`
  绑定，`class_id` 可以为空。沿用旧预览器会把正例池判空，或把多张图当成同一个身份。
- **死胡同**：继续直接读 `row["sample_id"]` 和 `row["class_id"]` 只对旧 manifest 有效；
  用事件 ID 去重还会丢掉真实的图像变体，使预览与模型实际输入不一致。
- **有效路径**：显式分开图像级身份和事件级身份：预览排序、去重与回执优先用
  `dataset_sample_id`，只在旧格式缺失时回退到 `sample_id`；类别优先读显式
  `class_id`，缺失时按冻结契约 `LONG→0 / SHORT→1` 解析。
- **通用规则**：任何抽样、预览或评估器接入新 manifest 时，先确认其去重键表示的是
  “事件”还是“实际模型输入图”，并用数据合同解析类别，不假设字段一定非空。
- **牵连**：`scripts/render_15m_ma_launch_t3_validation_preview.py`、
  `tests/test_render_15m_ma_launch_t3_validation_preview.py`、Grade-A manifest 的
  `sample_id / dataset_sample_id / direction / post_bars` 语义。
