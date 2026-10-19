# Codex 轻模型分工

Owner于2026-09-10明确选择：保留Astra主模型，主要依靠轻模型代理分工节省额度。本配置安装在本机用户目录，可用于其他项目；各项目自己的AGENTS.md、数据边界和授权规则仍然适用。

## 已安装配置

主模型保留 `gpt-6-astra / ultra`。全局 `~/.codex/config.toml` 新增：

```toml
[agents]
enabled = true
default_subagent_model = "gpt-5.6-luna"
default_subagent_reasoning_effort = "max"
max_concurrent_threads_per_session = 2
```

这给没有单独指定模型的子代理设置默认值。显式调用参数或角色文件自己的model/effort仍可能覆盖默认值，因此需核对实际角色选择。原有十个LazyCodex/OMo角色未修改；明确选用它们时仍按其原配置运行。

| 角色 | 模型 / 档位 | 职责 | 文件沙箱默认值 |
|---|---|---|---|
| budget-mapper | Luna Max | 代码入口、调用路径、分支与副作用 | read-only |
| budget-docs | Luna Max | 官方文档、API与版本语义 | read-only |
| budget-evidence | Luna Max | 已批准报告的数字、证据和结论核对 | read-only |
| budget-implementer | Terra High | 有明确文件所有权的一项修改 | workspace-write |
| budget-debugger | Terra High | 一个故障的复现/诊断与修复建议 | read-only |
| budget-reviewer | Terra High | 有范围的独立代码复核 | read-only |

六个配置位于 `~/.codex/agents/`，均显式设置模型和档位，并用角色内 `[agents] enabled = false` 禁用继续创建子代理。没有安装新MCP、第三方依赖或上游整套角色。

## 如何使用

全局 `~/.codex/AGENTS.md` 已添加Astra分工偏好，并指向 `~/.codex/skills/quota-aware-delegation/SKILL.md`。新会话加载这些指令后，有独立工作阶段时选择相应轻模型角色。也可以明确说：

> 保留Astra主模型，按轻模型分工处理：先让budget-mapper查清调用路径；有明确文件范围后由budget-implementer修改，Astra负责关键决策和整合。

或者显式调用 `$quota-aware-delegation`。小改动和单次已知查询直接本地完成；通常一个子代理足够，第二个只用于独立且有用的工作。不默认增加planner、reviewer、gate等多层流程，不重复跑已经通过且没有新疑点的检查。

新配置不能保证已经运行的会话立即刷新角色目录与默认值。本轮直接选择 `budget-evidence` 时，当前会话返回 `unknown agent_type`，说明这轮尚未识别新增角色，不能声称已热加载。请在新任务或刷新会话后使用新角色名；若仍不可见，重新核验角色发现。当前会话可以通过已有通用委派工具明确指定相同模型、档位和对应职责，不能静默继承Astra Ultra。无需为了这项配置重启交易服务或中断已有任务。

## 项目边界

- Sol主任务继续遵守owner的可见Luna Max任务约定；隐藏budget角色不能代替该机制。所需工具不可用时由Sol继续并如实说明。
- 只读角色也不能读取未经批准的holdout。budget-evidence仅处理任务明确允许的报告与证据；报告里的数据路径不是读取授权。
- 新模型训练、阈值/成本/障碍修改、ACTIVE/promote、实盘操作与通知，仍按项目规则和会话授权处理。
- 只有main，不建分支、worktree或其他研究仓。所有写入任务必须给定文件所有权，不撤销其他协作者的修改。
- 文件沙箱字段是配置默认值，运行时父会话权限覆盖和外部工具权限仍需分别考虑，不能把角色的read-only标签当作完整隔离证明。

## 验证与实际限制

安装时检查了六个TOML的必需字段、名称唯一性、模型/档位、嵌套委派禁用；全局原配置逐字段比对保持不变，原全局指令完整保留。Skill通过自带校验器。

原生Codex app-server的 `config/read` 已独立核验有效配置：主模型Astra Ultra、默认子模型Luna Max、最大并发2；`model/list` 确认Luna Max与Terra High受当前本地模型目录支持。只保留筛选后的非敏感回执于 `output/qa/codex_delegation_20260910/native_config.json`，没有启动模型推理。该公开接口没有独立列出custom roles的方法；本轮新角色名的实际spawn未成功，不能用配置读取替代角色发现验收。

独立Luna Max完成五场景决策演练：单字修改保持本地；独立代码与文档核验选择两个Luna Max角色；报告审查拒绝未授权holdout；Sol缺少可见任务工具时不改用隐藏代理；已有两个子代理时不再添加三层协调者。该结果是路由判断验证，不是实际节省额度的测量。

补充检查边界：`codex doctor` 的配置加载项通过；其整体退出码为1，原因包含当前终端 `TERM=dumb`，另有更新CDN连通性提示，不能汇报“Doctor全部通过”。这些不是本轮新增配置字段的解析失败，未借机改终端、网络或升级应用。

轻模型减少Astra承担的工作，但分工有额外上下文与整合成本。尚未测量节省比例；账户订阅额度不能直接由API单价推算。主模型仍为Astra Ultra，因此主会话自身的思考与整合继续消耗其额度。

## 备份与撤销

修改前的全局config.toml和AGENTS.md备份位于：

`~/.codex/backups/quota-delegation-20260910-211838/`

备份在本机私有目录，不提交到本仓。若之后要撤销，优先仅删除config.toml中带 `owner quota-aware delegation 2026-09-10` 起止标记的新增块、全局AGENTS.md中同日期的Astra分工节，以及六个budget角色和本skill；保留后续其他修改。不要在产生新配置修改后直接整份覆盖旧备份。

机制依据：[官方子代理文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。
