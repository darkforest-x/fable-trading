# IMACD 蓝橙双线默认样式

已按用户实际保存的 IMACD 样式更新原 TradingView 脚本，账户版本为第 3 版。副图没有柱状图。

参考为 `zzc-20251222-1.0-Impulse MACD`，读取了其实际“样式”设置，没有修改原脚本或保存的预设。

| 绘图 | 用户保存的设置 | 新版默认值 |
|---|---|---|
| 主线 | #2962FF，40% 不透明度，2px 实线 | 相同 |
| 信号线 | #FF9800，100% 不透明度，2px 实线 | 相同 |
| 零轴 | #787B86，50% 不透明度，1px 实线 | 相同 |
| 辅助柱图 | 参考实例仍显示 | 按用户明确要求去掉；新版无 histogram/columns 绘图 |

零轴蓄势点、辅助动量与背景默认关闭，保留为显示选项。主图均线、启动/结束标记及紧凑状态栏保留。

[新版完整源码](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/imacd_dense_mtf_v1_2.pine) · [验证记录](/Users/zhangzc/fable-trading/experiments/active/exp-imacd-ma-mtf-20260907-v3/pine_indicator/style_v1_2_receipt.json)

## 验证与诚实声明

TradingView 官方编译通过；同一 CRYPTO:BTCUSD 1 小时图并排核对蓝橙曲线，再移除本轮临时添加的参考实例。第 3 版保留在当前图表。核心计算、14 个非显示输入和 5 个警报条件与前版一致。

本轮只改外观，没有收益实验或新增研究候选，AUC、收益、胜率、置换 p、随机入场对照不适用；对应检查为源码逐字对照、官方编译和实际样式参数对照。没有改交易条件、发表脚本、创建警报或下单。

## 复现

在原账户脚本的版本历史选择第 3 版，或粘贴上方源码更新到图表。本文 HTML 可重新生成：

```bash
cd /Users/zhangzc/fable-trading
python3 scripts/md_to_html.py analysis/p0_imacd_pine_lines_20260907.md --out-dir analysis/html
```
