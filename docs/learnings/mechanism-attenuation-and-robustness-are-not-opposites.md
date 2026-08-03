# 机制衰减与控制后稳定不是互斥结论

- **问题**：P2-M 的预注册规则分别标记“控制后效应不超过 raw IC 一半”和“三条控制关联仍跨折稳定”，实际有 3 个特征同时满足两者。
- **死胡同**：用单一 `mechanical` / `real signal` 二分类会丢失信息：一个关联可以主要由尺度放大，同时仍留下较小但方向稳定的残余。
- **有效路径**：保留两个独立布尔维度，再把交集单列为 `mechanical_and_scale_robust`；全局裁决分别检查机械占比与是否存在稳健残余，不拿其中一个覆盖另一个。
- **通用规则**：机制审计要同时报告 attenuation magnitude 和 residual stability；“变小”回答效应组成，“仍稳定”回答残余一致性，它们不是同一个问题。
- **牵连**：`analysis/output/p2m_feature_mechanism_20260803.csv`、20 个 P2-R frozen stable features、50% attenuation rule、4/5-fold stability rule。
