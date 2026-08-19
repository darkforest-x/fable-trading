# yoyo-trading — 分层契约 / Gold 标注 / Dataset V3（已归档）

| 项 | 值 |
|---|---|
| 来源仓 | `darkforest-x/yoyo-trading` |
| 冻结 commit | `784766de45a3b876c986d3ba672779124b46a66f` |
| 最终状态 | `superseded` |
| 机器摘要 | [`summary.json`](summary.json) |
| holdout | **1 次消耗**（下详） |

## 这个仓为什么存在，又为什么必须收回来

2026-08-03 owner 决定四层重构，`yoyo` 包在 commit `d472a05` 从 fable-trading
**搬出**到本仓，开发在这里继续。结果是 fable-trading 的 **63 个文件 import `yoyo.*`**，
靠一个 editable 安装指回来。

**即：ACTIVE 仓跑不起来，除非另一个仓在磁盘上。**
归档 yoyo-trading 而不先把 `yoyo/` 迁回，等于让 ACTIVE 仓失去自己的包。
所以 C1 把整包 55 个 `.py` 字节一致地迁了回去。

## 最重要的一条：冻结 ≠ 可训练

固定 W10 / Core4 / Confirm1 金标已 freeze：

| 项 | 值 |
|---|---|
| SIGNAL / NO_SIGNAL | **1,247 / 1,402** |
| 训练图 | 2,649（train 1,849 · val 350 · test 450） |
| manifest SHA | `20686feba41d15b82e34109402840c2d640fe1e2daea0392b35e1ea79320a7fc` |
| 排除 | CONFLICT 44、无核 A 未当正例、17 条竞争核 SIGNAL |
| **`training_eligible`** | **false** |

**为什么 false：协议 17.6 要求一个 DIRECT 抽检错误率，而迁移产出 DIRECT=0，
所以那个错误率是未知的。** 其余门全过。

一个「已冻结、错误率未测」的数据集，正是那种看起来准备好了、其实没有的东西。
freeze 是一个检查点，不是一张资格证。

## holdout 账本：1 次消耗

`exp-yoyo-trading-fixed-w10-classifier-holdout3d`

- 窗口：UTC 2026-08-10 00:00 – 08-13 12:00，26 个 USDT-SWAP（owner 亲自点名）
- 去重后 SIGNAL 126，已平仓 119
- maker 净 **+0.0453**、taker 净 **−0.0023**，胜率 **31.9%**
- **未 promote、未部署**

三天是试跑不是验收：**换个费率路由符号就翻**，n=119、单一 3 天窗口，
没有 walk-forward 也没有匹配对照。

`reuse_allowed: false` —— 这个窗口对这个配置已经烧掉了。
本次收敛**没有重读**它。

## 迁进来的东西

| 能力 | 落点 | 裁决 |
|---|---|---|
| **`yoyo/` 整包（55 个 .py）** | `yoyo/` | `DIRECT_PORT`，55/55 字节一致 |
| 层边界 AST 测试 | `tests/boundaries/test_layer_imports.py` | `ADAPT_AND_PORT`（扩充 + 具名债务） |
| Gold schema / box / render / legacy 迁移 | 随包迁入 `yoyo/datasets/` | `DIRECT_PORT` |
| Label Studio 界面与协议 | `configs/labelstudio/` | `DIRECT_PORT` |
| 标注工具链（任务生成 / 导出转换 / 审计） | `tools/review/` | `DIRECT_PORT` |
| Dataset manifest（8 份） | `datasets/manifests/` | `DIRECT_PORT` |

## 明确拒绝的

- `configs/source_repo.json` —— 跨仓只读指针，收敛后指向自己。
  已加测试禁止 `yoyo/` 再引用它。
- `yoyo_trading.egg-info/`、`uv.lock` —— 打包产物与另一个仓的锁文件。
- `manifests/legacy_label_migration_v3.jsonl`（2.4 MiB）—— `REFERENCE_ONLY`。
  2.6 KB 的 summary 已入库承载结论；来源仓只读归档不删除，逐行数据按 commit + SHA 仍可取回。
- 竞争性的 `AGENTS.md` / `CLAUDE.md` / `HANDOFF.md` —— 单仓只留一套当前真相。

## 一个被这次收敛发现的边界违规

`yoyo/contracts/protocol.py::runtime_artifact` import 了
`src.judgment.{features,frozen}` —— **契约层伸进了业务层**。
它是函数内 import，且当时的替代方案（读旧 JSON sidecar）会造出第二个阈值权威（故障 C-07），
所以是刻意的。但它仍然是越界，因此记进
`tests/boundaries/test_layer_imports.py::KNOWN_LEGACY_EDGES` 具名登记，
并配一个测试：等 `src/judgment` 真正迁完、这条边消失时，那个豁免项自己会红，
逼人删掉它——否则一个没人清理的豁免会继续替**新**的越界打掩护。
