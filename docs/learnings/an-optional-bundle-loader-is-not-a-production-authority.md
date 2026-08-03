# 可选 bundle loader 不是生产单一权威

- **问题**：代码已经能校验 `active_bundle.json`，但文件缺失时仍继续读取 `models/ACTIVE`，ACTIVE 不可用时又回退“最新可加载 artifact”。校验器存在，却没有真正控制生产路径。
- **死胡同**：把“bundle 存在时优先”称作 exact bundle。这个设计只处理了损坏 bundle，却把“bundle 缺失”解释成使用旧发现链的许可，生产仍然有多个权威。
- **有效路径**：保留 optional loader 给 audit/research 区分“缺失”和“损坏”，另设 production-only `require_active_bundle()`；生产在读取 forward log、加载模型或创建交易 client 之前要求 bundle 存在且全字段/全哈希通过，并直接从 bundle 构造 runtime artifact，不再读取 sidecar/ACTIVE 作为第二真相。
- **通用规则**：单一权威的关键不是优先级，而是其他来源不可达；缺少权威配置必须是 fail-closed 状态，不能触发 discovery。
- **牵连**：`src/judgment/protocol.py`、`src/judgment/forward.py`、`src/execution/executor.py`、`models/active_bundle.example.json`；P0 C-01～C-08、D-07。当前仓库不创建 `models/active_bundle.json`，所以生产保持关闭，不构成 ACTIVE 切换。
