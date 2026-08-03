# P1-DATA：pre-holdout immutable short L2 dataset 重建验收

**日期**：2026-08-03  
**执行边界**：P1.0 → P1.7；完成后停止，不进入 P2  
**机器裁决**：`analysis/output/p1_preholdout_dataset_rebuild_20260803.json`

## 直接裁决

**P1-DATA = accepted。** 已从冻结的 pre-holdout L1 proposal ledger 重建一份
research-only、content-addressed、fail-closed 的 short L2 dataset：

- dataset：`data/p1/p1_short_l2_preholdout_aade2a334448d644.csv`
- SHA256：`aade2a334448d6443e71fb0d3dbbfcf450390875ce60e1f800f6dbe9c855e93a`
- 行数：18,103；币种：230；signal 时间：2026-02-01 01:00 → 2026-05-03 05:15 UTC
- manifest：`data/p1/p1_short_l2_preholdout_aade2a334448d644.manifest.json`
- manifest SHA256：`53b8a07612dae667a184da38bf8e0a694aaae15a5fd240d5b13238da3e13d682`
- tracked manifest 副本：`analysis/output/p1_dataset_manifest_20260803.json`
- `training_eligible=true` 只表示该数据产物通过 P1 数据门，**不是训练授权**。

本轮未训练、未调 threshold、未读取 holdout OHLC、未创建 active bundle、未修改
`models/ACTIVE`、未部署、未访问交易 client、未下单。按总指令，P1.7 后立即停止。

## P0 独立门

进入 P1 前独立复核了 `4333fa7..fba6a65` 中实际九个 P0 commits、源码 diff、报告、
机器 JSON、HANDOFF、完整 `tests/` 和保护对象，不使用上一轮摘要替代证据。

| 门 | 结果 |
|---|---:|
| `p0_independent_acceptance` | `accepted` |
| `p1_entry_allowed` | `true` |
| P0 聚焦测试 | 133 passed |
| 当时完整项目测试 | 473 passed / 2 skipped / 0 failed / 0 deselected |
| P0 九 commit parent chain | 完整 |
| ACTIVE / forward log / ledger | P0.0 与验收时 SHA 不变 |
| active bundle | 不存在，production fail-closed |

P0 机器结论 SHA256：
`90d402eafee0deb6a6937d33dd0035fec2c13728953795faee32ee25f31600e7`。

## P1.0 → P1.7 执行矩阵

| 阶段 | 实际交付 | 机器门 |
|---|---|---:|
| P1.0 | 环境、detector、344 币 universe、344 份 raw prefix、proposal ledger、保护对象快照 | accepted |
| P1.1 | 固定 dataset schema、28-feature schema、protocol、manifest 与 fail-closed loader | accepted |
| P1.2 | 共用 live box→signal mapper；next-open、TP5/SL2/72、same-bar SL、linear short、canonical taker cost | accepted |
| P1.3 | synthetic fixture；两个真实币小样本双重 replay；array↔PNG transport parity | accepted |
| P1.4 | 18,379 个冻结 proposal exact-window full replay；344 universe 全部记账 | accepted |
| P1.5 | schema、时间、feature、label、cost、event overlap、拒绝与数量守恒审计 | accepted |
| P1.6 | content-addressed manifest + explicit-path loader 复读 | accepted |
| P1.7 | MD、HTML、JSON、manifest、hash、tests、HANDOFF、commit list；停止 | accepted |

## 冻结输入与唯一权威

| 输入 | 事实 |
|---|---|
| detector | `models/owner_short_star_v10.pt` |
| detector SHA256 | `86d969c830189b2d1048dca24e10bacc27341e75643cf4f7a912e5a8d5542ad9` |
| threshold | 固定 `conf=0.30`，未扫描、未修改 |
| live universe | 344 个 OKX 15m non-stockish `*_USDT_SWAP` |
| raw pre-holdout prefix | 344 份；combined SHA `6bcc35c10b630bc0d3d97ebea9a9ec29ca9a58314c46c59b10307fb088e2eb3e` |
| L1 proposal source | `data/judgment_v10_wide.csv` |
| proposal SHA256 | `d672f8da37dbb09d9dde7fbf66da31e548f7dc37ba8601920ab1d23802050c4a` |
| proposal coverage | 18,379 行 / 232 币 / 0 holdout 行；key 无重复；同币间隔至少 18 bars |
| cutoff | `2026-05-04T00:00:00Z`，严格 `< cutoff` |

