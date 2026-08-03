# P0-SAFETY short 协议修复报告（2026-08-03）

## 直接裁决

**P0-SAFETY 本地验收通过；当前策略仍不可执行。**

- `models/active_bundle.json` 不存在，production 会在任何数据加载或交易 client 之前 fail-closed。
- `models/active_bundle.example.json` 只诚实描述 v10：`legacy_unaligned`、selector tie mass 异常、`live_entry_mode=none_until_p1`、`execution_eligible=false`、`paper_only=true`。
- v10 的实际状态是 **legacy / audit-only**，不是 execution-eligible paper/live bundle。
- P0 成功标准是错误协议无法下单，不是收益变好；P1/P2 未获授权且未开始。

## P0.0 基线事实

基线 HEAD `4333fa722b6a98fdaa8a36f37f1d468d43956b5f`，分支 `main`，当时 worktree clean 且与 `origin/main` 一致。基线产物位于 `analysis/output/p0_safety_baseline_20260803/`。

| 项目 | 基线事实 |
|---|---|
| ACTIVE | `models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.txt` |
| ACTIVE pointer SHA | `899c36259950a3d376067958ec040638253defa9ef545fa51af2a004f95bb6ef` |
| model SHA | `4ab5ab98af492e4b29a97be5b590990a9f3bdea3493955d5b6db400b5a541871` |
| sidecar SHA | `31170d758678d1ab950b65d62b380b5319170bc29259e2441319fa86f69ead68` |
| detector SHA | `86d969c830189b2d1048dca24e10bacc27341e75643cf4f7a912e5a8d5542ad9` |
| freeze dataset SHA | `9bca68023d5afd8687753cd42ca17865b3080077474bfc216a350ac3d22b5a94` |
| wide pool SHA | `d672f8da37dbb09d9dde7fbf66da31e548f7dc37ba8601920ab1d23802050c4a` |
| forward log | 存在，SHA `6035eb…34bb`；P0 前后未变 |
| executor ledger | 存在，SHA `de85b3…fe39`；P0 前后未变 |
| exact active bundle | 不存在；example 未激活 |
| VPS | 已退租；没有远端运行态证据 |
| 基线聚焦测试 | 61 passed |

P0.0 只对 data 文件采集存在性、大小和整文件 SHA；没有解析行、评分、作图或生成 holdout 指标。

## P0.0→P0.7 实施结果

| 阶段 | 结果 | 关键不变量 | commit |
|---|---|---|---|
| P0.0 | 完成基线快照 | 先取证再改代码 | `95ebfb0` |
| P0.1 | executor fail-closed | key=`source|symbol|signal_time|side|protocol`;不含 score/model | `cd9ca5a` |
| P0.2 | exact bundle | 必填字段 + model/dataset/detector SHA；production 不 fallback | `8cd2a56` |
| P0.3 | forward provenance | side/protocol/semantics/decision/hash 逐行传播；旧行独立 | `892964c` |
| P0.4 | feature contract | extractor 由 `feature_semantics` 选择；28 维快照与 as-of | `1cb669c` |
| P0.5 | canonical outcome/cost | label/forward 共用 resolver；TP5/SL2/72 显式；成本只扣一次 | `ee98ebd` |
| P0.6 | causal fill timeline | next-open 仅 research；无 fill 无 actual PnL | `8e90390` |
| P0.7 | tip/selector gates | 全局 tip age≤2；异常 selector 不可执行 | `969dda7` |

基线已有三个部分实现提交 `a34f87d`、`e6e1063`、`4333fa7`；本轮没有删除它们，而是在基线后按 P0.0→P0.7 顺序补齐并验证。

## H1–H7 证据与处置

| 假说 | 基线判定 | P0 最终状态 |
|---|---|---|
| H1 short→long/buy | confirmed | repaired：short/missing/NaN/unknown/mismatch 均拒绝，mock client 0 调用 |
| H2 ACTIVE ≠ research winner | confirmed | isolated：parity rejected，不转移 47-feature 历史收益，不 promote |
| H3 q90 实际放行异常 | confirmed | audited：pass 91.13%、equal 86.16%；v10 audit-only；eligible abnormal bundle 加载失败 |
| H4 return/cost 混用 | confirmed | repaired：gross↔net route 唯一，双扣 API/测试拒绝 |
| H5 无单一 artifact 权威 | confirmed | repaired：production 只认 exact bundle；当前因 bundle 缺失而诚实停机 |
| H6 时间点混一 | confirmed | repaired：signal/detection/decision/request/fill 分开；fill 后才有 actual outcome |
| H7 global tip-3 漏洞 | confirmed | repaired：所有局部候选最终按 whole-series tip age 再过滤 |

