# 视觉上合理的均线绳结分数也只能先当审核顺序

- **问题**：把“六线窄、反复交叉、K 线实体穿束、持续密集”编码后，标杆图看起来明显比发散图合理，容易顺势把低分样本自动删掉。
- **死胡同**：只在 ⭐ 标杆上看 recall，或凭 A/C 首尾样图的观感宣布过滤器有效。⭐ 同时参与阈值校准，不是独立证据；好看的极端样例也量不到中间 80% 的错排。
- **有效路径**：先用 104 个 pre-holdout exact ⭐ 固定 A/B 阈值，再原样跑独立的 390 条 Owner keep/drop。新分数 AUC=0.489、置换 p=0.635，A+B precision=18.21%，与 base rate 相同，因此只允许重排人工审核，不允许自动 keep/remove。
- **通用规则**：任何人工语义过滤分数都要拆成“校准锚点”和“未参与拟合的 Owner 反证集”；只有后者的 precision 下界显著超过 base rate，才有资格讨论自动门。
- **牵连**：`yoyo/datasets/ma_rope_filter.py`、`yoyo/datasets/ma_rope_review.py`、`analysis/p1_ma_rope_prefilter_20260821.md`；不得修改旧标签、split 或 `training_eligible`。
