# 全局复核图不能冒充模型输入图

- **问题**：为了判断 L2 选中事件在大行情中的位置，把 18/19 根 L1 短窗重投影到 168 根全局图；若交付时只说“入选事件图”，Owner 会合理地把 168 根误认为 YOLO 实际检测窗口。
- **死胡同**：仅在图标题里写 `L2` 或 `decision-only` 不足以区分输入与解释视图；二者都截止决策时点、都有同一个框，看起来像同一种模型证据。
- **有效路径**：从冻结扫描 ledger 读取 `window_len/window_start_i/window_end_i/input_pixel_sha256`，重新渲染 1280×742 的 18/19 根原始短窗并逐像素对账。原始无框 PNG 单独放 `raw/`，模型输出框只画到 `detected/` 审核副本；总览标题明确写 `BOX = OUTPUT OVERLAY (NOT INPUT)`。
- **通用规则**：任何模型可视化交付先回答三件事：模型实际看到几根、原始像素哈希是否一致、框是输入标签还是输出叠加。全局上下文只能命名为 review context，不能简称模型图。
- **牵连**：`scripts/render_15m_ma_launch_l2_side_split_selected20.py` 是 168 根 L2 review context；`scripts/render_15m_ma_launch_l2_side_split_selected20_l1_inputs.py` 是 18/19 根 exact L1 input；本修正未读取 holdout、未训练、未调阈值、未 promote 或部署。
