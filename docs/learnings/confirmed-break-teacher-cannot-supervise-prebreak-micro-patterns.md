# 确认跌破模型不能直接监督下破前微周期形态

- **问题**：15m v10 给 ETH 3m 预标时，owner 目测约 60% 的框不合理。坐标转换本身正常，但模型在 3m 上大量框住已经跌完或正在加速下跌的短段，而不是预期的下破前密集形态。
- **死胡同**：只调高置信度或把未来 3h 收跌当框精度都不能解决。v10 的正例构造先向未来找确认 break，再把窗口和框重锚到 break；同时相同 200 根 K 线从 15m 的 50 小时压缩成 3m 的 10 小时。置信度与未来 3h 收益也没有单调关系，因此 threshold 不能修正目标语义和墙钟尺度错位。
- **有效路径**：先审计标签生成语义，再用当前触发时点的因果数据复算。200 次触发中 181 次在此前 8 根 3m K 线已跌超 2 ATR，196 次已位于六条均线下方；中位约 13 根的框从训练口径约 3.3 小时缩成 3m 上约 40 分钟。由此把 `shape/box` 与未来 `outcome` 拆成两套标签，并用 owner-valid ETH 3m 金标、owner-invalid hard negatives 训练原生模型；在相同时间切分下单变量比较 COCO 冷启动和 v10 warm-start。
- **通用规则**：跨周期使用 teacher 预标前，第一步同时比较“标签锚点语义、输入 bar 数、墙钟跨度、框宽墙钟跨度”。确认态 teacher 只能提出候选，不能自动充当更早阶段目标的正例；任何 threshold 调优都必须先有逐张 shape validity 与事件去重后的 precision 曲线。
- **牵连**：`scripts/build_star_tip_dataset_v10.py`、`scripts/build_eth_3m_v10_prebox200.py`、`scripts/diag_eth3m_v10_prebox_quality.py`、`analysis/output/eth3m_v10_precision_diagnosis.json`、`datasets/eth_3m_v10_prebox200/manifest.csv`；延续 [多时间框架台架要先统一 bar 时钟](timeframe-generalization-needs-single-bar-clock.md)。未读取 holdout，未改阈值、模型或实盘配置。
