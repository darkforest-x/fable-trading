# 运行指针不等于最新研究模型

- **问题**：控制台要求接入“最新模型”，但仓库同时存在生产运行指针、冻结基线和更新的研究实验；只看 `owner_best` / frozen 路径会把旧 v10 误报成最新。
- **死胡同**：沿着当前运行链路寻找权重最安全，却回答了“目前允许生产使用什么”，没有回答“最近完成研究的模型是什么”；两条时间线并不相同。
- **有效路径**：先明确“最新”指研究时间轴，再从实验注册表、选择回执和训练回执交叉核对模型身份、特征族、迭代数、阈值与 SHA；同时原样保留实验的 `reject` / `production_eligible=false` 状态，通过只读适配器展示，绝不改 ACTIVE。
- **通用规则**：收到“用最新模型”时，第一步区分 latest-runtime、latest-research 与 latest-production-eligible；如果选 latest-research，界面必须同时展示来源和资格状态，不能把“新”翻译成“可生产”。
- **牵连**：`experiments/registry.yaml`、L1/L2 receipt、`yoyo/console/replay.py`、`prototypes/fable-signal-console/`、`models/active_bundle.json` 的 fail-closed 约束。
