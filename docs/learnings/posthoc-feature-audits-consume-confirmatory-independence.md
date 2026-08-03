# 全特征事后审计会消耗后续确认独立性

- **问题**：P2-R 在五个 pre-holdout test folds 上审计全部 28 个特征后，发现 20 个特征满足预注册的跨折 Spearman 稳定规则；这很容易诱发“挑最强特征再训练一次”的动作。
- **死胡同**：把审计中看过 outcome 后选出的特征拿回同一 P1 数据训练和验收，会把 feature selection 与 validation 混在一起；即使数字改善，也不是新的独立证据。
- **有效路径**：机器结果把 `single_variable_training_followup_supported` 限定为 `exploratory_only`，同时记录 contamination note；P2-R 不选择具体特征、不训练，也不改变 P2 rejected 裁决。
- **通用规则**：只要根因审计横扫过候选特征与验证折，后续基于该审计的实验必须显式降级为探索性；确认只能来自预注册后未被用于选择的新数据或真正前向样本。
- **牵连**：`analysis/output/p2r_feature_ic_20260803.csv`、P1 immutable dataset、P2 五折、holdout 与 ACTIVE 禁令。
