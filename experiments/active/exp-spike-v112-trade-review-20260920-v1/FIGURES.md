# 8张逐笔复盘图

图形风格沿用昨日：深色K线、六均线、IMACD、A/B/C趋势线、父V9框。增加橙色实际有效保护轨迹、联合入场和退出、独立诊断。原始Binance数据重绘，非TV截图。按机制选6亏损+2盈利，不是随机或比例样本。

## 1. T531 · PENDLEUSDT 1h · 方向启动较早，突破入场已贵20.18%

等待33根后才入场；父V9事后盈利，本笔却几乎未再推进。值得检验确认时机，不能把父V9收益当可提前挑中的策略。

入场UTC：2026-04-17 01:00:00+00:00；净-1.0512R；唯一键：`binance_um:PENDLEUSDT:1h:box_any:15516`。

![方向启动较早，突破入场已贵20.18%](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/01_T531_PENDLEUSDT_1h.png)

[该币全部复盘](by_symbol/PENDLEUSDT.md)

## 2. T447 · ZENUSDT 1h · 盘中浮盈4.44R，追踪仍未激活

最高存活收盘只有1.28R。原规则要求收盘达到2R；尖刺高点并不触发追踪。

入场UTC：2025-04-19 16:00:00+00:00；净-1.1116R；唯一键：`binance_um:ZENUSDT:1h:box_any:6819`。

![盘中浮盈4.44R，追踪仍未激活](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/02_T447_ZENUSDT_1h.png)

[该币全部复盘](by_symbol/ZENUSDT.md)

## 3. T206 · ETHUSDT 15m · 追踪已激活，保护仍在成本线下

曾收盘超过3R，但4ATR距离仍宽；有效保护低于入场，叠加成本后亏损。激活追踪不等于保本。

入场UTC：2025-06-29 07:30:00+00:00；净-0.7854R；唯一键：`binance_um:ETHUSDT:15m:box_any:29561`。

![追踪已激活，保护仍在成本线下](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/03_T206_ETHUSDT_15m.png)

[该币全部复盘](by_symbol/ETHUSDT.md)

## 4. T150 · BTCUSDT 15m · 止损1R，扣费却亏1.76R

初始风险只有约0.262%；20bp成本相当于0.763R。仅看命中方向，会忽略窄风险单位里的高成本。

入场UTC：2025-04-19 15:15:00+00:00；净-1.7633R；唯一键：`binance_um:BTCUSDT:15m:box_any:22776`。

![止损1R，扣费却亏1.76R](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/04_T150_BTCUSDT_15m.png)

[该币全部复盘](by_symbol/BTCUSDT.md)

## 5. T545 · BTCUSDT 4h · 入场当根止损，随后继续走弱

5分钟顺序审计确认止损前曾到约0.51R，仍未到1R。原账本MFE为0是退出根不更新造成的。

入场UTC：2025-04-02 20:00:00+00:00；净-1.0535R；唯一键：`binance_um:BTCUSDT:4h:box_any:2728`。

![入场当根止损，随后继续走弱](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/05_T545_BTCUSDT_4h.png)

[该币全部复盘](by_symbol/BTCUSDT.md)

## 6. T552 · APTUSDT 4h · 这笔亏损来自反向确认，不是止损

曾浮盈2.28R，最终因反向信号在下一开盘退出。需要分别评价反向退出与价格止损。

入场UTC：2025-07-11 00:00:00+00:00；净-0.2404R；唯一键：`binance_um:APTUSDT:4h:box_any:3323`。

![这笔亏损来自反向确认，不是止损](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/06_T552_APTUSDT_4h.png)

[该币全部复盘](by_symbol/APTUSDT.md)

## 7. T544 · DASHUSDT 4h · 盈利对照：趋势延续带来32.49R

这笔展示宽追踪保留大趋势的价值。若为挽救回吐单而提前退出，也必须计入失去这类赢家的代价。

入场UTC：2024-11-09 04:00:00+00:00；净+32.4852R；唯一键：`binance_um:DASHUSDT:4h:box_any:1860`。

![盈利对照：趋势延续带来32.49R](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/07_T544_DASHUSDT_4h.png)

[该币全部复盘](by_symbol/DASHUSDT.md)

## 8. T453 · AEROUSDT 1h · 1h盈利对照：追踪最终锁住8.38R

同样的收盘2R激活、4ATR追踪，在持续推进的行情中有效锁盈；不能只从亏损单判断退出机制。

入场UTC：2025-04-22 19:00:00+00:00；净+8.3796R；唯一键：`binance_um:AEROUSDT:1h:box_any:3340`。

![1h盈利对照：追踪最终锁住8.38R](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v112-trade-review-20260920-v1/figures/08_T453_AEROUSDT_1h.png)

[该币全部复盘](by_symbol/AEROUSDT.md)

