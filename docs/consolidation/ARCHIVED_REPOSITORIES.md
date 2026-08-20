# 四个来源仓的归档状态

> **✅ 已执行（2026-08-20）。** PR #1 合并（merge commit `31d6b2a`）后，
> 四个来源仓的 README 顶部已加只读声明并推送到各自 main。
> **一个仓都没删** —— 台账里多条 `REFERENCE_ONLY` 按 commit + SHA 指回它们。

冻结 SHA 见 `reports/consolidation/source_repo_snapshots.json`。

| 仓库 | 冻结 SHA | 最终状态 | 归档 commit（已推送） |
|---|---|---|---|
| `darkforest-x/darkforest-one` | `fd36dd1adc5844f241122c3853eb4d3e675a9c11` | `superseded` | `e6e5164` ✅ |
| `darkforest-x/yolo-xx` | `9296cfa8e5053d86cea44e29dbd45874c3dff689` | `historical_research` | `be1c7bb` ✅ |
| `darkforest-x/yoyo-trading` | `784766de45a3b876c986d3ba672779124b46a66f` | `superseded` | `a16f495` ✅ |
| `darkforest-x/yoyo-eth` | `6147810afb46be1c664128e9a5359e8e7d0a3923` | `closed_negative` | `06b97d5` ✅ |

## 状态用词为什么和任务书不同

任务书 §2.2 把 `darkforest-one` 标为 `closed_negative`，依据是 "P2 经济门失败 /
accepted candidate OOS 净收益为负 / 输给 matched controls / P3 被阻塞"。

**这四条在该仓库里都没有发生。** 在冻结 SHA `fd36dd1` 上核验（main 与两个 remote
agent 分支全查）：

- `ROADMAP.md` 的 P2 六个条目**一个都没勾**
- `reports/generated/` 只有 `.gitkeep`
- 全仓没有 LightGBM、标签、walk-forward 代码

真实状态是 **P1 完成、P2 从未开始**，所以状态记为 `superseded`（工程能力被本次收敛
吸收，研究线未产生结论），不是 `closed_negative`。任务书 §8.1 自己写了
"若实际最新结果不同，以真实 source commit 为准"。

把没发生的失败写进实验注册表，等于往永久实验日志里注入四条伪造结论——
比留白危险得多。

## 归档 README 模板

四个仓各自的 README 顶部加：

```markdown
# ARCHIVED

This repository has been consolidated into `darkforest-x/fable-trading`.

- Canonical repository: `darkforest-x/fable-trading`
- Source frozen commit: `<sha>`
- Migration commit / PR: `<sha-or-url>`
- Canonical module: `<path>`
- Status: read-only historical research

No further development occurs in this repository.
```

各仓的 `<path>` 填：

| 仓库 | canonical module |
|---|---|
| `darkforest-one` | `yoyo/evaluation/`、`yoyo/data/`、`experiments/historical/darkforest_one/` |
| `yolo-xx` | `yoyo/layers/l1_detection/`、`tools/review/`、`experiments/historical/yolo_xx/` |
| `yoyo-trading` | `yoyo/`（整包）、`datasets/`、`experiments/historical/yoyo_trading/` |
| `yoyo-eth` | `yoyo/layers/l1_detection/numeric_baseline/`、`yoyo/evaluation/`、`experiments/historical/yoyo_eth/` |

## 归档前必须解掉的外部引用

- `yoyo-trading/configs/source_repo.json` 把 fable-trading 的绝对路径当只读数据源。
  收敛后这个指针反向了：本仓自己就是数据源。
  `tests/boundaries/test_yoyo_package_is_local.py` 禁止 `yoyo/` 里再出现该文件的引用。
- fable-trading 的 venv 里有 `yoyo-trading` 的 editable 安装
  （`__editable__.yoyo_trading-0.1.0.pth`）。本次不动 owner 的环境；
  `tests/boundaries/test_yoyo_package_is_local.py::test_the_imported_yoyo_lives_in_this_repository`
  保证即使它还在，跑的也是本仓的 `yoyo/`。想彻底清掉：`pip uninstall yoyo-trading`。
