# P0-SAFETY 实施规格

**落盘自** Notion《Grok Build 接管计划》02 页。Notion 为权威版本。

- **阶段类型**:安全 / 正确性迁移,**不是 ML 实验**
- **优先级**:P0,阻塞所有后续重训与前向确认
- **允许范围**:源码、单元测试、fixture、文档、临时 `/tmp` 产物
- **禁止范围**:holdout、ACTIVE 切换、promote、全量重训、VPS deploy、真实下单、清 forward log

## 1. P0 目标

P0 不追求"收益变好"。它只回答:

> 当前 short 研究模型是否能够以一个明确、不可分叉、可审计的协议运行;
> 任何不匹配是否会在下单前失败。

P0 完成后,v10 可继续作为历史 / audit evidence,但**在 P1/P2 重建和重训完成前,
不得作为可执行 short bundle**。

## 2. 已确认的代码风险

### P0-01 short artifact 被写成 long
`models/frozen_tp5_sl2_swap_yolo_v10_reg_20260731.json` 是 short v10 pool、`net_barrier_taker`;
而 `forward_scan.py` 调 `extract_feature_rows()`、默认 `resolve_forward_exit()` 是 long barrier、
记录写死 `"side": "long"`;`executor.py` 是 long-only,long 会执行 market buy。
**上游 short 意图被静默转换成 long 执行路径。**

### P0-02 当前 side guard 被上游硬编码绕过
`executor.py` 对显式 short 会拒绝(好边界),但 `forward_scan.py` 已把 short 候选写成 long,
guard 看不到真实意图。此外 executor 对**缺失 side 默认 long**,对旧混账也过于宽松。

### P0-03 feature semantics 分叉
`features.py` 已有 `extract_feature_rows_for_side(..., side="short")`,
但 active v10 wide pool 和当前 forward 用的是 plain `extract_feature_rows()`。
**不能只修改 forward 为 short-aligned 后继续加载旧 v10 模型,否则制造新的 train/serve 分布错配。**

### P0-04 barrier 与 return contract 分叉
`labeling.py` 默认 TP4/SL2,forward 是 TP5/SL2;short return 同时存在 `entry/exit - 1` 与 `1 - exit/entry`;
wide pool 同 bar TP/SL 相等时可落 TIMEOUT,而 canonical short labeler 保守记 SL;
forward 主线目前是 long barrier。

### P0-05 Signal、Decision、Fill 被混为一体
tip 行先写 signal-close proxy,下一脉冲再回填 next-open。**不能作为"实际 live fill"证据**,
因为模型决策完成时间通常晚于 next-open。P0 必须把
`signal_time` / `candidate_detected_at` / `decision_at` / `entry_requested_at` / `fill_at`
拆成不同语义,且**禁止在没有 fill 的情况下产生 actual realized PnL**。

### P0-06 生产 artifact 不是单一权威
`frozen.py::latest_artifact()` 按配置寻找最新可加载 JSON,损坏最新文件时跳过并回退旧文件;
运行时不读 `models/ACTIVE`。当前 ACTIVE 和 default config 恰好都指 v10,**不代表治理正确**。

### P0-07 tip-only 缺少全局最终断言
live 扫 start back 0/1/2,每个局部窗口接受最后两根,组合可映射到全局 tip-3。
必须在最终 candidate 上按全局最新 closed bar 做 age 断言。

### P0-08 执行 identity 和成本证据不完整
executor `signal_key` 包含 score,重评分可能生成新 key;每条腿是 market/trigger-market,
maker 口径不可达;short execution 仍未实现。**P0 只修 identity 和 fail-closed。**

## 3. P0 设计决定

### 3.1 版本化协议对象 `src/judgment/protocol.py`

`StrategyProtocol` 需承载(字段名可不同,语义不可丢):
`protocol_version` / `strategy_id` / `side` / `timeframe` / `window_bars` /
`candidate_source` / `max_tip_age_bars` / `feature_schema` / `feature_semantics` /
`score_semantics` / `threshold` / `threshold_operator` / `tie_policy` /
`research_entry_mode` / `live_entry_mode` /
`tp_atr_mult` / `sl_atr_mult` / `horizon_bars` / `same_bar_policy` /
`return_convention` / `cost_route` /
`detector_path` + `detector_sha256` / `model_path` + `model_sha256` /
`dataset_path` + `dataset_sha256` / `execution_eligible` / `paper_only`

### 3.2 精确 active bundle `models/active_bundle.json`(不自动启用)

当前 v10 的诚实描述应为 `feature_semantics: "legacy_unaligned"`、
`tie_policy: "legacy_large_tie_mass"`、`live_entry_mode: "none_until_p1"`、
`execution_eligible: false`、`paper_only: true`。
**不要为了让校验通过而伪造缺失哈希或谎称 execution eligible。**

### 3.3 生产加载规则

```
production → 只加载一个显式 bundle → 全字段与 hash 校验 → 不匹配即失败
research   → 可显式指定 artifact/config,但必须标记 research,不得写主 forward log
```

禁止:找最新合法 JSON;损坏后静默回退旧模型;路径存在就算通过;缺字段按 long/1x/默认成本继续跑。

