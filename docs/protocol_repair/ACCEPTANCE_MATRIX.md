# 验收与测试矩阵

**落盘自** Notion《Grok Build 接管计划》04 页。Notion 为权威版本。

**原则**:P0 只有"协议正确 / 错误"判断,**不以收益指标代替正确性**。
**通过定义**:所有 Must 项通过;Should 项若未完成必须有明确阻塞和后续阶段。

状态列是 2026-08-03 落盘时的实测,不是原文的一部分。

---

## 1. 方向与执行安全

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| A-01 | Must | short protocol candidate | forward row `side == "short"` | ✅ |
| A-02 | Must | short row 进 long-only executor | `skipped_unsupported_side`;client 0 调用 | ✅ |
| A-03 | Must | missing/NaN/empty side | production 拒绝,**不默认 long** | ✅ `61b4dc3` |
| A-04 | Must | unknown side | 拒绝且只记一次 ledger | ✅ |
| A-05 | Must | strategy side 与 row side 不一致 | protocol mismatch,拒绝 | ❌ 无 protocol 对象 |
| A-06 | Must | current legacy v10 bundle | `execution_eligible == false` | ❌ 无 bundle |
| A-07 | Must | short signal | 不得出现 market buy 调用 | ✅ |

## 2. Signal identity 与幂等

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| B-01 | Must | 同 source/symbol/time/side/protocol,score 改变 | signal key 不变 | ✅ |
| B-02 | Must | model hash 改变但同 protocol event | 不重复下单;变更作为审计字段 | ⚠ 未测 |
| B-03 | Must | side 不同 | key 不同 | ❌ key 尚未含 side |
| B-04 | Must | protocol version 不同 | key 不同,账本不混 | ❌ key 尚未含 protocol |
| B-05 | Must | open row 后续 outcome 更新 | 保留首次 detected/decision/model identity | ⚠ 未测 |

## 3. Active bundle 与 artifact 身份

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| C-01 | Must | 完整合法 fixture bundle | 成功加载 | ❌ |
| C-02 | Must | model SHA 被篡改 | 加载失败 | ❌ |
| C-03 | Must | dataset SHA 被篡改 | 加载失败 | ❌ |
| C-04 | Must | detector SHA 被篡改 | 加载失败 | ❌ |
| C-05 | Must | 缺 side / entry / return / cost | 加载失败 | ❌ |
| C-06 | Must | latest JSON 损坏 | production **不回退**旧 artifact | ❌ 仍会回退 |
| C-07 | Must | `models/ACTIVE` 与 bundle 不一致 | 只认预定单一权威 | ❌ |
| C-08 | Must | `execution_eligible=false` | executor / actionable loader 拒绝 | ❌ |
| C-09 | Should | mutable dataset 路径内容改变 | provenance health 报错 | ⚠ |

## 4. Feature semantics

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| D-01 | Must | short + `side_aligned_v1` | 调 side-aware extractor | ✅ `61b4dc3` |
| D-02 | Must | legacy v10 | 明确 `legacy_unaligned`,不得默认冒充 aligned | ✅ `61b4dc3` |
| D-03 | Must | execution eligible short | semantics 必须是已批准的 side-aligned schema | ❌ 无 bundle |
| D-04 | Must | 缺 feature semantics | bundle 加载失败 | ⚠ 缺失读作 legacy,未知值才报错 |
| D-05 | Should | offline/live 同一 frame/index | 28 维 vector 逐列一致 | ✅ 已用 14 行验证 |
| D-06 | Should | feature as-of | 变动 signal bar 后未来 rows 不影响 vector | ⚠ 未测 |

## 5. Canonical short barrier

固定 `entry=100`、`ATR=1`、TP5、SL2、horizon 72,至少覆盖:

| ID | 级 | 路径 | 预期 | 状态 |
|---|---|---|---|---|
| E-01 | Must | low 先到 95 | short TP | ⚠ |
| E-02 | Must | high 先到 102 | short SL | ⚠ |
| E-03 | Must | 同 bar low≤95 且 high≥102 | 保守 SL / `sl_ambiguous` | ⚠ |
| E-04 | Must | 72 bars 无触发 | timeout,按冻结 return convention | ⚠ |
| E-05 | Must | 只有 partial bars 无触发 | forward status open;不生成 full label | ⚠ |
| E-06 | Must | exact touch | `<=` / `>=` 一致命中 | ⚠ |
| E-07 | Must | gap 过 barrier | fill 规则显式且三路径一致 | ⚠ |
| E-08 | Must | 非正价格 / NaN ATR | 拒绝 | ⚠ |
| E-09 | Must | label vs forward closed case | outcome/offset/exit/return 一致 | ⚠ |
| E-10 | Must | TP/SL 参数来源 | 从 protocol 显式传入,不依赖 TP4 默认 | ❌ |

