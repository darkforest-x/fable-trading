# 单仓收敛 — 最终验收

> 机器版本：[`final_acceptance.json`](final_acceptance.json)
> 迁移台账：[`migration_ledger.jsonl`](migration_ledger.jsonl)（131 条）
> 资产裁决：[`../../docs/consolidation/source_asset_registry.json`](../../docs/consolidation/source_asset_registry.json)
> 生成时间：`2026-08-19T16:48:12+00:00`

## 裁决：**accepted**

七项验收全部通过。逐项都是与 C0 记录的对比或对真实 diff 的扫描，不含判断。

| 检查 | 结果 |
|---|---|
| 运行安全哈希与 C0 一致 | PASS — 12 个对象逐字节相同 |
| 无新增测试失败 | PASS — 701 → 1185 passing，新增失败 **0** |
| 秘密扫描 | PASS — 新增行中无密钥形态内容 |
| 大文件扫描 | PASS — 无新增 >2 MiB 文件 |
| 迁移台账可追溯 | PASS — 131 项全部可追至 source commit |
| 无任何 promote | PASS — 无 artifact / experiment 为 `production_eligible` |
| holdout | PASS — 本次消耗 **0**；唯一一次记录早于本任务且未重读 |

## 五仓冻结 SHA

| 仓库 | HEAD |
|---|---|
| `fable-trading`（目的） | `59e13a61c43e1e72f397f38985fd8c700533550b` |
| `darkforest-one` | `fd36dd1adc5844f241122c3853eb4d3e675a9c11` |
| `yolo-xx` | `9296cfa8e5053d86cea44e29dbd45874c3dff689` |
| `yoyo-trading` | `784766de45a3b876c986d3ba672779124b46a66f` |
| `yoyo-eth` | `6147810afb46be1c664128e9a5359e8e7d0a3923` |

## 运行安全：C0 与 C7 哈希对比

**全部未变。** 这是"没有影响当前运行"的证明方式，不是声明。

| 对象 | SHA-256 (C0) | 状态 |
|---|---|---|
| `models/ACTIVE` | `899c36259950a3d3…` | unchanged |
| `models/ACTIVE_PREV` | `5142d8a143799a82…` | unchanged |
| `models/owner_best.json` | `1fb35c712dbff417…` | unchanged |
| `models/active_bundle.example.json` | `fb2bf9dd0fcf65e6…` | unchanged |
| `data/forward_log.csv` | `6035eb60482481fb…` | unchanged |
| `data/forward_log_ma206.csv` | `e03ada090caec2d7…` | unchanged |
| `src/costs.py` | `e2245409d815db6b…` | unchanged |
| `scripts/deploy_vps.sh` | `47389ad26bbdc30f…` | unchanged |
| `scripts/deploy_vps_short_protocol.sh` | `6ab012d6767dd9ba…` | unchanged |
| `deploy/fable-forward.timer` | `bfddb136a1fcf7be…` | unchanged |
| `deploy/fable-live-health.service` | `d6ecc03f37eb23ed…` | unchanged |
| `deploy/fable-live-health.timer` | `c133fad519f9e67d…` | unchanged |

## 迁移矩阵

| 裁决 | 数量 |
|---|---|
| `ADAPT_AND_PORT` | 7 |
| `DIRECT_PORT` | 97 |
| `HISTORICAL_REPORT` | 26 |
| `REFERENCE_ONLY` | 1 |

| 来源仓 | 分布 |
|---|---|
| `darkforest-x/darkforest-one` | ADAPT_AND_PORT 2 / HISTORICAL_REPORT 5 |
| `darkforest-x/yolo-xx` | ADAPT_AND_PORT 4 / DIRECT_PORT 8 / HISTORICAL_REPORT 10 |
| `darkforest-x/yoyo-eth` | DIRECT_PORT 13 / HISTORICAL_REPORT 3 |
| `darkforest-x/yoyo-trading` | ADAPT_AND_PORT 1 / DIRECT_PORT 76 / HISTORICAL_REPORT 8 / REFERENCE_ONLY 1 |

## 迁进来的核心能力

| 能力 | 落点 | 来源 |
|---|---|---|
| canonical `yoyo` 包（55 个 .py，字节一致） | `yoyo/` | yoyo-trading |
| 层边界 AST 强制 + 具名历史债务 | `tests/boundaries/test_layer_imports.py` | yoyo-trading（扩充） |
| Causal Onset v3 六锚点 + 渲染期盲化 review pack | `yoyo/layers/l1_detection/onset/` | yolo-xx |
| Gold 标注全链（Label Studio + 审计） | `tools/review/`、`configs/labelstudio/` | yoyo-trading |
| 数值基线（扫描器 / 27 因果特征 / 标签） | `yoyo/layers/l1_detection/numeric_baseline/` | yoyo-eth |
| closed-bar 连续性与可用性 | `yoyo/data/continuity.py` | darkforest-one（适配） |
| 产物血统 + 逐轴可复现性 | `yoyo/artifacts/lineage.py` | darkforest-one（适配） |
| 匹配对照 / walk-forward / 置换 / 经济门 | `yoyo/evaluation/` | 两仓合并 + 新增泄漏断言 |
| **canonical holdout 边界** | `yoyo/contracts/holdout.py` | 新增（原有 11 处定义） |
| **canonical PatternEvent** | `yoyo/contracts/pattern.py` | 新增（桥接两套存储 schema） |
| **CandidateProposal + Teacher 闸门** | `yoyo/contracts/candidates.py`、`yoyo/layers/l1_detection/teacher/` | 新增 |