这里有两个不能混用的集合：344 币是**允许的 current live universe**；18,379 条 / 232
币是**冻结的 L1 proposal source**。P1 是 L2 重建，只消费后者的 exact causal windows；
不会为另外 112 个零 proposal 币重扫历史负窗口并发明新 L1 candidate。

## Dataset schema 与协议

manifest 固定完整列顺序及 schema SHA
`9dd279337baba1d0cf3873c270004f59765335f5d3c992b10f98f824a4183fd0`。核心协议为：

- `source=okx`、`timeframe=15m`、`side=short`；
- box 右缘经 live 共用 pure mapper 落到信号 bar，local tip-edge=2，最终 global tip age ≤2；
- 28 个 `judgment_28_v1` 特征按 `side_aligned_v1` 固定顺序，在 mapped signal bar 截止；
- research entry 为下一根 bar open；
- signal-bar ATR14，short TP=5×ATR、SL=2×ATR、horizon=72 bars；
- 同 bar 同时触及 TP/SL 时保守记 SL；
- `gross_ret = 1 - exit/entry`；禁止 inverse short return；
- 仅保留 `gross_ret`、`fee_swap_taker`、`net_ret_swap_taker` 三个成本字段；
  canonical taker 往返成本 0.001 只扣一次。

## Fixture 与真实 dry-run

### Fixture

4 条 synthetic path 覆盖 TP、SL、timeout、same-bar `sl_ambiguous`；tip、tip-1、tip-2
接受且 tip-3 拒绝；28 feature 顺序与 short 方向变换通过；cutoff boundary 的 OHLC 未转换；
两次 CSV SHA 同为：
`57ef921b81a17dafc6d4f6e594f855be855611f066715c6e5c193633ac312454`。

### 真实 dry-run

在 `0G_USDT_SWAP`、`1INCH_USDT_SWAP` 各取 4 个真实 proposal，重复完整 replay：

| 项 | 结果 |
|---|---:|
| 两次行数 | 8 / 8 |
| 两次 SHA | `807f01a8e631a0a5b4f303762c7f9a435aa05fdde90f5b6ca251921b2209b71e` |
| array↔PNG box count/coords/conf | 完全相等，max delta=0 |
| device | MPS；torch/opencv native threads 固定为 1 |
| post-cutoff OHLC materialized | 0 |
| holdout rows read | 0 |

## Full build 数据统计

| 项 | 冻结 proposal ledger | P1 immutable dataset | 解释 |
|---|---:|---:|---|
| 行数 | 18,379 | 18,103 | 274 无 current selected box + 2 canonical outcome reject |
| 有 proposal / 有最终行的币 | 232 | 230 | `AI`、`USAR` 各一条 proposal，最终无行 |
| current universe 记账 | 344 | 344 | 112 个零 proposal 币不读 K 线，但写零 shard |
| 时间范围 | 02-01 01:00 → 05-03 05:15 | 相同 | 全部 `< 2026-05-04` |
| detector windows | 18,379 | 18,379 | 每个 source proposal 只重放 exact window |
| dataset bytes | — | 26,014,740 | content-addressed CSV，`data/` 不入 git |

端到端守恒：

```text
18,103 dataset rows
+ 274 source proposals without selected candidate
+   2 canonical row rejects
+   0 duplicate mapped signals
= 18,379 frozen source proposals
```

两条 row reject 都是 `outcome_contract:short TP price must remain positive`，没有放宽障碍、
ATR floor 或价格规则。检测映射层另外累计记录 1 个 `local_tip_edge` box reject、19 个
`series_bounds` box reject；它们是 box 级诊断，不能与 274 个 proposal 级缺口直接相加。

274/18,379（1.49%）proposal 在当前冻结 MPS 环境没有产生 selected candidate，18,105
（98.51%）产生；这些差异没有用调低 threshold、换 detector 或补扫邻窗掩盖。manifest
固定当前环境与输入，跨设备全量逐框等价性没有被夸大。