> P0 允许 return convention 仍处 Owner gate,但**不允许同一个 bundle 内存在两种公式**。

## 6. 时间可用性与 fill

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| F-01 | Must | signal bar 03:00–03:15,decision 03:20 | 03:15 next-open 不得成为 live actual fill | ❌ |
| F-02 | Must | decision 前已触 TP,decision 后未触 | 不得记为 live TP | ❌ |
| F-03 | Must | 无 fill | actual realized_ret 为空 / 不存在 | ❌ |
| F-04 | Must | paper next-open-after-decision | 选 decision 后第一根未来 open | ❌ |
| F-05 | Must | batch 扫多 symbol | 每候选有自身 `decision_at`,不共用 scan start | ❌ |
| F-06 | Must | old row | 标 legacy,不进新 protocol 100 笔 | ❌ |
| F-07 | Should | actual broker fill | `fill_at`/`fill_px` 来自 ledger,不来自 signal proxy | ❌ |

## 7. Tip-only

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| G-01 | Must | live start back=2 + box bar198 | 全局 tip-3 被拒绝 | ⚠ 未测 |
| G-02 | Must | global tip age 0/1/2 | 可通过 | ⚠ |
| G-03 | Must | local edge 合法但 global age>2 | 被最终 gate 拒绝 | ❌ |
| G-04 | Should | rejected counter | 区分 local edge reject 与 global age reject | ❌ |

## 8. Forward log 隔离

| ID | 级 | 场景 | 必须断言 | 状态 |
|---|---|---|---|---|
| H-01 | Must | 两个 protocol_version | summary 不混算 | ❌ |
| H-02 | Must | legacy long resolver row | 不计入 repaired short 100 笔 | ❌ |
| H-03 | Must | execution-ineligible row | 不进 actionable set | ❌ |
| H-04 | Must | candidate/scored 但未 fill | 可观察,但不计 closed trade | ⚠ |
| H-05 | Must | old schema 读入 | 不崩溃;明确 legacy status | ⚠ |
| H-06 | Must | P0 tests | 不写真实 `data/forward_log.csv` | ✅ |

## 9. Holdout 与治理

| ID | 级 | 检查 | 必须结果 | 状态 |
|---|---|---|---|---|
| I-01 | Must | P0 命令与测试 | 无 `--eval-holdout` / `--allow-holdout` | ✅ |
| I-02 | Must | P0 产物 | 无新 holdout metrics | ✅ |
| I-03 | Must | `models/ACTIVE` | 未修改 | ✅ |
| I-04 | Must | YOLO promote | 未执行 | ✅ |
| I-05 | Must | forward_log | 未清空/覆盖/合并迁移 | ✅ |
| I-06 | Must | VPS | 未 deploy/restart/下单 | ✅ |
| I-07 | Must | 分支 | 全部提交在 main,无新 branch/worktree | ✅ |

## 10. 旧测试修正要求

以下旧测试可能把错误主线固化为预期,必须审阅:
`test_forward_runtime_policy.py` / `test_exit_parity.py` /
`test_tip_realtime_path.py` / `test_executor_side_guard.py`

**修正原则**:不删除有价值的 long legacy 测试,将其显式标为 legacy long contract;
新增 short production contract 测试;**不通过简单改 expected 值让测试变绿,要先说明协议身份**。

> 2026-08-03:`test_executor_side_guard.py` 已按此原则改 —— 原先断言
> `None/NaN/"" → "long"` 的用例是在断言 fail-open 本身,已替换为其反面;
> `test_tiered_sizing.py` 的 fixture 改为显式写 `side="long"`,因为那些用例测的是分档仓位,
> 此前是在依赖一个已不存在的默认值。其余三个尚未审阅。

## 11. 报告验收

`analysis/p0_safety_protocol_repair_*.md` 必须含:基线 SHA 与 tests;每个 defect 的修复路径;
active bundle schema;legacy v10 的 execution eligibility 判定;targeted 与 full test 结果;
数据/holdout/ACTIVE/deploy 安全声明;old forward evidence 处理建议;未完成事项;
Owner 下一决策;HTML 已生成。

---

## 落盘时汇总

```
Must 项  ✅ 已过 14   ❌ 未达 24   ⚠ 未测/部分 13
```

**九组里第 9 组(治理)全绿,第 1、4 组基本绿,第 3、6、8 组基本全红** ——
后三组都依赖同一件未做的事:**P0.2 的精确 bundle 机制**。
在它落地前,C 组(artifact 身份)、F 组(时间/fill)、H 组(forward log 隔离)无法开始。
