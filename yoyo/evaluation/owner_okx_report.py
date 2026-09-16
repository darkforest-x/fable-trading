"""Build a local, source-backed Chinese report from the reviewed OKX audit.

No market data or trading actions. Numerical findings are descriptive; the
personal trading protocol is explicitly a simulation hypothesis, not a model
promotion or a backtested optimal parameter set. Runtime is the isolated
bundled Python environment, not the project training environment.
"""
from pathlib import Path
import csv
import json
import re
import subprocess
import sqlite3
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-owner-okx-history-20260916-v1'
DATA = ROOT / 'data/owner_okx_history_20260916'
S = json.loads((DATA / 'summary.json').read_text())
O = S['overall']

def money(x):
    return f'{x:+,.2f}'

def pct(x):
    return f'{100*x:.2f}%'

def table(rows, fields):
    head='| '+' | '.join(label for key,label,fmt in fields)+' |'
    out=[head,'| '+' | '.join('---' for _ in fields)+' |']
    for row in rows:
        out.append('| '+' | '.join('—' if row.get(key) is None else fmt(row[key]) for key,label,fmt in fields)+' |')
    return '\n'.join(out)

F=[('group','分组',str),('n','记录数',str),('gross','毛盈亏 U',money),('fee','手续费 U',money),('net','重构净盈亏 U',money),('win_rate','净胜率',pct),('profit_factor','净利润因子',lambda x:f'{x:.3f}')]
TITLE='你的交易复盘与执行系统'
GENERATED=datetime.now(timezone.utc).isoformat()
report=f'''# {TITLE}

## 核心结论

- **这份记录里的交易总体亏损，暂不支持“已经能够稳定盈利”。** 2024 年 2 月 23 日至 2026 年 9 月 13 日，4,898 条 USDT 持仓记录重构净损益 **{money(O['net'])} USDT**；另有一条 BTC 币本位记录单独列示。这里评价的是这份文件，不是你全部资产或所有账户。
- **你确实抓到过大行情，但正收益高度集中。** 最大一笔 BTC 空单净赚 **29,002.99 U**；其余记录合计 **−43,831.47 U**。这笔盈利是真实成绩，也需要检验能否以可承受风险重复。
- **主要问题是交易优势尚不稳固、费用侵蚀和风险约束失效同时存在。** 毛盈亏 +15,129.63 U，手续费 −27,811.16 U；177 条强平记录净损益 −9,983.44 U。即使只看普通全部平仓记录，仍净亏 −4,712.98 U。
- **下一步建议建立一套小风险、低复杂度、可记录、先模拟验证的系统。** 本报告给出完整操作草案。它不是已验证盈利策略，也不意味着现在应该加本金或放大杠杆。

## 1. 先把账算对：本报告能说明什么

文件名申请区间为 2023 年 9 月 14 日至 2026 年 9 月 14 日，但实际最早记录是 2024 年 2 月 23 日。2024 年只有 79 条原始记录、若干月份没有记录，无法分辨是未交易还是导出覆盖不全。**缺记录不能当作零交易或零盈亏。** 时区按文件声明使用北京时间 UTC+8。

原始 4,899 条、21 列；没有整行重复、持仓周期键重复、空字段或负持仓时间。90 条保证金币种写为“−”：其中 89 条由合约名与价格公式识别为 USDT 线性合约，另 1 条为 BTC-USD 币本位合约，不能把 BTC 数量直接加进 U。

**一行是一段持仓周期的累计快照，不是一笔成交。** 4,898 条 USDT 记录包括 4,702 条全部平仓、177 条全部强平、19 条部分平仓。金额统计保留部分平仓已列金额；持仓时长比较排除这 19 条。它们的剩余仓位和后续结果未知。

净损益口径：**收益额 + 累计手续费 + 累计资金费用 + 强平清算费**，全部保留原正负号。4,898 条线性记录的收益额均能由“方向 × 累计平仓张数 × 面值 × 乘数 ×（平仓均价−开仓均价）”复算，最大差异小于 0.00004 U，因此这份 CSV 的收益额是价格毛盈亏，不能套用 App 上其他“已实现收益”标签的净口径。[OKX 导出字段说明](https://www.okx.com/help/how-to-check-download-order-history-position-history-and-trading-history)；[OKX API 字段定义](https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history)。

币本位那一条为 2024 年 6 月 10—11 日 BTC 多单，部分平仓；已列净损益约 **+0.0004480031 BTC**，不作汇率换算。即使完全不另计强平清算费，USDT 净损益仍为 **−12,202.34 U**，总体亏损判断不依赖这一项。CSV与API的逐字段对应是官方定义结合本文件公式复核的推断，未登录账户逐项勾稽；API另有settledPnl列而此CSV没有，故只重构已列项，任何未列结算收益必须由账单补证。强平成交手续费不再额外加一次，避免与累计手续费重复。

缺少出入金、转账、返佣、现货及其他账户、逐笔成交、余额与权益快照。因此不报告账户本金回报率、年化、夏普、真正最大回撤、真实有效杠杆或历史 R；原表“收益率”也不能平均成账户收益率。以下“净”均指**CSV 已列项目的重构净损益**，不是完整资金审计。

## 2. 毛利润被费用吃掉，但不能只归咎于手续费

| 组成 | 金额（USDT） | 含义 |
| --- | --- | --- |
| 价格毛盈亏 | +15,129.63 | 所有线性持仓价格损益之和 |
| 累计手续费 | −27,811.16 | 原表实际记录，未套用统一费率 |
| 累计资金费用 | +479.18 | 此处合计为收入 |
| 强平清算费 | −2,626.14 | 原表独立列项 |
| 重构净盈亏 | **−14,828.48** | 上述四项相加 |

手续费相当于毛净额的 **183.82%**。195 条原本价格盈利的记录在计入各项费用后变成非盈利。每条平均净损益 **−3.03 U**，中位数 **−2.41 U**；净胜率 **39.77%**，盈利记录平均 +83.12 U，亏损记录平均 −59.92 U，利润因子 **0.916**，即历史每亏 1 U，只赚回约 0.916 U。按历史平均盈亏结构计算，盈亏平衡胜率约 **41.89%**，比实际高 2.12 个百分点；这些均值并非未来保证。

累计平仓量按开仓均价折算的单边名义金额约 **3,289 万 U**。以此分母计算，价格毛收益约 **4.60 bp**，手续费约 **8.46 bp**，净收益约 **−4.51 bp**。这只是周转归一化统计，不是本金回报或精确往返费率；累计平仓量也不等于某一时刻的在仓规模。

下面两条累计线从零起算，显示按持仓更新时间归集的毛损益与净损益。两线差距体现费用负担。**这不是账户权益曲线**：分批实现损益可能被归到最后更新时间，未平仓浮盈亏没有进入曲线。

[[CHART:cumulative]]

因此，“手续费降下来就能赢”还不成立：去掉最大一笔赢家，连毛盈亏都会变成 −13,654.07 U。改善成本与确认交易优势，两件事都要做。

## 3. 年度表现有所改善，仍没有出现盈利年度

{table(S['by_year'],F)}

2026 年截至导出的净胜率与利润因子高于 2025 年，但金额权重、市场行情、仓位与交易对象都变了，不能据此认定个人能力已稳定提升。2026 年去掉最大赢家后净损益 **−32,106.94 U**。三个年份均不是净盈利，且 2024 年覆盖稀疏。

{table(S['by_period'],F)}

2026 年上半年接近净盈亏平衡；7 月至 9 月 13 日又净亏 **−2,115.27 U**。29 个有记录月份只有 **8 个净盈利**。2025 年 10 月 +3,961.07 U、2026 年 5 月 +12,559.63 U、8 月 +2,905.08 U，确实存在赚钱阶段；2026 年 4 月 −7,097.12 U、6 月 −4,578.87 U、9 月前半段 −4,138.88 U，又说明利润留存不稳定。

下图保留有记录月份；没有记录的月份没有被补成零。它展示结算归集结果，不展示当月每天的真实权益波动。

[[CHART:monthly]]

一个值得逐笔复盘的例子：2026 年 8 月 +2,905.08 U 后，9 月前半段 −4,138.88 U，已经回吐前月利润并额外亏损约 1,233.80 U。新系统必须把“利润回撤后暂停”写成规则，而不是依靠临场克制。

## 4. 最大赢家值得研究，不能拿它为全部交易背书

最大赢家：**BTC 做空，2026 年 5 月 15 日 19:22 开始，6 月 6 日 00:48 更新结束，约 21.23 天，净赚 29,002.99 U。** 原记录杠杆设置为 100 倍；无法从 CSV 还原这段时间的保证金、加减仓、盘中风险或入场依据。

| 敏感性分析 | 剩余净盈亏（USDT） | 应当怎样理解 |
| --- | --- | --- |
| 全部记录 | −14,828.48 | 历史基准 |
| 去掉最大 1 笔赢家 | −43,831.47 | 全部毛盈利依赖这一笔补偿 |
| 去掉最大 5 笔赢家 | −55,963.26 | 大赢家之外的日常损耗很大 |
| 去掉最大 10 笔赢家 | −65,435.38 | 检查利润集中，不是建议删除赢家 |

趋势策略本来可能依靠少数大行情，**利润集中本身不等于没有优势**。关键在于：同一种可识别的入场逻辑，能否在有限亏损和可承受仓位下持续获得这类尾部收益。这里没有策略标签和入场截图，无法证明大赢家与其他记录属于同一系统。

建议先逐笔复盘 20 个最大赢家、20 个最大输家和按日期固定抽取的 20 个普通样本，先遮住结局再看入场图，记录是否存在共同触发；不要只挑盈利截图。重点还原“入场时知道什么、初始止损在哪里、为何持有、有没有加仓”，而不是归纳“后来跌得多”。

## 5. 做空显著好于做多，仍不足以直接改成只做空

{table(S['by_side'],F)}

2025 年做空 +3,418.93 U、做多 −14,119.55 U；2026 年做空 +19,776.18 U、做多 −22,880.13 U。这个方向差异是本次最值得继续研究的线索。

但空单更大：平仓量折算名义金额中位数约 2,687.94 U，多单约 999.57 U；市场阶段也可能有利空头。**空单拿掉最大 BTC 赢家后变成 −5,921.15 U。** 因此不能把“做空组盈利”当作已经证明的做空优势，更不能用历史差异要求现在无条件看空。

按品种，BTC 合计 +10,222.21 U，ETH −15,736.83 U，其他标的合计 −9,313.86 U；BTC 去掉最大赢家同样转负。涉及约 315 个底层标的，部分小样本币种利润因子很高，但不宜根据本次排行榜直接设未来白名单。下一阶段缩小观察池是为了控制执行复杂度，不是宣布某币一定更赚钱。

## 6. 强平与普通大亏，是两种都必须限制的损失

{table(S['by_close_type'],F)}

177 条强平记录占全部 USDT 记录 **3.61%**，净损益 −9,983.44 U，相当于总净亏损的 **67.33%**。这是一种记账分组，不能说“避免强平就能无成本节省这些钱”；提前止损也会产生亏损。强平记录分布在 **87 天、101 个不同更新时间**，有一次同秒涉及 10 个持仓，因此 **177 条不等于账户爆仓 177 次**。

更重要的是，全部普通平仓组本身仍亏 −4,712.98 U。最大单笔亏损不是强平：2026 年 6 月 4 日 23:06 开始的 ETH 多单，次日结束，净亏 **−9,816.93 U**；8 秒后开始的 BTC 多单又净亏 **−3,482.62 U**。两条合计 **−13,299.55 U**，反映相关品种同向暴露需要共同限额。仅凭文件无法判断它们是否为组合对冲的一部分，必须核对当时完整仓位。

{table(S['by_leverage'],F)}

所有杠杆分组都净亏。大于50倍组1,413条，92条强平；不超过10倍组336条，1条强平。但不同组品种、风险预算和时期不同，不能把该差异当作随机实验。尤其100倍是界面设置，缺少账户权益时无法还原真实有效杠杆。

4,825 条原始记录采用全仓。全仓共享保证金，孤立看单个币的名义止损，无法完整代表组合风险。[OKX 全仓与逐仓说明](https://www.okx.com/en-gb/help/how-do-i-trade-using-cross-and-isolated-modes)。报告建议的逐仓、低仓位与硬止损用于限制风险传播，不能承诺完全没有跳价或强平风险。

逐更新时间归集的累计净损益最大峰谷下降约 **33,026.02 U**，谷底归集于 2026 年 6 月 5 日；由于缺少权益和浮盈亏，**绝不可标成账户最大回撤百分比，也不一定是实际盘中回撤下界**。

## 7. 短线频繁操作表现弱，但“多拿一会”不是答案

下表只包含完整结束的 4,879 条 USDT 持仓周期。持仓时长为创建到更新，无法知道过程中是否已经多次加减仓。

{table(S['by_duration'],F)}

1 小时以内合计 **2,216 条，净亏 −37,160.48 U**。大于 3 天的 15 条合计 +29,491.33 U，但拿掉最大赢家后，只剩 14 条合计 +488.34 U。获利持仓时长中位数约 1.74 小时，亏损持仓约 0.92 小时，因此本记录**不支持泛泛指责你“赢了马上跑、亏了死扛”**。

图中比较的是事后形成的时长组。亏损单可能因为止损而更早结束，所以**不能以此延迟止损，也不能声称持有满 3 天就有优势**。

[[CHART:duration]]

409 个有开仓记录的日期，每日新持仓周期中位数 9 条，最多一天 52 条。频次与净结果并非单调关系：21—50 条/日组账面盈利，但同样被最大 BTC 赢家影响；移除后转为 −18,008.28 U。每日限制次数是执行和成本管理假设，不是这份历史已证明的最优阈值。

在上一个已结束持仓结算组亏损后 5 分钟内新开的 554 条记录合计 −4,327.45 U；上一个盈利组结束后 5 分钟内新开的 217 条也合计 −7,266.75 U。新系统可以试验短暂冷静期，但这些只是相关性，不能据此认定你当时在“报复交易”。不同持仓可能重叠，上一结算组并不一定是你的上一笔成交。

## 8. 是否有统计证据证明你“能稳定赚钱”

**当前答案是没有足够证据。** 本文件整体净损益为负；以自然周为块重抽样 10,000 次，估计每条净损益均值的 95% 区间约 **[−9.12，+4.17] U**。区间很宽，反映大额异常记录、不同仓位与市场阶段混杂。它不是未来赚钱的概率。

以周总损益正负号置换作探索性零假设对照，单侧“净收益为正”p≈**0.8060**。该方法依赖周块近似独立、分布对称等不稳定假设，因此只作敏感性说明；不把它宣传成严格 alpha 检验，也不对每个币/时段反复找最小 p 值。

本轮不具备同币、同时间块、同波动桶、同成本、同持仓规则的随机入场对照，**无法区分择时能力、方向 beta、仓位选择与市场运气**。没有预测评分，val AUC、top-decile 策略毛/净收益、单特征基线不适用；没有预设初始止损，历史 R 不可计算。没有独立验证集，val 样本数为 0。全部分组都是事后描述，不能被称为样本外验证。

你的信心可以保留为待验证假设：“我能识别少数大行情，而且能减少日常无效损耗。” 要让这句话成立，需要一份固定规则的未来记录，而不是更多历史获利截图。

## 9. 个人交易系统 v1：先把风险与执行写死

**定位：未验证的模拟执行草案。** 本金、可接受回撤、时间投入和其他账户覆盖尚未补充，下面按比例设计，不承诺收入目标。数值是保守起点，未通过这份历史优化；所有规则一起组成一个拟验证版本，不声称每项有独立因果增益。不修改现有 Spike/V9 或任何实盘配置。

| 事项 | v1 操作规则 | 对应历史问题 |
| --- | --- | --- |
| 资金边界 | 仅使用可承受损失的独立交易预算；不以借款、生活支出或补仓转账扩大预算 | 没有权益记录，无法知道此前承担多少账户风险 |
| 起步阶段 | 先模拟；通过下述前向验收后，再由你决定是否小额实盘 | 全体历史尚未净盈利 |
| 单笔风险 | 计划最坏正常止损损失含手续费/滑点为账户参考权益的 0.25%，记为 1R | 限制普通大亏，不等待强平 |
| 总开放风险 | 全部未平仓计划损失合计≤0.5%；BTC/ETH等相关同向仓位合计≤0.25% | 同向组合不能算独立分散 |
| 仓位上限 | 起步总名义仓位≤参考权益1倍；逐仓、界面杠杆≤3倍；关闭自动追加保证金的建议由你手动检查 | 杠杆数字不能代替实际仓位/权益控制 |
| 加减仓 | v1不亏损加仓、不翻倍、不摊平、不临时扩大止损；先不设计盈利加仓 | 避免无法审计的风险扩张 |
| 日止损 | 日内已实现+未实现净损益≤−0.75%日初权益，停止新开；按预案处理已有仓位 | 阻断连续损耗 |
| 周止损 | 周内净损益≤−1.5%周初权益，余周停止新开并复盘 | 约6个初始R的预算 |
| 回撤停机 | 剔除外部现金流后的权益较高点跌4%，回到模拟并审核版本 | 防止盈利阶段被后续操作完全回吐 |
| 次数/冷静期 | 每日最多3次新风险事件；任何平仓后至少等一根完整15m K线，同币同向要出现新形态；两次连续止损后至少休息60分钟 | 限制反复入场及费用，阈值待验证 |
| 执行故障 | 没有确认生效的保护止损、行情/订单状态异常、剩余预算不足时不新开；无法保护既有仓位时按事先故障预案人工减险 | 止损必须能执行 |

每日开仓预算用当日日初交易权益与当前权益的较低者计算，避免日内盈利后立即放大风险；外部转入不自动提高预算。1R 是开仓前锁定的 U 金额，不能亏损后重新定义。日/周/回撤限制按净权益监控，不能只看已经平仓的损益。

拟定停机预案：触及任一账户级损失限制时停止新开，人工有序平掉剩余风险仓位，确认平仓后清理遗留委托；平仓确认前保持保护止损，不能先撤掉保护再等待。若价格跳变造成超限，如实记录超限金额，不修改触发线以继续交易。规则只有经你确认后才可用于实盘。

**仓位公式（USDT 线性合约）：** 设权益 E、风险比例 r=0.0025、入场价 P、止损价 S，止损价格比例 d=|P−S|/P，预计往返手续费+滑点+不利资金费比例为 c，则名义仓位 **N≤E×r/(d+c)**，还要受组合风险与名义仓位上限约束；张数=N/(P×合约面值×乘数)，按交易所步长向下取整。风险由止损距离与数量决定，不能用“保证金×100倍”倒推想要的盈利。

例子仅演示算术：E=10,000 U、d=1%、c=0.2%，风险预算25 U，N≈2,083 U；3倍杠杆初始保证金约694 U，但正常止损计划损失仍约25 U。这里的0.2%仅为算例与保守预算，不改仓库成本契约；实际下单必须使用当时费率与保守滑点估计。跳空、流动性恶化、触发机制或订单失败仍可能令实际亏损超过预算。

## 10. 只保留一种入场语言：趋势中的回调再启动

**以下是可执行、可证伪的模拟模板，不是从历史反推出的有效信号。** 你已有的主观交易没有策略标签，直接宣称“最适合你的指标”会是编造。先从观察范围 BTC/ETH 开始，仅为简化记录与执行；方向上把空头作为优先研究假设，多头另记一组，不因历史空单盈利而强制做空。

所有判断只使用已经收盘的 K 线，EMA 使用收盘价；只选一种数据源与固定 UTC K 线边界，至少200根预热。v1使用4H判断环境、1H找回调、15m触发；不要在同一验证期混入3m/5m临时信号。

| 步骤 | 做多模板 | 做空模板 |
| --- | --- | --- |
| 4H环境 | 最近完整4H收盘价>EMA20>EMA50，EMA20高于3根前 | 收盘价<EMA20<EMA50，EMA20低于3根前 |
| 1H回调 | 最新完整1H的低点触及/跌破其EMA20，收盘重新在EMA20之上且高于EMA50 | 高点触及/突破其EMA20，收盘重新在EMA20之下且低于EMA50 |
| 形态有效期 | 回调1H收盘后最多4根15m等待确认；期间结构低点被跌破即作废 | 对称；结构高点被突破即作废 |
| 15m触发 | 一根完整15m收盘突破其之前4根完整15m最高价 | 收盘跌破其之前4根完整15m最低价 |
| 入场 | 信号后下一根15m开始观察可成交价；较信号收盘偏离超过初始止损距离0.1倍则放弃，不追价 | 对称 |
| 初始保护 | 回调1H结束时最近6根完整1H最低价下方一个最小价格档 | 最近6根完整1H最高价上方一个最小价格档 |
| 风险审查 | 以实际入场重算张数，止损必须在逻辑失效处；预计费用c≤价格止损距离d的20%，否则放弃 | 相同 |
| 形态唯一性 | 同一回调1H只允许一条风险事件；失败后等待新回调，不能反复点击同一突破 | 相同 |

止损价若在入场价错误一侧，或距离导致最小下单量已经超风险预算，该信号不交易。保护单采用明确触发价类型并确认生效的减仓止损；市价止损优先考虑退出确定性，但实际成交价不保证等于触发价。限价止损则存在触发后不成交风险，应先在模拟环境核对。

**退出模板也固定，避免每单临场改剧本：**

1. 初始止损永不扩大。价格先碰初始止损，按预案退出；不等待15m收盘确认亏损。
2. 当价格沿盈利方向走到初始价格风险的2倍，平掉一半；剩余一半的止损提高到含预计退出成本的盈亏平衡附近。2倍价格风险是触发距离，实际净R仍需扣费计算。
3. 剩余仓位每根1H收盘后，用最近3根完整1H的低点（多）/高点（空）外一档跟踪；止损只允许收紧，下一时点才生效。若新止损已经被当前可成交价穿越，则按退出处理，不能假定早已成交在更好价格。
4. 入场满12根1H收盘仍未触发第一档获利退出，按时间退出；触发过第一档则由跟踪止损管理。这个时间阈值只是待验证约束，不来自“长持仓更赚钱”的因果结论。
5. 同一根K线同时可能触及止损和止盈、且没有更细成交顺序证据时，模拟按不利顺序；真实记录按实际成交。手续费、滑点、资金费都计入净R。

该模板故意不拼接更多指标。之后若要改止盈倍数、时间退出或其他参数，一次只改一项、另存新版本；先有对照，再谈优化。当前没有为这个模板运行历史回测，也没有为它消耗项目行情holdout。

## 11. 每天照着做：开仓前、持仓中、收盘后

### 开始交易前（约15分钟）

记录日初权益、外部现金流、日/周剩余亏损预算；检查是否触发停机条件。只看约定品种的完整4H趋势，列出“可做多／可做空／不交易”，没有满足条件就空仓。为候选形态写清入场、失效、止损、N、1R、预计成本和组合相关性。

### 每次下单前（约1分钟）

必须回答六项：规则版本是否固定；K线是否已收盘；形态是否首次触发；止损价是否明确且订单可生效；全部成本和组合风险是否在预算内；是否处于冷静期或达到日限。任一项不满足就放弃。这不是靠“感觉很好”可以豁免的清单。

### 持仓中

只执行预定保护、分批退出与跟踪，不因刷到新闻、浮亏扩大或急于回本而改计划。记录每一次修改的时间、价格、理由以及修改前后的开放风险。不要把现有持仓切换成另一个周期的故事。

### 每日结束（约10分钟）

对账权益、实际成交、手续费和资金费，记录计划R、实际净R及规则偏差。把“按规则亏损”与“违规亏损”分开；也把“按规则盈利”与“违规侥幸盈利”分开。只有前两者能用于判断策略，后两类中的违规行为都要单独整改。

### 每周复盘（约45分钟）

只评估已冻结版本：净R均值、利润因子、总成本R、最大回撤R、最大单笔损失R、最大赢家占比、按多空分组、规则遵守率。检查空仓是否是主动等待，而不是需要找单填满次数。复盘只提出下周研究问题，不允许看完亏损就当场重写当前验证期规则。

## 12. 用未来证据决定是否扩大规模

**第一阶段：把账补齐。** 补充同范围资金流水、成交明细、返佣、账户权益快照和其他主要账户覆盖。重点核验2024年稀疏记录、BTC大赢家的完整加减仓、2026年6月大亏及强平日。账户净资产变化需剔除外部净流入后再与交易损益对账。

**第二阶段：先完成30条模拟记录的流程检查。** 目标是日志完整、止损可执行、没有未来K线、没有随意改规则；30条不用于宣布盈利。若修改规则，保留旧数据和原因，重新开始新版本的正式前向期。

**第三阶段：至少100条连续有效样本且覆盖至少3个月，取较晚达到者。** 不为凑样本强行交易；没有信号就继续等。记录全部合格信号、拒绝原因和实际执行，不只记录下过单的信号。行情阶段或实际独立机会不足时继续积累，不以刚好赚钱的日期提前结束。

| 验收项 | v1参考门槛 | 为什么 |
| --- | --- | --- |
| 规则遵守 | ≥95%，强平0、亏损加仓0、风险超额0 | 先确定研究的是同一个系统 |
| 经济结果 | 所有成本后总净R>0、净R均值>0、利润因子>1.2 | 需要对估计误差留余量，阈值为拟定标准 |
| 风险 | 不突破已批准账户回撤与日/周预算；实际滑点超预算要复盘 | 不能用未来大赢家为当前破限辩护 |
| 稳定性 | 分月/分方向报告，最大单笔和前三笔剔除敏感性必须公开 | 集中获利可以存在，但必须解释可重复性 |
| 对照 | 同币×同时间块×同波动桶匹配随机入场，沿用同退出、成本与风险规则；超额净R与区间公开 | 区分行情红利与择时价值 |
| 不确定性 | 采用时间块重抽样；样本少或区间很宽继续模拟 | 100条不是充分性定理 |

这些门槛是个人执行草案，不能替代仓库的生产准入和owner人工审批；已有模型的top-decile扣成本与p<0.01等门槛没有被改动。满足以上条件也不自动变成实盘或自动放大仓位。

若允许小额实盘，仍先用0.25%单笔风险、另观察至少50笔真实成交与成本偏差，再讨论风险预算。是否提高到0.5%必须由你在清楚本金、回撤和验证证据后另行决定；本报告不给收益保证或月赚目标。

## 13. 需要你补充的四件事

1. 目前独立交易本金是多少，能接受多大的账户净值回撤；这些钱是否有生活用途。
2. 每天可投入多长时间，能否在持仓时确认保护单并按1H节拍管理；若不能，应调整交易周期而不是漏执行规则。
3. 这份导出覆盖哪些账户，是否还有现货、子账户、其他交易所、返佣与转账。
4. 最大 BTC 空单与两笔6月大亏的原始入场理由、加减仓及截图。它们决定下一步应该研究真实存在的哪一种能力。

这些信息补齐前，不把10,000 U算例当作你的本金，也不把任何拟定风险比例当作你已接受的实盘设置。

## 14. 风险与诚实声明

本报告确认了已有CSV的金额关系，没有确认所有账户齐全，没有对历史资金流做完整审计，没有用交易所API读取账户。原始账号标识不进入报告；私人逐条账本保留本地，未推送远程Git或公开网站。

按更新时间归集可能把跨月多次结算集中在一个月；持仓记录可能包含加减仓、双向持仓与重叠持仓。杠杆为表内设置值，不等于账户有效杠杆。分组相关性不证明因果，删赢家/删强平也不代表可实现策略。止损和逐仓只能帮助控制风险，不能消灭跳价、系统故障与流动性风险。

本轮是owner明确授权的个人历史复盘；读取了附件中的2026年记录，但没有读取项目价格数据、没有评估或选择任何模型配置、没有新训练、没有promote或实盘操作。个人系统为待验证假设，禁止将它标记为“已验证”或“实盘”。

你需要证明的不是“曾经赚过一大笔”，而是：在预先写明的入场规则和有限风险下，扣除全部成本后，连续样本仍能留下正收益。
'''

