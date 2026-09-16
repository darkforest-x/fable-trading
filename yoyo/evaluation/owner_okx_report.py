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
- **下一步以你的原V9趋势与最新BB×Stoch v2反转为两条独立研究主线，补V1门禁、统一账户风控与净收益账。** 本报告给出完整操作草案。它不是已验证盈利策略，也不意味着现在应该加本金或放大杠杆。

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

{(OUT/'indicator_system.md').read_text()}

## 13. 需要你补充的四件事

1. 目前独立交易本金是多少，能接受多大的账户净值回撤；这些钱是否有生活用途。
2. 每天可投入多长时间，能否在持仓时确认保护单并按1H节拍管理；若不能，应调整交易周期而不是漏执行规则。
3. 这份导出覆盖哪些账户，是否还有现货、子账户、其他交易所、返佣与转账。
4. 最大 BTC 空单与两笔6月大亏的原始入场理由、当时指标版本、初始止损、加减仓及截图。它们决定下一步应该研究真实存在的哪一种能力。

这些信息补齐前，不把10,000 U算例当作你的本金，也不把任何拟定风险比例当作你已接受的实盘设置。

## 14. 风险与诚实声明

本报告确认了已有CSV的金额关系，没有确认所有账户齐全，没有对历史资金流做完整审计，没有用交易所API读取账户。原始账号标识不进入报告；私人逐条账本保留本地，未推送远程Git或公开网站。

按更新时间归集可能把跨月多次结算集中在一个月；持仓记录可能包含加减仓、双向持仓与重叠持仓。杠杆为表内设置值，不等于账户有效杠杆。分组相关性不证明因果，删赢家/删强平也不代表可实现策略。止损和逐仓只能帮助控制风险，不能消灭跳价、系统故障与流动性风险。

本轮是owner明确授权的个人历史复盘；读取了附件中的2026年记录，但没有读取项目价格数据、仅复用已完成指标研究报告，没有新评估或选择模型配置、没有新训练、没有promote或实盘操作。个人系统为待验证假设，禁止将它标记为“已验证”或“实盘”。

你需要证明的不是“曾经赚过一大笔”，而是：在预先写明的入场规则和有限风险下，扣除全部成本后，连续样本仍能留下正收益。
'''

# A canonical narrative source plus native charts/tables; no separate chart runtime.
OUT.mkdir(parents=True,exist_ok=True)
md=ROOT/'analysis/p1_owner_okx_history_20260916.md'
# The repository requires reproduction commands in the archived Markdown source.
repro='''\n## 复现与审计附录\n\n统计生成器先于本地结果入库；初始修正提交9983ba3b34，最终统计版本及SHA见summary.json的audit字段，报告与指标模板版本见交付manifest。全部金额采用附件原列，不变更项目成本参数。此为首次个人账本审计，无上一版本；对照列为原始毛损益与扣费重构净损益，非策略对照。\n\n```bash\ncd /Users/zhangzc/fable-trading\nPY=/Users/zhangzc/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3\n"$PY" yoyo/evaluation/owner_okx_history.py --input '/Users/zhangzc/工作/未命名文件夹/MTY4MjQ4NTU=_欧易持仓历史__2023-09-14~2026-09-14~8~29f81692d9af87c8826aafca8ff5dad3.zip' --out data/owner_okx_history_20260916\n"$PY" yoyo/evaluation/owner_okx_report.py\n```\n\n原始ZIP SHA256：e7d5d176bc9f1e730f2db06b733281b6f2f2e32e12cd89a3721c5e6f8c4b2385。数据检查与机器可读汇总在data/owner_okx_history_20260916；可检查笔记本在本实验目录audit.ipynb。外部参考核对日期：2026年9月16日。\n'''
md.write_text(report.replace('[[CHART:cumulative]]','（HTML报告中有累计毛/净损益图。）').replace('[[CHART:monthly]]','（HTML报告中有月度净损益图。）').replace('[[CHART:duration]]','（HTML报告中有持仓时长分组图。）')+repro)
subprocess.run(['python3',str(ROOT/'scripts/md_to_html.py'),str(md),'--out-dir',str(ROOT/'analysis/html')],check=True)
source=dict(id='ledger',label='本次欧易个人持仓历史：经核验的USDT重构账本',path='data/owner_okx_history_20260916/summary.json',query=dict(description='用户提供的欧易持仓历史CSV；2024-02-23至2026-09-13，UTC+8；线性4898条；币本位单独；净=价格毛P&L+signed fee+funding+liquidation clearance。原ZIP SHA256 e7d5d176bc9f1e730f2db06b733281b6f2f2e32e12cd89a3721c5e6f8c4b2385。代码yoyo/evaluation/owner_okx_history.py。'))

sources=[source,dict(id='okx_pnl',label='OKX API：历史持仓及损益字段',href='https://www.okx.com/docs-v5/en/#trading-account-rest-api-get-positions-history'),dict(id='okx_export',label='OKX：导出持仓历史字段',href='https://www.okx.com/help/how-to-check-download-order-history-position-history-and-trading-history'),dict(id='okx_margin',label='OKX：全仓与逐仓',href='https://www.okx.com/en-gb/help/how-do-i-trade-using-cross-and-isolated-modes')]
for fname in ['p1_spike_v9_full_backtest_20260915','p1_spike_v9_implementation_20260915','p1_eth_bb_stoch_strategy_20260916','p1_eth_bb_stoch_backtest_20260916','p1_eth_bb_stoch_optimization_20260916','p1_ma_shift_stoch_eth_month_20260915','p1_ma_stoch_exit_optimization_v2_20260915','p1_spike_eth_v9_yolo_entry_20260915','p1_spike_v9_cost_be2_20260915','p1_spike_eth_lowtf_cost_diagnostic_20260914']:
    sources.append(dict(id=fname,label=fname.replace('p1_',''),path=f'analysis/{fname}.md'))
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
# One row per lifecycle; partial fills are linked separately, never summed as display-R.
journal_headers=['交易编号','模块ID','规则版本与哈希','信号ID','开仓前权益','信号收盘时间','标的','方向','信号周期','V1四周期状态与确认时间','门禁判定与拒绝原因','行情环境','形态编号','计划入场价','初始止损价','预计成本比例','初始价格风险金额U','含费风险预算U','计划名义仓位U','计划合约张数','组合开放风险U','实际入场价','实际开仓数量','平半成交价与数量','尾仓退出价与数量','逐笔成交明细路径','平仓时间','手续费U','资金费U','强平清算费U','滑点估计U_不可重复扣除','净损益U','净价格风险R','预算R','最大不利波动R','最大有利波动R','是否遵守规则','偏差原因','入场截图','退出原因','复盘记录']
with (OUT/'trading_journal_template.csv').open('w',encoding='utf-8-sig',newline='') as f:
    csv.writer(f).writerow(journal_headers)
print(json.dumps({'markdown':str(md),'artifact':str(OUT/'artifact.json'),'blocks':len(blocks),'charts':len(charts),'characters':len(report)},ensure_ascii=False))
