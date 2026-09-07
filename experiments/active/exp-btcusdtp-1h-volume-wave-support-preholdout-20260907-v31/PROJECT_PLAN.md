# V31 — K1 之前方向量改善的支持检查

## Decision and authorization

Owner: “下一步，继续”（2026-09-07）。本轮只选择一个新时序假设、实现并检查覆盖；
不是收益优化授权的扩张，不读该门新分组收益、不读2025+/holdout，不改成本/TP/SL/TV/ACTIVE。
问题：原小时大实体/吞没贯穿SMA40的251个K1入口，在出现之前是否已有同向方向量改善？
V30经济失败不变；这里不叠加V29分类器、不以78子集为母池。

## Frozen single variable, before first support calculation

ChartPrime DeltaPulse Wave [MPL2.0, © ChartPrime]：
https://www.tradingview.com/script/lfaZVLub-DeltaPulse-Wave-ChartPrime/
固定已保存source SHA e92978a8e5fa0873ba25e60c37d0d4b5bea6b33533bbc28cbcb2ad39d2a9e2fe。
源默认20/5：buy = volume if close>open else0；sell=volume if close<open else0；
total input=volume if volume>0 else1；raw=100*(EMA20(buy)-EMA20(sell))/EMA20(total)；
wave=EMA5(raw)。不采用源背离信号、offset=-1、阈值25或minGap5。

K1开盘T、闭合/决策E=T+1h。唯一门：
direction * (wave[T-1h] - wave[T-2h]) > 0。
这是方向量家族的预先改善假设，不是首次启动事件，不是订单流/CVD，也不是
ChartPrime原生买卖信号。BTC连续开盘接近前收盘，close-open不是独立信息保证。
旧15m多因子12/48滚动方向量水平及OBV12净量水平与本门不完全重复；
本轮不同时跑Gaps/FVG，不按支持率挑门。

EMA首值递归；缺小时重置整段。T-2至少100根连续历史（5×源wave长度，保守研究预热，
不是Pine原生预热契约，不测试其他长度）。T段count至少102；缺时钟/预热不足unknown；
已知反向/严格等于0为abstain，不可丢母。OHLCV有限、价格正、几何合法、volume>=0。
最新方向量证据在T即可见，比E早1小时；K1自身只核对身份/源完整，不进入门数值。
不填补缺失、不用asof、不用后续小时修正历史门。

## Population, clocks and provenance

沿用V25校验的V20保存18222小时OHLCV（2022-11-30T16至2024-12-28T22 UTC）。
先只读open_time检查行数、排序、显式UTC与截止，再materialize OHLCV。
沿用V24原SMA251/744自己时钟控制、248三控、3无匹配；原四半年55/66/55/75。
控制为同币×同UTC月×原波动桶匹配，保留原抽样与成员、无补抽。
时间分割2023H1/H2和2024H1/H2，各72h embargo；不新增其他data/行情源。
所有输入、引用源码运行前后SHA相等；source-first提交builder/config/plan/tests/audit。

## Frozen feasibility gates

原有总accepted>=80、每半年>=12、活跃UTC月>=12、每半年活跃月>=3。
只是下一收益阶段的可行性门，不是功效/显著性/盈利判断。
输出原母和原控每个own-clock context、全/方向/半年/24月62行count、251组匹配状态。
无收益label/AUC/胜率/top-decile/成本或置换收益p：本阶段不定义方向性经济标签。
同等零假设控制：合成全doji/零方向量应零通过；镜像/严格0边界与
已知方向反转互补计数；原744随机控制自身时钟的通过数作为覆盖背景，不叫alpha。

## Validation

合成标量EMA对照、零量/十字/空输入/重复时钟/非有限/价格几何/gap/预热边界、
未来/K1OHLCV扰动不影响预先门、prefix一致性、输入不变和own-control时钟。
root另写stdlib scalar replay，逐18222小时、995context、62count、251组三控重算；
不import feature模块。核对sourcehash和输出hash、initial/final相同。
审计只代表保存源上的数值一致，不是原始行情真实性、raw5聚合或Pine内置/交易一致性。
若失败，保留failure receipt；修实现先提交再只读审计，不改冻结参数。

## Report plan

Delivery HTML; technical audience because本轮是实验可行性/因果钟验证。
使用build-report、visualize-data、validate-data；portable canonical artifact，不发布Site。
结构：标题；技术摘要；定义/范围（前移至数值解释之前）；全/对照及前版本；
四半年/24月覆盖；预先门方法；未知/匹配及合成零假设/审计；风险；下一步/待答问题。
一张24月case与control通过率line，48行，0..1，蓝/金两色+明确系列、numerator/total/
unknown保留到dataset，源完整62count。不是净值/收益曲线。小表用于精确半年查数。
MD立即scripts/md_to_html.py到analysis/html，再官方portable renderer覆盖同一HTML为
最终产物。报告源、数据、QA、registry保存。若无浏览器仅structural_only，不假称手机验收。

## Stopping rule

本轮支持检查完成即交付；通过后需要另行冻结经济检验（同原控制、同4h/20bp）才可读取收益。
支持不足不降低80/12，不改20/5/100，不择另一门偷换失败。任何结果不宣称策略赚钱。
