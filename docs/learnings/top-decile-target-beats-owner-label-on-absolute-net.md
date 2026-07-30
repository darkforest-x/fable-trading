# 把「是否 top-decile」当目标比学 owner label 绝对净收益更好，但仍薄

- **问题**：owner 标注（label_barrier）与真正 top-decile（net 90% 分位）的重合度低（Jaccard 0.30），学 owner label 的模型挑出的 top 绝对净收益接近 0。
- **死胡同**：一开始以为「标注更干净、模型应该更强」，但单特征和分布差异显示 top 落在「更极端波动+弱势」的区域，而 owner 标注混杂了很多中性样本；直接学 label 的 AUC 只有 ~0.50，top 绝对水平很低。
- **有效路径**：
  1. 用 train 内 realized net 90% 分位构造 is_top_decile 影子标签（B）。
  2. 对比正交性（Jaccard/MI/特征 KS）。
  3. 分别以 label 和 is_top 训练二分类器（A），在 val 单切分 + CPCV 15 折上评估「模型挑的 top10% 的 realized net」。
  4. CPCV 显示：istop 目标下 top 绝对均净中位 +8.3bp vs label 目标 +0.9bp；lift 略好（+13.9 vs +12.8bp）。
- **通用规则**：对经济目标明确的排序任务，优先用「可直接映射到盈亏的定义」（如 top-decile net）构造监督信号，而不是把人工标注当天然标签；标注与可交易定义的正交性要先量。
- **牵连**：v10 池 `judgment_v10_wide.csv`；FEATURE_COLUMNS + af_*；`src/judgment/train.py:evaluate`；报告 `analysis/p_judgment_topdecile_target_ab.md` + HTML；与 `p_judgment_topdecile_profile_v10.md` 画像呼应。
