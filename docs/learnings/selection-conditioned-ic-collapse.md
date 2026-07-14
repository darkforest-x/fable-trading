# 规则入池后，与门槛共线的因子 IC 会塌缩

- **问题**：H15 密集质量二阶（order_score 六线版、spread 二阶差分、bandwidth）在 expanded 候选池上 IC 全死（|IC|<0.02），尽管假说在全样本上可能成立。
- **死胡同**：以为「再造一版更精细的质量特征」就能抬 IC；实际上候选规则已经用 `order_score_min` / `fast_spread` / `full_spread` 硬截断了同一族信息。
- **有效路径**：先问「该因子是否已被扫描规则消费」；若是，应在规则外或放宽门槛后的条件分布上测 IC，而不是在池内叠二阶。
- **通用规则**：selection-conditioned IC：特征在全市场有用 ≠ 在规则子集上有用。IC 筛前先列「与门槛共线吗」。
- **牵连**：`src/judgment/candidates.py` 阈值；`src/factors/library.py` H15；`analysis/p2b_h15_quality.md`
