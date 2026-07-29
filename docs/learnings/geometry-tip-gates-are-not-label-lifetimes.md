# Geometry Tip Gates Are Not Label Lifetimes

- **问题**：ETH 3m short pilot v2 把 tip/tip-1/tip-2 几何门解释成标签寿命，导致 T+1/T+2 进入正类、T+3/original_v10 进入负类，训练集混入了 owner 没有逐点确认的样本。
- **死胡同**：沿用检测器的 tip gate 看起来能扩大样本量，但这只证明生产扫描允许的空间位置，不证明 owner 对这些偏移时点也给了当前 tip 标签。
- **有效路径**：把 owner 明确证据和审计证据拆开：train/val 只保留 30 个 batch-confirmed current-T 正例和 107 个 owner-no current-tip 负例；T-1/T+1/T+2/T+3/original_v10 全部写入 blank-target weak/review manifest。
- **通用规则**：遇到从检测几何、容忍窗口、扫描窗派生的标签时，第一步先问“这是可训练目标，还是只用于候选召回/审计”；没有 owner 当前时点确认的样本默认不得进 train/val。
- **牵连**：`scripts/build_eth3m_short_pilot_dataset_v2.py`、`scripts/validate_eth3m_short_pilot_dataset_v2.py`、`tests/test_build_eth3m_short_pilot_dataset_v2.py`、`datasets/eth_3m_short_pilot_v2/weak_or_review_manifest.csv`。
