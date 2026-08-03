# 执行信号身份必须包含方向和协议，而不能包含分数

- **问题**：只用 `source/symbol/signal_time` 虽能避免重评分重复下单，却会把同一时刻的 legacy long 与 repaired short 当作同一个事件；反过来，把 score 或 model hash 放进 key 又会让重评分重复开仓。
- **死胡同**：仅从旧 key 删除 score 只解决了幂等的一半。它没有回答“哪个协议、哪个方向产生了这笔事件”，因此协议迁移时仍可能让旧账吞掉新账。
- **有效路径**：把身份冻结为 `source | symbol | signal_time | side | protocol_version`；score、model hash 只作审计字段。缺失 side 先归一为不可执行的 `missing`，缺失协议归入明确的 legacy marker。
- **通用规则**：事件 key 只包含事件本身不可变的身份维度；会随重新计算变化的评分、模型版本和展示字段不得进入幂等 key。
- **牵连**：`src/execution/executor.py`、`src/judgment/forward_records.py`、`src/execution/ledger.py`、P0 验收 B-01～B-04；short/missing/unknown 仍必须在任何 client 调用之前拒绝。