# A canonical narrative source plus native charts/tables; no separate chart runtime.
OUT.mkdir(parents=True,exist_ok=True)
md=ROOT/'analysis/p1_owner_okx_history_20260916.md'
# The repository requires reproduction commands in the archived Markdown source.
repro='''\n## 复现与审计附录\n\n统计生成器先于本地结果入库，提交9983ba3b34。全部金额采用附件原列，不变更项目成本参数。此为首次个人账本审计，无上一版本；对照列为原始毛损益与扣费重构净损益，非策略对照。\n\n```bash\ncd /Users/zhangzc/fable-trading\nPY=/Users/zhangzc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3\n"$PY" yoyo/evaluation/owner_okx_history.py --input '/Users/zhangzc/工作/未命名文件夹/MTY4MjQ4NTU=_欧易持仓历史__2023-09-14~2026-09-14~8~29f81692d9af87c8826aafca8ff5dad3.zip' --out data/owner_okx_history_20260916\n"$PY" yoyo/evaluation/owner_okx_report.py\n```\n\n原始ZIP SHA256：e7d5d176bc9f1e730f2db06b733281b6f2f2e32e12cd89a3721c5e6f8c4b2385。数据检查与机器可读汇总在data/owner_okx_history_20260916；可检查笔记本在本实验目录audit.ipynb。外部参考核对日期：2026年9月16日。\n'''
md.write_text(report.replace('[[CHART:cumulative]]','（交互HTML中有累计毛/净损益图。）').replace('[[CHART:monthly]]','（交互HTML中有月度净损益图。）').replace('[[CHART:duration]]','（交互HTML中有持仓时长分组图。）')+repro)
subprocess.run(['python3',str(ROOT/'scripts/md_to_html.py'),str(md),'--out-dir',str(ROOT/'analysis/html')],check=True)
source=dict(id='ledger',label='本次欧易个人持仓历史：经核验的USDT重构账本',path='data/owner_okx_history_20260916/summary.json',query=dict(description='用户提供的欧易持仓历史CSV；2024-02-23至2026-09-13，UTC+8；线性4898条；币本位单独；净=价格毛P&L+signed fee+funding+liquidation clearance。原ZIP SHA256 e7d5d176bc9f1e730f2db06b733281b6f2f2e32e12cd89a3721c5e6f8c4b2385。代码yoyo/evaluation/owner_okx_history.py。'))

