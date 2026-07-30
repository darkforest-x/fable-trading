# regression-on-net-plus-maker-route-concentrates-alpha-better-than-binary-labels

- **问题**：二分类目标（学 label_barrier 或学 is_top_decile）训出来的 top-decile 绝对净收益薄（CPCV 中位 +0.9bp / +8.3bp），接近或低于摩擦，是否能通过改目标函数直接对齐经济量来改善？
- **死胡同**：先用 owner label 做二分类，AUC 合理但 top 绝对净接近 0；后改学 is_top_decile（train 内 net 90% 分位），CPCV top 绝对净提升到 +8.3bp（11-13/15 正），但仍薄；白盒规则（atr_pct + pre_range48 - dense_frac 打分或简单阈值）在 val 上接近 0 或负 lift，完全无法近似模型。
- **有效路径**：直接回归 `net_barrier_taker`（或 maker），CPCV 选 top10% 后绝对中位净 +15.4bp（15/15 正折），比学 is_top 再高 ~7bp；换算到 maker 成本（~6bp 往返）后中位 +19.4bp，15% 限价 TP 未成交情景仍 +16.5bp；匹配随机对照（同币×同月×同 ATR 分桶）下仍有 +8.5bp lift，证明不是纯 beta。
- **通用规则**：当成功标准是「top-decile 扣成本后净为正」，优先用回归直接预测净/毛，而不是代理二分类；二分类只在「事件概率」本身是优化目标时才合适；白盒规则在波动+范围+量能+多 alpha 的非线性组合场景下大概率失效，模型必要性由此得到验证。
- **牵连**：`src/judgment/train.py`（需支持 regression objective）、`scripts/diag_judgment_big_pool.py`（CPCV）、`src/judgment/features.py`、`data/judgment_v10_wide.csv`（net_barrier_maker/taker 列）、成本假设（SWAP_MAKER ~6bp）；报告 `analysis/p_judgment_reg_whitebox.md` 及 HTML；铁律：pre-holdout、时间切分、无 holdout 消耗、无 promote。
