# 金标形态指标：目标纠偏与首轮回放（2026-09-14）

**当前结论：V1没有对齐Owner的金标识别目标；候选A提高了框内识别，但误报过多，也未达到可交付标准。V2源码只留作验证候选，本轮未将它保存到TradingView。**

[打开逐图对照：156张开发段人工审核图](/Users/zhangzc/fable-trading/experiments/active/exp-gold-ma-indicator-20260914-v2/results/gallery.html)

## Owner请求与参考身份

Owner原话：“写的不太对！没有达到我想要的效果，我想要的是通过指标，识别我们之前做 yolo 数据集，打标的金标那些能识别出来”。这明确否定了单凭初始截图自定“线下缓跌”的验收方法。

本轮暂以2026-09-08T13:05:56Z的真实人工答案快照为工作基准，未把模型预框当作金标。该快照共323个有效人工框（166多头、157空头），33个明确无目标答案，12个矛盾答案。它们属于2513历史图的人工改框审核池，并不是后来的1043张HL2自动候选池；它们仍未被自动升级为新发布Gold。

仓库另有旧 `owner_short_gold_center_v1` 的1345条来源确认正例，以及 `gold_v1.jsonl` 的23个Owner正例与1个IGNORE。三者确认层级不同，本轮不混为一个验收集。已向Owner询问应以近期人工审核还是较早收藏/打星金标为主，以及是否允许右侧3～5根确认；截至本报告生成尚未收到该澄清。

## 数据、时间与对照口径

- 开发段为主窗口完整收盘不晚于2025-07-01T00:00:00Z：134个正框、22个无目标答案，95个源CSV。全部156例通过原始索引/时间/连续性与主窗口OHLC+六MA摘要核对，源读取失败0。
- 剩余200个人工答案（189正框、11无目标）保留，未运行候选评分；12条矛盾答案不计作正负。原始train/val字段只作来源记录，实验按时间划分。
- 实际源为本批图使用的close SMA/EMA20/60/120；不是另一个HL2提案池。每个读取前缀在物化OHLC前先检查时间边界，至多到该开发组最后一张主窗口的收盘。没有读取2026-05-04起的holdout OHLC。
- 原人工矩形的x范围包含哪些K线中心，就映射为哪些核心bar；不采用居中裁一半的旧自动框，不重新主观画框。这里只验实际事件点是否落在原框的时间范围，不能称为完整二维YOLO框IoU一致。
- 样本是逐人工答案/逐框计数，同一源相邻或重叠事件可能重复出现；134并非已去重的134个独立市场事件，不做独立样本显著性声明。
- 无目标误报按整张主窗口中出现任意对应事件计算。未知背景不伪装成负例。

## 同表结果

| 版本与事件 | 框内命中 | 仅框后1～3根 | 仅框后4～5根 | 无目标图误报 | 错位边界对照均值 |
|---|---:|---:|---:|---:|---:|
| 旧V1 识别/预警 | 12/134 (9.0%) | 0 | 0 | 9/22 | 4.9% |
| 候选A 识别/预警 | 84/134 (62.7%) | 10 | 0 | 18/22 | 26.5% |
| 旧V1 后续确认 | 9/134 (6.7%) | 3 | 0 | 5/22 | 1.2% |
| 候选A 后续确认 | 95/134 (70.9%) | 10 | 4 | 16/22 | 11.2% |

“后续确认”在真实确认bar记录，可能比最早候选标记更接近人工核心，所以其框内比例可以高于识别标记。该结果不是提前预测证明。候选A仅识别标记的多/空分别命中47/73、37/61；V1多头分支不存在，空头仅12/61。

错位边界零假设对照：固定实际同向事件与原框宽度，在同图里枚举所有与原人工框不相交的合法起点，计算这些位置的命中比例，再逐图等权平均。它衡量时间定位是否集中于人工框，**不是**将错位处标成真实负例，也不提供市场精确率。表中比较显示有部分定位信息，但16/22或18/22的明确无目标误报足以否定当前候选可用性。

初始结果JSON保留了字段 `no_hit_by_available_end`，它实际表示“未框内命中且未在可见的框后前5根命中”，命名过宽；不能理解为整张图从未有标记。下列补充由原始事件列表直接计算，不重跑或覆盖冻结结果：

