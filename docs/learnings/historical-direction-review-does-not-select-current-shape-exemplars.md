# 历史方向复核不能替代当前形态筛选

- **问题**：Owner在审核2513个历史框派生任务时反馈，很多图不符合想要的标准。复查源表与manifest：2525条方向复核记录减去12条skip，保留1361空头和1152多头；2513是框级任务数，不是重新精选的标准形态数。
- **死胡同**：完整保留历史资产、优先排列104个精确匹配的旧⭐框，再用中央一半且限制4–7根的规则生成建议框，便把全池作为人工默认工作量。来源真实、方向曾被确认、几何合法，都没有回答“当前这段是不是目标平台/启动”。机械缩框还可能截掉平台或包入启动段；把这些问题全留给Owner拖框会消耗审核时间。
- **有效路径**：本次先从源表、入队代码和冻结manifest区分方向证据、形态证据、派生框证据，纠正执行路线：2513保留为候选档案，撤下全量逐张审核要求；下一步按已有Owner语义参考逐项筛选，再为拟入选项准备可调整预框。形态重筛尚未完成，不能宣布整批已修复。已提交答案独立保留。
- **通用规则**：恢复旧标签后，先核对当时审核的问题与本轮目标是否一致，再决定哪些候选值得占用人工时间。优先级排序不等于形态准入；旧⭐也不自动确认新裁图和新框。未选中的候选保留未知状态，不能直接改为背景。
- **牵连**：`yoyo/datasets/owner_box_refinement.py`的`plan_rows`与`central_core`；`analysis/output/owner_side_review/review_sheet.csv`；`datasets/owner_box_refinement_20260907_v1/manifest.jsonl`；`docs/protocol/local_signal_v2.md`；`HANDOFF.md`本周计划更正。相关教训：[重裁剪不能修复标签语义](dynamic-recrop-does-not-repair-label-semantics.md)、[协议确认不是逐样本确认](protocol-confirmation-is-not-sample-confirmation.md)。本轮仅核对既有元数据及源码，没有读取行情、修改标注或开启训练。
