# 预打标预览必须同时写清锚点数与全区间 bar 数

- **问题**：把高频 K 线的三个月历史逐 bar 渲染并送入 YOLO，会产生数万张高度重叠的输入；本机实测耗时达到小时级，而 Owner 当前需要的是先判断预框是否值得标，不是正式穷举。
- **死胡同**：直接沿用逐 bar 扫描口径，一方面交付过慢，另一方面大量相邻窗口会把同一形态重复显示，HTML 体积和“事件数”都会被重叠窗口放大。只写“扫描最近三个月”又会让等距抽样被误读为完整月密度。
- **有效路径**：先预注册固定数量的等距 causal-tip 锚点；每个锚点仍使用截至当时的完整 200 根模型图，并保留原始命中账本。HTML 和 summary 同时展示 `causal_anchors_scanned` 与 `all_bars_in_range`，明确标成“预览、非逐根穷举”；人工未来图与模型图继续物理隔离。若预览值得继续，再单独批准全量扫描或建设专用模型。
- **通用规则**：任何高频历史预标包，先回答交付目的是“可学习性预览”还是“完整开火密度”。前者固定锚点预算并公开覆盖率，后者必须逐 bar 且做事件级去重；两种数字绝不能混用。
- **牵连**：`scripts/scan_eth_3m_v10_prelabels_html.py`、`analysis/p_eth_3m_v10_prelabels_3m.md`、`docs/learnings/human-review-may-see-future-but-model-input-must-be-re-rendered.md`；holdout 消耗、v10(15m)→3m OOD、tip/tip-1/tip-2 纪律。
