# ETH 15m Pine：Forward V2 与状态感知 LR 合同（2026-08-21）

## 结论先行

现在正确的处理不是继续拿已消费的最近半年反复调参，而是把下一阶段的入口锁死：

1. **当前 Pine 对照仍是冻结 V9 与冻结 V12F。** V12F 是目前相对更好的研究 comparator，但半年结果仍为 `-3.46%`、最大回撤 `24.74%`、58 笔、胜率 `8.62%`、PF `0.912`，不能叫盈利版本，更不能上线。
2. **旧 V9/V10/V11 paper 协议保留为历史，不覆盖。** 新建 V2，只允许 V9 与 V12F 两臂。
3. **V2 当前 NO-GO。** 它不会启动 scanner、不会写 forward log、不会发 paper/live order；P0/P1、TradingView exact parity、精确 venue/cost 和新的 Owner 启动批准全部是硬门。
4. **LR 现在只定义接口，不训练。** 335 个 raw candidates 必须全部评分；position、stop、cooldown、equity 必须在每条候选策略路径中在线重算，不能复用 V9 基线路径上的静态状态。
5. **Phase A 只判断是否开仓。** 空仓时决定开/不开；反向信号仍无条件平掉原仓，LR 只决定是否反手重开。让模型决定“平还是继续持有”属于另一个需要成对反事实标签的 Phase B，当前禁止混做。

这轮没有重读 repository holdout、没有改 Pine 参数、止损、止盈、ATR、风险或 20 bp 成本假设，也没有训练 LR/LightGBM。

## 为什么必须这样做

V12F 在已批准的 holdout 消耗 #1 中失败。继续看同一个半年、再寻找 W9、W10、另一组 TBSL 或 LR 阈值，会把 holdout 变成训练集。另一方面，直接启动 paper 也不成立：本地 OKX 回放不是 TradingView broker emulator 的逐笔同源证明，而且当前项目阶段只允许 P0/P1，paper-forward 属于 P4。

因此本轮完成的是两项可复现基础设施：

- `paper_forward_protocol_v2.json`：把所有外部门和启动状态写成 fail-closed 合同；
- `state_aware_lr_contract_v1.json`：把未来 LR 的状态、标签、评分覆盖和动态 replay 顺序写成不可训练合同。

## Paper Forward V2

### 历史协议与新协议

| 项目 | 历史 V1 | 新 V2 |
|---|---|---|
| 比较臂 | V9 / V10 / V11 | V9 / V12F |
| V12F | 不存在 | 冻结 comparator |
| V10/V11/V12E/V12T/L2 | 历史或后验候选 | 明确禁止进入本轮 |
| 正式收集 | 未开始 | 未开始 |
| TradingView parity | 未通过 | 未通过 |
| forward eligible | false | false |
| 历史文件 | 保留 | 单独新增，不覆盖 V1 |

历史 V1 的 SHA-256 为 `4513ef3de18e4bb91263ed5839812eaeabd89419a1afb610fc7cce920e1c40a5`。V2 只声明“未来规划上替代”，没有改写历史事实。

### 两个冻结臂

| 臂 | Pine SHA-256 | 冻结账本 | 历史月均交易 | 规划达到 100 笔新鲜交易 |
|---|---|---:|---:|---:|
| V9 | `6465fa80...73dfe9` | 110 | 7.90 | 12.66 个月 |
| V12F | `9e03c295...7567de` | 97 | 6.96 | 14.36 个月 |

“规划月份”只是按历史频率估算收集周期，不是收益预测。正式读取必须等两臂**各自**达到 100 笔真正的新鲜交易；此前只能看延迟、缺失、重复等数据质量，不能据此调参数。

### 当前硬门

| 门 | 当前事实 | 裁决 |
|---|---|---|
| 项目阶段 | P0/P1 尚未通过，paper 属于 P4 | NO-GO |
| V9 官方编译 | 只有 Pine v6 编译 smoke；研究窗口与冻结输入未形成 exact receipt | NO-GO |
| V12F 官方编译 | 无 receipt | NO-GO |
| V9 TradingView ledger | 无 110 笔逐笔 exact parity receipt | NO-GO |
| V12F TradingView ledger | 无 97 笔逐笔 exact parity receipt | NO-GO |
| venue | `OKX:ETHUSDT.P` 只是 proposed，Owner 尚未锁定 | NO-GO |
| commission/net | 新工具已能自动核对，但还没有真实 TV 导出 | NO-GO |
| funding/slippage | 尚未完成 venue-exact review | NO-GO |
| Owner prospective approval | 无新的 paper 启动批准和 activation timestamp | NO-GO |

所有门通过之后也不能回填历史：activation timestamp 之前的事件不得写入正式 forward log。

### 对账器补强

旧对账器只要求 `commission_total` 与 `net_profit` 两列存在，所以一份价格/时间正确、但费用全填 0 的导出也可能通过价格账本。现在它同时验证：

