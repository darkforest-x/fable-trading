# 入选集的随机对照必须完整覆盖，不能只解释容易配对的样本

- **问题**：精确短窗 L2 的 q90 全体净均值略正，但只有 32/61 个入选事件能找到预注册要求的同币、同月、同时段、同波动桶、同方向 8/8 随机对照。
- **死胡同**：只在“能配对”的子集上算超额收益，再把结论外推到全部入选事件；或为了补齐覆盖而缩小金标禁入区、放松 72 根间隔和匹配桶。前者留下不可观测选择偏差，后者改变零假设并污染对照。
- **有效路径**：同时并列报告全部入选、有完整对照、无完整对照三组的净收益，并把 `matched_controls_cover_every_selected_event` 设为硬门。本轮完整覆盖组净均值为负，而正数集中在缺配组，因此总体 q90 正数被诚实判为不可解释、不可通过。
- **通用规则**：匹配对照的成功条件不是“有一些 controls”，而是**被用于成功声明的整个 selected set 都满足冻结的匹配合同**；覆盖不足时可以报告描述性结果，但必须失败关闭，不能用已覆盖子集替代全体。
- **牵连**：`scripts/research_15m_ma_launch_l2_short_window_side_split.py`、`experiments/active/exp-15m-ma-launch-l2-short-window-side-split-v1/preregistration.json`、`analysis/p3_15m_ma_launch_l2_short_window_side_split_20260901.md`；保护条件仍服从[负样本比例不能削弱金标禁入区](negative-ratio-must-not-weaken-gold-exclusion.md)。
