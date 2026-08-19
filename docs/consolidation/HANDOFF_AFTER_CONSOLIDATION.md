# 收敛之后的交接

> 给下一个会话/模型。读完这页 + `HANDOFF.md` 顶部就够开工。

## 一句话

五个仓收敛成一个。`fable-trading` 是唯一 ACTIVE；四个来源仓的有效能力已回迁，
待 PR 形成后转只读归档。**ACTIVE 指针、forward log、成本合同、部署配置一位没动。**

## 你需要知道的三件事

### 1. 包名是 `yoyo`，不是 `src`

```
yoyo/contracts/     跨层合同：costs / outcomes / protocol / holdout / pattern / candidates / artifacts
yoyo/data/          bars / indicators / loader / universe / continuity
yoyo/layers/l1_detection/    render / data / candidates / scan / onset / numeric_baseline / teacher
yoyo/layers/l2_judgment/     features / labeling / train / frozen
yoyo/layers/l3_backtest/     （空）
yoyo/layers/l4_execution/    config / executor / ledger / okx_client / symbols
yoyo/evaluation/    walk_forward / matched_controls / permutation / economic_gates
yoyo/artifacts/     registry / lineage
```

`src/` 下 23 个模块是**转发壳**，旧 import 全部仍可用，
由 `tests/boundaries/test_legacy_shims_forward.py` 保证转发的是同一个对象。
**新代码写进 `yoyo/`。**

层之间禁止互相 import，只能经 `yoyo/contracts/` 和 `yoyo/data/`，
由 `tests/boundaries/test_layer_imports.py` 用 AST 强制。

### 2. 四道守门测试，别绕过

| 测试 | 防的是什么 |
|---|---|
| `test_yoyo_package_is_local.py` | `yoyo` 被解析到仓外（editable 安装静默借用另一个仓） |
| `test_no_cross_repository_bridges.py` | `sys.path` 里出现兄弟仓（35 个脚本曾经这么干） |
| `test_holdout_boundary_is_single_valued.py` | 11 处 holdout 定义中任何一处漂移 |
| `test_migration_ledger_parity.py` | 迁进来的文件被改动而没记账（131 项逐个重算哈希） |

### 3. 注册表是入口，不是装饰

```bash
python3 -c "
from yoyo.artifacts import load_registries
r = load_registries()
for e in r.experiments: print(e.status, e.experiment_id, '|', e.result[:70])
"
```

新实验必须注册到 `experiments/registry.yaml`；新产物必须注册到 `artifacts/registry.yaml`。
`production_eligible` / `training_eligible` 默认 false，且**本次收敛没有把任何一项设为 true**。

## 等 owner 决定的两件事

1. **ATR 分歧**（`docs/consolidation/DUPLICATE_SEMANTICS.md` §4）。
   两个实现 warmup 播种不同，bar 14 差 0.109，200 根后耗尽。ATR 定义 TP/SL 障碍距离。
   选项 A 保持现状 / B 统一到严格版（下游全部重算）/ C 只在新代码用严格版。
2. **归档 commit**。四个来源仓的 archive README 已备好
   （`docs/consolidation/ARCHIVED_REPOSITORIES.md` 有模板与逐仓 canonical module 对照），
   但任务书要求 PR 形成后才执行。**不删库。**

## 下一条允许的动作

**只有 P0 → P1**（见 [`../../ROADMAP.md`](../../ROADMAP.md)）。

禁止：新 YOLO / Onset / LightGBM 训练、多周期扩展、强化学习、仓位优化、
执行层重构、模型 promote、实盘替换、清空 forward log、开新仓、开新分支。

## 环境注意

venv 里还有 `yoyo-trading` 的 editable 安装（本次没动 owner 环境）。
从仓库根用 `python -m pytest tests` 跑，本仓的 `yoyo/` 会赢；
守门测试会在它没赢的时候直接红。彻底清除：`pip uninstall yoyo-trading`。
