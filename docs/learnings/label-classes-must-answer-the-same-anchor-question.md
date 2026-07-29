# 正负类必须回答同一个锚点问题

- **问题**：ETH 3m v2 的文件、哈希、因果窗和时间切分全部通过，但分类器 train 全对、val
  全判负。追查发现正例来自“owner-yes 形态内另提橙色 T 后是否来得及”，负例却来自“原 v10
  红框是不是做空形态”；`label_provenance` 因而 100% 决定类别。
- **死胡同**：把“两个来源都有人工确认”当成标签对称，或只检查 target/class/path 是否一致。
  这些结构检查抓不到两类在回答不同问题，也抓不到 candidate source 与 target 的纯度。
- **有效路径**：把人工问题冻结为同一句 current-T 决策，并对每个候选来源都采正反例；同时跑
  只看 `candidate_source / anchor_rule / date` 的 source-only baseline。本例仅用“anchor 是否正好
  等于六 MA 首次下破”就能以 136/137=99.27% 区分类别，直接证实构造混杂。
- **通用规则**：二分类数据集在训练前先做 `source × label × anchor_rule` 交叉表和 source-only
  baseline；若来源或锚点规则近乎决定 target，先补同源交叉样本，不得靠扩图、调阈值或 class
  weight 掩盖。构造混杂应与未来泄漏分开表述。
- **牵连**：`datasets/eth_3m_short_pilot_v2/manifest.csv`、
  `analysis/output/eth3m_v10_label_timing/task_timing_metrics.csv`、
  `src/detection/eth3m_v2_quality_audit.py`、Project 53 形态问题、固定未来 3h 的 current-T 问题。
