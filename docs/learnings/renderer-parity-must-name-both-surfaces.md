# 渲染一致性必须明确比较的是哪两种视图

- **问题**：候选审核图看起来是完整的均线密集后爆发，训练图却像另一种形态；只验证训练与扫描共用 renderer，仍无法解释 Owner 看到的差异。
- **死胡同**：把“训练图与检测图逐像素同源”泛化成“训练图与候选审核图也一致”。候选图固定画 48 根并含 `t+1..t+17`，训练图只画 14--22 根且最晚到 `t+2`；两者还各自按窗口做纵轴归一化，因此底层 OHLCV 相同也会产生完全不同的视觉比例。
- **有效路径**：用 `event_id` 联结同一个候选的 review manifest 与 training manifest，再同时核对 bar 起止、核心边界、未来可见量、画布尺寸和纵轴取值范围。以 NMR `63acc9881b00c71dfdcc` 为例，审核图是 48 根、蓝线在选择 `t`，训练图是 16 根、核心为 `t-6..t-3` 且输入止于 `t`；差异来自视图合同，不是颜色通道或 OHLCV 换源。
- **通用规则**：任何“渲染 parity 已通过”的结论都必须写成 `A ↔ B`，并用同一事件做端到端配对；不得用 train↔inference parity 回答 review↔train 是否同形。训练前还应交付同事件的“审核图 / 因果训练图 / 标签叠框图”三联证据。
- **牵连**：`yoyo/datasets/fifteen_minute_launch_candidates.py`、`yoyo/datasets/ma_launch_t3_training.py`、`yoyo/layers/l1_detection/render.py`、`experiments/active/exp-15m-ma-launch-candidate1000-v1/results/review_manifest.jsonl`、`datasets/ma_launch_t3_10000_v1/manifest.jsonl`；另见 [人工验真可以看未来，但模型输入必须从截止时刻重渲染](human-review-may-see-future-but-model-input-must-be-re-rendered.md)。
