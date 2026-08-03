# 新旧路径对照(2026-08-03 重构)

owner 2026-08-03 决定重构为四层,项目更名 **yoyo**。

**这张表存在的理由**:`analysis/` 里 47 份报告、`docs/learnings/` 里 91 条笔记提到旧路径。
**那些文件一个字都不会改** —— 它们记录的是「当时发生了什么」,里面的路径就是当时真实的路径,
复现命令也是当时真实跑过的命令。改它们等于篡改实验日志(见 CLAUDE.md 易犯错清单)。
读旧文档遇到旧路径,来这里查对应。

## 迁移状态

| 层 | 目标目录 | 状态 |
|---|---|---|
| 契约 | `yoyo/contracts/` | **已迁** |
| L1 检测 | `yoyo/layers/l1_detection/` | 未开始 |
| L2 判断 | `yoyo/layers/l2_judgment/` | 未开始 |
| L3 回测 | `yoyo/layers/l3_backtest/` | 未开始 |
| L4 执行 | `yoyo/layers/l4_execution/` | **已迁** |
| 数据 | `yoyo/data/` | 未开始 |
| 看板 | `tools/dashboard/` | 未开始 |

迁移期 `src/` 下的模块是**转发壳**,旧 import 全部仍然可用,499+ 测试无需改动。
壳会在调用方全部切过去之后才删。

## 已完成的对应

| 旧 | 新 | 备注 |
|---|---|---|
| `src/judgment/protocol.py` | `yoyo/contracts/protocol.py` | 迁移时内联了 `file_sha256`,不再向 judgment 层借 |
| `src/judgment/outcomes.py` | `yoyo/contracts/outcomes.py` | |
| `src/costs.py` | `yoyo/contracts/costs.py` | |
| `src/judgment/forward_records.py` | `yoyo/contracts/forward_log.py` | **分层暴露出来的**:forward log 是 L2 写、L4 读的账本,属于契约而非任一层 |
| `src/judgment/forward_types.py` 的 schema 部分 | `yoyo/contracts/forward_log.py` | 列定义 / LEGACY 标记 / ForwardRecord / MergeResult;L2 私有的 ForwardScanInput 等留在原处 |
| `src/execution/*.py` (5 个 + `__main__`) | `yoyo/layers/l4_execution/` | 只依赖 contracts,零跨层 import |
| `src/notify.py` | `yoyo/notify.py` | L2/L4 共用工具,不是层 |
| `src/timefmt.py` | `yoyo/timefmt.py` | 同上 |

## 计划中的对应(尚未执行,写在这里是为了让人能预判)

| 旧 | 新 |
|---|---|
| `src/detection/render.py` | `yoyo/layers/l1_detection/render.py` —— **像素不得改动一位**,检测器绑死在这些像素上 |
| `src/detection/data.py` | `yoyo/layers/l1_detection/data.py` |
| `src/judgment/yolo_candidates.py` | `yoyo/layers/l1_detection/candidates.py` |
| `src/judgment/features.py` | `yoyo/layers/l2_judgment/features.py` |
| `src/judgment/frozen.py` | `yoyo/layers/l2_judgment/frozen.py` |
| `src/judgment/train.py` | `yoyo/layers/l2_judgment/train.py` |
| `src/judgment/forward*.py` | 拆分:候选发现 → L1,打分 → L2,结果解算 → contracts/outcomes |
| `src/backtest/run.py` | `yoyo/layers/l3_backtest/run.py` |
| `src/data/*.py` | `yoyo/data/` |
| `src/webapp/` | `tools/dashboard/` —— 观察工具,不是交易层 |
| `src/scout_mtf` `src/eth_micro` `src/short_tf` `src/factors` | `archive/sidequests/` —— 支线,均两周以上未动 |
| `scripts/` 253 个 | `archive/scripts/`,只把真正复用的提进 `yoyo/cli/` |

## 命名

旧仓库目录名仍是 `fable-trading`(git 历史连续,单分支纪律不允许另起仓库)。
**代码里的包名是 `yoyo`。** 文档里出现 "fable" 一律指旧结构或仓库目录本身。

`yoyo` 与 PyPI 的 `yoyo-migrations` 同名(其 import 名就是 `yoyo`)。当前 venv 未安装,
且 CLAUDE.md 禁止新增重型依赖,冲突只是理论上的 —— 记在这里免得半年后有人困惑。
