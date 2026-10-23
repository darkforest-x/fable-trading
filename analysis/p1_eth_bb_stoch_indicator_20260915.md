# ETH 5m · BB × Stoch 反转指标

按 Owner 的想法实现独立 Pine 指标。**本版不加 V1 多周期门禁，先供看图；没有收益回测。**

## 使用

[打开完整源码复制页](eth_bb_stoch_reversal_v1_code.html)。点击“复制完整源码”即可粘贴。

Pine 源码：[eth_bb_stoch_reversal_v1.pine](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/eth_bb_stoch_reversal_v1.pine)。

1. TradingView 打开 ETHUSDT.P 的 5 分钟普通 K 线。
2. Pine 编辑器新建指标，粘贴完整源码并保存、添加到图表。
3. 保持“合成自检”关闭。默认 BB 为 200/2，初始止损为 1.5%。
4. 指标自身包含 BB 和原 Stoch 箭头计算，无需从其他指标手工选取数据源。

## 规则

| 项目 | 多头 | 空头 |
|---|---|---|
| 入场 | 实体完整在 BB 下轨外＋原 Stoch 绿箭头 | 实体完整在 BB 上轨外＋原 Stoch 红箭头 |
| 影线 | 可以进入 BB，不影响实体判定 | 可以进入 BB，不影响实体判定 |
| 初始止损 | 开仓参考价下方 1.5% | 开仓参考价上方 1.5% |
| 第一次止盈 | 碰到上轨，平 50% | 碰到下轨，平 50% |
| 平半后 | 剩余 50% 止损移到开仓价 | 剩余 50% 止损移到开仓价 |
| 尾仓退出 | 实体在上轨外＋Stoch 红箭头 | 实体在下轨外＋Stoch 绿箭头 |

入场与完整反向退出按收盘确认，开仓价为信号收盘参考价；不是实际成交价格。
首次触轨与有效止损在实时价格更新时处理，不等收盘。
同向重复信号不重置仓位；反向完整信号先平掉旧参考，再开启反向参考。
反向退出也可能亏损，图上标“反向平仓”，不会把亏损标成获利。

BB 取自 V9：close 的 SMA200 ±2 倍总体标准差。
Stoch 取自 Owner 原脚本：K=SMA3(Stoch5)，D=SMA3(K)，金叉时 K、D 都严格低于20才有绿箭头，
死叉时 K、D 都严格高于80才有红箭头。原脚本的 WVF 绿色柱不属于箭头条件，本版未附加。

## 框和 R 怎么看

- 红色框：初始风险范围，保留不缩小，作为 R 的固定分母。
- 绿色框：对侧 BB 的平半目标；首次触轨后冻结该目标参考价。
- SL／BE 线：当前有效止损；平半后显示开仓价保护。
- 1R／2R／3R：固定初始风险计算的观察线，不是额外止盈条件。
- 加权毛 R：已平部分加上剩余仓位的浮动结果；平仓后显示两部分合计。

例如初始风险30美元，半仓在盈利60美元处退出（该份额2R），剩余半仓在开仓价退出，
总结果是 `0.5×2R + 0.5×0R = 1R`，不是2R。金额只是算术示例，非历史交易。

## 与已有脚本的关系

| 内容 | 旧 V9 | 本指标 |
|---|---|---|
| 价格 BB | SMA200／2倍总体标准差 | 沿用 |
| 进场 | V9 自身的组合确认与过滤 | BB 实体越轨＋原 Stoch 箭头 |
| 止损与退出 | 原 V9 风险参考规则 | 1.5%初始止损、触对侧BB平半、移保本、完整反向退出 |
| 绘图 | 红绿透明框、价格与 R | 沿用视觉风格，显示本策略状态 |
| V1 门禁 | 不适用于本独立实现 | Owner 要求暂不加 |

## 验证记录