- `entry_time + direction` 唯一；
- entry/exit time 与价格逐笔一致，价格容差一个研究 tick；
- TradingView 总手续费等于冻结 Pine 的每边 `0.10%` 会计；
- TradingView `net_profit = gross_profit - commission_total`；
- 货币字段容差 `0.02`，只用于导出显示舍入；
- TradingView net 已含手续费，禁止再扣一次项目 20 bp 比较成本；
- funding 和 venue slippage 仍保留为独立人工/venue gate。

测试已证明：任一手续费或净利润改动 `0.03`、重复 entry identity、少一笔或价格偏离都会 fail closed。

## 状态感知 LR 合同

### 数据边界

| 项目 | 数值 |
|---|---:|
| raw guarded candidates | 335 |
| 多 / 空 | 170 / 165 |
| 冻结因果市场特征 | 28 |
| V9 基线路径实际交易 | 166 |
| 基线未执行候选 | 169 |
| 基线覆盖率 | 49.55% |
| 基线成本后正收益交易 | 27 |
| 当前每个验证折正类 | 4–8 |
| consumed-final rows read | 0 |
| repository holdout rows read | 0 |

166 笔成交不能代替 335 个候选。前面拒掉一次开仓，会改变后续持仓、反转、止损、冷却与权益，所以原本没有成交的 169 个候选以后可能变成可执行事件。

### 正确的动态顺序

```text
t 开盘：执行 t-1 提交的订单
  -> 处理 t bar 的 stop / target
  -> confirmed-bar BE 更新只对 t+1 生效
  -> t 收盘识别 raw candidate，并截取 pre-signal state
  -> 对每个 raw candidate 核验一条有限且及时的 score
  -> 冷却则只消耗一次；否则把 score 用于 entry permission
  -> 反向原仓的 close 永远不被 LR 取消，LR 只控制是否重开
```

每个状态事件至少绑定 `candidate_id + policy_path_hash + state_before_hash + action_context`。在线状态记录包含持仓关系、方向、持仓年龄、未实现毛收益、stop stage、到 active stop 的 ATR 距离、信号前 cooldown 和权益比例。它们只记录为候选字段，当前没有自动选入模型。

### Phase A 与 Phase B

| 场景 | 是否记录 score | LR 是否控制动作 | 动作合同 |
|---|---:|---:|---|
| flat_open | 是 | 是 | open / stay flat |
| opposite_reopen | 是 | 是 | 先无条件 close，再 reopen / stay flat |
| same_side_noop | 是 | 否 | 维持原仓 |
| cooldown_consume | 是 | 否 | cooldown 减 1，且只减一次 |
| calendar/volatility 未成 raw candidate | 否 | 否 | 维持 V9 原顺序 |

### 为什么不把全局 close-only 当成下一版 Pine

2026-08-22 的 Luna Max 查重确认，V9 已经在 2023–2024 development
做过 `opposite_signal_action=close_only` 消融，不应重复：

| 时段 | V9 reverse 净 bp/笔 | V9 close-only 净 bp/笔 | close-only 增量 | 总收益增量 | 胜率增量 | PF 增量 |
|---|---:|---:|---:|---:|---:|---:|
| 2023 discovery | +52.01 | +30.29 | -21.72 | -13.87pp | -1.80pp | -0.083 |
| 2024 confirmation | +141.35 | +146.77 | +5.41 | -15.05pp | -1.22pp | -0.130 |

2024 的每笔均值略高，但交易数从 83 降至 72，胜率、PF 和总收益仍更低；2023
又明确退化。V12F 没有同语义的完整实验，但现有证据不足以把它升级为新的经济假设，
也不允许进入 V2 forward roster。`allow_none_opposite` 只保留为动态 simulator 的
边界控制，不是候选策略。

查重还发现历史 simulator 在 close-only 平仓时仍把 `exit_reason` 写成 `reverse`。
现在已拆成：只有“同一反向信号平仓并立即重开”才记 `reverse`；只平仓不重开记
`opposite_signal_close_only`。该修正不改变成交、手续费、收益、cooldown 或订单时点，
只修复未来状态标签的语义。

Phase B 的 `close versus hold` 会改变一笔已在运行的交易，不能复用开仓标签。它必须比较“现在平仓”和“继续持有”两条成对反事实净效用，另立实验、另获 Owner 批准。

### 标签与模型门

当前只冻结词汇，不生成标签：`target / initial_stop / break_even_stop / opposite_signal_exit / period_timeout / intrabar_ambiguous / censored`。`ambiguous` 与 `censored` 必须为空值/排除并记录原因，不能偷偷当负类；“先到 +1.5%”不等于真实策略盈利。

未来 Phase A 即使获批，也只允许从极小、事先指定的 `side_aligned_v1` 集合开始：L2 正则 Logistic Regression、每折只在训练段拟合 scaler、扩展时间窗、按 `label_end` purge。当前 `C`、特征子集、概率阈值全部是 `null`；28 特征 LightGBM 禁止，已失败的 `pre_cross_path_efficiency_32` 也禁止进入 LR。