sources=[source,dict(id='okx_pnl',label='OKX API：历史持仓及损益字段',href='https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history'),dict(id='okx_export',label='OKX：导出持仓历史字段',href='https://www.okx.com/help/how-to-check-download-order-history-position-history-and-trading-history'),dict(id='okx_margin',label='OKX：全仓与逐仓',href='https://www.okx.com/en-gb/help/how-do-i-trade-using-cross-and-isolated-modes')]
blocks=[]
sections=re.split(r'(?m)(?=^## )',report)
for i,section in enumerate(sections):
    parts=re.split(r'\[\[CHART:(\w+)\]\]',section)
    for j,p in enumerate(parts):
        if j%2:
            blocks.append(dict(id=f'chart_{p}_block',type='chart',chartId=p))
        elif p.strip():
            block=dict(id=f'section_{i}_{j}',type='markdown',body=p.strip())
            # All personal numeric claims in descriptive sections share this source.
            if 2<=i<=8 and i not in [2,7]: block['sourceId']='ledger'
            blocks.append(block)
# Native appendix table stays at the end for exact monthly lookup.
blocks.append(dict(id='month_table_heading',type='markdown',body='## 月度明细\n\n按持仓最后更新时间归集，金额单位USDT；仅列有记录月份，2026年9月截至13日。跨月仓位的累计损益可能集中计入结束月。'))
blocks.append(dict(id='month_table_block',type='table',tableId='months'))
rows=list(csv.DictReader((DATA/'daily_realized.csv').open()))
cumulative=[]
for row in rows:
    for field,label in [('cumulative_gross','累计毛损益'),('cumulative_net','累计净损益')]:
        cumulative.append(dict(date=row['date'],value=float(row[field]),series=label,day_net=float(row['net']),records=int(row['n'])))
