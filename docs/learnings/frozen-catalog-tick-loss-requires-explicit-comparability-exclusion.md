# 冻结 catalog tick 丢失必须显式排除可比性单元

- **问题**：冻结的 OKX catalog 顶层 tick 经低精度 JSON 序列化变为零；同一 raw API 快照仍含有效 tickSz，但原 V1 已因零 tick 跳过执行并留下零交易覆盖单元。
- **死胡同**：从 raw tickSz 直接补回新回放会让 V6/V7 对该单元可执行，而原 V1 native 从未执行，破坏共同样本口径；删除原 V1 覆盖记录又会改写历史。
- **有效路径**：保留原 catalog、来源哈希和 3,534 原 evaluated 参考；将恰好三个无效 tick 单元预注册为不可比，启动前静态验证无效 tick 集合与排除集完全一致，回放只冻结 3,531 个可比流。
- **通用规则**：历史快照的字段损失若改变可执行性，不能从旁路字段悄悄修复；先保留历史参考，再明确、可审计地限定可比样本。
- **牵连**：`experiments/active/exp-spike-v7-v1-compare-20260912-v1/config.json`、`yoyo/evaluation/spike_v7_v1_compare.py`、OKX SATS 30m/60m/240m、V1 two-year coverage ledger。
