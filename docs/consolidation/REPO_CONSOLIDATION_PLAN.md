# 单仓收敛计划 — fable-trading 成为唯一 ACTIVE 仓

> 来源任务书：`FABLE_TRADING_SINGLE_REPO_CONSOLIDATION_CLAUDE_TASKBOOK.md`（owner 提供）
> 冻结现场：`reports/consolidation/source_repo_snapshots.json`
> 迁移台账：`reports/consolidation/migration_ledger.jsonl`
> 最终验收：`reports/consolidation/FINAL_ACCEPTANCE.md`

## 为什么必须做

不是"五个仓库看起来乱"。是这条实测事实：

```
fable-trading 的 63 个文件 import yoyo.*
yoyo 解析到 /Users/zhangzc/yoyo-trading（setuptools editable 安装）
```

`yoyo/` 在 commit `d472a05` 搬出 fable-trading，开发在 yoyo-trading 继续。
**本仓当前跑不起来，除非另一个仓库在磁盘上。** 把 yoyo-trading 归档而不先迁回
`yoyo/`，等于让 ACTIVE 仓失去自己的包。

更隐蔽的一层，实测确认（`tests/boundaries/test_yoyo_package_is_local.py` 的 docstring
记录了机制）：editable 安装是 meta-path finder，把顶层名 `yoyo` 硬映射到仓外绝对路径，
且排在普通 PathFinder **之后**。所以

- 本地 `yoyo/` 有的模块 → 用本地的
- 本地 `yoyo/` **缺**的模块 → 静默落回 yoyo-trading，一声不吭

即：**迁一半和迁完了，长得一模一样。** 字节校验、因果测试、边界测试会全绿，
同时 import 着一个即将归档的仓库。因此 `yoyo/` 是一次性整体迁回，并加一道
provenance 守门测试，而不是分阶段慢慢搬。

## 裁决

```
ACTIVE
└── fable-trading

ARCHIVED / READ-ONLY（验收通过并推送 PR 之后才打归档 commit）
├── darkforest-one
├── yolo-xx
├── yoyo-trading
└── yoyo-eth
```

新研究一律进 `experiments/active/<experiment_id>/`，不再开新仓。

## 命名：为什么是 `yoyo/` 而不是任务书写的 `src/fable/`

任务书 §4 给的目标结构是 `src/fable/contracts/`、`src/fable/layers/…`。
本仓采用同样的**逻辑分层**，但包名沿用 `yoyo`：

| 任务书路径 | 本仓实际路径 |
|---|---|
| `src/fable/contracts/` | `yoyo/contracts/` |
| `src/fable/data/` | `yoyo/data/` |
| `src/fable/layers/l1_detection/` | `yoyo/layers/l1_detection/` |
| `src/fable/layers/l2_judgment/` | `yoyo/layers/l2_judgment/` |
| `src/fable/layers/l3_backtest/` | `yoyo/layers/l3_backtest/` |
| `src/fable/layers/l4_execution/` | `yoyo/layers/l4_execution/` |
| `src/fable/evaluation/` | `yoyo/evaluation/` |
| `src/fable/artifacts/` | `yoyo/artifacts/` |

依据，按任务书 §1.1 自己给的优先级（当前实际代码 > 文档描述）：

1. **canonical package 已经存在**，只是暂时不在本仓。任务书 §4 明写
   "若现有仓库中已经存在同名 canonical package，沿用现有结构…不要为了目录美观
   强制大规模移动已稳定代码"。
2. **CLAUDE.md 铁律 14** 用 `yoyo/layers/` 定义层间契约，是本仓生效中的 owner 规则。
3. **字节校验**（任务书 §11.2 的硬性验收）只有在包名不变时才成立。改名意味着
   重写 29 个测试 + 40 个 tools + 63 个调用点的 import，把一次"不许影响运行"的
   收敛变成一次大规模改写。

差异已在此登记；`docs/RESTRUCTURE_MAP.md` 是新旧路径的长期对照表。

## 阶段与状态

| 阶段 | 内容 | 提交 |
|---|---|---|
| C0 | 冻结五仓现场、基线测试、运行安全哈希 | `consolidation(c0)` |
| C1 | 治理骨架：两份注册表、契约、边界测试、`yoyo/` 迁回 | `consolidation(c1)` |
| C2 | 统一 Gold 标注与 Causal Onset 工作流 | `consolidation(c2)` |
| C3 | 数值基线、匹配对照、因果测试、经济门 | `consolidation(c3)` |
| C4 | Pattern Teacher 登记与 proposal 语义隔离 | `consolidation(c4)` |
| C5 | 兼容壳与语义去重 | `consolidation(c5)` |
| C6 | 历史与负面结论回迁 | `consolidation(c6)` |
| C7 | 全量验收、哈希对比、最终报告、归档准备 | `consolidation(c7)` |

## 本次任务的禁止清单

不训练模型、不调参、不读新的 holdout、不改 ACTIVE、不清 forward log、不 promote、
不部署、不下单、不整仓 merge、不把大产物提进 git、不复制任何密钥。

运行安全的证明方式不是声明，是 C0 与 C7 两次哈希对比：
`reports/consolidation/source_repo_snapshots.json` 里的 `safety_hashes` 段。

## 已知偏差（逐条登记，不隐藏）

1. **分支名**。任务书 §13.1 指定 `consolidation/fable-single-repo-v1`；实际工作在
   harness 创建的隔离 worktree 分支 `claude/fable-trading-consolidation-758d61` 上。
   意图（不直推 main、隔离分支）满足；重命名会打断 owner 侧的 worktree 工具链。
   推送命令在最终报告里给全。
2. **worktree**。CLAUDE.md 铁律 13 禁止开 worktree，需 owner 点头。owner 交付本
   任务书时环境已在 worktree 中，任务书 §9 C0.1 也明确要求隔离 worktree；按 owner
   最新指令执行，用完当轮删除。
3. **基线测试有 11 个先存失败**。全部是本 worktree 缺少 gitignore 掉的 `data/`
   产物，不是代码缺陷；C7 比对的是失败**集合**，不是计数。
