# 待审核 ledger 不能冒充已经物化的 bar 证据

- **问题**：候选账本只有全局行号和时间戳时，可以计算两个窗口相隔多少个 15 分钟时间格，却不能证明源 OHLC 中间实际存在同样数量的有效 bar。把时间格差写成“实际 bar gap”会让尚未读源数据的 purge 门看起来已经通过。
- **死胡同**：为了补一个哈希而读取仍在增长的完整 CSV，既可能触碰 holdout，也会让文件 EOF SHA 随后续追加而漂移；同样，看到 nominal gap=158 就直接写 actual gap=158，只是把假设登记成证据。
- **有效路径**：待审核 ledger 只冻结事件 ID、原始标注谱系、名义时间切分和 `actual_ohlc_gap_bars=null`。Owner 回执通过后，只用 bounded pre-holdout prefix loader 读取所需前缀，分别记录前缀、样本窗口、图片和标签 SHA，以及 `max_materialized_time`、`holdout_rows_materialized=0`，再从真实有效 bar 索引重验 purge。
- **通用规则**：证据等级必须和实际读取动作一致。路径存在、时间戳差、文件 EOF SHA、因果前缀 SHA、样本窗口 SHA 是五种不同证据，不能互相替代；未物化字段应显式为 `null/deferred`，不能填入推算值让门禁变绿。
- **牵连**：`yoyo/datasets/owner_long_candidate.py`、`datasets/owner_long_gold_center_candidate_v2/`、holdout `2026-05-04`、150-bar purge、所有先审核后渲染的数据集构建流程。