# Derive chart rows directly from reviewed position-level fields with real SQL.
# The packaged renderer requires SQL provenance even for file-backed sources.
connection=sqlite3.connect(':memory:')
with (DATA/'positions_usdt.csv').open() as f:
    position_rows=list(csv.DictReader(f))
columns=['updated','hours','close_type','gross','fee','funding','liquidation_fee']
connection.execute('CREATE TABLE positions_usdt(updated TEXT,hours REAL,close_type TEXT,gross REAL,fee REAL,funding REAL,liquidation_fee REAL)')
connection.executemany('INSERT INTO positions_usdt VALUES(?,?,?,?,?,?,?)',[[r[c] if c in ['updated','close_type'] else float(r[c]) for c in columns] for r in position_rows])
net_expr='gross+fee+funding+liquidation_fee'
queries={
'monthly': f'SELECT substr(updated,1,7) AS "group", COUNT(*) AS n, SUM(gross) AS gross, SUM(fee) AS fee, SUM(funding) AS funding, SUM(liquidation_fee) AS liquidation_fee, SUM({net_expr}) AS net FROM positions_usdt GROUP BY substr(updated,1,7) ORDER BY "group"',
'cumulative': f"WITH daily AS (SELECT substr(updated,1,10) AS date, SUM(gross) AS gross, SUM({net_expr}) AS net, COUNT(*) AS n FROM positions_usdt GROUP BY substr(updated,1,10)), running AS (SELECT *, SUM(gross) OVER (ORDER BY date) AS cum_gross, SUM(net) OVER (ORDER BY date) AS cum_net FROM daily) SELECT date,cum_gross AS value,'累计毛损益' AS series,net AS day_net,n AS records FROM running UNION ALL SELECT date,cum_net AS value,'累计净损益' AS series,net AS day_net,n AS records FROM running ORDER BY date,series",
'duration': f"WITH grouped AS (SELECT *,CASE WHEN hours<=5.0/60 THEN 1 WHEN hours<=0.25 THEN 2 WHEN hours<=1 THEN 3 WHEN hours<=4 THEN 4 WHEN hours<=24 THEN 5 WHEN hours<=72 THEN 6 ELSE 7 END AS bucket FROM positions_usdt WHERE close_type!='部分平仓') SELECT bucket, CASE bucket WHEN 1 THEN '≤5分钟' WHEN 2 THEN '5–15分钟' WHEN 3 THEN '15–60分钟' WHEN 4 THEN '1–4小时' WHEN 5 THEN '4–24小时' WHEN 6 THEN '1–3天' ELSE '>3天' END AS \"group\", COUNT(*) AS n, SUM(gross) AS gross, SUM(fee) AS fee,SUM({net_expr}) AS net FROM grouped GROUP BY bucket ORDER BY bucket"
}
chart_datasets={}
for key,sql in queries.items():
    cursor=connection.execute(sql)
    chart_datasets[key]=[dict(zip([c[0] for c in cursor.description],r)) for r in cursor.fetchall()]
    (OUT/f'chart_{key}.sql').write_text(sql+';\n')
    sources.append(dict(id=f'sql_{key}',label=f'欧易持仓CSV核验后的{key}汇总',path=f'experiments/active/exp-owner-okx-history-20260916-v1/chart_{key}.sql',query=dict(sql=sql,language='sql',engine='SQLite',tables_used=['positions_usdt'],description='positions_usdt由经币种分账并逐行价格核验的原始持仓字段导入；源文件data/owner_okx_history_20260916/positions_usdt.csv。生成器为yoyo/evaluation/owner_okx_report.py。非账户权益。')))
