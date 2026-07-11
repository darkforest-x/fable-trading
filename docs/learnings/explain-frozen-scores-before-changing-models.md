# 换模型前先解释冻结分数并核对候选语义

- **问题**：前向账本分数可以正常重算，但账本跨越过候选去重逻辑迁移，直接把全部记录放在一起评估可能混用两种信号定义。
- **死胡同**：只看全局 feature importance 无法解释单条信号，也无法发现分数正确但候选定义已经变化的历史记录；直接换 XGBoost 或调 LightGBM 参数同样不会修复统计口径。
- **有效路径**：用 LightGBM 原生 contribution 重构每条概率，同时用当前因果扫描器复核 signal time；把“模型分数一致”和“当前候选一致”拆成两个独立检查。
- **通用规则**：模型或扫描规则迁移后，先做逐记录 score replay 与 candidate-membership audit，再累计统一口径的前向经济性。
- **牵连**：`src/judgment/explain.py`、`scripts/explain_forward_signals.py`、`data/forward_log_ma206.csv`；不得为统一口径删除或改写 append-only 历史账本。
