# 单向执行器必须拒绝显式方向冲突

- **问题**：执行器固定买入并使用多头 TP/SL，但前向记录没有方向契约；如果未来的 short 研究记录进入该路径，有被静默变成 buy 的风险。
- **死胡同**：仅在 docstring 写“long-only”或相信上游永远不会传 side，都无法在研究与实盘管道共用时防止语义错配。
- **有效路径**：前向新记录显式盖章 `side=long`；执行器为历史缺失值保留 long 兼容，但对显式 short 或未知值记录一次性拒单，不调用交易客户端。
- **通用规则**：当上游可能扩展方向而下游仅支持单向时，先加“显式不匹配拒绝”边界，再考虑实现另一方向；不应用默认值覆盖显式意图。
- **牵连**：`src/judgment/forward_types.py`、`src/judgment/forward_scan.py`、`src/execution/executor.py`、`src/execution/ledger.py`；当前屏障不实现 short 下单。
