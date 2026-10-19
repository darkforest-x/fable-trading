# K 线颜色依赖必须按因果区域定位

- **问题**：冻结 YOLO 在全图 K 线改单色后 recall 明显下降，但这个结果无法说明颜色承载的是启动前结构、owner 核心形态，还是启动后的确认行情；把三者统称为“模型需要红绿色”会掩盖 fresh-tip 风险。
- **死胡同**：只做全图改单色或红绿互换。它能证明现有权重对调色板敏感，却把不同时间语义混在一起；全图改单色 `-15.48pp` 甚至小于仅改单色 post-core 的 `-21.29pp`，说明区域效应还会非线性交互，不能靠总效应倒推来源。
- **有效路径**：固定 OHLC 几何、六均线、标签、权重、阈值与 155 个独立 pre-holdout 事件，只分别中和 pre-core、owner core、post-core 的蜡烛实体色。post-core 单独中和使 recall 从 `74.84%` 降至 `53.55%`（差 `-21.29pp`，block bootstrap 95% CI `-27.74～-14.84pp`，Holm `p=6.98e-10`）；core 只有 `-3.23pp` 且不显著；pre-core 反而 `+6.45pp`。149/155 张还是最早 post=2 变体，因此现有颜色依赖主要来自框外确认 K，而非核心框内颜色。
- **通用规则**：发现视觉模型依赖某个特征时，第一步不是删特征或宣布它有用，而是按决策时点切开输入区域做单变量扰动；凡依赖 decision/core 之后像素的提升，只能算 completed-history 能力，不能作 fresh-tip 证据。
- **牵连**：`scripts/audit_yolo_color_semantics.py`、`scripts/audit_yolo_candle_color_regions.py`、`analysis/output/p1_yolo_color_semantics_audit_20260902/`、`analysis/output/p1_yolo_candle_color_region_audit_20260902/`、实盘纪律 12。