详表见 `analysis/p0_runtime_parity_audit_20260803.md` 与对应 JSON。

## Active bundle schema 与 fail-closed

协议对象显式承载以下类别，任一必填字段缺失即 `BundleError`：

- identity：`protocol_version`、`strategy_id`、side、timeframe、candidate source、tip age；
- features：schema、semantics、objective、iteration、score semantics；
- selector：threshold、operator、tie policy、calibration quantile/pass/equal、selector status；
- entry/outcome：research/live entry mode、TP/SL/horizon、same-bar、gap、return convention；
- return/cost：target column/semantics/cost included、reporting route；
- artifacts：detector/model/dataset path + SHA256；
- authority：`execution_eligible`、`paper_only`。

额外不变量：

- `legacy_unaligned` 永远不能 execution eligible；
- `paper_only && execution_eligible` 非法；
- abnormal selector 永远不能 execution eligible；
- short 不允许 `linear_long`；gross 与 `target_cost_included=true` 冲突；
- production bundle 的 `max_tip_age_bars` 不得大于 2；
- bundle absence、JSON 损坏、hash mismatch、语义矛盾均拒绝，不读取 `models/ACTIVE` 或 latest artifact 兜底。

## Canonical barrier / return / cost

labeling 与 fixed forward 共用 `src/judgment/outcomes.py`。协议显式传入 side、entry、ATR、TP5、SL2、72 bars、same-bar conservative SL、gap barrier-price idealization 和 return convention。

边界测试覆盖 short TP、SL、同 bar 双触发、timeout、partial open、exact touch、gap、非法价格/ATR、label-forward closed parity。P0 没有替 Owner 选择新的 return convention，只把 legacy v10 的 `linear_short = 1 - exit/entry` 如实写入 audit-only example。

成本审计确认冻结 target `net_barrier_taker` 已含 taker 成本。正确 maker 换路是 `net_taker + taker_cost - maker_cost`；历史 `net_taker - maker_cost` 是双扣。完整重算表见 runtime parity 报告和 `analysis/output/p0_return_semantics_20260803.json`。

## Signal / Decision / Fill

新 forward schema 区分 `signal_time`、`signal_closed_at`、`candidate_detected_at`、`decision_at`、`entry_requested_at`、`fill_at`、`fill_px` 和 `reference_px`。

- signal close 只能写 `reference_px`；不能写 entry/fill；
- research next-open outcome 写在 `research_*`；`realized_ret` 与 `actual_realized_ret` 在无 fill 时为空；
- paper fill 是严格晚于 decision 的第一根 future open；
- broker fill 必须由 ledger 显式提供 `fill_source=broker_ledger`、`fill_at`、`fill_px`；
- mid-bar broker fill 不使用包含该 fill 的不完整 OHLC high/low，避免纳入 fill 前极值；
- 看板、data hub、status strip 只计显式 `paper_filled|broker_filled` 且 actual PnL 完整的行。

## 测试结果

### 聚焦测试

阶段内聚焦结果依次为：P0.1 `19 passed`，P0.2 `116 passed`，P0.3 `119 passed`，P0.4 `67 passed`，P0.5 `102 passed`，P0.6 `159 passed`，P0.7 selector/tip `99 passed`。最终关键矩阵复核 `133 passed`。

### 全量安全测试

```text
PYTHONPATH=. python3 -m pytest -q tests
→ 472 passed, 2 skipped, 1 failed
```

唯一失败：`tests/test_eth3m_v2_classification.py::test_full_frame_transform_is_deterministic_and_uncropped`，本机未安装可选重型依赖 `torchvision`，报 `ModuleNotFoundError`；不是 P0 assertion failure。

当前环境可运行集合：

```text
PYTHONPATH=. python3 -m pytest -q tests \
  --deselect tests/test_eth3m_v2_classification.py::test_full_frame_transform_is_deterministic_and_uncropped
→ 472 passed, 2 skipped, 1 deselected, 0 failed
```

测试没有写真实 `data/forward_log.csv`，没有调用真实或 demo 交易 client。

## 数据统计与研究指标适用性