容量门同样未过：只有 27 个正类，当前验证折仅 4–8 个正类，而合同要求每折至少 20 个验证正类、每个有效系数至少 10 个训练正类。因此 `training_eligible=false` 不是形式标记，而是有数字依据的 NO-GO。

## 零假设与经济指标说明

本轮是协议/接口工程，没有生成新策略臂、标签、模型、score、阈值或收益路径，所以 val AUC、top-decile 毛/净收益、胜率、置换 p 和匹配随机入场对照按字面均不适用。填入任何数字都会把“没有运行的实验”伪装成结果。

同等严格的零假设控制是合同突变测试：

- V1 SHA 固定，V2 不能覆盖；
- roster 只能是 V9/V12F；
- canonical compact ledger 必须正好 110/97；
- V9/V12F 任一编译、窗口、输入、venue、ledger、fee gate 缺失即 blocked；
- TradingView fee/net 数值变动 `0.03` 即 parity fail；
- LR 必须覆盖 335/335，missing、duplicate、late、early、non-finite 或 hash mismatch 全部 fail closed；
- allow-all 动态 replay 必须精确复现 V9 2023/2024 账本；
- 所有模型、训练、阈值、forward、production 标志保持 false。

未来真正跑 V2 时，经济验收将同时报告 V9 与 V12F 各自相对“同币 × 同时间块 × 同波动桶 × 同 horizon × 同成本”的匹配随机对照，以及 V12F−V9 的前向 calendar-block 差；三项主假设用 Holm 控制 familywise `alpha=0.01`。

## Luna Max 独立复核

本机可见任务 **“Pine paper V2 与状态 LR 合同复核”**，任务 ID `01a024ee-82d2-79c1-8072-326a8e56850f`。本机 session 元数据实际记录 `model=gpt-5.6-luna`、`reasoning_effort=max`，不是隐藏 sub-agent 或 Hermes。

复核只读了规范、当前 main 的协议/manifest/脚本/测试、pre-holdout compact artifacts 与 path-efficiency 失败报告；没有读取或重跑 `>=2026-05-04` 的原始 K 线、交易或 forward 数据。它给出的最终裁决是 V2 NO-GO，并要求补强逐笔费用语义、335 候选覆盖、动态 state path 和 P0/P1 门；这些要求已进入当前合同与测试。

## 风险与诚实声明

- V12F 只是“相对 V9 少亏”的冻结 comparator，不是已验证盈利策略。
- V12F 最近半年 holdout 已消费一次且失败；本轮没有打开、重算或用它选参。
- 207 行只是已暴露的 110+97 compact canonical ledger 身份计数，没有读取市场 bar，也没有产生新的收益评估。
- `OKX:ETHUSDT.P` 目前只是 proposed symbol；Owner 没锁定前不能把它写成 venue 事实。
- TradingView 官方编译通过不等于 ledger parity；V9 当前 receipt 也不是冻结 2025-01 至 2026-02 输入窗口的完整 exact receipt。
- 未来 100 笔门按历史速度可能需要约 14.4 个月才能让较慢的 V12F 达标；不能因为慢就中途偷看收益和换参数。
- 本轮没有训练、paper、promote、deploy、ACTIVE 切换、forward log 写入或真金操作。

## 下一步

按正确顺序只有以下动作：

1. Owner 确认精确 TradingView venue；当前 proposed 为 `OKX:ETHUSDT.P`。
2. 在 15m、冻结输入、`2025-01-01 <= time < 2026-03-01` 下分别编译 V9/V12F，保存两个 official receipts。
3. 分别导出 V9 110 笔与 V12F 97 笔 Strategy Tester 历史交易，用强化后的工具做逐笔 price/time/commission/net parity。
4. 继续完成项目 P0/P1；阶段门不过，不启动 P4。
5. 上述全部通过后，由 Owner 单独批准 prospective log-only paper 并记录 activation timestamp；不回填。
6. LR 暂不训练。等 P0/P1、标签语义、样本容量和 Owner 单模型批准全部通过后，再实现 335-candidate 在线状态 replay。

## 复现命令

以下命令只生成/验证 blocked 合同，不读取 repository holdout：

```bash
cd /Users/zhangzc/fable-trading

PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_design_pine_eth_15m_paper_protocol_v2.py \
  tests/test_design_pine_eth_15m_state_aware_lr_contract.py \
  tests/test_reconcile_pine_eth_15m_tradingview.py \
  tests/test_design_pine_eth_15m_paper_protocol.py \
  tests/test_replay_pine_eth_15m_judgment_gate.py \
  tests/boundaries/test_layer_imports.py \
  tests/contracts/test_registries.py

PYTHONPATH=. .venv/bin/python \
  scripts/design_pine_eth_15m_paper_protocol_v2.py

PYTHONPATH=. .venv/bin/python \
  scripts/design_pine_eth_15m_state_aware_lr_contract.py

python3 scripts/md_to_html.py \
  analysis/p0_pine_eth_15m_forward_lr_contract_20260821.md \
  --out-dir analysis/html
```
