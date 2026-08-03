# 决策记录与停止门

**落盘自** Notion《Grok Build 接管计划》05 页。Notion 为权威版本。
本页把"可直接执行的安全决定"与"必须等 Owner 的经济/实盘决定"分开,
避免执行方在实现中自行猜测。

---

## 1. 已冻结,可直接执行

| ID | 决定 |
|---|---|
| **D-01** | 主线方向是 **short**。实现必须传播 `side=short`,**不得为了兼容 long-only executor 把它改成 long**。 |
| **D-02** | P0 **不实现也不启用真实 short 下单**。先封住反向 buy 风险,short execution 后续单独设计。 |
| **D-03** | 当前 v10 是 **legacy / audit-only**,P0 不把它变成 execution eligible。理由:legacy feature semantics、q90 tie mass、walkforward 不稳定、forward side/barrier 属旧协议、detector 为 interim。**不可通过"只修推理端"把它升级为可执行。** |
| **D-04** | P0 **不调 threshold**。现有数值只用于复现 / audit;P2 才在新数据和 calibration split 上重新标定。 |
| **D-05** | P0 **不碰 holdout**。`signal_time >= 2026-05-04` 不用于构建、训练、校准、选择、看板评分或新指标。 |
| **D-06** | P0 **不清 forward log**。旧记录保留并标 legacy;归档名与重启 0/100 需 Owner 决定。 |
| **D-07** | 生产 artifact 必须 **exact bundle + hash + fail-closed**。不允许"最新合法 JSON"自动选择或静默回退。 |
| **D-08** | **live fill 必须在 decision 之后**。离线 `next_bar_open` 可保留为 research convention;live/paper 不得用决策前价格冒充 fill。 |
| **D-09** | **全局 tip age 最大 2 bars**。局部 window edge 不是最终门。 |
| **D-10** | **同 bar TP/SL 使用保守 SL**。仅有 OHLC、无法确认触发顺序时记 `sl_ambiguous`/SL,不落 TIMEOUT,不选有利方向。 |

---

## 2. 推荐方案,但正式经济迁移前需 Owner 确认

| ID | 事项 | 推荐 |
|---|---|---|
| **O-01** | short return convention | 线性 USDT 永续用 `1 - exit/entry`。仓库另有 `entry/exit - 1`,两者会系统性改变 TP/SL 幅度与 PF。P0 先显式化并支持测试;P1 重建前 Owner 定最终值。 |
| **O-02** | 实际成本路由 | 当前 executor 入场 market、TP/SL trigger 后也是 market,**不能继续用 maker 作为实际执行证据**。滑点/资金费规则需 Owner 在 P2/P4 前批准。 |
| **O-03** | P1/P2 的 active cutover | 不能由 freeze script 自动 promote。 |
| **O-04** | 旧 forward log | 清账/归档属 Owner 决策,P0 只提迁移计划。 |
| **O-05** | short executor | 后续如实现,须单独形成执行规格,至少覆盖 net/hedge position mode、sell entry、buy reduce-only exit、short TP 在下 SL 在上、OCO 方向、timeout close、actual fill reconciliation、demo shadow 验证、Owner 逐次授权。**不能在 P0 里顺手复制 long 代码后启用。** |

---

## 3. 必须停止并询问 Owner 的动作

出现以下任一需求,**立即停止**:

- 修改 TP/SL/horizon
- 选择 return convention 作为 active 目标
- 修改 fee / slippage / funding 假设
- 修改 q90/q95/q99 或 threshold operator 以追求收益
- 读取 holdout
- 修改 `models/ACTIVE` 或 active bundle
- promote detector
- 清空 / 归档 / 重启 `forward_log`
- deploy / restart VPS 服务
- 切 demo / live
- 改 kill switch
- 实现完成后启用 short order
- 修改新鲜度分钟门或 pulse 预算
- **创建 branch / worktree**

## 4. 技术停止条件(无需 Owner 决策也要停)

- 实际 full repo 与 lite 快照 active 状态不一致
- active model / dataset / detector hash 对不上
- full data 的 feature semantics 无法从生成器和 manifest 证明
- 测试需读真实 data 才能通过,且无法用 fixture 替代
- forward service 或 executor service 的实际代码不在仓库快照中
- old log schema 无法无损区分 legacy / new protocol
- P0 修改导致需要重训才能解释,但任务还在 P0
- 发现 secrets、真实订单或未授权 live 状态
- 发现 holdout 已被某个 P0 命令自动读取
- **需要删除历史报告或改旧结论才能让现状"看起来一致"**

## 5. Owner 审批模板

需要批准时给出具体内容,**不要只问"要不要继续"**。

## 6. 当前推荐的 Owner 决策顺序

P0 报告完成后再依次决定:

1. 确认 short return convention
2. 批准 P1 pre-holdout immutable dataset rebuild
3. 批准 P2 的实际成本压力线
4. 审查 P2 固定 runtime gate
5. 审查 P3 tip-gold detector
6. 批准 P4 shadow bundle
7. 决定旧 forward log 归档和新 0/100
8. 最后才讨论 short executor 和 P5

---

## 落盘时的实际情况(2026-08-03)

- **D-01 已满足**:`forward_scan.py` 按 artifact 传播 side,不再硬编码 long。
- **D-02 已满足**:`executor.py` 对非 long 一律 `skipped_unsupported_side`,不调交易 client;
  且缺失 side 自 `61b4dc3` 起不再默认 long(见计划书 P0-02 / 验收 A-03)。
- **D-03 部分**:v10 仍是 ACTIVE 且被评分,但 `feature_semantics=legacy_unaligned` 已显式化,
  提取器随之回到训练时的坐标系。**尚未打上 `execution_eligible=false` 标记 —— 那需要
  D-07 的 bundle 机制,属未完成项。**
- **D-05 已满足**:holdout 消耗计数仍为 9,本轮未动。
- **D-06 注意**:`data/forward_log.csv` 已是空文件(仅表头),与"保留旧记录"的前提不符。
- **停止门"创建 branch / worktree"** 与本仓库铁律 13 一致,本轮全部提交在 main。