for group in chart_datasets['monthly']:
    expected=next(r for r in S['by_month'] if r['group']==group['group'])
    assert abs(group['net']-expected['net'])<1e-6
cumulative=chart_datasets['cumulative']
charts=[]
for cid,title,dataset,x,y,xtype in [('cumulative','累计记账损益（非账户权益）','cumulative','date','value','temporal'),('monthly','有记录月份的净损益（USDT）','months','group','net','nominal'),('duration','完整持仓周期的时长分组净损益（USDT）','duration','group','net','nominal')]:
    enc=dict(x=dict(field=x,type=xtype),y=dict(field=y,type='quantitative',unit='USDT'))
    if cid=='cumulative':enc['color']=dict(field='series',type='nominal')
    charts.append(dict(id=cid,title=title,type='line' if cid=='cumulative' else 'bar',dataset=dataset,sourceId=f'sql_{cid}',encodings=enc))
artifact=dict(surface='report',manifest=dict(version=1,surface='report',title=TITLE,generatedAt=GENERATED,blocks=blocks,charts=charts,tables=[dict(id='months',title='月度原始口径对照',dataset='months',sourceId='sql_monthly',columns=[dict(field=k,label=l,format='number') if k!='group' else dict(field=k,label=l) for k,l in [('group','月份'),('n','记录数'),('gross','毛盈亏 U'),('fee','手续费 U'),('net','净盈亏 U')]],defaultSort=dict(field='group',direction='asc'))],sources=sources),snapshot=dict(version=1,status='ready',generatedAt=GENERATED,datasets=dict(cumulative=cumulative,months=chart_datasets['monthly'],duration=chart_datasets['duration'])),sources=sources)
(OUT/'artifact.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2))
# Source notes retain process details rather than putting them into the reader flow.
notes=dict(audience='product stakeholders',delivery='local HTML per owner repository requirement',structure=['title','summary','evidence with charts','proposed system and next steps','questions','caveats'],summary_heading_override='中文owner要求，Executive Summary译为核心结论',chart_map=[dict(chart='cumulative',family='two-series line',question='How fees separate gross from net',grain='observed update days',warning='not account equity',palette='two series; native shared renderer'),dict(chart='monthly',family='bar',question='Monthly consistency',grain='29 observed months; absent months not zero',warning='lifecycle attribution'),dict(chart='duration',family='bar',question='Where realized results concentrate',grain='7 duration groups, completed only',warning='post-outcome grouping; not a causal entry rule')],method='weekly cluster bootstrap; exploratory; no matched market benchmark',builder_commit=S['audit']['source_commit'],data=S['audit'])
(OUT/'source_notes.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2))
nb={'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'}},'cells':[]}
for kind,content in [('markdown','# OKX个人持仓历史核验\n\n统计对象为持仓周期快照；USDT与BTC分开。结果不是账户收益率，未来规则未验证。需从仓库根目录启动。'),('code',"from pathlib import Path\nimport json, pandas as pd\nroot=Path.cwd()\ns=json.loads((root/'data/owner_okx_history_20260916/summary.json').read_text())\nd=pd.read_csv(root/'data/owner_okx_history_20260916/positions_usdt.csv')\ns['audit']"),('code',"assert len(d)==4898\nassert (d['gross_error'].abs()<0.01).all()\nassert abs(d.net.sum()-sum(d[c].sum() for c in ['gross','fee','funding','liquidation_fee']))<1e-7\nd[['source_row','instrument','side','opened','updated','gross','fee','funding','liquidation_fee','net']].head(10)"),('code',"pd.DataFrame(s['by_year'])[['group','n','gross','fee','net','win_rate','profit_factor']]"),('code',"pd.DataFrame(s['sensitivity']), s['bootstrap'], s['realized_drawdown']")]:
    cell={'cell_type':kind,'metadata':{},'source':content.splitlines(True)}
    if kind=='code':cell.update(execution_count=None,outputs=[])
    nb['cells'].append(cell)
