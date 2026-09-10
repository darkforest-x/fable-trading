"""Present the preregistered SPIKE return study without rerunning or selecting rules.

Sources are the authenticated dataset/validation receipts and fixed summary CSVs
from exp-spike-burst-validation-20260910-v1. Tables use only completed outcomes;
this renderer does not create features, candidate ranks, orders, or parameters.
The narrative distinguishes retrospective event returns from allocated cashbooks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-burst-validation-20260910-v1"
NAMES = {"burst_trail": "爆发 V1", "focus_trail": "原 IMACD 入场"}
SCOPES = {"combined": "合并", "okx": "OKX", "binance": "币安", "gate": "Gate"}


def table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |",
                       "| " + " | ".join(["---"] * len(headers)) + " |"] +
                      ["| " + " | ".join(map(str, row)) + " |" for row in rows])


def number(value, decimals=2):
    return "不适用" if pd.isna(value) else f"{value:.{decimals}f}"


def build(folder: Path, output: Path):
    receipt = json.loads((folder / "validation_manifest.json").read_text())
    pins = {Path(x["path"]).name: x["sha256"] for x in receipt["artifacts"]}
    frames = {}
    for name in ("accounts_summary", "event_summary", "score_summary", "period_summary"):
        path = folder / f"{name}.csv"
        if hashlib.sha256(path.read_bytes()).hexdigest() != pins[path.name]:
            raise ValueError(f"Unauthenticated summary: {path}")
        frames[name] = pd.read_csv(path)
    accounts, events, scores, periods = [frames[n] for n in frames]
    data_receipt = json.loads((folder / "dataset_manifest.json").read_text())
    if hashlib.sha256((folder / "dataset_manifest.json").read_bytes()).hexdigest() != receipt["input_manifest_sha256"]:
        raise ValueError("Dataset receipt changed")
    full = accounts.query('population == "all_assets" and account == "full"')

    def account(scope, minutes, arm, kind):
        return accounts.loc[(accounts.scope == scope) & (accounts.minutes == minutes) &
                            (accounts.arm == arm) & (accounts.population == "all_assets") &
                            (accounts.account == kind)].iloc[0]

    def account_table(scopes):
        rows = []
        for row in full.itertuples():
            if row.scope not in scopes:
                continue
            actual = account(row.scope, row.minutes, row.arm, "paired_actual")
            random = account(row.scope, row.minutes, row.arm, "paired_random")
            rows.append([SCOPES[row.scope], f"{row.minutes//60}H", NAMES[row.arm],
                         number(row.return_pct), number(row.max_drawdown_pct), row.trades,
                         number(row.win_rate*100), number(row.profit_factor),
                         number(actual.return_pct), number(random.return_pct)])
        return table(["范围", "周期", "入场", "全账户净收益%", "最大回撤%", "成交", "净胜率%", "PF",
                      "配对子集账户%", "匹配随机账户%"], rows)

    stats_rows = []
    for row in events.query('scope == "combined"').itertuples():
        stats_rows.append([f"{row.minutes//60}H", NAMES[row.arm], row.candidates, row.valid,
                           number(row.win_rate*100), number(row.median_net_r),
                           f"{row.natural_exits}/{row.boundary_marks}",
                           f"{row.natural_20pct}/{row.natural_50pct}/{row.natural_5r}",
                           number(row.risk_pct_median), number(row.initial_stop_rate*100),
                           number(row.paired_actual_net_bp/100), number(row.paired_random_net_bp/100)])
    stats = table(["周期", "入场", "候选", "有效", "净胜率%", "中位净R", "自然退出/边界估值",
                   "自然≥20%/≥50%/≥5R", "中位初始风险%", "初始止损率%", "匹配事件均值%", "随机均值%"], stats_rows)

    tests = table(["范围", "周期", "入场", "任一有效匹配率%", "资产数", "匹配事件净均值%", "随机净均值%",
                   "资产均衡超额bp", "原始p", "Holm p"],
                  [[SCOPES[r.scope], f"{r.minutes//60}H", NAMES[r.arm], number(r.matched_fraction*100),
                    r.permutation_assets, number(r.paired_actual_net_bp/100), number(r.paired_random_net_bp/100),
                    number(r.asset_balanced_excess_bp), number(r.permutation_p, 4), number(r.holm_p, 4)]
                   for r in events.itertuples()])
    diagnostics = table(["周期", "入场", "单特征", "有效数/前10%数", "回顾AUC", "前10%毛均值%",
                         "前10%净均值%", "净胜率%", "其中可匹配均值%", "随机均值%", "匹配超额bp"],
                        [[f"{r.minutes//60}H", NAMES[r.arm], "量比" if r.feature == "relative_volume" else "TR扩张",
                          f"{r.n}/{r.top_n}", number(r.descriptive_auc, 3), number(r.top_gross_bp/100),
                          number(r.top_net_bp/100), number(r.top_win_rate*100), number(r.top_matched_actual_bp/100),
                          number(r.top_random_bp/100), number(r.top_excess_bp)]
                         for r in scores.query('scope == "combined"').itertuples()])
    costs = table(["周期", "入场", "20bp净收益%", "40bp固定成交压力%", "60bp固定成交压力%", "按出入实际名义收费%",
                   "配对随机20bp%"],
                  [[f"{r.minutes//60}H", NAMES[r.arm], number(r.return_pct), number(r.stress40_static_pct),
                    number(r.stress60_static_pct), number(r.actual_turnover_fee_static_pct),
                    number(account(r.scope, r.minutes, r.arm, 'paired_random').return_pct)]
                   for r in full.query('scope == "combined"').itertuples()])
    period_rows = []
    for r in periods.query('scope == "combined" and population == "all_assets" and account == "full" and period != "full"').itertuples():
        random = periods.loc[(periods.scope == r.scope) & (periods.minutes == r.minutes) &
                             (periods.arm == r.arm) & (periods.population == 'all_assets') &
                             (periods.account == 'paired_random') & (periods.period == r.period)].iloc[0]
        actual = periods.loc[(periods.scope == r.scope) & (periods.minutes == r.minutes) &
                             (periods.arm == r.arm) & (periods.population == 'all_assets') &
                             (periods.account == 'paired_actual') & (periods.period == r.period)].iloc[0]
        period_rows.append([f"{r.minutes//60}H", NAMES[r.arm], '前31天' if r.period == 'first31' else '后30天',
                            number(r.return_pct), number(r.max_drawdown_pct), number(actual.return_pct), number(random.return_pct)])
    calendar = table(["周期", "入场", "子期", "连续账户净收益%", "子期回撤%", "配对账户%", "匹配随机%"], period_rows)
    figures = json.loads((folder / 'figures_manifest.json').read_text())
    chart_parts = []
    for r in figures['examples']:
        relative = Path(r['path']).relative_to(ROOT)
        chart_parts.append(f"### {r['venue'].upper()} · {r['symbol']} · {r['minutes']//60}H\n\n"
                           f"选图依据：{r['reason']}。峰值 {r['peak_r']:.2f}R，实际模拟退出净 {r['net_r']:.2f}R，"
                           f"单笔净涨幅 {r['net_return']*100:.2f}%，账户盈亏 ${r['account_pnl']:,.2f}。\n\n"
                           f"![完整持有路径：{r['symbol']}]({ROOT/relative})")
    body = f"""# SPIKE 强劲爆发 V1：抓到了哪些行情，实际留下多少利润

