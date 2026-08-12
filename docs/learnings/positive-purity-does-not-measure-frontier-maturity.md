# 正样本纯度不衡量信号处在启动前沿还是事后成熟段

- **问题**：旧 Positive Pool 有 85% Owner YES，看似已经足够纯；但其 causal decision 相比连续 Canary YES 更靠后：框后跌幅中位数 -60.1bp vs -13.1bp，收盘相对均线束 -147.2bp vs -73.0bp。模型离线学到的是更明显、更成熟的释放，连续市场真正要分的是早期 YES 与相似 NO。
- **死胡同**：只用 YES 率评价 positive，或因为纯度高就继续追加 hard negative。YES/NO 只回答“语义对不对”，没有回答“时间成熟度是否覆盖实盘目标”；正例全部偏成熟时，negative 再多也补不出启动前沿的正类边界。
- **有效路径**：在相同 causal 坐标下同时比较 Positive YES、Canary YES 和 Canary NO 的价格相对均线束、核心/框后收益、短均线斜率与释放倍数。三组形成“成熟正例—早期正例—普通密集”的连续梯度后，才看见训练分布缺口。
- **通用规则**：语义数据集验收至少分两轴：Owner positive purity 与 causal maturity coverage。检测目标是启动前沿时，必须单独量早期 YES 的数量、位置和结构覆盖，不能让成熟 YES 的高纯度掩盖缺口。
- **牵连**：`analysis/output/local_signal_v2_positive_semantic_review200_v2/`、`scripts/diagnose_local_signal_v2_semantic_boundary.py`、Local Signal V2 的 3–5 根确认延迟、禁止自动把审计 verdict 转训练标签。
