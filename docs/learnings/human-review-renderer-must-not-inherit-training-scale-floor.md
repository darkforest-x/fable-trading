# 人工审核图不能继承训练渲染器的尺度下限

- **问题**：未来对照K线普遍看起来很平，但原始OHLC并非无波动。331张中291张真实OHLC+均线跨度低于6%，中位仅2.99%，而训练renderer固定至少展示6%的价格跨度。
- **死胡同**：为了像素分布稳定，训练图用相对跨度下限抑制极端纵向放大；直接复用它生成审核图，会把多数真实波动压缩到画面中部。仅增加图片分辨率或未来K线宽度不能修复纵轴压扁。
- **有效路径**：保持因果训练图及其SHA不动，为`future_review_only/`实现独立的人类审核renderer；纵轴按本图OHLC和均线真实极值加小边距，不设训练尺度下限，并把真实跨度百分比写入图头、卡片和manifest。
- **通用规则**：训练渲染器优化的是分布稳定，审核渲染器优化的是可辨识性，两者必须是独立产物合同。遇到“行情都很平/都很陡”时，第一步统计真实相对跨度与renderer最终跨度的比值，而不是怀疑原始数据。
- **牵连**：`scripts/build_owner_short_hardneg_canary_review.py`、`analysis/output/owner_short_gold_center_hardneg_canary_review331_v3/`、`analysis/html/p2_owner_short_gold_center_hardneg_canary_review331_20260811.html`；不改变因果输入、YOLO标签、模型、阈值、holdout或训练资格。