日期：2026-09-10。实验：exp-spike-burst-validation-20260910-v1。状态：完成冻结回测，盈利优势验收未通过。

## 先看结论

**新指标能找到部分强劲爆发，并用跟踪退出留下大幅利润；但当前规则加上固定账户分配，还没有证明能稳定优于对照。**

你关心的 USELESS 1H 被新指标在北京时间 **8月31日06:00** 识别。OKX 下一根开盘模拟入场 0.06746，9月6日17:00—18:00这根K线盘中触及保护（具体时刻未知），模拟退出 0.21586，净 **219.78%、26.15R**。它没有在3R平仓，也没有在最高点平仓。这是信号后的模拟路径，不是已发生的真实交易回执。

但跨交易所合并账户没有持有这笔 USELESS：最后一个席位先分给了同批成交额更高的 ZEN。全期合并账户爆发版 **1H +1.34%、最大回撤7.99%；4H +0.61%、最大回撤7.07%**。爆发版两个合并可配对账户均弱于对应随机入场。16项主检验的 Holm p 全为1，未通过预登记的 p<0.01 门槛。

因此，本轮交付是可复查的收益验证与问题定位，**没有把成功案例包装成最优参数，也没有据此修改线上参数或通知**。上一轮“工程验收通过”只证明 Pine 实现了形态，不等于这轮收益验收通过。

