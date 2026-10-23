# ChartArt BB＋RSI v1.1：原逻辑与亏损后翻倍
本报告是固定规则的研究模拟，源策略与仓位层分别核查。未复现用户截图那段近期交易，未运行或部署实盘。
## 原策略究竟在做什么
- RSI使用收盘价、长度6，阈值是50/50；布林带使用SMA200、总体标准差乘2。
- 多头要求同一根已收盘bar：RSI从≤50穿到>50，价格从下轨外回到下轨内；空头要求同bar RSI下穿50且价格回落到上轨内。必须两次cross同时发生。
- 原版没有固定止损、固定止盈或移动保本。持仓等相反入场信号反手，同向不重复加仓。名称中的Double是两个指标共同确认，不是资金倍投。
- strategy.entry里的stop是入场订单参数。条件成立时该触发价已经被越过，默认历史执行可简化为下一open。取消未成交订单不会平掉持仓；图表的背景颜色条件也不是入场条件。
[作者原版](https://www.tradingview.com/script/uCV8I4xA-Bollinger-RSI-Double-Strategy-by-ChartArt-v1-1/) · [TradingView订单执行文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)
## 用户截图是观察，不是本次验证结果
|截图周期|显示日期|闭合笔数|显示胜率|显示PF|显示净利润|反推平均盈利/平均亏损|
|---|---|---:|---:|---:|---:|---:|
|15m|2026-06-01—09-15|31|80.65%|2.083|+821.14 USDT|约0.50|
|3m|2026-08-24—09-15|17|76.47%|3.502|+271.97 USDT|约1.08|
|1m|2026-09-07—09-15|27|85.19%|20.8|+579.69 USDT|约3.62|
上述比例=截图PF×亏损笔数÷盈利笔数，受显示四舍五入影响。截图均显示初始资金100K，不能直接当成1000U账户收益；下单单位、手续费、滑点、保证金属性未核实。日期不一致，不能据此排周期优劣。截图15m高胜率仍可能对应平均一次亏损约两次盈利。
## 数据与研究假设
本金1000U，首单**名义仓位**100U；不是每单风险1U。原版无初始止损，所以R、止损次数、10R单数不适用。固定仓与亏损后2倍分别模拟；净赢重置、净零维持，未平仓不改变下一档。
每笔固定往返成本0.2%×入场名义价值，入场/出场各半，退出费也按入场本金；这与仓库旧研究口径一致，不代表OKX实际费表。没有额外资金费率或滑点。反手先结算再开新仓，两腿假设同open价、无延迟，因此倍投执行是乐观近似。
1x/10x仅指初始保证金预算。下一档保证金加开仓成本超过余额即停止，不能偷偷封顶后重置；bar内净权益耗尽不可因后面反弹复活。未建模OKX维持保证金与阶梯费率，真实强平通常会更早，本报告不能提供精确爆仓概率或强平价。
|源周期|源首bar UTC|批准前缀末bar闭合UTC|读取bar数|缺根/重复|
|---|---|---|---:|---|
|1m|2026-04-17 14:56:00+00:00|2026-05-01 00:00:00+00:00|19264|0/0|
|3m|2023-07-31 11:12:00+00:00|2026-05-01 00:00:00+00:00|482176|0/0|
|5m|2025-12-20 10:00:00+00:00|2026-05-01 00:00:00+00:00|37896|0/0|
|15m|2022-01-03 15:30:00+00:00|2026-05-01 00:00:00+00:00|151522|0/0|

所有计算≤2026-05-01，新增holdout消耗0次。1m仅4月18日起；3/5/15m共同1月1日起；3/15m另从2024年1月1日起。窗口分别空仓重置，较长窗口包含短窗口，**不是独立验证集**。没有从结果选参数。
## 不倍投的单位交易：胜率、亏损幅度与随机对照
单位收益用每笔入场名义本金归一；未平仓从胜率、PF和连亏排除。匹配对照是同币×同方向×同UTC月×决策时BB宽度桶的随机入场，同反向信号退出、同成本。
|周期/窗口|闭合/未平|毛胜率|净胜率|PF净|均值毛/净bp|平均赢/亏bp|最大单亏bp|最长净连亏|随机净bp|匹配超额bp|周块p / Holm|
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|1m/short_available|42/1|76.19%|66.67%|1.46|33.68/13.68|65.59/-90.13|-311.66|2|-3.31|16.99|0.2572 / 1.0000|
|3m/jan_apr|105/1|61.90%|60.95%|0.60|-34.41/-54.41|132.77/-346.59|-3450.54|4|-37.90|-16.51|0.6681 / 1.0000|
|3m/long|815/1|64.29%|60.98%|0.76|-9.35/-29.35|154.65/-316.93|-3450.54|6|-23.27|-6.09|0.6943 / 1.0000|
|5m/jan_apr|61/1|57.38%|54.10%|0.74|-20.63/-40.63|212.63/-339.11|-1557.22|5|-46.86|6.24|0.4109 / 1.0000|
|15m/jan_apr|17/1|70.59%|64.71%|0.89|-12.12/-32.12|420.31/-861.57|-2597.84|2|-63.46|31.34|0.3997 / 1.0000|
|15m/long|148/1|66.22%|65.54%|1.05|35.40/15.40|465.29/-840.28|-4156.00|3|29.87|-14.48|0.6218 / 1.0000|

随机样本每笔20次固定seed抽取，保留censored且不补抽；表中超额只基于共同可闭合样本。周块符号置换4000次，6个窗口做Holm校正；少量周块不足以证明稳健。无概率模型，val AUC、top-decile与单特征排序不适用；原固定仓是仓位实验单变量基线，匹配随机是入场的零假设对照。
## 1000U账户：固定仓 vs 亏损后翻倍
所有末权益包含未平仓按收盘估值并预留退出费。回撤使用bar内假定价格路径，顺序为open→较近极值→另一极值→close。每个账户表行的策略方向背景对照见上表对应窗口，不能把重叠随机交易拼成可执行账户。
![共同窗口账户比较](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/comparison.png)
|周期/窗口|保证金预算|仓位规则|末权益U|净盈亏U|最大回撤U/%|最高执行/请求倍数|已平笔数|亏后首赢仍未回本的循环|停止原因|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|1m/short_available|1x|固定|1005.55|+5.55|5.68/0.57%|1/1|42|8|未停止|
|1m/short_available|10x|固定|1005.55|+5.55|5.68/0.57%|1/1|42|8|未停止|
|1m/short_available|1x|亏后2倍|1011.52|+11.52|6.55/0.65%|4/4|42|6|未停止|
|1m/short_available|10x|亏后2倍|1011.52|+11.52|6.55/0.65%|4/4|42|6|未停止|
|3m/jan_apr|1x|固定|942.08|-57.92|67.30/6.73%|1/1|105|20|未停止|
|3m/jan_apr|10x|固定|942.08|-57.92|67.30/6.73%|1/1|105|20|未停止|
|3m/jan_apr|1x|亏后2倍|927.62|-72.38|109.76/10.96%|8/16|51|6|insufficient_margin|
|3m/jan_apr|10x|亏后2倍|1069.77|+69.77|132.83/12.97%|16/16|105|13|未停止|
|3m/long|1x|固定|759.98|-240.02|264.38/26.04%|1/1|815|145|未停止|
|3m/long|10x|固定|759.98|-240.02|264.38/26.04%|1/1|815|145|未停止|
|3m/long|1x|亏后2倍|852.58|-147.42|200.21/19.61%|8/16|219|35|insufficient_margin|
|3m/long|10x|亏后2倍|977.07|-22.93|273.57/26.80%|64/64|815|111|未停止|
|5m/jan_apr|1x|固定|974.40|-25.60|48.22/4.82%|1/1|61|15|未停止|
|5m/jan_apr|10x|固定|974.40|-25.60|48.22/4.82%|1/1|61|15|未停止|
|5m/jan_apr|1x|亏后2倍|982.40|-17.60|85.93/8.48%|8/16|37|6|insufficient_margin|
|5m/jan_apr|10x|亏后2倍|1147.54|+147.54|240.12/20.34%|32/32|61|7|未停止|
|15m/jan_apr|1x|固定|994.83|-5.17|39.87/3.90%|1/1|17|2|未停止|
|15m/jan_apr|10x|固定|994.83|-5.17|39.87/3.90%|1/1|17|2|未停止|
|15m/jan_apr|1x|亏后2倍|1009.54|+9.54|56.68/5.46%|4/4|17|2|未停止|
|15m/jan_apr|10x|亏后2倍|1009.54|+9.54|56.68/5.46%|4/4|17|2|未停止|
|15m/long|1x|固定|1023.08|+23.08|92.31/9.15%|1/1|148|27|未停止|
|15m/long|10x|固定|1023.08|+23.08|92.31/9.15%|1/1|148|27|未停止|
|15m/long|1x|亏后2倍|1210.83|+210.83|124.44/10.76%|8/8|148|19|未停止|
|15m/long|10x|亏后2倍|1210.83|+210.83|124.44/10.76%|8/8|148|19|未停止|

“亏后首赢仍未回本”指连续净亏后第一次净赢结束的循环总和仍为负，不包括还没结束的亏损循环。保证金不足不是已经爆仓，也不能标成完成整个区间。最高请求倍数包含因资金不足未执行的下一档。
## 风险与诚实声明
- 这是原版交易语义的独立Python重建，未在用户TV逐笔比对。UI属性、历史预热起点和旧Pine版本差异可能影响截图笔数；不声称已经复现截图。
- 图表高胜率与倍投可持续性是不同问题：无固定止损使每次亏损幅度不同，下一笔翻倍后的盈利不保证盖住上一笔亏损。表内用真实循环净额检验。
- 没有优化入场或止盈止损，没有加保本。不得将这些结果套到V9，或把观察性好成绩标记为实盘准入。没有训练、promote或真金改动。
- 本轮只消费5月以前价格。用户提供的近期截图已有结果暴露，不能作为盲测；复核其逐笔必须另行记录该新配置的明确holdout授权。
## 复现与逐笔文件
冻结builder提交：`28156aff38c34a7bffe67d2208978081739fba4c`。源码证据见source_receipt.json，实际输入及所有结果SHA见results/inputs.json和manifest.json。原作者完整源码仅保存在本地source/；不重新公开发布。
```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_chartart_bbrsi.py tests/evaluation/test_chartart_bbrsi_study.py tests/evaluation/test_chartart_martingale_account.py
# Builders/config/plan/tests must already match committed HEAD. Results path must not exist.
.venv/bin/python -m yoyo.evaluation.chartart_bbrsi_study
.venv/bin/python experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/report.py
.venv/bin/python scripts/md_to_html.py analysis/p1_chartart_bbrsi_martingale_20260915.md --out-dir analysis/html
```
完整结果含每笔方向、进出价、净收益、最大浮亏与浮盈，倍投下单数额、循环亏损、停止记录；密集账户曲线本地保留，哈希入manifest。
- 1m/short_available（2026-04-18T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/1m_short_available/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/1m_short_available/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/1m_short_available/m2_l10_trades.csv)
- 3m/jan_apr（2026-01-01T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_jan_apr/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_jan_apr/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_jan_apr/m2_l10_trades.csv)
- 3m/long（2024-01-01T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_long/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_long/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/3m_long/m2_l10_trades.csv)
- 5m/jan_apr（2026-01-01T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/5m_jan_apr/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/5m_jan_apr/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/5m_jan_apr/m2_l10_trades.csv)
- 15m/jan_apr（2026-01-01T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_jan_apr/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_jan_apr/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_jan_apr/m2_l10_trades.csv)
- 15m/long（2024-01-01T00:00:00Z—2026-05-01T00:00:00Z）：[原交易](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_long/unit_trades.csv) · [1x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_long/m2_l1_trades.csv) · [10x保证金倍投](/Users/zhangzc/fable-trading/experiments/active/exp-chartart-bbrsi-martingale-20260915-v1/results/15m_long/m2_l10_trades.csv)

## 下一步

先依据本表判断倍投是否改善同一窗口；若要对应截图31/17/27笔，则需确认TV属性并授权这套新配置读取截图期间历史，冻结后只作最终核对。任何另加固定止损、分批止盈或保本均属于后续新实验。
