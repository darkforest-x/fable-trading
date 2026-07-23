# Holdout PF 塌到 ~1.0 不等于测量 bug

- **问题**：A 因果空边 train PF@maker≈1.42 在 holdout#7 塌到 ≈1.00，是否结算/切分/成本实现错了？
- **死胡同**：只盯「数字反常」就改代码或再烧假设；不先做同机对照（随机入场、币种交集、逐笔验算）会把 regime 证伪误判成 bug。
- **有效路径**：train/holdout 共用同一 `EXIT_RESOLVERS` + `next_open` + maker 0.06%；空头 `fill/exit−1` 与人工路径一致；同 holdout 窗随机 no_tp PF≈1.15 **高于**规则 0.997——机器能报出 >1，规则只是没边。
- **通用规则**：怀疑测量时先查（1）路径参数一字不差（2）3～5 笔人工 entry/exit/ret（3）同机随机/固定障碍对照；三者过关则按过拟合/regime 收口，勿改 ACTIVE、勿新主题 holdout。
- **牵连**：`scripts/short_trend_ab.py`；`src/judgment/labeling.py`（`label_candidate_sl_only` / `label_short_candidate_trailing`）；`analysis/p_short_trend_holdout7.md`；对照 train 2026-04 PF 0.678。
