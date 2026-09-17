# V9 信号卡片接上持仓跟踪：R 与胜率不再是破折号

日期：2026-09-17。源码提交：`20998213a1`。前置状态：前端与 Bark 自 2026-09-15 14:56 切换 V9 后正常出信号，
但看板统计区的「运行中 / 已结束 / R / 胜率」全部显示 `—`。Owner 询问「v9 前端是不是没接好」。

## 本轮结论

**病因不是前端没接好，是后端从来没生产过前端要的那个对象。** 前端 `performanceView()` 早就会渲染
`performance.status / current_r / peak_r / stop_price / trailing_active / bars_held`；而 V9 事件在
`v9_signals.analyze` 里写的是字符串 `"performance": "not_tracked"`，
`signal_analytics.performance()` 只接受 dict，于是每张卡片都落进「仅入场参考 · —」分支。

本轮新增 `yoyo/monitor/v9_performance.py`，用**与 V9 全量回测同一个冻结串行引擎**为每条准入信号
生成持仓投影。前端一行未改。

## 关键判断：用哪个引擎

`yoyo/evaluation/spike_v9.py::replay_v9` 是全量回测的入口，但它经
`spike_exit_policy_study._cohort_inputs` 把 `START=2024-09-10 / END=2026-09-10` 之外的信号**全部清零**。
今天是 2026-09-17，直接调用它会让所有实时信号静默消失。

因此卡片投影调用同族的 `spike_v6_wvf_study.simulate_v6_variant`，并把两者的一致性**钉成测试**
（`tests/parity/test_duplicate_semantics.py`，该文件的既有职责就是「一个语义多个实现，谁必须一致」）。

## 冻结的投影规则（与回测逐条相同）

| 项目 | 取值 |
| --- | --- |
| 入场 | 信号 K 线的**下一根开盘**，不是确认收盘 |
| 初始止损 | 信号收盘冻结：`min(近5根最低 − 0.2ATR, 收盘 − 2ATR)`，向外取整 tick，风险 ≥ 2ATR |
| 跟踪 | 收盘毛 2R 启动 4ATR 收盘跟踪，只收紧 |
| 反向退出 | 原始反向 V6 确认（不过 V9 三门）在下一根开盘平仓 |
| 成本 | 固定 0.2% 往返，`current_r`/`exit_r` 都是**净 R** |
| 串行 | 每个 symbol×周期同时只有一个持仓 |
| 未平仓 | 以最后一根已收盘 K 线标记（`mark=last_closed_bar_close`），状态 `active` |
| 引擎已有持仓时的同向信号 | 状态 `unknown`、`reason=serial_position_already_open`，**不借用别人的 R** |
| 数据缺口 | 状态 `unknown`、`reason=data_gap_censored` |

## 验证

| 检查 | 证据与结果 |
| --- | --- |
| 与冻结回测引擎逐笔一致 | 窗口内合成流：两引擎开出相同笔数，`signal_bar_open/entry_time/side/entry_price/initial_stop/initial_risk/mfe_r/exit_time/exit_price/exit_reason/censored` 逐字段相同；已平仓 `gross_r`/`net_r` 差 ≤1e-12 |
| 真实数据抽查（ETH 1H，OKX，2025-05-30→2026-04-30，未读 holdout） | 14 笔逐笔一致；`gross_r` 最大差 3.55e-15、`net_r` 1.11e-16；唯一差异是未平仓那笔：冻结回测给 NaN，卡片给 mark |
| 因果性 | 延长供给前缀不改变已定字段（entry/stop/risk），只有运行中的 mark 与 bars_held 前进 |
| 不借用 R | 持仓期间的第二条同向准入信号返回 `unknown`，`current_r`/`peak_r` 均为 None |
| 契约形状 | `signal_analytics.performance()` 读出 `("active", current_r)`，与事件内取值一致 |
| 输入拒绝 | minutes≤0、tick≤0、evidence 未对齐一律 ValueError；空前缀返回 `{}` |
| 回归 | `tests/monitor + tests/boundaries + tests/parity`：改动前 **275 failed / 986 passed**，改动后 **275 failed / 994 passed**（+8 即本轮新增 7 项单测与 1 项 parity）。既有 275 项失败与本轮无关，来自工作树中其他会话的未提交改动与 V1 旧夹具 |
| 扫描开销 | 720 根 1H 窗口：`analyze` 全流程 448.4ms，其中 `project` 34.3ms（+7.7%）。整点四周期同时收盘是峰值场景，重启后需实测 `scan.duration_seconds` |

## 非方向性声明与零假设对照

本轮不产生任何收益主张，因此 val AUC、置换检验 p、top-decile 毛/净收益、胜率、匹配随机入场对照
**按字面不适用**：这是把既有回测口径接到展示层，没有新规则、新参数、新样本。

对应的同等严格零假设是**引擎等价性**：如果卡片用的是另一套语义，它与冻结回测在同一批 K 线上
必然开出不同的笔或不同的 R。上面两项逐笔对照就是这个零假设的检验，结果是除未平仓标记外完全一致。
第二条零假设是回归基线：若本轮动了别处，失败数应上升；实测失败数不变（275 → 275）。

**卡片上的 R 仍不是账户成交。** 它是下一根开盘的参考成交价 + 冻结止损的投影，不含滑点、
不含实际下单、不含仓位规模；Pine 图上的确认收盘参考与这套 next-open 口径依然是两套时序。

## 复现命令

```bash
cd /Users/zhangzc/fable-trading
python3 -m pytest -q tests/monitor/test_v9_performance.py tests/monitor/test_v9_signals.py \
  tests/parity/test_duplicate_semantics.py
python3 scripts/md_to_html.py analysis/p0_spike_v9_card_performance_20260917.md --out-dir analysis/html
```

## 尚未完成

- **监控服务未重启**，运行中的进程仍是旧代码；卡片要等重启后的扫描才会带上投影。重启属 owner 决定。
- 重启后只有仍在 720 根供给窗口内的信号会被刷新；更早的历史事件保持原样（不回填）。
- 本轮未动通知口径、未改阈值、未 promote、未触碰 forward_log 或任何真金路径。
  Bark 徽标「异常」是另一件事（历史累计 2 条 unknown 永久钉住徽标），本轮未改。

## 顺带记录的现场事实

工作树里 `yoyo/monitor/static/app.js`、`index.html`、`tests/monitor/frontend_cards.test.cjs`
有**其他会话留下的未提交改动**，本轮未纳入本次提交，也未验证其内容。
