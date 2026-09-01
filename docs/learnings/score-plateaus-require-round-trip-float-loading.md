# 分数平台的经验分位必须 round-trip 读取

- **问题**：小样本 LightGBM 会产生大量完全相同的分数。模型分数和 q90 KEEP 决策已经逐项复现，但把 tune 分数写成 CSV 再用 pandas 默认解析后，少数 final 样本的经验分位仍错开一个完整名次。
- **死胡同**：只核对模型文件、原始分数和阈值，以为误差小于 1e-12 就足够。经验 CDF 用的是小于等于比较；CSV 回读只移动一个 ULP，也会把本应相等的平台分数判成一大一小，导致百分位跳 1/n，继而改变 aggregate AUC 或 top-decile 成员。
- **有效路径**：用 pandas 的 float_precision="round_trip" 读取分数账本，并把经验百分位逐项差异加入基线复现门。原始 score、threshold、percentile 和 KEEP 四轴都通过，才允许比较新配置。
- **通用规则**：凡是阈值、分位数或排序依赖并列浮点值，持久化后先做位级 round-trip 检查；不要用一个宽松的绝对误差代替离散决策与排名复现。
- **牵连**：scripts/research_15m_ma_launch_l2_feature_group_ablation.py；empirical_percentile；pandas CSV parser；LightGBM 小叶子/早停产生的分数平台。