- TradingView Pine v6 原生编译通过；图表面板实际显示 `SELFTEST PASS`。
- 11 组合成检查、13 次共享转移函数调用：多空平半与保本、初始止损、只平半一次、当前 BB 与旧极值区分、同根历史保本、完整反向请求、入场根不追溯止损、跳空、不倒推保本、实体与箭头反例。
- 自检副本仅把 `runSelfTest` 默认改成 true，市场输入置空；正式源码默认 false。首次编译发现保留字 `text` 冲突，改名后通过。
- 原生验证覆盖编译和合成状态转移，尚未验证真实行情上的逐笔路径与所有框布局，不等同于收益或全量 Pine/Python 对齐验收。
- 最终源码 SHA-256：`ff7a83965ea6701971c8790c114b9ae69fc653df88f330131754fbd4e81d54a5`。
- 源码提交：`a5976113f48a8081a02ccaddefbef02e7186e978`。
- 仓库守门结果：480 passed / 11 failed。9 项由已有注册记录缺 `source_commit` 阻塞（`spike-v8-total2-1h-native-20260914-delivery`、`exp-eth-ma120-longest-runs-20260914-v1`）；2 项为已有 `candidates.py` / `render.py` 与迁移清单哈希不一致。本轮未改这些文件或记录，未宣称全仓检查通过。完整输出在实验目录 `repository_gates.txt`。

本轮真实研究行情样本数0，候选数／正类率／时间范围／val样本数不适用，原因是仅实现指标。
val AUC、置换p、top-decile收益、胜率及匹配随机入场不适用：本轮没有经济评价。
等价工程零假设是“必要条件缺失仍会触发、同一笔会重复平半或旧极值会被误用成后续保本”：
用缺条件反例和相反顺序的人工事件检查，不用伪造收益替代验证。

## 风险与诚实声明

历史 K 线只有开、高、低、收，无法知道盘中 BB 的全部变化，也不能总是还原碰轨与止损的先后。
历史绘图先处理已有止损，再按该根收盘 BB 检查触轨；新移保本后，只有收盘回到保本外侧才在
同根确认保本退出，否则等后续 K 线。实时使用收到的后续价格更新，避免把止盈前的影线当作
止盈后的保本触发。刷新后，实时已走过的路径可能按历史近似重算而变化。

R 是毛价格结果，未扣手续费、滑点、资金费；开仓价保护是价格保本。
本指标不证明盈利；没有全历史收益统计、训练、promote 或实盘下单。本配置 holdout 消耗0次。

## 复现与下一步

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/boundaries tests/causality tests/parity tests/contracts/test_registries.py -q
python3 scripts/md_to_html.py analysis/p1_eth_bb_stoch_indicator_20260915.md --out-dir analysis/html
python3 experiments/active/exp-eth-bb-stoch-indicator-20260915-v1/build_delivery.py
```

原生合成检查：打开实验目录的 `native_selftest.html`，复制到新建 Pine 指标，添加后应显示 `SELFTEST PASS`；正式看图则使用上方正式源码。

下一步由 Owner 在图上检查信号、止盈止损框和 R；如认可效果，再单独指定回测范围。
后续 V1 的5m/15m/30m/1H信号及持仓门禁另行实现。

## 来源

- [规则与授权记录](/Users/zhangzc/fable-trading/experiments/active/exp-eth-bb-stoch-indicator-20260915-v1/PROJECT_PLAN.md)
- [V9 价格BB与框样式](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/spike_burst_v9.pine)
- [Owner 原Stoch源码](/Users/zhangzc/fable-trading/experiments/active/exp-spike-fanshen-exit-multitf-20260914-v1/source/owner_fanshen_v1_20250706.pine)
- [Pine 官方执行与回滚模型](https://www.tradingview.com/pine-script-docs/language/execution-model/)
- [Pine 官方对象与 varip 字段说明](https://www.tradingview.com/pine-script-docs/language/objects/)
- [Spike 知识库记录（Inbox／想法）](https://app.notion.com/p/3dc8856479af813280cfc5d397400f0b)
