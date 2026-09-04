# P0 — 双关键 K 线 + SMA40 回踩 Pine v6 指标交付（2026-09-04）

> 结论先行：Pine v6 指标已经完成，并由 TradingView 官方编译器以 0 个错误通过。
> 它是研究/标注指标，不会自动下单；默认用于宽口径形态召回，不把图例相似度冒充盈利概率。

## 交付物

| 项目 | 路径 / 结果 |
|---|---|
| Pine 源码 | experiments/active/exp-two-key-candle-feature-atlas-v3/pine/fable_two_key_candle_sma40_retest_v1.pine |
| 官方编译回执 | experiments/active/exp-two-key-candle-feature-atlas-v3/results/pine_compile_receipt.json |
| 一致性验证 | experiments/active/exp-two-key-candle-feature-atlas-v3/results/pine_validation.json |
| 验证脚本 | scripts/validate_two_key_candle_pine_indicator.py |
| 自动测试 | tests/test_two_key_candle_pine_indicator.py |

TradingView 官方 Pine v6 编译结果：source SHA-256 为
8df89cb961a9ac50df10af6cbd8be9a9a980286dffa077c7a0b657194082aeda，
编译错误数 0。编译器随后进入“添加到图表”阶段，但当前 Basic 布局已达到指标数量上限，
因此没有完成叠加。没有保存或发布脚本，也没有删除 owner 现有指标或改变布局。

## 指标在图上做什么

| 层 | 行为 |
|---|---|
| 主参考线 | SMA40(HL2)，与图中的 Moving Average Shift 设置一致 |
| 背景均线 | SMA/EMA 20、60、120 六线与绳带，可独立隐藏 |
| K 线颜色 | HL2 在 SMA40 上方为青色，下方为橙色；不是原生涨跌色 |
| K1 | 方向实体贯穿/近似贯穿 SMA40，检查实体、范围、收盘位置、ATR 深度与可选量能/颜色 |
| K1→K2 | 搜索 2–8 根或 3–6 根距离；严格档同时检查收盘距离、顺向延伸、错误侧收盘、颜色连续与 K2/K1 量比 |
| K2 | 方向侧影线触碰 SMA40、实体较小、拒绝位置合格、收盘回到趋势侧 |
| 状态 | 还原 MA Shift 四态振荡器与延迟确认的 Market Break 10/10 结构状态 |
| 入场 | K2 收盘只显示确认圆点；下一根第一笔更新才产生因果入场三角形 |
| 风控画法 | 下一根 open 为入场，K2 极值为止损，并画出可调 R 倍目标框 |
| 告警 | K2 收盘预警与下一开盘正式事件均有独立 alertcondition；动态 alert 带 entry、stop、target、gap 和形态分 |
| HUD | 显示周期、档位、距离、MA 色、振荡器、10/10 结构和最近事件 |

## 三个预设档位

| 档位 | 目的 | 关键差异 |
|---|---|---|
| Broad recall · 2–8 | 默认候选召回 | K1 实体≥0.50、范围≥1 ATR、方向收盘≥0.75；K2 影线≥0.45、实体≤0.50、触线深度 -0.05～1.50 ATR |
| Owner morphology · 3–6 | 更接近两张图例 | K1 实体≥0.65、范围≥1.25 ATR、量≥1.25 倍；K2 影线≥0.60；再加路径连续性与量比约束 |
| Owner morphology + state · 3–6 | 图例状态完整复刻 | 在上一档基础上要求 K1 振荡器顺向加速、K2 同向但冷却、10/10 结构同向 |
| Custom | 手工研究 | 所有形态、距离、路径、量能、风险和状态门均可调 |

默认选择 Broad recall，不默认选择严格档。原因不是代码限制，而是 V2/V3 的跨期结果：
严格视觉相似度没有稳定盈利能力，越像 owner 图例也没有越高的收益排序。指标中的“形态分”
只回答像不像图例，不回答是否应该下单或下多大仓位。

## 因果时点

完整 K2 形态必须看到该根的 high、low、close 才能确定，所以：

1. K2 收盘时：显示 K2 confirmed，只是形态成立预警。
2. 下一根开盘时：open 已可观察，才计算 open 到 K2 极值的真实风险距离。
3. 风险位于当前档位允许的 ATR 区间后，才显示正式入场、止损与目标框。

