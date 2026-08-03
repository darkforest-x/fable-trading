# P0 Runtime Parity 审计（2026-08-03）

## 直接裁决

**REJECTED：当前 `models/ACTIVE` 不是 2026-07-30 研究优胜配置，研究结论不得转移。**

ACTIVE 是 28 特征、`best_iteration=1`、固定 q90 阈值的冻结 v10；研究参考是同一 wide 池起源上经 Kronos join 后的 18,255 行、47 特征、每折固定 250 轮的 CPCV arm。后者没有冻结为一个可部署模型，因此没有可绑定的 model SHA。共同的 regression 目标不能抵消 feature schema、训练轮数、selector 和模型身份的不一致。

机器可读证据：`analysis/output/p0_runtime_parity_audit_20260803.json`。

## 证据边界

- 仓库：`main`，代码审计 HEAD `969dda71f5eb090c44c439f255496be0985b7531`。
- `models/active_bundle.json` 不存在；未激活示例 bundle。
- 未解析或评分 `signal_time >= 2026-05-04`；P0 收益审计使用的数据最大时间为 `2026-05-03 05:15:00+00:00`。
- 本报告只读取 sidecar、既有 pre-holdout 审计 JSON、源码配置和文件 SHA；没有训练、调参或新收益实验。
- VPS 已退租，本地无远端 service、ledger reconciliation、kill switch、账户、仓位或订单状态证据。

## Parity matrix

| 维度 | 当前 ACTIVE v10 | 研究参考 BASE 28+19 | 是否同构 |
|---|---|---|---|
| candidate pool | wide → freeze copy，18,379 行 | wide + Kronos join，18,255 行 | **否**，同源但行集不同 |
| side | short | short | 是 |
| objective | regression | regression | 是 |
| target | `net_barrier_taker`（sidecar 写作 realized_ret） | `net_barrier_taker` | 是，名称需规范化 |
| feature count | 28 | 47（28 + 19 causal alpha） | **否** |
| feature semantics | `legacy_unaligned` | legacy base + research alpha schema | **否** |
| rounds / early stopping | `best_iteration=1` | 每折固定 250 轮，无 early-stop metric | **否** |
| selector | 固定阈值、`>=`、pass 91.13%、equal 86.16% | 每折 q90，约 10%，边界 tie 0 | **否** |
| return/cost | target 已是 net taker；旧报告路由曾含糊 | 直接评估 net taker | 仅 target 同 |
| model identity | SHA `4ab5ab98af492e4b29a97be5b590990a9f3bdea3493955d5b6db400b5a541871` | 无单一冻结模型 | **否** |
| execution status | legacy / audit-only | research-only arm | 都不可执行 |

研究参考的历史结果为顶档提升 `+23.49bp`、14/15 折为正；这是既有 `analysis/output/diag_kronos_feature_value.json` 的历史证据，不是本轮重跑结果，也不能归属于 ACTIVE。

## H1–H7 核验

| 假说 | 完整仓库基线 | P0 处置 | 可核验证据 |
|---|---|---|---|
| H1 short 静默写 long | **confirmed** | fail-closed；side/protocol 进 identity，short/missing/unknown/mismatch client 0 调用 | `test_executor_side_guard.py` |
| H2 ACTIVE 不是研究优胜模型 | **confirmed** | parity rejected；不 promote、不转移收益结论 | 本报告与 JSON matrix |
| H3 q90 名称不等于实际十分位 | **confirmed** | 协议记录 operator/tie/pass/equal；异常 selector 不可 execution eligible | pass 0.9113408，equal 0.8615719 |
| H4 收益/成本语义混用 | **confirmed** | 显式 gross/net taker/net maker；换路先还原 gross；拒绝双扣 | `p0_return_semantics_20260803.json` |
| H5 生产 artifact 无单一权威 | **confirmed** | production 只认 exact bundle + 三哈希；无 bundle 即拒绝，不 latest fallback | `src/judgment/protocol.py` |
| H6 signal/decision/request/fill 混一 | **confirmed** | actual fill 只能来自 decision 后 paper open 或 broker ledger；无 fill 无 actual PnL | `test_execution_timeline.py` |
| H7 局部窗口可映射全局 tip-3 | **confirmed** | 最终全局 age `<=2`；局部/global reject 分开计数 | `test_global_tip_age_gate.py` |

## 收益与成本重算表

冻结 v10 val 仅作 pre-holdout 语义审计，`n=3,677`。没有改阈值、成本或 return convention。

| 集合 | gross | net taker（已含 10bp） | 正确 net maker | 历史错误：net taker 再减 6bp |
|---|---:|---:|---:|---:|
| `score >= threshold` | -1.5554bp | -11.5554bp | -7.5554bp | -17.5554bp |
| top 367 | +58.6178bp | +48.6178bp | +52.6178bp | +42.6178bp |

唯一合法的 taker→maker 路由是：

```text
gross = net_taker + 10bp
net_maker = gross - 6bp
          = net_taker + 10bp - 6bp
```

历史 `net_taker - 6bp` 被标为 superseded-for-return-cost-route-only；旧脚本保留，不改写历史结果。

## Artifact 与保护对象

| 对象 | SHA256 / 状态 |
|---|---|
| `models/ACTIVE` | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef`，与 P0.0 相同 |
| active model | `4ab5ab98af492e4b29a97be5b590990a9f3bdea3493955d5b6db400b5a541871` |
| active sidecar | `31170d758678d1ab950b65d62b380b5319170bc29259e2441319fa86f69ead68` |
| detector | `86d969c830189b2d1048dca24e10bacc27341e75643cf4f7a912e5a8d5542ad9` |
| freeze dataset | `9bca68023d5afd8687753cd42ca17865b3080077474bfc216a350ac3d22b5a94` |
| `data/forward_log.csv` | `6035eb60482481fb60d7e73aa72dd15d1b8884ee4c2da5410fbffa18b17b34bb`，与 P0.0 相同 |
| `data/executor_ledger.jsonl` | `de85b3dded80717a1bc0399411c6fc59c2f11842095aac2e105b0d128941fe39`，与 P0.0 相同 |

## 复现命令

```bash
PYTHONPATH=. python3 scripts/audit_l2_v10_return_semantics_20260803.py
PYTHONPATH=. python3 scripts/audit_p0_runtime_parity_20260803.py
PYTHONPATH=. python3 -m pytest -q \
  tests/test_active_bundle_protocol.py \
  tests/test_return_cost_contract.py \
  tests/test_execution_timeline.py \
  tests/test_global_tip_age_gate.py
```

## 风险与诚实声明

- parity rejected 不等于 ACTIVE 收益为负；它只证明不能把另一配置的历史 lift 归给 ACTIVE。
- 研究参考没有部署模型 SHA，所以不可能仅靠当前仓库把它升级成 exact bundle。
- 当前 v10 walkforward 并非全折净收益为正，且 selector 大规模并列；它只能是 audit evidence。
- 没有 VPS 或 broker fill 样本，P0 只验证因果记录协议，不证明真实成交质量、滑点、资金费或 short executor 正确性。
- 本轮没有新增 AUC、置换 p 或匹配随机对照实验；这些指标对 P0 safety parity 不适用。表中历史研究数字只作身份对比。
