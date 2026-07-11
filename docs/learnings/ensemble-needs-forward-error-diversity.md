# 交易模型集成前先验证前向错误互补性

- **问题**：LightGBM、CatBoost 和 XGBoost 都能在同一 tabular 数据上得到显著 AUC，直觉上等权集成应该更稳。
- **死胡同**：只比较单模型 AUC，或在已经复用的 val 上训练 ensemble 权重，会把高度相关的模型误当成独立证据，并引入选择偏差。
- **有效路径**：固定相同特征和时间切分，先比较 base score 的 Spearman、top-decile 扣费收益和实际推理开销；本轮相关性 `0.852–0.898`，等权集成没有改善经济性，因此直接拒绝。
- **通用规则**：只有新鲜前向窗口证明收益残差或错误显著互补后，才允许固定权重 ensemble；stacking 的 meta model 只能使用按时间生成的 OOF 分数。
- **牵连**：`src/judgment/shadow_boosters.py`、`analysis/output/shadow_booster_benchmark.json`；不得在旧 val 上学习权重或据此修改 ACTIVE。
