# 检索邻域"整体富集"和"邻域内能排序"是两件独立的事

- **问题**：用 11 个 Owner YES / 89 个 NO 做 5-NN 因果相似度检索，选出 150 个
  `yes_like` + 150 个 `similar_no_boundary` 让 Owner 盲审。解盲后两层几乎一样：
  40.7% vs 38.7%，置换 p=0.81；连续版 affinity AUC 0.534（p=0.33）、
  最近邻距离 AUC 0.509（p=0.78）。

- **死胡同**：把"这批检索出来的候选 YES 率 40%，远高于硬负例包的 9%–13%"当成
  "检索有效"。这是两个不同的命题被同一个词粘住了。邻域整体富集只说明**参考点附近确实
  比池子平均更像目标**；而分层/打分要有用，必须是**同一邻域内部**沿着 YES↔NO 方向还能分开。
  本轮前者成立、后者完全不成立——继续按 affinity 排序去挑训练样本，等于按噪声挑。

- **有效路径**：解盲时必须同时报三个量，而不是只报"每层的 YES 率"：
  1. 两层的 YES 率差 + 置换 p（分层有没有区分度）；
  2. 连续分数的 AUC + 置换 p（把分层退化成打分后还有没有）；
  3. 逐块/逐层交叉表（方向稳不稳定）。
  本轮第 3 项直接给出反证：B04 是 boundary 0.56 > yes_like 0.29，C03 反过来 0.74 > 0.32。
  方向在块之间翻转 = 没有共同方向。

- **通用规则**：任何"主动检索/难例挖掘"包解盲时，先问"**邻域富集**还是**邻域内排序**"。
  只有后者成立，才允许把这个分数继续用于选样、加权或半自动标注；只有前者成立时，
  这批数据仍然有价值（它是发现集），但选样机制必须换掉或先离线验证再用。
  也别拿它和抽样偏向不同的历史包直接比 YES 率——那些数字都不是基率，
  要基率就单独做一次块内随机抽样包。

- **牵连**：`scripts/build_local_signal_v2_early_frontier_review.py`（检索与分层）、
  `scripts/summarize_local_signal_v2_early_frontier_review.py`（解盲与置换检验）、
  `analysis/p2_local_signal_v2_early_frontier_review300_owner_result_20260812.md`。
  相关：[semantic-review-strata-must-be-relative-to-the-source-pool.md](semantic-review-strata-must-be-relative-to-the-source-pool.md)、
  [regime-block-can-dominate-every-per-sample-score.md](regime-block-can-dominate-every-per-sample-score.md)。