## 1. 全局净值：先看整段，再看成功图

三所为 OKX、币安、Gate。两个周期分别以10万美元起步，单仓名义最多10%、初始风险最多0.5%、最多10个不同资产，无杠杆放大；同币跨所互斥。表内净收益扣20bp入场名义往返成本，尚未计资金费和真实冲击。最大回撤为收盘权益回撤，盘中可能更大。

{account_table(['combined'])}

“全账户”使用全部有效候选；最后两列只使用相同可匹配候选身份，因此应横向比较最后两列，不能把全账户收益直接减去随机账户。配对随机选择同交易所、同币、同决策周、同因果波动桶的随机时点，沿用同成本、同止损、同跟踪退出和同现金约束。不同入场顺序会改变后续持仓，配对不是逐笔仓位相等。

![两个月账户收益和回撤全景]({folder/'figures_account_v2/account_paths.png'})

绿色为爆发版全账户，蓝色为旧 IMACD 入场加相同退出，紫色为爆发版可配对账户，灰色为其匹配随机账户。图中后半段有明显上行，但不能只截那一段证明系统有效。

## 2. 为什么 USELESS 抓到了，合并账户却没赚到

信号出现时原有9个持仓：ACE、FOLKS、S、TOWNS、XAN、O、TWT、GIGGLE、LIT。固定规则按信号当时已知的此前24小时成交额排序：

| 同批顺序 | 信号 | 此前24h成交额 | 实际分配结果 |
| --- | --- | --- | --- |
| 1 | 币安 ZEN | 1,031万美元 | 占用最后一席，最终净亏519.75美元 |
| 2 | 币安 USELESS | 657万美元 | 10席已满，拒绝 |
| 3 | OKX USELESS | 212万美元 | 10席已满，拒绝 |
| 4 | OKX ZEN | 170万美元 | 同资产已持有，去重 |
| 5 | Gate USELESS | 14.6万美元 | 10席已满，拒绝 |

三个 USELESS 事件的净涨幅分别为219.66%、219.78%、214.02%，是同一资产跨所表现，不能算三次独立发现。OKX 单独账户确实分配了2,124.54美元，模拟利润4,669.36美元；不能直接把这笔利润加回合并账户，改变分配后所有后续交易都需要重算。

![USELESS从启动到跟踪退出的完整走势]({folder/'figures_known_case/trade_06.png'})

新指标这里是“价格先启动”路径，比用户之前讨论的旧 IMACD 08:00 箭头早两小时。该案例早已被讨论，不是未见数据。图中峰值44.34R与兑现26.15R分开记录；退出触发根不再增加最高浮盈，避免假设先冲高再止损。

合并1H的444个候选中：152个成交、200个因10席限制拒绝、92个因同资产已持有拒绝。34.29%的小时在收盘仍满10席，而这些收盘时点的现金/权益中位数仍为74.72%，说明主要受席位与成交量容量约束，不能简单归因为钱不够。

