# 难负例挖掘与正例检索必须使用不同审核页

- **问题**：同一批 YOLO 触发既可用于寻找误报，也可用于寻找接近目标语义的正例；若只说“做空候选”，Owner 会合理地预期页面主要展示目标形态，而按负例相似度排序的页面却会集中展示错误形态。
- **死胡同**：用“靠近已确认负例、远离已确认正例”的 affinity 选出 200 张，再把页面交付为普通做空候选审核。算法完成了难负例挖掘，但 182/200 被 Owner 判错，交付语义与 Owner 的正例检索预期相反。
- **有效路径**：保留该页为难负例账本；另建正例检索页，排序方向改为“靠近 Owner 正例、远离 Owner 负例”，并在标题、说明、字段名和报告中明确页面目的。两页都只使用 decision bar 及之前的数据，未来图只在选样完成后供人工审核。
- **通用规则**：构建主动学习审核页前，先把目标写成单一谓词：`maximize confirmed negatives` 或 `maximize target retrieval`。不得让同一排序同时承担两个相反目标，也不得用偏置审核集的命中率冒充全分布 precision。
- **牵连**：`scripts/build_owner_short_train_hardneg_review.py`、训练区间 review200 的 Owner 裁决、后续 positive-retrieval builder；仍受 holdout、时间切分、无前视和不自动训练约束。
