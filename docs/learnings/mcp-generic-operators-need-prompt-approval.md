# 无工具注解的 MCP 不能靠 writes 审批保护

- **问题**：FiftyOne MCP 用一个通用的 `execute_operator` 承载只读分析与去重删除；若 Codex 只对“写工具”提示确认，删除类 operator 可能无法被可靠识别。
- **死胡同**：最初把 `default_tools_approval_mode` 设为 `writes`，以为 Codex 能按调用语义区分读写；实际 MCP `list_tools` 返回的 `execute_operator`、`download_plugin` 等工具 `annotations=null`，没有可供分类的写操作元数据。
- **有效路径**：先用真实 MCP 握手检查工具注解，再把该 server 的默认审批模式收紧为 `prompt`，让所有 FiftyOne MCP 调用都由 owner 确认；数据目录和插件目录继续与训练环境隔离。
- **通用规则**：接入带“通用执行器”的 MCP 时，第一步检查 `list_tools` 注解；只要读写共用同一工具或注解缺失，就使用全提示审批，不能仅凭工具名称或 Skill 文案推断安全性。
- **牵连**：`~/.codex/config.toml` 的 `[mcp_servers.fiftyone]`、`fiftyone-mcp-server==0.1.13`、`@voxel51/brain` 和 `@voxel51/utils` 中的删除/去重 operator；这不改变 YOLO 数据、训练依赖或 holdout 纪律。
