# 突破＋SPIKE新增RSI退出：实现准备（2026-09-20）

Owner已明确授权“保留原来的价格保护，两者谁先触发就先退出”。本轮先完成不依赖计数选择的适配层；前端尚未启用新RSI退出。不得把准备完成写成新止盈已上线。

## 已确定与待明确

确定：原联合入场、初始止损、收盘2R激活的4ATR追踪、原始V9反向退出与0.2%成本保留；新增第7个反向RSI强菱形全平作为附加退出。使用闭合K线，信号确认后的下一开盘执行；原保护先到则先退出。

以下问题已发给Owner，尚无答案，预选选项不视为答复：

- 从入场后累计7个反向大菱形，还是沿用同色连续计数、异色重置？
- 与持仓同周期，还是上一级周期？
- 第7个时亏损仍全平，还是仅扣成本后盈利时退出？若后者，还需明确错过第7个的后续处理。

## 实现边界

新适配层`yoyo/evaluation/spike_joint_rsi_exit.py`已实现：只接收与闭合行情严格对齐的RSI退出布尔序列，通过原回放引擎已有“下一开盘退出”通道执行，避免复制或改变止损与追踪算式。只有确认由RSI触发的实际退出才修改原因标签；原V9同根确认保留原原因，跳空穿保护仍标为保护退出。该层不自行决定计数、周期或盈亏门。

同模块提供ChartPrime RSI14/SAR强菱形计算，使用现有算式和30/70强弱判定；有效断档bar作为新分段第一根，非有限close保持未知并从下一有效bar重启。只依赖当前及此前闭合close。当前适配器明确仅面向前端已有多头仓，不宣称实现空头对称持仓。

旧信号ID、运行数据库与历史绩效均未修改。接入时必须绑定独立绩效版本并保留旧账本证据；策略改动后的历史重算不得称为旧规则当时真实前向结果。Pine V12.5与当前前端V11.2模拟投影是不同入口。

只读接入核查：worker的`refresh_performance`目前原地重写结果，仅增加版本字段不足以保存旧账本；须额外保留旧性能版本。worker的`seen`按检查点时间缓存，规则版本也需要参与失效或在切换时受管重启。前端`LINE_EXITS`需增加RSI原因，亏损的RSI退出不可统一标作“已止损”。以上尚未实施，避免未确定口径的版本进入运行。

## 旧规则基线

2026-09-20 19:25:40北京，GET本机`/api/lines/status`和`/api/lines/ledger?kind=joint&limit=2000&period=all&scope=all`：扫描idle，83条信号，68闭合、15运行，已实现97.30571208932957R，浮动6.641284792789817R。

原始JSON与SHA清单：`output/qa/joint_rsi_exit_20260920/baseline_20260920T112540Z/`。本轮未写订单接口或运行数据库。这些数值发生在新规则接入前，不是新规则效果。

原监控与RSI基线检查：

```bash
.venv/bin/python -m pytest -q tests/monitor/test_spike_lines.py tests/evaluation/test_parabolic_rsi_sar.py
```

18通过，1项现有urllib3/LibreSSL环境警告。

新增适配层与相关引擎测试（实现代理执行，父端审查源码并纠正有效断档bar丢失及合成OHLC不合法问题后通过）：

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_joint_rsi_exit.py tests/evaluation/test_parabolic_rsi_sar.py tests/evaluation/test_spike_v10_4_increment.py
```

23通过；py_compile与diff检查通过。包括关闭新增退出与基线完全相等、下一开盘成交、同根先触发保护、开盘跳空保护优先、开盘RSI退出早于随后盘中止损、原V9更早/同根优先、末端不虚构成交、入场前信号无效、数据断档、输入不可变与指标前缀因果/重新播种。18与23两组存在共享SAR测试，不相加宣称41项独立检查。未对“恰好第7个”的计数做实现或验收。

## 风险与诚实声明

本轮为实现准备，无新的市场回测或盈利结论；候选数、正类率、val AUC、置换p、top-decile收益、胜率和匹配随机入场不适用。对应功能对照为未添加退出时与原回放逐字段相等，以及止损/信号先后顺序的合成反例测试。第7个不是最优阈值的声明，能否减少回吐且保留趋势收益仍未验证。

Notion想法与授权记录：https://app.notion.com/p/3e18856479af819eb65ed970222e0521 。方法笔记：`docs/learnings/oscillator-event-count-is-not-a-profit-giveback-limit.md`。