## 标签、收益与数据质量

| 指标 | 结果 |
|---|---:|
| `label_tp_before_sl=1` | 4,533 / 18,103 = 25.04% |
| exit TP / SL / same-bar SL / timeout | 4,533 / 11,850 / 2 / 1,718 |
| gross mean | +4.08 bp |
| taker-net mean | -5.92 bp |
| taker-net > 0 | 5,923 / 18,103 = 32.72% |
| detector confidence min / median / max | 0.300024 / 0.515101 / 0.949991 |
| global tip age 0 / 1 / 2 | 18,101 / 2 / 0 |
| event groups | 15,604；4,700 rows 位于重叠组 |
| 28 features missing / inf / constant | 0 / 0 / 0 |
| duplicate candidate / event id | 0 / 0 |
| feature after signal / source index after signal | 0 / 0 |
| entry not after signal | 0 |
| cost identity failures | 0 |
| data-quality flagged rows | 0 |
| holdout signal / post-cutoff OHLC materialized | 0 / 0 |

这些是 dataset 描述统计，不是策略成功宣称。负的全池 taker-net 均值也再次说明 P1 只完成
数据层；没有 selector 或经济验收，不得据此训练、promote 或进入生产。

## Immutable 与 fail-closed consumer

full assembly 对同一排序结果写两次，两个 dataset SHA 完全相同。最终路径包含内容哈希；
manifest 固定 dataset SHA/size/row count、完整列顺序、feature schema、协议、detector、raw
prefix、proposal source、source commit/hash、fixture/dry hashes 和安全声明。

`load_immutable_dataset(manifest_path)` 必须显式给 manifest，没有 `latest` fallback；它会在
返回 DataFrame 前校验 manifest eligibility、dataset bytes、schema、protocol、row count 与
cutoff。独立复读结果为 18,103 行、dataset SHA 与 manifest SHA 均匹配。

## 测试

```text
P1/candidate/cutoff/cost 相关测试：29 passed
完整项目 tests/：488 passed, 2 skipped, 0 failed, 0 deselected
```

两项 skip 与 P0 相同，均为 `tests/test_factor_causality.py` 中已注明的 pandas rolling
伪差/慢脆弱用例。另一次从 repository root 直接执行 `pytest -q` 在收集 vendored
`external/Kronos` 时出现 2 个可选依赖错误：缺 `qlib`、上游相对 `model` 导入不可解析。
没有安装依赖或 deselect 来美化结果；项目规范测试入口 `pytest -q tests` 全部通过。

机器测试结果：`analysis/output/p1_test_results_20260803.json`。

## 范围偏差与修正记录

full 初次实现曾把“full build”解释成扫描 344 币的全部历史窗口，首个 CPU shard 8,834
windows 耗时 726.3 秒；随后两个 MPS partition 各完成一个 shard。复核 P1.0 输入角色后确认
这是 L1 proposal mining，不是 L2 rebuild，立即中止，没有发布 dataset 或 manifest。

三个不可消费 staging 目录仍保留为审计证据：

- `data/p1/_staging/p1_20260803_335fbebc9ddcb6cb`
- `data/p1/_staging/p1_20260803_d94d98d1c7a0df25`
- 正式 accepted：`data/p1/_staging/p1_20260803_bdfb72ffafec973b`

修正后的 full 只跑 18,379 个 proposal windows，809.4 秒完成。对应 learning：
`docs/learnings/l2-rebuilds-must-not-expand-into-l1-proposal-mining.md`。

另一个已解决的非平凡问题是 macOS/arm64 native thread 初始化和 Ultralytics 单元素 list
source 的 SIGSEGV；修复只固定执行调度/输入 wrapper，未改模型、图片、threshold 或协议，
并由双跑哈希与 array↔PNG parity 约束。对应 learning：
`docs/learnings/native-thread-pools-must-be-frozen-before-detector-data-libraries.md`。

## 研究指标适用性

P1 没有训练、val split、threshold selection 或策略比较，因此项目常规报告中的 val AUC、
置换检验 p、top-decile 毛/净收益、单特征 baseline 和匹配随机对照全部为 **N/A**。本报告
只给 dataset 全池描述统计；不能把它解释为模型或交易策略验收。