实现使用 barstate.isconfirmed 固定 K2，使用 barstate.isnew 处理下一根第一笔更新。
TradingView 官方说明 confirmed 在历史棒和实时棒最后一次更新为真，而 isnew 在每根历史棒及实时棒第一笔更新为真；
这正好对应上面的两个时点：[Bar states](https://www.tradingview.com/pine-script-docs/concepts/bar-states/)。
指标执行模型与实时 rollback 依据：[Execution model](https://www.tradingview.com/pine-script-docs/language/execution-model/)。

## 两张 owner 图例的一致性

| 图例 | Pine 宽口径候选 gap | 因果入场 | 精确 K2 止损 | 形态分 | 结果 |
|---|---:|---:|---:|---:|---|
| 空头 2026-09-01 | 仅 gap=6 | 78135.5 | 78388.0 | 81.5377 | 通过 |
| 多头 2026-09-03 | 仅 gap=3 | 77771.9 | 77050.0 | 83.4443 | 通过 |

两组均在正确方向 2/2 命中；把同一 K2 时间的方向反转后 0/2 命中。
反转方向是本轮非方向性实现 QA 的零假设对照：若代码只是宽松地把任意长影线都当信号，
反向也会通过；实际为 0，说明方向化贯穿、影线和收回关系确实生效。

## 为什么本轮不报告收益指标

本轮只把已经完成的因果形态规格移植为 Pine 指标，并验证编译与两张明确锚点的一致性，
没有新增交易假设、训练、参数选择或收益评估。因此 val AUC、top-decile 收益、PF、
置换检验和匹配随机入场对照按字面不适用，不能编造。盈利性证据仍以
analysis/p0_two_key_candle_ma_retest_deep_dive_20260904.md 为准：55 个维度跨期通过数为 0。

本轮只读取两组 owner 明确给出的 2026-09 形态时间和紧随其后的因果 open；
没有读取其后收益、止盈止损结果、信号全集或任何聚合指标，也没有把这些时间用于选参。
项目正式 holdout 未被用于模型或策略验收。

## 使用方法

1. 在 BTCUSDT.P 图表切到 1 小时。
2. 打开 Pine 编辑器，把 Pine 源文件全文粘贴进去。
3. 点击“添加到图表”。若仍提示指标数量上限，需要 owner 自己决定移除哪个现有指标；本轮没有替 owner 删除。
4. 初次使用保留 Broad recall · 2–8；蓝色标签是 K1，橙色标签是 K2，三角形是下一根开盘的因果入场。
5. 创建预警时可单独选择 Long/Short K2 confirmed 或 Causal long/short next-open；若选 Any alert() function call，可收到含价格的动态消息。
6. 修改脚本输入后，TradingView 已运行的旧预警不会自动更新，需要删除旧预警并按新输入重建。官方说明见 [Alerts](https://www.tradingview.com/pine-script-docs/concepts/alerts/)。

风险/收益框使用 line、box 和 label 对象；TradingView 对对象数量有限制，脚本已在 indicator 声明中显式设置上限，
旧对象会由平台垃圾回收。官方对象行为见 [Lines and boxes](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/)
与 [Text and shapes](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/)。

## 复现命令

    PYTHONPATH=. .venv/bin/python scripts/validate_two_key_candle_pine_indicator.py
    PYTHONPATH=. .venv/bin/pytest -q tests/test_two_key_candle_pine_indicator.py
    python3 scripts/md_to_html.py analysis/p0_two_key_candle_sma40_pine_indicator_20260904.md --out-dir analysis/html

当前验证结果：脚本静态契约全部通过，官方编译回执与源码 SHA 一致，owner 锚点 2/2，
反向零假设 0/2，自动测试 1 passed。

## 风险与诚实声明

- 这是指标，不是实盘策略，不会下单、撤单、改变仓位或接触 API key。
- 形态分是视觉相似度，不是概率、收益预测或仓位权重。
- 下一开盘标记在历史图上位置正确，但实时告警仍受交易所首笔 tick 与 TradingView 服务器延迟影响。
- 指标默认锁定 1h；关闭周期锁后可在其他周期运行，但参数语义和已有研究证据不再成立。
- Owner 严格档是复刻工具，不是已验证盈利参数。
- 当前 Basic 布局达到指标数量上限；源码已编译通过，但本轮未擅自移除现有指标以腾位置。
