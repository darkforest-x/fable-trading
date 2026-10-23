# VoltAgent Awesome Codex Subagents：源码审阅与项目适配分析

审阅日期：2026-09-10。对象固定为上游提交 `fbfd996d9f0572ab35487c00259d6c55aeb33e82`，提交时间 2026-09-07。本报告属于工具配置与提示词技术审阅，不是交易策略实验。

**结论：值得作为角色模板库借鉴；是否提升任务质量，需要在本项目验证。对 fable-trading，建议选择少量角色，保留清晰的分工与证据格式，再补齐本仓研究纪律。**

本轮读取完整公开快照，核对 190 个文件的 Git blob，检查全部 174 个 TOML，并逐行抽审代码审查、Python、调试、量化、数据科学、ML、协调与安装等角色。同步对照当前官方 Codex 文档。没有安装上游 agent、启动其 MCP、修改 Codex 配置、读取行情或 holdout、训练、部署、下单或切换模型权重。

**一、它提供什么**

这个项目将工作角色写成可复用的 Codex 会话配置。每个 TOML 定义角色名称、适用任务、基础模型、推理档位、沙箱默认值以及工作指令；两个文件还声明 MCP 服务。真正生成子任务、传递消息、等待结果的能力由 Codex 宿主提供。

因此，安装一个 `quant-analyst` 的效果是为通用模型提供量化分析的任务要求；没有额外下载一个经过量化专项训练的模型，也没有同时获得回测引擎、行情数据或经过验证的盈利方法。`agent-installer` 的实际交付物是安装指导，`multi-agent-coordinator` 的交付物是分工、依赖与整合计划。[上游角色目录](https://github.com/VoltAgent/awesome-codex-subagents/tree/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories)、[安装指导角色](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/09-meta-orchestration/agent-installer.toml)、[协调角色](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/09-meta-orchestration/multi-agent-coordinator.toml)

可以这样区分本地常见的几类扩展：

| 组件 | 主要回答的问题 | 对本项目的意义 |
|---|---|---|
| AGENTS.md | 整个项目必须遵守哪些规则 | holdout、时间切分、实盘授权、分层契约 |
| Skill | 一类工作按照什么方法完成 | 数据审计、统计分析、学习笔记等具体流程 |
| Custom agent TOML | 由哪种角色、模型和配置处理子任务 | 代码路径梳理、审查、文档核验的职责划分 |
| MCP / 工具 | 角色实际能查询或操作什么 | 外部文档、浏览器、数据服务等真实能力 |

以上是功能分工，不是指令优先级表。角色还可以使用已有 skills 与工具；仅复制角色文件不能补齐环境依赖。[官方 Skills](https://learn.chatgpt.com/docs/build-skills)

**二、全量统计：按文件计算，不按宣传数字推断**

| 检查项 | 本次结果 | 能支持的结论 |
|---|---|---|
| 完整文件 | 190 | 174 TOML、15 Markdown、1 LICENSE |
| 分类 | 13 | 覆盖开发、研究、基础设施、治理等领域 |
| TOML 解析 | 174/174 通过 | 配置语法有效 |
| 必需字段 | 174/174 齐全 | 都包含 name、description、developer_instructions |
| 唯一名称 | 174/174 | 当前目录内没有同名角色 |
| Git blob 校验 | 190/190 一致 | 本地审阅字节对应固定提交 |
| 模型 | Sol 136、Terra 28、Luna 10 | 全部文件预先指定基础模型 |
| 推理档位 | high 132、medium 42 | 没有配置 max 的角色 |
| 沙箱声明 | read-only 99、workspace-write 75 | 这是文件默认值，不能单独证明运行时权限 |
| 指令长度 | 最短139、中位250、最长506英文词 | 使用空白分词统计，不是 tokenizer token 数 |
| MCP 声明 | 2 | docs-researcher 与 browser-debugger |
| 根 README 的 TOML 链接 | 173，坏链接0 | 漏列 visual-asset-generator |
| 仓内执行代码、CI、效果评测 | 未发现 | 快照只有配置、Markdown 和许可证 |

这些是对固定快照的独立静态统计。README 标示 172+，实际为174，不能把带“+”的近似描述本身当严重缺陷。没有上个版本的同方法审计，本报告不声称版本质量上升或下降。[固定提交目录](https://github.com/VoltAgent/awesome-codex-subagents/tree/fbfd996d9f0572ab35487c00259d6c55aeb33e82)

| 分类 | 实际角色数 | 主要覆盖 |
|---|---:|---|
| Core Development | 13 | 前后端、接口、代码路径 |
| Language Specialists | 31 | Python、TypeScript、Go 等 |
| Infrastructure | 16 | 云、容器、网络、运维 |
| Quality & Security | 19 | 审查、调试、测试、安全 |
| Data & AI | 14 | 数据科学、ML、MLOps |
| Developer Experience | 15 | 依赖、文档、工具链 |
| Specialized Domains | 14 | 量化、金融、区块链等 |
| Business & Product | 17 | 产品、需求、商业分析 |
| Meta & Orchestration | 11 | 分工、协调、上下文整合 |
| Research & Analysis | 12 | 检索、文献、研究 |
| AI Governance & Safety | 4 | 治理与模型风险 |
| Platform Engineering & IDP | 4 | 内部开发平台 |
| LLMOps, Evals & Observability | 4 | 评测、回归与可观测性 |

**三、值得借鉴的设计**

第一，模板普遍有明确的工作步骤和返回要求。174个都有 Working mode，173个有 Focus on，171个有 Quality checks，173个有 Return。这些结构要求模型说明范围、证据、验证和剩余疑点，比只有头衔的角色提示更容易验收。但这些小节存在不等于其内容必然充分。

第二，reviewer 的要求比较实用：围绕受影响行为判断风险，区分事实与假设，关联文件/行号，说明复现和验证缺口。code-reviewer 更偏维护性、复杂度、所有权与长期影响，两者存在真实侧重差别。对于一次小改动通常选择一个即可；确有不同审查问题时再并行拆分。[reviewer](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/04-quality-security/reviewer.toml#L11-L38)、[code-reviewer](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/04-quality-security/code-reviewer.toml#L17-L31)

第三，code-mapper 很适合排查“行为到底由谁负责”：它要求追踪入口、状态转换、条件分支、策略检查点和副作用边界，并指出哪里还不能确定。对分层代码，这比先提出架构重写更有用。[code-mapper](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/01-core-development/code-mapper.toml#L7-L32)

第四，模板短、易审阅、容易移植。python-pro 的关注点包括异常语义、类型契约、import结构、副作用与测试性，但没有强制使用某个 Web 框架。这种通用性有利于复用，也意味着具体库知识和项目方法仍需另外提供。[python-pro](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/02-language-specialists/python-pro.toml#L11-L38)

本轮抽审的角色没有发现虚构的 agent 通信 JSON 协议或“必须达到95%准确率”一类无基线目标。不能把旧版本、其他 agent 列表或角色扮演提示词的常见问题直接扣到这个快照上。

**四、明确发现的问题与实际影响**

**1. README 的指令字段示例有误，但实际174个文件没有同样的问题。**

README 的示例使用 `[instructions]` 表里的 `text`；当前官方和仓库 CONTRIBUTING 要求顶层 `developer_instructions`。照抄 README 示例不能满足当前官方必需字段。实际角色文件使用的是正确字段，本轮未启动这些角色，因此不推断特定版本究竟报错还是忽略。[README 示例](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/README.md#L89-L106)、[贡献规范](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/CONTRIBUTING.md#L26-L43)、[官方字段要求](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents)

这也是本轮的结构反例：TOML解析成功不能推出它是合格的Codex角色配置。语法、schema、宿主发现与实际行为是四个不同验收对象。

**2. 通用结尾错误地给诚实要求设置了可豁免例外。**

159个文件包含“除非主代理明确要求”这一通用限定。用于“不要扩大重构范围”时可以合理；用于事实与证据底线时就不合理。例如：

- debugger：无证据不能确认根因，却为主代理要求留了例外。
- data-scientist：不能把探索性相关性当因果证明，却为主代理要求留了例外。
- quant-analyst：不能把模拟业绩当现实保证，却为主代理要求留了例外。
- agent-installer：不能编造安装与市场机制，却为主代理要求留了例外。

父代理可以改变工作范围，不能改变证据是否存在。应按语义逐项处理，保留合理范围例外，删除事实与诚实底线的例外。这是提示词缺陷，不能据此宣称模型一定会照做、或平台更高层规则一定会失效。[debugger 第40行](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/04-quality-security/debugger.toml#L40)、[data-scientist 第40行](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/05-data-ai/data-scientist.toml#L40)、[quant-analyst 第40行](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/07-specialized-domains/quant-analyst.toml#L40)、[agent-installer 第40行](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/09-meta-orchestration/agent-installer.toml#L40)

**3. “Smart Model Routing”是静态角色配置。**

没有发现按任务难度动态比较模型、切换模型或评估成本的实现。136/174，即78.2%的角色写死Sol；所有Luna角色都是medium。基础模型分配本身有用，但仓库没有提供证据说明这组选择优于统一模型或用户自己的分配策略。也不能将安装174个角色误解为每次会启动174个角色。[上游模型说明](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/README.md#L108-L116)

**4. 沙箱默认值不是完整权限证明。**

官方允许自定义文件设置模型、档位和沙箱；明确写入的model/effort有配置优先级，父会话的实时权限覆盖则会在子任务启动时重新施加。应检查实际启动结果。独立上下文也不等于独立工作目录，并行写入仍可能冲突。[官方子代理配置与权限](https://learn.chatgpt.com/docs/agent-configuration/subagents)

对本项目更直接的限制是：即使只读沙箱实际生效，它仍然允许读取文件；它不能单独执行“未经授权不得读取holdout”的数据纪律。MCP的外部写操作也需要按工具权限约束，不能由本地文件沙箱一概推断。[官方 MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)

**5. 两个MCP角色有真实的环境前置条件。**

docs-researcher 指向OpenAI文档MCP。它能帮助核验OpenAI文档，不意味着已经配置PyTorch、Ultralytics、LightGBM等各自文档工具。browser-debugger 指向 `http://localhost:3000/mcp`，文件没有启动对应服务的命令；本机没有这个服务时，复制配置不会自动创建它。该角色还配置workspace-write，工作范围包含最小修复，不能按角色名称把它当作纯观察器。[docs-researcher](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/10-research-analysis/docs-researcher.toml#L43-L44)、[browser-debugger](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/04-quality-security/browser-debugger.toml#L1-L45)

**6. 职责重叠会增加协调成本。**

agent-organizer 与 multi-agent-coordinator 都要求拆分关键路径、明确所有权、安排依赖与整合。通常先选一个即可。根据相似提示词、同一模型和同一组证据得到多个一致答案，也不能直接算多个独立的实验验证。需要将代码追踪、反例构造、来源核验等工作实质性区分。[agent-organizer](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/09-meta-orchestration/agent-organizer.toml#L11-L38)、[multi-agent-coordinator](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/09-meta-orchestration/multi-agent-coordinator.toml#L11-L38)

**7. 文档命名与索引仍有小范围不一致。**

README工作流使用 `docs_researcher`、`code_mapper` 等下划线名称，实际TOML使用连字符。自然语言可能识别作者意图；精确角色调用应读取真实name。另一个角色visual-asset-generator未列入根README。它们是维护一致性问题，严重程度低于错误schema和诚实约束例外。[README 工作流](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/README.md)、[code-mapper 实际name](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/01-core-development/code-mapper.toml#L1)

**五、适配 fable-trading：先建立小而明确的角色集合**

通用模板没有写本仓规则属于正常边界，不能据此批评它不能服务所有项目。但把通用模板直接用于本仓实验裁决，需要承担这个适配缺口。

| 上游角色 | 本项目可借鉴的任务 | 必须补充的内容 |
|---|---|---|
| code-mapper | 追踪信号、前端、队列和各层调用路径 | 四层import边界、实际副作用、当前HANDOFF |
| reviewer | 审查具体diff的正确性与回归 | 行号、触发条件、证据、最小修复；不把建议冒充验证 |
| docs-researcher | 核验版本相关API与默认值 | 与目标库匹配的官方来源、三机依赖契约 |
| debugger | 复现并定位已发生的问题 | 删除“无证据确认根因”的例外；不擅自重启生产 |
| data-scientist | 审查实验设计、混杂与解释 | 时间切分、效应量、负对照、单变量、holdout禁入 |
| quant-analyst | 审查回测合同及结果解释 | 既定成本、匹配随机对照、净收益与置换检验门 |

quant-analyst 已提醒前视、成本、样本外和过拟合，这是有价值的起点；但它没有本仓的同币×同时间块×同波动桶匹配随机入场，也没有owner逐次授权访问holdout的规则。data-scientist提到randomization是通用实验设计内容，不能误读为明确要求本项目随机切分训练集。[quant-analyst](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/07-specialized-domains/quant-analyst.toml#L17-L38)、[data-scientist](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/05-data-ai/data-scientist.toml#L11-L38)

ml-engineer、mlops-engineer和git-workflow-manager需要额外收紧范围。前两者把阈值、特征、推理集成、模型注册与部署纳入工作范围，后者讨论分支与发布流程；本仓对此有明确的owner决策边界及main-only约束。它们的名字和默认写权限都不能视为这些动作的授权。[ml-engineer](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/05-data-ai/ml-engineer.toml#L11-L30)、[mlops-engineer](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/05-data-ai/mlops-engineer.toml#L11-L30)、[git-workflow-manager](https://github.com/VoltAgent/awesome-codex-subagents/blob/fbfd996d9f0572ab35487c00259d6c55aeb33e82/categories/06-developer-experience/git-workflow-manager.toml#L17-L30)

本仓规则依据当前AGENTS.md及HANDOFF。尤其保留：holdout未经明确批准不能读取；评估按时间切分；固定成本与障碍不能擅改；无前视；一次实验一个变量；不得自动promote；不得擅自下单/改仓；只用main，不建worktree。当前状态应继续读取HANDOFF顶部，不能用通用模板或旧状态段覆盖新记录。

**还需要尊重你的Sol委派约定。** 当主任务由Sol执行时，你要求通过本机可见的Luna Max任务开展并行工作。上游模板没有任何max配置，十个Luna角色全部medium；安装这些子代理不满足这个约定。若未来采用其提示词，可以把它们作为Luna Max任务的工作要求，保留现有任务机制，并核对实际模型档位。当前分析没有改变该约定，也没有安装或新建可见任务。

**六、怎样验证它对你有没有用**

建议先用少量已知代码缺陷和人工构造的合同反例开展工具评测；无需读取holdout。对同一组任务保持基础模型、推理档位、上下文、工具权限、输出要求和预算一致，比较“通用任务描述”与“经过本仓适配的角色指令”。

- 代码路径任务：是否找到真实入口、副作用与关键分支，是否编造不存在的调用关系。
- 审查任务：确认缺陷的召回和误报、每项结论能否定位到证据、是否提出越界改动。
- 研究合同任务：能否识别构造的前视、缺少匹配对照、未经授权的holdout读取路径。
- 环境任务：能否识别未配置的MCP、模型档位与预期不符、只读不等于数据访问隔离。
- 成本与效率：总token、墙钟时间、主代理整合时间、需要owner纠错次数。

同一个任务用不同顺序分配，评分依据预先固定；不要让评判者只凭“语言更专业”给高分。多次运行才能观察稳定性。以上是建议的验收方案，本轮没有运行这组行为评测，没有产生准确率或节约成本数字。

对子代理数量的建议是按任务独立性逐步增加：先让一个角色梳理代码，一个角色做证据审查；只有第三项工作确实独立时再增加角色。协调者本身不应成为必须经过的额外层。

**七、复现静态统计**

以下命令从固定公开快照重新计算主要统计及Git blob；使用本仓已有venv中的tomli，不安装依赖，不创建Git仓库，不读取交易数据。GitHub公开API若限流则如实失败，不跳过校验后宣称通过。

```bash
cd /Users/zhangzc/fable-trading
review_dir="$(mktemp -d /tmp/voltagent-review.XXXXXX)"
curl --fail --location 'https://codeload.github.com/VoltAgent/awesome-codex-subagents/tar.gz/fbfd996d9f0572ab35487c00259d6c55aeb33e82' --output "$review_dir/source.tar.gz"
tar -xzf "$review_dir/source.tar.gz" -C "$review_dir"
.venv/bin/python - "$review_dir" <<'PY'
import collections
import hashlib
import json
import pathlib
import re
import statistics
import sys
import urllib.request
import tomli

root = next(pathlib.Path(sys.argv[1]).glob('awesome-codex-subagents-*'))
files = sorted(root.rglob('*.toml'))
agents = [tomli.loads(path.read_text()) for path in files]
assert all(all(a.get(k) for k in ('name', 'description', 'developer_instructions')) for a in agents)
assert len({a['name'] for a in agents}) == len(agents)
print('files', sum(p.is_file() for p in root.rglob('*')))
print('agents', len(agents))
print('categories', collections.Counter(p.parent.name for p in files))
for field in ('model', 'model_reasoning_effort', 'sandbox_mode'):
    print(field, collections.Counter(a.get(field) for a in agents))
words = [len(a['developer_instructions'].split()) for a in agents]
print('prompt_words_min_median_max', min(words), statistics.median(words), max(words))
print('mcp', [a['name'] for a in agents if a.get('mcp_servers')])
readme = (root / 'README.md').read_text()
example = tomli.loads(re.search(r'```toml\n(.*?)```', readme, re.S).group(1))
print('README_example_missing', [k for k in ('name', 'description', 'developer_instructions') if k not in example])
linked = set(re.findall(r'\]\(([^)]+\.toml)\)', readme))
actual = {str(p.relative_to(root)) for p in files}
print('unlisted_agents', sorted(actual - linked))
print('broken_main_agent_links', sorted(linked - actual))
sha = 'fbfd996d9f0572ab35487c00259d6c55aeb33e82'
url = f'https://api.github.com/repos/VoltAgent/awesome-codex-subagents/git/trees/{sha}?recursive=1'
request = urllib.request.Request(url, headers={'User-Agent': 'Codex-repository-analysis'})
with urllib.request.urlopen(request, timeout=30) as response:
    tree = json.load(response)
assert not tree.get('truncated')
checked = 0
for item in tree['tree']:
    if item['type'] != 'blob':
        continue
    data = (root / item['path']).read_bytes()
    digest = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
    assert digest == item['sha'], item['path']
    checked += 1
print('verified_git_blobs', checked)
PY
python3 scripts/md_to_html.py analysis/p0_codex_subagents_review_20260910.md --out-dir analysis/html
```

**风险与诚实声明**

- 全量检查的是配置结构与文件身份；提示词语义为针对性抽审，没有声称逐条证明174个角色的实际行为。
- 未实际加载上游角色，也未对所有模型、档位或MCP进行端到端验收。本机CLI版本查询为0.153.4，不代表已验证全部兼容性。
- 该快照未提供可复核的角色效果benchmark。本文对使用价值和适配方案的判断来自代码与提示词审阅，不是性能实验结论。
- AUC、置换检验p、top-decile毛/净收益、胜率、val样本数和随机交易对照对本次工具审阅不适用：没有交易信号或收益标签。等价的结构对照是用README可解析却缺必需字段的反例检验“语法正确即可使用”的假设；它不能替代未来的行为对照评测。
- holdout消耗0次。没有读取数据集、训练、调参、改阈值、promote、部署或发送通知。
- 官方配置机制可能继续变化；未来安装时应重新核验当前文档、实际agent发现、模型档位与权限，不能把本报告视为永久兼容承诺。

下一步选项：保持现状，将该库作为提示词参考；或由owner决定是否为少量角色制作本仓版本并进行上述工具评测。第三方角色安装、现有模型配置变化和工具接入都没有在本轮执行。