SKYAI 两所事件净涨幅约224.5%—225.2%也被满席拒绝。Gate BTR 则成交且净涨381.34%，但因此前24h成交额仅15.53万美元，容量上限把仓位压至155.31美元，实际模拟利润只有592.25美元。ZEC其他交易所的拒绝只是同币去重，币安那笔已贡献3,695.56美元，不能列为漏抓。

剔除 USELESS 后重新分配：爆发版1H仍为1.34%，因为移除的3个候选本来就未持有；这不能称为“剔除最大赢家后仍稳健”。旧IMACD1H从8.16%变为7.75%，移除9个候选后有3笔新交易被选中。4H两组原本均无 USELESS 候选。

**推论：下一轮值得先研究分配层，而不是仅增加信号条件。** 但不能事后把排序换成最有利于 USELESS 的指标：当时 ZEN 量比7.16倍，高于USELESS的4.29倍；简单按量比排序也未必选到赢家。这轮只定位机制，不声称某个新排序已被验证。

## 3. 箭头本身：更少、更集中，但仍有许多失败

以下为合并候选事件，尚未套账户席位，**事件平均收益不是账户收益**。50%/5R只计自然退出兑现，不用最高浮盈凑数；边界估值是样本结束或断档边界仍持有，不能当完成交易。

净胜率、中位净R、初始止损率的分母是全部有效事件，包含边界估值；只有“自然≥20%/≥50%/≥5R”明确只统计自然退出。账户净值及其净胜率/PF也包含期末未退出持仓的估值，不能冒充全部平仓后的实收收益。

{stats}

1H爆发版从旧入场的4,578个候选缩至444个，净胜率由27.09%升至36.49%，自然退出兑现≥50%的次数从23变成7。筛得更少提高了部分候选集中度，也减少了大赢家绝对数量。1H爆发版约59.23%事件仍以初始止损结束；套账户后成交样本初始止损率69.74%，不能用筛选前胜率代表实际持仓胜率。

4H爆发版只有154个候选，其中46个仍是边界估值；108个自然退出中，没有净涨幅达到50%的事件。未完成比例更高且样本不同，不能据此断言所有4H趋势策略都无效。

| 中位特征 | 1H爆发 | 4H爆发 |
| --- | --- | --- |
| 信号K线实体涨幅 | 3.40% | 8.19% |
| 信号ATR/价格 | 1.24% | 2.77% |
| 初始风险/入场价 | 5.29% | 11.86% |
| 初始风险/信号ATR | 4.45 | 4.46 |
| 持有时间上界 | 57.5小时 | 100小时 |
| 价格先启动占比 | 71.62% | 71.43% |

两个周期的风险ATR倍数相近，4H的百分比止损更宽，主要因为信号根与ATR本身更大。它提示4H收盘确认可能已经消耗较多行情空间，但这不是同一信号改变周期后的因果实验。当前证据也不支持“量越大越好”。

## 4. 怎么拿住：实际入场、移动保护、失败退出

两套入场共用：信号收盘后下一根开盘成交；初始止损取近5根低点减0.2ATR与信号收盘减2ATR中更低者，按tick向外取整。实际R由下一根开盘与初始止损定义。某根收盘达到2R后永久开启跟踪：保护取已有保护与该根收盘减4ATR的较高者，**下一根才生效，且不下调**。没有固定3R止盈。

每根先检查此前已生效止损；若跳空穿越则按更差开盘价。盘中触发时间未知，用所在K线收盘作为现金释放上界，不假设能提前用这笔钱开同根新仓。

图中绿色三角是实际下一根开盘成交，橙色阶梯是本根已生效保护，红叉是实际模拟退出；SMA20只作参考，退出不以跌破SMA20代替冻结规则。浅蓝区是当时尚不可见的后续走势。每图包含信号前100根、完整持有段和退出后最多24根。

{chr(10).join(chart_parts)}

