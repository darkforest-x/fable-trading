# Agent 配置能解析，不代表角色约束已经生效

- **问题**：审阅 VoltAgent/awesome-codex-subagents 的固定提交 `fbfd996d9f0572ab35487c00259d6c55aeb33e82` 时，需要判断第三方角色模板能否直接用于本仓的代码与研究复核。174 个实际 TOML 全部能解析、必需字段齐全，但这只能证明配置的结构性质。
- **死胡同**：把角色名称、`read-only` 声明或合法 TOML 当作质量与隔离证据。本轮没有安装这些角色，也没有发生运行事故；这是被静态反例排除的判断路径。README 的示例同样能被 TOML 解析，却缺少官方要求的 `developer_instructions`；个别模板还给“不得把相关性当因果”“不得保证模拟收益”加了主代理可豁免的例外。
- **有效路径**：固定提交，逐文件核对 Git blob；分别检查语法、官方字段、提示词语义、外部工具依赖、模型与权限的运行时覆盖规则，再与本仓授权边界对照。当前官方说明父会话的实时权限覆盖会在生成子代理时重新应用，因此角色文件里的沙箱值不能单独充当隔离证明。
- **通用规则**：先区分四种证据：文件合法、宿主发现、配置实际生效、任务表现合格。每一层都单独验收；没有后一层证据就停留在前一层结论。诚实性、holdout 纪律与 owner 授权边界不可由主代理临时豁免，需同时落实到角色指令、工具权限和项目守门检查。
- **牵连**：[技术审阅报告](../../analysis/p0_codex_subagents_review_20260910.md)；[官方子代理配置与权限说明](https://learn.chatgpt.com/docs/agent-configuration/subagents)；[上游 README](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/README.md#L91-L107)；本仓 `AGENTS.md` 的 holdout、单仓单分支、模型提升与实盘授权规则。