(OUT/'audit.ipynb').write_text(json.dumps(nb,ensure_ascii=False,indent=2))
fields=['trade_id','strategy_version','account_equity_before','signal_closed_at','symbol','side','regime','setup_id','entry_plan','initial_stop','planned_cost_fraction','initial_risk_usdt','notional_plan','contracts_plan','portfolio_open_risk','actual_entry','actual_exit','closed_at','fees','funding','slippage','net_pnl','net_R','MAE_R','MFE_R','followed_rules','deviation_reason','entry_screenshot','exit_reason','review_note']
with (OUT/'trading_journal_template.csv').open('w') as f:
    csv.writer(f).writerow(['交易编号','规则版本','开仓前权益','信号收盘时间','标的','方向','行情环境','形态编号','计划入场价','初始止损价','预计成本比例','初始风险金额U','计划名义仓位U','计划合约张数','组合开放风险U','实际入场价','实际退出价','平仓时间','手续费U','资金费U','滑点U','净损益U','净R','最大不利波动R','最大有利波动R','是否遵守规则','偏差原因','入场截图','退出原因','复盘记录'])
print(json.dumps({'markdown':str(md),'artifact':str(OUT/'artifact.json'),'blocks':len(blocks),'charts':len(charts),'characters':len(report)},ensure_ascii=False))
