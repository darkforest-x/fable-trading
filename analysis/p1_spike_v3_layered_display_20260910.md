# V3 分层观察：保留事件，减少反复开仓的视觉暗示

**本地研究版已完成，23 项静态/合成测试与独立审核通过；尚未在 TradingView 原生编译、保存或替换图表。** 本项只改显示，原 V3 的信号数量和警报条件不变。它不是已通过回测的降噪策略，主要降噪目标仍在研究中。

[打开 Pine 源码](/Users/zhangzc/fable-trading/yoyo/evaluation/pine/spike_burst_v3_layered_display.pine) · [六项过滤与原版对照](/Users/zhangzc/fable-trading/analysis/html/p1_spike_v3_noise_overview_20260910.html)

## 图上如何区分重点

| 场景 | 原 V3 / 完整显示 | 分层观察默认显示 |
|---|---|---|
| 无参考时产生原结构预警，且风险数据可用 | 结构预警大标签、亮色 K 线，新建原参考框 | 突出“观察起点”、真实收盘价、亮色 K 线；建立同一个原参考框 |
| 当前参考的原预警得到第二阶段确认 | 结构确认标签及亮色 K 线 | “参考升级”，亮色 K 线；只升级质量，不重置入场和止损 |
| 参考期间另一个结构预警 | 同样大标签和亮色 K 线，容易被当作重新开仓 | 小点及“补充观察”轻量文字，保留实际时点和价格 |
| 另一预警的后续确认 | 确认标签及亮色 K 线 | “补充确认”；不得把另一预警的质量归给当前参考 |
| 关闭标签文字、或历史文字超过平台限额 | 最近标签受平台限制 | 已加载历史中每个原事件仍有连续小点，数据窗口保留原事件与价格 |
| 切换“完整显示” | 原显示 | 恢复原标签、面板和 K 线染色 |

“补充观察”可能是独立机会，不能凭已有参考就断言是重复或噪音。参考框不是用户持仓。是否重复开单应由明确的交易管理规则决定，不能让图表标签暗示每个观察都必须下单。

六条均线、IMACD 双线、零轴、副图信号点、风险参数及盈亏双框保持原样。没有固定止盈；峰值 R 仍是历史路径参考，不是已实现收益。

## 验证证据

| 检查 | 结果 | 范围 |
|---|---|---|
| 原文件 SHA 固定 | 通过 | 原 V3 源码没有被修改 |
| 特征、ALERT STATE、REFERENCE STATE、风险函数字节守恒 | 通过 | 显示变量不反馈检测或参考状态 |
| 原常数、参数及 3 条 alertcondition | 通过 | 提示数量和通知触发语义没有改变 |
| 原风险框、六均线、双线、零轴、原 Data Window 项 | 通过 | 静态源码对照 |
| 同根/延迟确认、另一父、退出根、无效风险、前缀及状态重置 | 23 项测试通过 | 合成事件，没有读取市场样本 |
| 独立提取 Pine 显示表达式 | 96 组检查通过 | 事件守恒及参考归属 |
| plot 预算 | 静态保守上界 52，小于 64 | 不是原生编译测量 |
| TradingView 原生编译、实际深浅色视觉 | 待验 | 本机显示账号已在另一台 PC 活跃；保留该会话，未重新连接 |

参考起点编号只在已收盘原参考起点增加。确认必须同时满足原 `confirmedSignal`、参考仍活动、`parentBar == entryBar`；所有标签在真实当前 bar 绘制，时间使用当根收盘，绝不回填到父预警 K 线。

主图连续 `plotshape` 独立于文字标签生命周期。文字、悬浮说明与风险/里程碑共享原来的 300 个 label 对象上限，因此旧文字可被 TradingView 回收；这不等于事件从检测、副图或数据窗口中消失。“完整保留”指原事件与已加载数据，不承诺平台未加载的无限历史。

## 数据与统计适用范围

本项没有新市场输入、候选池或未来收益标签；没有新增 holdout 评估、训练、调参或经济模拟。市场候选数/正类率/val 样本数、val AUC、置换 p、top-decile 毛净收益、胜率和匹配随机在本项均不适用。严格对照为原 V3 检测及警报字节一致、合成输入事件不增不减、显示状态前缀一致。

交易统计请查看上方原版/V3及六项固定过滤报告，不将显示守恒当作胜率改善，也不将补充提示淡化当作成功过滤亏损交易。

## 复现

源码、测试、计划和独立审核已先提交在 `b79c863`。

```bash
.venv/bin/python -m pytest -q tests/test_spike_burst_v3_layered_display.py
python3 scripts/md_to_html.py analysis/p1_spike_v3_layered_display_20260910.md --out-dir analysis/html
```

- 原 V3 SHA：`d604051c0c633e3ee9dac4c23bbae51ce20fe9365da60aa33a760e08cf4ea882`
- 分层 Pine SHA：`5c54df7adfa0146c5e124578c5ad18d797f3372e218f07fadcb2bc631b73a2e7`
- 测试 SHA：`1f584fa672e170fb828fd41b1cc04316cb4b2e741df79e8a422ce51fb0e735e5`
- [独立审核](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-layered-display-20260910-v1/qa/independent_review.json)：`fb613a1276a1f09e06a69bfcad2f3c396c8d5aeab33daf420aad04c7d0ff176c`
- [计划](/Users/zhangzc/fable-trading/experiments/active/exp-spike-v3-layered-display-20260910-v1/PROJECT_PLAN.md)
- [实现经验](/Users/zhangzc/fable-trading/docs/learnings/display-ownership-must-not-filter-independent-signal-events.md)

## 风险与诚实声明

原 V3 仍会产生较多结构预警；本版没有降低统计上的假启动或保证未来正确信号不丢。原研究主要为 1H，其他周期不因显示改动而自动得到验证。静态与合成测试不替代原生 Pine 编译或原生/Python 全历史逐根对照。

没有接管另一台电脑的 TradingView 会话，没有改动线上通知、执行或已有图表。HTML 只完成静态文档检查；未将其宣称为真实图表视觉验收。

## 后续验收

原生活跃会话恢复到此 Mac 后，再验证编译、浅/深色、历史标签回收后的连续标记、完整模式切换及原风险框。主要研究继续区分独立启动、趋势内再次突破和真正失败，仍需事前定义、失败样本对照与独立验证，不能靠删箭头或改分母宣称完成目标。
