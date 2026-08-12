# 逐样本分数全是噪声时，先看时间块——块效应可能吃掉全部方差

- **问题**：300 张早期前沿盲审里，检索 affinity、最近邻距离、模型置信度、核心框长度、
  decision 延迟、检测窗口长度全都不显著（AUC 0.51–0.55）。看起来"什么都解释不了"。
  但按候选块切开，YES 率从 B03_20251115 的 4.0% 一路到 C05_20260215 的 73.5%，
  极差 69.5 个百分点，置换 p=1e-4。

- **死胡同**：一层层加逐样本特征去找区分度（几何、置信度、波幅分箱）。这些切片各自
  只有 10 个百分点上下的差异，还没做多重比较校正，很容易被读成"6–7 根核心更好"这类
  假结论——实际上它们全被块间 70 个百分点的差异淹没。

- **有效路径**：先做**分组极差 + 置换检验**（`group_spread`），再确认分组在各抽样层里
  是均衡的（本轮每块 `yes_like`/`similar_no_boundary` 都是 24–26 或 7–8，所以块效应不是
  抽样混淆）。确认块效应显著后，任何逐样本结论都必须改成"块内比较"才有意义。

- **通用规则**：审核/标注类结果先切时间块，再切逐样本特征。若块极差显著，
  报告里必须写明"以下逐样本切片未做块内控制"，并把下一轮实验设计成块内随机抽样或块分层，
  而不是继续在混合池里找 per-sample 信号。注意块效应本身还是混合物：市场状态 vs 该块候选
  质量，单靠一个包分不开，别急着叫"regime effect"。

- **牵连**：`scripts/summarize_local_signal_v2_early_frontier_review.py` 的
  `group_spread` / `permutation_p`；
  `analysis/p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md`。
  相关：[retrieval-neighborhood-enrichment-is-not-within-neighborhood-ranking.md](retrieval-neighborhood-enrichment-is-not-within-neighborhood-ranking.md)、
  [chain-failure-is-regime-plus-entry-mismatch.md](chain-failure-is-regime-plus-entry-mismatch.md)、
  [pool-internal-metrics-cannot-see-beta.md](pool-internal-metrics-cannot-see-beta.md)。