- v1：有框前标记的图 9 张；有框后第6根及更晚标记的图 0 张。此计数可与框内命中重叠，不并入召回分母。
- candidate_a：有框前标记的图 64 张；有框后第6根及更晚标记的图 2 张。此计数可与框内命中重叠，不并入召回分母。

## 偏差与实现证据

V1要求六码下压、多数收盘在所有均线下方，而且只做空；这从目标定义就排除了大量人工框。候选A在双向密集、交织/收缩、实体接触、短MA转向条件下识别，召回提高，但把普通盘整、噪声转向和延续图也放进来。冻结破位确认只能小幅降低误报，不能修复这种形态语义偏差。

Python行为检查12项通过。已执行原样Pine源码的辅助PineTS0.9.33测试：1m/3m/15m四类事件位置与Python相同；平坦反例、截断前缀、未来价格与时间变异检查通过，共6组。使用完全合成OHLC，不是市场泛化证据。候选A未进行TradingView原生编译或云保存，当前不能宣称TV原生行为已经验证。

官方运行语义参考：[TradingView barstate.isconfirmed](https://www.tradingview.com/pine-script-docs/concepts/bar-states/#barstateisconfirmed)、[Pine执行模型](https://www.tradingview.com/pine-script-docs/language/execution-model/)。

## 复现命令与版本

```bash
cd /Users/zhangzc/fable-trading
python3 -m pytest -q tests/test_ma_drift_v1_reference.py tests/test_owner_gold_indicator_bridge.py tests/test_gold_ma_candidate.py
PYTHONPATH=. python3 experiments/active/exp-gold-ma-indicator-20260914-v2/replay_gold.py --phase dev --output-root experiments/active/exp-gold-ma-indicator-20260914-v2/reproductions/check1 --verify-against experiments/active/exp-gold-ma-indicator-20260914-v2/results/dev/source_audits.json
PYTHONPATH=. python3 experiments/active/exp-gold-ma-indicator-20260914-v2/replay_gold.py --phase dev --signals confirmation --output-root experiments/active/exp-gold-ma-indicator-20260914-v2/reproductions/check1 --verify-against experiments/active/exp-gold-ma-indicator-20260914-v2/results/dev_confirmation/source_audits.json
node experiments/active/exp-gold-ma-indicator-20260914-v2/verify_pine.mjs
python3 experiments/active/exp-gold-ma-indicator-20260914-v2/build_report.py
```

初始识别回放的源提交为 `428a4ef6c559e2c461aa70a7ced44e914a6c02ee`；确认回放为 `1a05685bff88c39256b81ca7d5d10a280f79cb32`。回放器要求源已提交；已存在的结果目录不允许覆盖。以上命令将复现写入新的reproductions/check1目录，并在计算指标之前核验完整前缀摘要；重复执行需另取check2等新目录，不得删除本轮证据来重跑。Python/PineTS检查不需要重跑真实行情。所有输入元数据摘要、原始有界OHLC前缀摘要和源码摘要保存在结果审计中；未来复现须逐项比对，不能只看文件名。

## 风险与诚实声明

- 本轮是非收益的形态识别工程诊断。val AUC、收益排序置换p、top-decile毛/净收益、胜率、交易单特征基线和匹配随机入场收益均不适用：没有收益标签、没有持仓或入场实验；替代对照为上表的原始无目标答案与同图边界错位零假设。
- 后段验证样本尚未评分。图内有命中不代表指标能辨别所有金标，也不保证1m/3m迁移；两个用户给出的2026年9月ETH具体点位未被本配置读取/评分。
- 主窗口OHLC与close MA与原标注渲染摘要一致；ATR使用本轮所记录源前缀计算，未宣称它与原审核时某个未记录ATR序列逐值一致。当前完整源前缀已绑定摘要，主窗口摘要不能代替该前缀摘要。
- 这不是新训练或策略上线，training_eligible、production_eligible维持false，无订单、仓位、ACTIVE、frozen、forward或告警更改。

## 下一步

先根据Owner回复选定唯一主基准及允许的确认时点；以图库中的实际漏检/误报样本为反例，定义下一条有解释的形态差异后再开新候选版本。当前候选A应保留为失败证据，不以放宽阈值或把所有标记都算命中来交付。
