# 子代理创建响应缺字段时应继续核查运行日志

- **问题**：Owner 要求所有子代理实际运行 gpt-6-luna/max。两次 spawn 都成功，但响应仅含 task_name，list_agents 仅有运行状态。
- **死胡同**：主代理把“返回里没模型字段”当成无法继续核实，立即中断，让正常启动表现成配置故障。配置值和请求参数也不能单独当作实际运行证据。
- **有效路径**：按本次创建时间定位 Codex 本地 session JSONL，只读取 session_meta 与 turn_context。session_meta 的 parent_thread_id/agent_path 匹配本任务后，turn_context 同时显示 model=gpt-6-luna、effort=max；两次均如此，turn_aborted=interrupted 是主代理自己的中断。核实后恢复原子代理，不修改全局配置。
- **通用规则**：先看创建返回；缺少有效模型字段时，定向查本次子会话运行上下文，不能凭代理自报模型，也不能把缺字段等同启动失败。确实没有可核实证据时才按 owner 规则由主代理继续。
- **牵连**：本次子任务 `/root/backtest_anchor_audit`；运行日志 `~/.codex/sessions/2026/11/01/rollout-2026-11-01T20-38-13-01a1971e-14d9-70d3-ab96-a297126f3921.jsonl`。只摘必要元数据，不输出会话全文或凭据；不改变 owner 的模型与核验要求。