### 3.4 当前 v10 的处理

建立准确的 legacy protocol 描述;标 `execution_eligible=false`;可保留 audit scoring;
**不得只把 live 改为 short-aligned features 后继续配这个模型**;
P1 重建数据、P2 重训后才产生 `side_aligned_v1` 可执行候选 bundle。

### 3.5 executor 的 P0 行为

| 输入 side | P0 行为 |
|---|---|
| `short` | `skipped_unsupported_side`,不调 client |
| 缺失 / NaN / 空 | `skipped_missing_side` 或统一 protocol mismatch,拒绝 |
| 未知值 | 拒绝 |
| `long` 但 bundle strategy 为 short | protocol mismatch,拒绝 |

**旧"缺失 side 默认 long"必须从 production 路径移除。**

## 4. 实施顺序(每步一个独立 commit)

- **P0.0 基线审计与快照**(只读):branch/status、`models/ACTIVE`、artifact SHA256、
  测试现状、data 是否齐、VPS 证据。输出 `analysis/output/p0_safety_baseline_*/`。**不得复制或上传 secrets。**
- **P0.1 执行层 fail-closed**:缺失 side 不再默认 long;`signal_key` 不含 score
  (推荐 `source|symbol|signal_time|side|protocol_version`);short/unknown/missing 不调任何 client。
- **P0.2 协议对象与 bundle loader**:hash 校验;production loader 不做 latest fallback;
  `latest_artifact()` 可留给 research/dashboard,production 入口不得调用。
- **P0.3 Forward provenance 与 side 传播**:side 来自 protocol;新增
  `protocol_version` / `strategy_id` / `feature_semantics` / `decision_at` / `execution_eligible`;
  旧行只读兼容,不自动进入新协议 100 笔;`detected_at` 与 `decision_at` 按候选实际完成时间记录。
- **P0.4 Feature semantics 合同**:extractor 由 `protocol.feature_semantics` 决定;
  至少支持 `legacy_unaligned` 与 `side_aligned_v1`;legacy v10 只允许 audit。
- **P0.5 Canonical barrier / return contract**:提取纯 resolver(建议 `src/judgment/outcomes.py`),
  labeling / forward / tip-replay 共用;TP5/SL2/72 显式传入,不依赖 `labeling.py` 默认 TP4;
  same-bar 保守 SL;return convention 变显式 enum。
- **P0.6 Signal / Decision / Fill 时间拆分**:live 不得把 `signal_time+15m` 的 next-open 当实际成交;
  proxy 字段须叫 `reference_px`;无 fill 时不算 actual realized_ret;
  barrier 起点从 fill 后开始。
- **P0.7 Tip age、报告与迁移边界**:在最终 `signal_i` 上断言
  `latest_closed_i - signal_i <= protocol.max_tip_age_bars`;旧 forward_log 标 legacy 不删。

## 5. P0 完成标准(不可妥协)

- **A.** short artifact 永远不能产生 buy
- **B.** old v10 不会因为只修 live 特征而被误认为可执行
- **C.** 没有 decision-after-fill 的时间倒置
- **D.** production 只加载一个 exact bundle,不 silent fallback
- **E.** label / replay / forward 使用同一 short outcome contract
- **F.** P0 没有碰 holdout、ACTIVE、promote、VPS、真实订单和 forward_log 删除

**不要在 P0 顺手重构 detection、webapp UI 或训练超参数。**

---

## 落盘时的完成情况(2026-08-03)

| 步骤 | 状态 |
|---|---|
| P0.0 基线审计 | **已做** → [`analysis/p0_baseline_audit_20260803.md`](../../analysis/p0_baseline_audit_20260803.md) |
| P0.1 执行层 fail-closed | **部分**:缺失 side 已不再默认 long(`61b4dc3`);`signal_key` 已不含 score,但尚未含 side/protocol_version |
| P0.2 协议对象与 bundle loader | **已做** —— `src/judgment/protocol.py`,37 个测试;`frozen.py` 的 glob 保留给 research/看板,生产入口不再靠它 |
| P0.3 forward side 传播 | **已做** —— side(`32e556b`)+ provenance 五字段与逐候选 `decision_at`(P0.3,17 个测试) |
| P0.4 feature semantics 合同 | **已做**(`61b4dc3`):`feature_semantics` 进 artifact,缺失读作 `legacy_unaligned`,未知值报错 |
| P0.5 canonical barrier | **已完成** `ee98ebd` |
| P0.6 时间拆分 | **已完成** `8e90390` |
| P0.7 全局 tip age | **已完成** `969dda7` |

**P0.4 先于 P0.2 完成,顺序与规格不符。** 原因是 P0-03 已在 live 路径上成为实际故障
(短模型被喂 6 个符号翻转的特征),止血优先于按序推进。

**该欠账现在只还了一半**:`feature_semantics` 同时存在于两处 —— `FrozenArtifact`(缺失读作
`legacy_unaligned`)与 bundle(缺失即加载失败)。bundle 是更严的那个,但只在 owner 放置
`models/active_bundle.json` 后才生效。两者合一要等 P0.3 把 provenance 字段并进 forward row。
