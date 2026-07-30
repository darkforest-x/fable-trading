# 顶十分位不是「最密集」，而是「更极端的波动+弱势」

- **问题**：判断层能稳定挑出 +17~28bp 顶档提升，但「剖开」后发现 dense_frac48 在 top 反而更低（0.13 vs 0.29），与「双均线密集启动」的直觉相悖。
- **死胡同**：一开始以为「模型在学更密的 MA 收敛」，用单特征 dense_frac48 排 top10% 只拿到 +4bp（几乎无用），ma_spread_pct 甚至反向。
- **有效路径**：在 val 集上用回归器（目标=net_barrier_taker）排分，取出 top10%，对比其余90%的全特征分布（KS + 均值差 + 匹配对照）。
  - 关键发现：atr_pct、pre_range48/168、drawdown24、full_spread、spread_mean8/24 全面更高；ret_12 更负；dense_frac48 更低。
  - 月×ATR 桶匹配对照后，顶档仍超对照 +38.7bp，说明不是「挑了高波动就行」。
- **通用规则**：对方向性策略，top-decile 的画像要用「匹配对照 + 分布差异」双锚，而不是只看池内绝对收益或单特征相关。
- **牵连**：v10 池 `judgment_v10_wide.csv`；FEATURE_COLUMNS + af_* alphas；`src/judgment/train.py:evaluate`；`scripts/diag_judgment_big_pool.py`（attach_alphas/cpcv）；报告 `analysis/p_judgment_topdecile_profile_v10.md`。
