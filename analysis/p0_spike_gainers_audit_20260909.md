# Spike 涨幅榜信号核对 · 2026-09-09

交付主报告：[打开完整便携 HTML](/Users/zhangzc/fable-trading/output/qa/spike_gainers_audit_20260909/report.html)。本文件是核对来源与复现记录；没有修改监控或推送规则。

## 核心结论

截图指定的11个币，在今天当前四周期的多头“蓄势释放”口径下分成三类：**9个没有新释放、GRASS已触发且Bark服务器接受、ZEC的30m释放早于该周期启用**。不能将上涨名单中的每个币都视为一次通知漏发。

现行提醒对应`focusRelease`：主线和信号线同时近零，`max(abs(md),abs(sb)) <= 0.10 * ATR[1]`，连续至少12根取得资格；资格成立后带宽冻结，主线离开该带才生成释放。主线精确零轴根数与双线近零根数不相等。`showMarks=false`隐藏的内部密集启动，不是现行通知事件。

## 范围与统计

- 币种：IOST、GRASS、MINA、USELESS、TRUTH、GPS、ZRX、MET、RAY、ENA、ZEC，均为OKX USDT永续。
- 当前周期：15m、30m、1H、4H，共44个无缺口的图表窗口；截止截图后，42窗各240根、2窗各239根已就绪K线。
- 今天定义：北京时间2026年9月9日00:00至16:29:59.999；15m最新纳入16:15收盘，其他周期16:00收盘。
- 独立近零状态重放比较10,396个可比较状态，差异0；今天多头释放的图表时点与SQLite账本一致。
- 价格对照：IOST、GRASS、GPS、MINA各66个15m收盘点，共264行；北京时间00:00收盘价=100，到16:15。它们是事后选出的描述性路径，不是策略收益。
- 本轮没有训练/验证集、收益标签或随机入场实验；“2个今天有释放”是事件计数，不是正类率或胜率。

## 11币明细

时间均为北京时间。“无”只限今天、当前四周期、当前多头释放定义；不表示没有上涨机会。

| 币种 | 今天可见多头释放 | 关键证据 | 通知核对 |
|---|---|---|---|
| IOST | 无 | 15m 13:45：主线零轴3根、双线近零0根；1H从9月4日00:00旧启动后主线持续为正 | 今天无释放推送；早期标签为历史重放，不能当作当时已通知 |
| GRASS | 15m 13:15，0.3475 | 双线近零39根后释放；14:45 YOLO追加确认 | 原箭头13:16:14、模型14:46:27获Bark服务器接受 |
| MINA | 无 | 15m 13:15：零轴11根、双线近零6根；02:00另有内部启动 | 内部识别未纳入可见释放通知 |
| USELESS | 无 | 1H昨天21:00启动，0.25691，主线此后持续为正 | 昨天15m、1H原箭头已有Bark接受回执 |
| TRUTH | 无 | 1H昨天23:00启动，0.012757，今天主线持续为正 | 昨天15m 22:00 YOLO接受；1H 23:00同根YOLO的Bark回执未知 |
| GPS | 无 | 15m 11:00：零轴13根、双线近零9根；4H昨天20:00已启动 | 昨天4H原箭头有Bark接受回执 |
| ZRX | 无 | 15m 11:45：零轴5根、双线近零0根，当前密集诊断未满足 | 今天无对应释放推送 |
| MET | 无 | 15m 14:45：零轴2根、双线近零0根，当前密集诊断未满足 | 今天无对应释放推送 |
| RAY | 无 | 15m 03:15：零轴4根、双线近零0根；1H 05:00双线近零1根 | 今天无对应释放推送 |
| ENA | 无 | 15m 14:15：零轴5根、双线近零0根，内部密集启动成立 | 今天无对应释放推送 |
| ZEC | 30m 08:30，1185.09 | 30m直到今天15:24:16才启用 | 历史信号不补发；更早4H标签不证明当时已通知 |