## 只登记不迁移（REFERENCE_ONLY）

- 3060 上 59 个检测权重（`host://windows-3060/C:/fable`）
- `yoyo-trading/manifests/legacy_label_migration_v3.jsonl`（2.4 MiB 逐行审计）
- darkforest-one 的 pydantic fail-closed 配置（绑死 Python 3.11）

## 明确拒绝（REJECT）

第二套 package 根、重复 CLI、重复 ETH canonical 数据、未接入的 paper 外壳、
已被替代的 bbox-only CLI、旧 outcome 支线、`configs/source_repo.json` 跨仓指针、
egg-info 与 uv.lock、竞争性的 AGENTS/CLAUDE/HANDOFF。

## 本次发现的问题（都已处理或已上报）

1. **fable-trading 此前跑不起来，除非 `~/yoyo-trading` 在磁盘上。** 63 个文件
   import `yoyo.*`，靠 editable 安装指向仓外。已整包迁回，55/55 字节一致。
2. **半迁移与全迁移长得一模一样。** editable finder 排在 PathFinder 之后，本地缺
   哪个子模块就静默落回另一个仓。已加 provenance 守门测试。
3. **35 个脚本一直在 import 另一个仓的 `yoyo`。** 跨仓桥把 yoyo-trading 插在
   sys.path 第一位。全部删除 + 两道 AST 防回归。
4. **`tools` 是隐式命名空间包，被 yoyo-trading 的同名 regular package 抢走。**
   单文件跑绿、全量跑红。已加 `__init__.py` + 测试。
5. **holdout 边界有 11 处定义、6 个名字。** 已建 canonical + 逐处比对测试。
6. **两个 ATR 实现不一致**（warmup 播种，bar 14 差 0.109，200 根后耗尽）。
   ATR 定义 TP/SL 障碍距离。**已量化钉住，需 owner 裁决**——见
   `docs/consolidation/DUPLICATE_SEMANTICS.md` §4。
7. **任务书 §8.1 的四条 darkforest-one 结论没有发生。** 已按真实 source commit 更正。

## 提交

- `4f9203c consolidation(c0): add source repository snapshot tool`
- `813cd75 consolidation(c0): add test-run recorder for baseline/final comparison`
- `595821d consolidation(c0): point safety hashes at the paths that actually exist`
- `e9b4d73 consolidation(c0): freeze source repository state`
- `8082deb consolidation(c1): add the port tool that writes the migration ledger`
- `e01c61a consolidation(c1): establish canonical governance and bring yoyo home`
- `87f13da consolidation(c2): unify gold annotation and causal onset workflow`
- `6ff7c74 consolidation(c3): port causal numeric baselines and evaluation gates`
- `13b3038 consolidation(c4): register pattern teacher and isolate proposal semantics`
- `8281599 consolidation(c5): cut the cross-repo bridges and inventory duplicate semantics`
- `fdc1f57 consolidation(c6): add the generator for the source asset registry`
- `6ad1c2b consolidation(c6): register historical and negative research results`
- `39e2870 consolidation(c7): add the acceptance checker`

## 仍存在的风险

- **ATR 分歧未裁决**（见上第 6 条）。当前两套并存，差异已钉住。
- **11 个先存测试失败**：本 worktree 缺少 gitignore 掉的 `data/` 产物，不是代码缺陷。
  C7 比对的是失败**集合**，与 C0 完全一致。
- **`yoyo/layers/l1_detection/data.py` 保留一个硬编码绝对路径**
  （`/Users/zhangzc/Documents/Codex/...` 的旧 kline 缓存）。字节一致迁移的代价；
  改它会破坏与来源仓的字节校验。
- **7 个文件哈希实现、3 个 MA 实现未合并**：实测一致且已钉住，合并留待专门一轮。
- **venv 里 yoyo-trading 的 editable 安装未卸载**（不动 owner 环境）。
  测试保证即使它还在，跑的也是本仓的 `yoyo/`。彻底清除：`pip uninstall yoyo-trading`。
- **归档 commit 尚未执行**：按任务书 §9 C7.5，需先推送并形成 PR。

## 下一条允许的动作

**只有 P0（owner 形态定义与重复标注稳定性）→ P1（Gold Dataset）。**

在 P0/P1 通过前禁止：新 YOLO 训练、新 Onset 训练、新 LightGBM 训练、多周期扩展、
强化学习、仓位优化、执行层重构、模型 promote、实盘替换。