这些图按预登记的合并账户自然盈利最高/最低和峰值回吐挑选，另加已知USELESS。它们解释路径，不估计全体成功概率。LIT说明强阳线后仍可能快速失败；SUI说明信号后可能横盘多日再止损、持续占用名额。后者值得另测时间退出，但不能看完失败图就直接加规则。

## 5. 三个交易所都展示，不挑最好的一家

{account_table(['okx', 'binance', 'gate'])}

OKX1H爆发版16.56%明显好于合并1H1.34%，不是收益可以相加或按最好交易所上线的理由。市场覆盖、资金容量、同时信号竞争和持仓顺序都不同。Gate小成交额资产的持仓经常很小，所以事件暴涨与账户利润相差很大。

## 6. 与随机相比，是否真有额外识别能力

每个候选最多3个随机对照，同交易所、同币、同UTC决策周、同自身ATR%因果五分位。随机候选至少340根历史，排除当根及此前12根两套箭头，**不借未来箭头排除背景**。先锁全部匹配索引，再算收益；控制0用于配对账户，事件表则先对每个事件所有有效控制取均值。

收益超额先按资产×周平均，再按资产平均；10000次资产符号置换，单侧检验。两入场×两周期×三所及合并共16项主检验按Holm校正。交易所之间同币高度相关，不能把三所视为独立复制。

{tests}

全部未达到“匹配净超额为正且Holm p<0.01”。原始p也不能只选最小的报告。随机对照是已知同币同周环境的回顾式条件抽样，并非可实时部署的随机策略，也不意味着未来随机买入就会赚钱。

## 7. 量与动能：单特征诊断

没有训练分类器，**模型val AUC不适用**。下表以最终净收益>0作为标签，单独检查量比/TR扩张与胜负的回顾排序关系。取全期前10%的阈值是事后描述，不能直接用作实时门槛。特征只用当根及之前，结果标签才看后续。

{diagnostics}

1H爆发版量比AUC0.427，最高量比十分位净均值为负；TR扩张AUC0.443但顶部净均值为正，反映少数大赢家可能拉高均值，不能用AUC或均值其中一个替代净收益验证。4H顶部匹配覆盖与全体不同，必须比较“其中可匹配均值”和“随机均值”，不能混用分母。三所及合并共32行诊断保存在score_summary.csv。

上表“其中可匹配”的实际分母（量比/TR扩张顺序）：1H爆发43/45、42/45；1H旧入场443/458、444/458；4H爆发8/16、11/16；4H旧入场95/127、93/127。尤其4H爆发的量比顶部只有一半能匹配，解释了全顶部净均值为负、可匹配子集却为正的差异。

## 8. 行情阶段、成本与利润集中

{calendar}

同一账户连续运行，子期继承持仓。前31天两版两个周期均亏损，后30天均转正；这与趋势系统依赖行情环境的理解一致，但不足以推断接下来几个月一定是山寨季。子期收益需要复合而非相加，子期交易计数按入场归属，退出可能晚于子期。

{costs}

40/60bp只是对既有成交额追加固定成本的压力归因，不是按新成本重新分配账户。20bp基础按入场名义计；“实际名义收费”按入场和退出名义各10bp补充归因。均未重建真实资金费、滑点和冲击。

1H爆发版最大一笔盈利占所有正利润23.84%，前五笔64.50%；4H为33.20%与83.65%。这符合少数趋势承担收益的形态，也使结果对遗漏一笔、退出偏差或容量非常敏感。这里是交易级集中度，不冒称币种级集中度。完整账本可追溯同币跨所去重和资金分配。

## 9. 数据、参数来源与可复现边界

评分期：UTC 2026-07-10 00:00 至2026-09-09 00:00，右端不含，共61天；对应北京时间07-10 08:00至09-09 08:00。原始历史从5月1日开始供预热。目录获取记录1,496项：OKX281、币安654、Gate561，不能理解为1,496个互不重复币种。来源是上一轮固定目录，不按本期涨幅榜挑币。