P0 是 safety/correctness 迁移，不是 ML 实验；没有训练、特征选择、threshold sweep 或新策略评分。因此 AGENTS.md 的 val AUC、置换 p、top-decile 净收益、胜率、单特征和匹配随机对照在本轮均 **N/A**，不能伪造为 P0 成绩。

唯一解析的数据审计是冻结 v10 pre-holdout val：`n=3,677`，最大 `signal_time=2026-05-03 05:15 UTC`。它只验证 threshold tie/pass rate 与 return/cost 路由，不用于选择配置。历史研究 parity 数字在另一报告中明确标为既有证据。

## Learnings

- `execution-signal-identity-needs-side-and-protocol.md`
- `an-optional-bundle-loader-is-not-a-production-authority.md`
- `provenance-must-be-injected-once-not-rediscovered-mid-scan.md`
- `trade-side-does-not-identify-model-feature-semantics.md`
- `barrier-outcomes-need-one-explicit-contract.md`
- `net-route-conversion-must-restore-gross-before-recosting.md`
- `a-price-after-the-signal-is-not-a-fill-unless-it-is-after-the-decision.md`
- `a-local-tip-edge-is-not-a-global-freshness-proof.md`
- `a-research-result-cannot-be-assigned-to-a-different-runtime-artifact.md`

H3 的 tie-mass 根因已由既有 `a-loose-gate-can-be-a-model-with-no-resolution.md` 覆盖，本轮没有复制同主题 learning。

## 复现命令

```bash
PYTHONPATH=. python3 scripts/audit_l2_v10_return_semantics_20260803.py
PYTHONPATH=. python3 scripts/audit_p0_runtime_parity_20260803.py
PYTHONPATH=. python3 -m pytest -q \
  tests/test_executor_side_guard.py \
  tests/test_active_bundle_protocol.py \
  tests/test_forward_provenance.py \
  tests/test_forward_feature_semantics.py \
  tests/test_canonical_outcomes.py \
  tests/test_return_cost_contract.py \
  tests/test_execution_timeline.py \
  tests/test_global_tip_age_gate.py
PYTHONPATH=. python3 -m pytest -q tests \
  --deselect tests/test_eth3m_v2_classification.py::test_full_frame_transform_is_deterministic_and_uncropped
python3 scripts/md_to_html.py analysis/p0_safety_protocol_repair_20260803.md --out-dir analysis/html
python3 scripts/md_to_html.py analysis/p0_runtime_parity_audit_20260803.md --out-dir analysis/html
```

## 风险与诚实声明

- P0 证明的是 fail-closed，不证明 short 策略盈利、可部署或可实盘。
- 当前没有 active bundle，因此 production forward 不能运行；这是安全结果，不是运营完成。
- v10 是一棵树、selector 大规模并列、walkforward 非全正、feature semantics legacy，不能在 P0 被洗成 deployable baseline。
- 没有 VPS 与 broker reconciliation，F-07 的代码协议已实现和测试，但真实 fill 证据仍为 0。
- `gap_policy=barrier_price` 是旧经济假设的显式化，不等于证明跳空可按障碍价成交。
- 未安装 `torchvision` 的旧测试仍不能在本机运行；没有为了 P0 安装重型依赖。
- 不修改旧历史报告；仅用新版本化审计标明 return/cost 路由的 superseded 范围。

## 仍未做事项与 Owner 决策

P0 完成后必须停止。以下全部未做：

1. 确认 short return convention（O-01）；P1 重建前建议线性 USDT 永续 `1-exit/entry`，须 Owner 明确批准。
2. 批准 P1 只用 pre-holdout 重建 immutable、`side_aligned_v1`、可部署的 28-feature baseline。
3. 批准 P2 的真实成本/滑点/资金费压力线与 selector calibration 方案；P0 未改阈值或成本。
4. 决定是否以及何时生成并激活新的 `models/active_bundle.json`；不得自动 promote。
5. 决定旧 forward evidence 的归档/0-of-100 重启；P0 未清账或迁移。
6. short executor 必须另立规格、demo shadow 与逐次授权；P0 未实现或启用 short 下单。
7. 如需恢复 VPS/paper，另行批准部署与运行时核验；P0 没有部署。

## 安全声明

本轮 **未训练、未读取或评分 holdout、未修改 `models/ACTIVE`、未 promote、未部署或重启 VPS、未清空/覆盖/迁移 `data/forward_log.csv`、未修改 executor ledger、未访问 API key、未触发真实或 demo 订单**。
