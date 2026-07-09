# Short mirrors need directional feature semantics

- **问题**：H10 要把多头 dense-MA 启动策略镜像到 SWAP 空头侧，但候选、标签和特征如果只换 TP/SL 方向，模型仍会读到多头语义的 `ext_up`、`order_score`、`ret_*`。
- **死胡同**：直接复用 `FEATURE_COLUMNS` 最省事，但会让空头样本里的“上涨动量/多头排列”保留原义，得到的 AUC 和收益很难解释；脚本第一版还把 holdout split 摘要带进输出，容易把发现级实验污染成隐性验收。
- **有效路径**：新增空头专用扫描和标签函数，保持多头路径不变；训练数据仍用同一列名，但在构建空头数据集时把列值方向对齐，例如 `ext_up←ext_down`、`order_score←down_order_score`、`drawdown24←runup24`、近期收益取反，并且只输出 train/val 统计。
- **通用规则**：做多空镜像时，第一步先列出每个候选指标和模型特征的方向语义；发现级脚本默认不汇总 holdout，即使只是 split 摘要也不要输出。
- **牵连**：`src/judgment/candidates.py`、`src/judgment/labeling.py`、`scripts/short_replication.py`、`analysis/p15_h10_short_report.md`。