处理任务2,990项：2,966项含连续数据，22项因原排除规则跳过，2项无连续段。共{data_receipt['event_count']:,}条两规则候选，6,496有效；{data_receipt['control_count']:,}条匹配控制，18,649有效。各有1条下一根开盘不在冻结止损上方而无效。合并表排除未经身份认证的千倍/万倍等合约别名；不能把数额倍数品种强行合并。

4H必须完整四根1H，按UTC对齐；断档分段独立预热、不插值。Gate LAPTOP与ZZZ无连续段；此前币安GAIB获取有HTTP400，保留来源缺口记录，不补造。匹配位置18,169个，481个复用、最大2次；复用不能增加独立样本量。

参数来自上一轮形态实现并在本轮前冻结：量比4、TR扩张3、实体占比0.55、收盘位置0.75、近零12根、密集宽度3/交叉2、6根等待。**这些不是本轮在ETH、BTC或山寨币上选出的最优参数。** 当前只验证这套固定多头规则的1H/4H；不能外推成做空、小周期或每个币都适配。

正式holdout边界仍为2026-05-04。Owner已明确允许任何时间段研究；这是此冻结配置第 **1次收益评估消耗holdout**。这61天与USELESS等已被先前研究查看过，**不是新的样本外或实盘前向验证**。无训练、无调参、无重新选择结果赢家。

## 10. 工程验证与风险诚实声明

200项本地测试通过；独立审计核验5,968项来源/产物SHA、1,496份原OHLCV、2,988项原特征归属、全部事件与控制、124项账户产物、60个账户及7,775条成交、180个子期和16项检验。另以固定seed20260910按两规则×两周期各抽5笔，独立重算20笔ATR、实际成交、保护、退出及净R，未发现结果阶段新bug。