IOST、MINA、GPS、ENA的内部启动不只是后来图表重放：本机账本首次记录分别为IOST 13:46:38、MINA 13:16:21（02:00另一次于02:01:43记录）、GPS 11:01:57、ENA 14:15:52。它们当时已被内部逻辑识别，但未达到现行可见释放通知合同。见`internal_entry_receipts.json`。

## 验证与零假设对照

采用独立状态机从可确认清空的状态开始重放，避免从240根显示尾部重新播种均线。44个窗口检查K线连续性、就绪状态、开盘/收盘时间和图表/账本释放时点。

除真实数据0差异外，在内存中将GRASS一根近零K线的`sb`改为`3 * ATR`，独立检查器捕获12个后续状态差异；原始快照未修改。这是本次非方向性事件审计的负对照。详见[validation_checks.json](/Users/zhangzc/fable-trading/output/qa/spike_gainers_audit_20260909/validation_checks.json)。

**AUC、置换p、top-decile毛/净收益、胜率、随机入场和TP/SL对照不适用**：本轮不预测交易结果，只核对事件条件、时钟与回执。用收益数字评价这次运行审计会改变问题；等价严格对照为独立重放、图表/账本逐时点匹配和注入错误负对照。未改变20bp成本、障碍、模型、仓位、实盘配置或通知规则。

便携HTML使用规范`artifact.json`和插件统一打包器。规范校验、打包与精确载荷结构校验通过。自动工具未找到兼容headless-shell；指定已安装Chrome的唯一追加尝试超时，因此最终QA为`structural_only`，未声称桌面/窄屏浏览器交互验收通过。HTML仍包含统一阅读器及语义图表数据表回退。

## 风险与诚实声明

1. 11个币由事后上涨截图选择，不能据此计算漏掉机会比例或证明放宽参数更赚钱。需加入同期失败候选，固定规则后另行比较。
2. 本机固定参数快照不自动同步TradingView图表；若Owner改过TV输入，本次并不证明与当前TV画面完全相同。
3. Bark服务器接受不证明手机显示、响铃或阅读；`unknown`必须保留未知。不同钟源的小差异不应被解释为负传输耗时。
4. 通知账本含历史重建，图表仅有限显示窗口；历史标签不能追认当时曾检测或发送。
5. 今日价格指数口径为北京时间00:00收盘价=100；TradingView自选列表通常相对前一根日线收盘计算，交易所日线时区可能不同。未重现截图涨幅。参见[官方定义](https://www.tradingview.com/support/solutions/43000653369-how-are-change-and-change-calculated-in-the-watchlist/)。

## 下一步

先将已有内部密集启动作为独立研究候选，与现行释放在同币、同时段、同成本下对照；同时在观察视图保留持续趋势、原始启动时间和回踩再启动候选。不要仅为覆盖这11个赢家降低12根阈值，也不要将新增候选直接视为更好的正式信号。新增正式推送规则或部署由Owner决定，本次未实施。

## 来源与复现

源代码：`yoyo/evaluation/spike_gainers_audit.py`（当前已提交的时钟与分类修正）；冻结原始输入：`chart_snapshot.json`、`notification_audit.json`。主结果：`evidence_summary.json`。源SHA随主结果保存。

报告的SQLite语句仅在隔离内存数据库读取冻结JSON，满足插件图表/表格SQL契约；原Python核对仍是分析来源。四币264行价格指数与原Python输出逐值校验，没有读取在线服务或重新获取价格。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.spike_gainers_audit --snapshot-dir output/qa/spike_gainers_audit_20260909
python3 output/qa/spike_gainers_audit_20260909/report_build.py
npm --prefix /Users/zhangzc/.codex/plugins/cache/openai-curated-remote/data-analytics/0.2.10-13ceeea1f599 run report:deliver -- --input /Users/zhangzc/fable-trading/output/qa/spike_gainers_audit_20260909/artifact.json --output /Users/zhangzc/fable-trading/output/qa/spike_gainers_audit_20260909/report.html
python3 scripts/md_to_html.py analysis/p0_spike_gainers_audit_20260909.md --out-dir analysis/html
```

从冻结JSON可复现本次核对；它不是对当时账户、真实设备显示或历史HTTP状态的重建。没有重新推送，也没有下单。
