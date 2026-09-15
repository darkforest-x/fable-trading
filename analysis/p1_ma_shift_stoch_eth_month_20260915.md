# ETH 近一月：15分钟颜色 × 5分钟 Stoch

**反向箭头全平主方案：1000U → 829.64U，净收益 -17.04%，最大持仓回撤 17.72%。** 已平仓116笔，净胜率37.07%，期末持仓1笔。

同区间单独用Stoch：-30.11%。加入15m颜色后的账户收益变化为13.08个百分点；这是一段已暴露历史的冻结规则回测，不是生产准入结论。

## 1. 你当前图上的两个指标

TradingView桌面实查为OKX:ETHUSDT.P、普通K线。MA Shift [ChartPrime]设为SMA40、hl2；K线染色由hl2相对SMA决定。Stoch为你保存的「翻身版本V1-2025-07-06」，K/D为5/3/3。

- 多头：最近已收盘15m的hl2 ≥ SMA40(hl2)，且5m的K上穿D、交叉当根K和D都 <20。
- 空头：最近已收盘15m的hl2 < SMA40(hl2)，且5m的K下穿D、交叉当根K和D都 >80。
- 相反5m箭头收盘后，下一根开盘全平；退出不受15m方向限制。相反方向同时准入时允许平后反开。15m单独翻色不平仓。
- 单仓、不加仓，无额外止损/固定盈利目标。分批触发点和比例尚未确定，本次没有替你挑选盈利目标。