## 保护对象

| 对象 | P1.0 SHA256 | P1 full 后 SHA256 | 结果 |
|---|---|---|---|
| `models/ACTIVE` | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef` | 同左 | unchanged |
| `data/forward_log.csv` | `6035eb60482481fb60d7e73aa72dd15d1b8884ee4c2da5410fbffa18b17b34bb` | 同左 | unchanged |
| `data/executor_ledger.jsonl` | `de85b3dded80717a1bc0399411c6fc59c2f11842095aac2e105b0d128941fe39` | 同左 | unchanged |

`models/active_bundle.json` 始终不存在；没有创建 active bundle。

## Commit 列表

| commit | 内容 |
|---|---|
| `524e092` | 独立接受 P0 safety gate |
| `9d511d4` | 冻结 P1 数据输入、detector、universe 与保护对象 |
| `4edba4d` | immutable short dataset contract / schema / loader |
| `a222380` | 共用 live box→signal pure mapper |
| `dfd71e2` | 区分 detector eval ruler 与 live universe authority |
| `86776c2` | fixture/dry/full gated replay 基础实现 |
| `9023d3a` | 接受 fixture 与真实 dry-run |
| `6b1438a` | 初版 per-symbol checkpoint（随后范围修正） |
| `e593e24` | 接受 deterministic MPS dry-run |
| `8caa4f7` | 修正 L2/L1 边界，full 改为 proposal-led exact-window replay |
| `decc98e` | 在修正提交上重新接受 fixture/dry-run |
| `5aaf239` | 提交 full JSON、manifest 副本、hash 与测试结果 |

## 从零复现

精确复现 canonical dataset bytes 需要源码 commit
`decc98ef818503b4c28f3fe020c521e989e96766`、P1.0 manifest 中相同的本地 raw prefix 和同一
MPS/package 环境；dataset 的 `build_id` 有意绑定 source commit。

```bash
# P1.0：只做输入与保护对象快照
PYTHONPATH=. .venv/bin/python scripts/snapshot_p1_data_inputs_20260803.py

# P1.3：必须先通过 fixture 和真实双跑 dry-run
PYTHONPATH=. .venv/bin/python scripts/build_p1_preholdout_dataset_20260803.py fixture
PYTHONPATH=. .venv/bin/python scripts/build_p1_preholdout_dataset_20260803.py \
  dry-run --device mps --batch-size 8 --render-workers 4

# P1.4–P1.6：proposal-led full、audit、manifest、loader
PYTHONPATH=. .venv/bin/python scripts/build_p1_preholdout_dataset_20260803.py \
  full --device mps --batch-size 16 --render-workers 10
PYTHONPATH=. .venv/bin/pytest -q tests
shasum -a 256 -c analysis/output/p1_dataset_hashes_20260803.sha256

# P1.7 报告
python3 scripts/md_to_html.py analysis/p1_preholdout_dataset_rebuild_20260803.md \
  --out-dir analysis/html
PYTHONPATH=. .venv/bin/python scripts/gen_analysis_index.py
```

## 风险与诚实声明

- 274 个 source proposal 在当前 MPS environment 没有 selected candidate；这是显式、可审计
  的重放差异，不冒充 100% 跨设备复现。fixture/dry-run 证明样本 transport parity，未做
  CUDA 与 MPS 的 18,379-window 全量逐框对照。
- 两个 near-cutoff/极端价格候选因 canonical short TP 必须为正而拒绝；没有改 TP/SL/ATR。
- `data/` 按仓库纪律不进 git；CSV 由 content hash、tracked manifest 副本和 SHA 文件交付。
- Git 哈希和本地 ledger 不变能证明本轮没有持久化本地运行状态，不能证明外部世界没有他人
  人工操作；本轮没有访问 VPS、账户或密钥。
- `training_eligible=true` 不是 P2 授权。P1 已完成，必须停在这里。

## 最终安全声明

未训练；未调 threshold；未读取或评分 holdout；未创建 active bundle；未修改 ACTIVE；
未部署；未通知；未下单。**P1.7 完成后停止。**