当前Pine原生探针发现微小值的series与input/simple比较不同。官方[类型系统说明](https://www.tradingview.com/pine-script-docs/language/type-system/)的比较精度文字不能直接当作全部qualifier的重放算法：本轮实际series微值仍按非零比较；simple tick边界另测并匹配。最初“统一九位四舍五入”探针假设失败，保留原文件，没有写成通过。收益计算前已修正重放，并保存原生截图。它验证当前冻结代码相关分支，不保证所有Pine表达式、历史版本及微价品种通用一致。

- 现存目录未完整重建历史退市品种，仍有存活者偏差；历史tick变化未重建。
- 同币、同周、跨所事件相关，61天不足以覆盖完整行情周期；没有年度稳定性结论。
- K线撮合只能用保守顺序近似，盘中路径、真实交易深度与资金费不完整；没有执行真实订单。
- Pine图上收盘参考R与下一根实际开盘R分开，不能把参考峰值当最终利润。预热期Pine参考持有状态允许压制期内箭头，账户期初却为空，明确保留这一差异。
- 参数没有按本轮结果调整；固定跟踪会主动容忍回吐，不能事前知道最高点。已看到的194R一类最高浮盈，不代表可按194R成交。
- 线上IMACD、SPIKE默认值、监控服务、Bark、YOLO和ACTIVE均未变；没有发测试/交易通知，没有下单。

## 11. 下一步：一次只回答一个问题

优先候选是 **在冻结信号与退出不变时，检验持仓分配/席位占用**：当前排序优先历史流动性；它是否偏离趋势延续目标尚需独立验证。需要先登记一个有因果依据的替代方案，在新时间段与现有分配并行纸面跟踪，再比较兑现收益、漏掉大赢家与回撤。不能把本轮USELESS当训练目标，也不能靠无限增仓改善数字。

随后才适合分别检验停滞时间退出、趋势环境或1H启动加4H背景；每次只改变一项。新的成本、止损、阈值或线上切换仍需按Owner授权范围登记。这些是下一步实验选项，**本报告没有宣称已验证或已上线**。

## 12. 复现与证据入口

先核对当前main和固定来源manifest，不覆盖已有results。以下从已认证的上一轮固定OHLCV目录重建本轮；不声称联网重新下载的后修订数据必然逐字节一致。fresh目录必须不存在，已有原产物保留：

```bash
cd /Users/zhangzc/fable-trading
git branch --show-current
.venv/bin/python -m pytest tests/test_spike_burst_replay.py tests/test_spike_burst_execution.py tests/test_spike_burst_dataset.py tests/test_spike_burst_validation.py tests/test_spike_burst_contract.py tests/test_spike_burst_state_probe.py -q
.venv/bin/python -m yoyo.evaluation.spike_burst_dataset --old-experiment experiments/active/exp-altseason-multivenue-20260910-v1 --out experiments/active/exp-spike-burst-validation-20260910-v1/reproduction
.venv/bin/python -m yoyo.evaluation.spike_burst_validation --results experiments/active/exp-spike-burst-validation-20260910-v1/reproduction
.venv/bin/python -m yoyo.evaluation.spike_burst_figures --results experiments/active/exp-spike-burst-validation-20260910-v1/reproduction
```

上述命令重建全局图与五张预登记案例。补充本报告的已知USELESS图及日期布局副本（不改变收益）可在同一个全新reproduction目录执行：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
from yoyo.evaluation.spike_burst_figures import style, curves, example
from yoyo.evaluation.spike_burst_validation import read_events
p = Path('experiments/active/exp-spike-burst-validation-20260910-v1/reproduction').resolve()
style()
a = p / 'figures_account_v2'
a.mkdir()
curves(p, a)
b = p / 'figures_known_case'
b.mkdir()
ledger = read_events(p / 'accounts/okx_60_burst_trail_all_assets_full_ledger.csv.gz')
row = ledger.loc[ledger.asset.eq('USELESS') & ledger.portfolio_selected.eq(True)].iloc[0]
example(row, '已知 USELESS：OKX 单所账户成交', b, 6)
PY
.venv/bin/python -m yoyo.evaluation.spike_burst_report --results experiments/active/exp-spike-burst-validation-20260910-v1/reproduction --output analysis/p1_spike_burst_validation_20260910_reproduction.md
python3 scripts/md_to_html.py analysis/p1_spike_burst_validation_20260910_reproduction.md --out-dir analysis/html
```

本轮实际执行使用模块默认results目录，日志留在实验根目录。builder先于评分提交649a1a5；dataset记录该提交，账户计算时HEAD为52ede2e，核心回放/成交/账户SHA未变，期间仅图形检查与来源认证改动。d5fafa2仅调整全景图日期布局，旧图保留，account_figure_v2_manifest.json记录显示替代。补充USELESS图选定的是OKX单所实际持仓，独立known_case_manifest.json记录。

报告从已冻结汇总重建（不重算收益）：

```bash
.venv/bin/python -m yoyo.evaluation.spike_burst_report
python3 scripts/md_to_html.py analysis/p1_spike_burst_validation_20260910.md --out-dir analysis/html
```

原Pine SHA256：18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2。

数据与全部逐文件认证：[dataset_manifest.json]({folder/'dataset_manifest.json'})。
账户认证：[validation_manifest.json]({folder/'validation_manifest.json'})。
独立重算：[independent_audit.json]({EXP/'qa/independent_audit.json'})。
分配调查：[allocation_audit.json]({EXP/'qa/allocation_audit.json'})。
全量汇总：[账户]({folder/'accounts_summary.csv'})、[候选事件]({folder/'event_summary.csv'})、[单特征]({folder/'score_summary.csv'})、[子期]({folder/'period_summary.csv'})。

体积较大的逐市场特征、候选、对照与现金账本在实验results目录保留，由manifest验证，未推送所有研究数据到git。最终RESULTS_MANIFEST.json联结本报告、HTML、图和审计文件。
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")
    print(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=EXP / "results")
    parser.add_argument("--output", type=Path, default=ROOT / "analysis/p1_spike_burst_validation_20260910.md")
    args = parser.parse_args()
    build(args.results.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
