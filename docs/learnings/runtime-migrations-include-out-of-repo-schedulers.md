# 运行迁移必须检查仓库外定时任务

- **问题**：仓库代码和 VPS 已切到 MA206，但本机 Claude 定时任务与 Codex automation 仍从旧 checkout 启动，并在说明中引用旧前向日志。
- **死胡同**：只用仓库内 `rg` 判定迁移完成；它看不到 `~/.claude/scheduled-tasks`、`~/.codex/automations` 和任务自身的 cwd 元数据。
- **有效路径**：把仓库内执行入口、外部 scheduler prompt、cwd、冻结模型和日志路径作为同一运行链审计，并让命令显式 `cd` 到权威工作树。
- **通用规则**：任何运行时迁移完成前，至少检查代码、部署、定时任务、服务配置和操作文档五类入口。
- **牵连**：MA206、`daily-okx-data-update`、Codex `fable` automation、VPS deploy、前向账本。