MA Shift的15/0.5振荡器参数不参与K线染色；Stoch的多头报警额外含WVF绿色柱条件，本次按图上箭头而非报警。公式来自当前编辑器，尚未做TradingView全部历史逐bar数值核验。[ChartPrime原始发布](https://www.tradingview.com/script/aApUyBnk-Moving-Average-Shift-ChartPrime/)

## 2. 数据、成交和成本

北京时间2026-08-15 00:00—2026-09-15 18:15，冻结结束时间，不随运行延长。OKX ETH-USDT-SWAP原生5m，共9867根，其中720根只预热；区间9147根。缺根0、重复0、未收盘0；15m严格由同源完整三根聚合。

信号只在收盘确认，下一根开盘成交；同收盘的5m可使用刚完成的15m，但较早5m不能看到该15m最终颜色。起点空仓，期末未平仓只按最后收盘估值并预留退出费，排除真实平仓胜率。

资金示例每笔按入场前权益1倍名义建仓，初始1000U，逐笔复投；20bp往返以入场名义计，开平各10bp。没有额外资金费率或滑点，不能当成交易所真实账单。最大回撤从每根5m收盘估值计算，未模拟K线内最坏路径。

**这是该配置第1次消耗holdout。** 用户本次明确要求近一月；原预热和评估区间、单Stoch消融及匹配控制均提前冻结，未调参。旧策略曾观察本历史，不能称为盲OOS。源码冻结提交 `6864e834895fc38b39cc737ad22c3483fdb708d6`。

## 3. 结果与单变量基线

|规则|准入箭头|入场|已平仓|净胜率|PF净收益比|单笔毛bp|单笔净bp|1x账户净收益|最大回撤|匹配随机净bp|配对超额bp|
|---|---|---|---|---|---|---|---|---|---|---|---|
|15m颜色 + 5m Stoch|176|117|116|37.07%|0.58|4.62|-15.38|-17.04%|17.72%|-5.66|-10.11|
|单用5m Stoch|356|191|190|45.26%|0.58|2.57|-17.43|-30.11%|32.73%|-4.72|-12.63|

PF采用逐笔净收益率正值之和/负值绝对值之和。每笔bp以该笔入场名义为分母；账户收益含复投和期末估值，不能把逐笔收益率直接相加冒充账户收益。没有旧版同策略，表中单Stoch是去掉MA过滤的消融基线。

![1x净权益与持仓回撤](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/results/equity.png)

组合每笔平均毛收益4.62bp，扣20bp后为-15.38bp；单Stoch对应2.57→-17.43bp。MA改变准入及后续空仓机会，因此两组交易不是简单的一一删选关系。

## 4. 多空与时间稳定性

|规则|方向|已平仓|净胜率|平均净bp|PF|同方向匹配随机净bp|
|---|---|---|---|---|---|---|
|15m颜色 + 5m Stoch|多|59|42.37%|-8.95|0.77|-5.46|
|15m颜色 + 5m Stoch|空|57|31.58%|-22.05|0.37|-5.86|
|单用5m Stoch|多|95|50.53%|-2.06|0.93|-4.10|
|单用5m Stoch|空|95|40.00%|-32.79|0.37|-5.33|

|规则|按入场分段|已平仓|平均净bp|PF|分段匹配随机净bp|
|---|---|---|---|---|---|
|15m颜色 + 5m Stoch|前半窗|56|-11.80|0.62|4.09|
|15m颜色 + 5m Stoch|后半窗|60|-18.73|0.56|-14.91|
|单用5m Stoch|前半窗|92|-26.89|0.42|-3.35|
|单用5m Stoch|后半窗|98|-8.55|0.76|-6.02|

上表仅按入场时间把连续回测账本分两半，不重开账户、不改退出，不用这两半选参数；对应随机对照完整保留在CSV（同UTC日桶），全期对照见主表。

组合最长连续净亏10笔；持仓中位187.50分钟、最长750.00分钟。最大单笔净收益6.39%、最小-3.25%。无初始止损风险单位，因此R、10R频率不适用。

## 5. 匹配随机对照和统计边界

|规则|配对数|未配对|独立控制键数|UTC日块|目标净bp|控制净bp|超额bp|单侧p|双侧p|
|---|---|---|---|---|---|---|---|---|---|
|15m颜色 + 5m Stoch|115|1|115|31|-15.77|-5.66|-10.11|0.8396|0.3110|
|单用5m Stoch|188|2|188|32|-17.35|-4.72|-12.63|0.8310|0.3384|

每个已平目标抽一个同ETH、同方向、同UTC日、同20根均值真实波幅/close固定桶的随机入场；不看未来结果重抽。控制使用同一反向箭头退出、同成本。随机交易可能跨目标重复或重叠，因此只作事件对照，不能把它们相加为真实单仓账户。少数删失配对被列出，目标/控制均已平仓才入配对检验。

预设9999次UTC日块符号置换，单侧检验正超额、双侧检验差异；零假设依赖日块交换对称，仅为描述性配对检验。约一个月、跨日持仓及有限波动桶可能残留相关性，p值不证明因果或未来盈利。

**必报项的适用性：** 无模型训练、验证集或预测概率，val样本数/val AUC不适用；没有预先定义排序分数，top-decile毛净收益不适用，不能用事后收益排序来凑指标。替代证据是全部冻结交易、单Stoch消融、匹配随机对照、按日块置换。

## 6. 风险与诚实声明

- 本次只对反向箭头全平给结论，分批止盈仍待确定规则。没有止损意味着持仓风险只由未来反向箭头终止，表内历史最大亏损不是风险上限。
- 15m使用收盘确认颜色；实时正在形成的颜色可能变化，本研究没有重建盘中颜色变化。
- 20bp为固定研究成本，资金费率、额外滑点、强平、交易量冲击均未建模，净结果可能进一步变差。
- 周期与标的为用户指定；单月可能受行情主导，已经暴露的历史不适合继续挑最佳参数。
- 当前公式和时间处理经源码及定向测试核对，未宣称TradingView策略测试器与Python全部成交逐笔一致。
- 研究结果不能自动promote，training_eligible与production_eligible均为false。

## 7. 复现、验证与交付

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.data.spike_eth_yolo_sources --config experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/config.json --output experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/sources
.venv/bin/python -m pytest -q tests/test_ma_shift_stoch.py tests/test_spike_fanshen_exit.py
.venv/bin/python -m yoyo.evaluation.ma_shift_stoch_study
.venv/bin/python -m yoyo.evaluation.ma_shift_stoch_report
```

上面的研究runner拒绝覆盖已有summary；从零复现需在没有结果的新恢复副本中使用冻结源文件，或保留原results后单独安排重现版本。抓取器可验证相同配置缓存；以已冻结CSV SHA作为本次事实，不能把未来重新抓取后改变的行情视为同一输入。

来源CSV SHA256：`5d33100baf3eb9ef3ce4b4032d141e2aed9d94701319802da3c1924fbb8dd086`。验证收据和独立核对随结果目录保存。

[组合全部逐笔CSV](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/results/ma_stoch_trades_zh.csv) · [组合成交份额账本](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/results/ma_stoch_fills.csv) · [随机配对CSV](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/results/ma_stoch_controls.csv) · [所有结果摘要](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/results/summary.json)

## 8. 下一步

先按逐笔记录核对入场箭头和15m颜色。若继续比较分批止盈，需要Owner先定触发点与每批比例；新退出规则另建版本，保留本次失败或成功记录，不在当前月上反复择优后声称样本外有效。

## 9. 验证记录

18项Stoch/多周期执行定向测试通过；独立只读核对全部306笔已平交易、份额成交、复投/期末权益和随机配对，未发现可复现的正确性缺陷。

项目边界/因果/parity检查：468通过、7失败。5项被已有TOTAL2资产缺source_commit阻断，另2项为已有candidates.py与render.py迁移哈希不一致，与本轮新引擎无关；未改写旧资产或绕过守门。详见实验gates.log。

HTML结构检查4表、1张内嵌图、所有本地链接存在；已目视检查独立权益图。内置浏览器URL安全策略拒绝本地file地址，因此未完成HTML浏览器像素验收，未绕过该限制。

[独立核对](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/review.md) · [验证收据](/Users/zhangzc/fable-trading/experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1/validation.json) · [Spike Notion研究记录](https://app.notion.com/p/3dc8856479af81828e6cc91ec50f88d0)
