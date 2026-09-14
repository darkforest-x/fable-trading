# -*- coding: utf-8 -*-
"""Render completed, frozen ICT session research; never replay or tune prices."""
import json
import subprocess
from pathlib import Path

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
EXP=ROOT/'experiments/active/exp-spike-eth3m-ict-sessions-20260914-v1'
LABEL=dict(all='全天',london='London',lunch='London lunch',new_york='New York',
           union='三段合并',london_new_york='London＋New York（去午间）')
ARM=dict(original='原始V8退出',next_bar='净1R＋费用保本（下根生效）',
         ohlc='净1R＋费用保本（先高后低）',olhc='净1R＋费用保本（先低后高）')
PERIOD=dict(development='开发：2023年8月至2024年末',validation='2025年',
            preholdout='2026年前4月',continuous_pre='2023年8月至2026年4月连续')


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(map(str,r))+' |' for r in rows])+'\n'


def number(x,places=3):
    return '不适用/无样本' if pd.isna(x) else f'{x:.{places}f}'


def main():
    out=EXP/'results'
    summary=pd.read_csv(out/'summary.csv');controls=pd.read_csv(out/'controls.csv');cash=pd.read_csv(out/'cash.csv')
    joined=summary.merge(controls,on=['period','arm','session'],how='left',validate='one_to_one')
    selection=json.loads((EXP/'selection.json').read_text());chosen=selection['chosen_session']
    receipt=json.loads((out/'receipt.json').read_text())
    audit=json.loads((out/'validation_receipt.json').read_text())
    ny=summary[(summary.period=='continuous_pre')&(summary.session=='new_york')&summary.arm.isin(['ohlc','olhc'])]
    ny_fixed=cash[(cash.period=='continuous_pre')&(cash.session=='new_york')&cash.arm.isin(['ohlc','olhc'])&(cash.cash=='fixed1')]
    ny_double=cash[(cash.period=='continuous_pre')&(cash.session=='new_york')&cash.arm.isin(['ohlc','olhc'])&(cash.cash=='double')]
    primary=summary[(summary.arm=='next_bar')&(summary.session=='union')&(summary.period!='continuous_pre')]
    later=primary[primary.period!='development']
    claim=('三段合并在两个后期窗口平均净R均为正，仍需看匹配对照和资金路径。'
           if (later.mean_net_r>0).all() else '三段合并未在两个后期窗口都取得正的平均净R，暂不能认定可用于倍投。')
    text=f'''# ETH3m V8：只在ICT指定时段开仓

**{claim}**

**只做New York＋盘中费用保本，在两种路径假设下，完整历史自然交易的最长连续净亏均为{int(ny.max_net_loss_streak.max())}次。这个改善值得保留，但固定1U风险账户仍从1000U降至{ny_fixed.final_balance.min():.2f}–{ny_fixed.final_balance.max():.2f}U，亏后翻倍账户剩{ny_double.final_balance.min():.2f}–{ny_double.final_balance.max():.2f}U且提前无法继续。** 这里的连亏来自覆盖全期的逐笔序列，不是资金早停后的短序列；盘内路径不是实际成交验证。

按你当前图表核实的三段，夏令时北京时间为14:00–17:00、17:00–20:00、20:00–23:00；合起来是14:00–23:00。我们比较“全天、三段各自、三段合并、去午间”六组，保持V8及每组退出规则不变。

开发期按事前规则选出的候选是 **{LABEL[chosen]}**（净1R/下根费用保本，至少50笔，以平均净R排序）。其开发平均净R为{selection['development']['mean_net_r']:.4f}；若该值为负，只代表候选中少亏，不能称已找到盈利时段。选择记录先于2025及2026前4月的评估写入；后期没有重选冠军。

## 时段定义

| 色带 | 纽约本地时间 | 夏令时北京时间 | 冬令时北京时间模型 |
| --- | --- | --- | --- |
| London | 02:00–05:00 | 14:00–17:00 | 15:00–18:00 |
| London lunch | 05:00–08:00 | 17:00–20:00 | 18:00–21:00 |
| New York | 08:00–11:00 | 20:00–23:00 | 21:00–次日00:00 |

三个区间左闭右开，按**实际计划开仓时刻**过滤：13:57信号bar于14:00确认并次开入场时，可落入London；23:00不再新开。周末也包括。时段结束不强制平仓；原止损、跟踪与所有原始V6反向退出继续运行。被排除的信号不排队、不占用影子仓位；跳过信号或隔夜不重置倍投债务。

公开介绍的初版GMT+0时段已经过多次更新，不直接当当前参数。我们读取了你启用的London/London lunch/New York开关，在2026-09-13图上核对14:00、17:00、20:00、23:00边界。[脚本作者及变更记录](https://www.tradingview.com/script/1POwxCmq-ICT-Killzones-28Trades/)
跨季节按`America/New_York`自动转换。当前EDT有运行时证据，**冬季同纽约钟面是模型假设，未直接验证受保护脚本冬季代码**。[TradingView时间文档](https://www.tradingview.com/pine-script-docs/concepts/time/)

## 你提出的三段合并：与全天直接比较

以下是完整自然交易序列的每笔R，不受倍投资金耗尽截短；费用为冻结的名义本金往返0.2%。赢/保本/亏把费用保本的tick微利单列，严格净胜率则包括所有净正收益。毛/净3R为实际兑现，不是最高浮盈。
'''
    for arm in ['original','next_bar']:
        text+='\n### '+ARM[arm]+'\n\n'
        rows=[]
        for period in ['development','validation','preholdout']:
            for session in ['all','union']:
                r=joined[(joined.period==period)&(joined.arm==arm)&(joined.session==session)].iloc[0]
                rows.append([PERIOD[period],LABEL[session],int(r.natural),f'{int(r.wins)}/{int(r.be)}/{int(r.losses)}',
                    number(100*r.win_rate,1)+'%',number(r.mean_net_r),number(r.sum_net_r,2),
                    f'{int(r.max_initial_stop_streak)}/{int(r.max_net_loss_streak)}',
                    number(r.random_mean_net_r),number(r.excess_net_r),number(r.p,4)])
        text+=table(['区间','时段','自然笔数','赢/保本/亏','严格净胜率','每笔净R','累计净R','完整SL/净亏最长','匹配随机每笔净R','超额R','月块p'],rows)
    text+='\n## 六组时段完整对照\n\n各行均对应匹配随机入场。单独看最长连损变短不足以证明优势：开仓次数更少本来就会缩短可观察的连续段。\n'
    for period in ['development','validation','preholdout']:
        for arm in ['original','next_bar']:
            text+='\n### '+PERIOD[period]+' · '+ARM[arm]+'\n\n'
            rows=[]
            for r in joined[(joined.period==period)&(joined.arm==arm)].itertuples():
                rows.append([LABEL[r.session],int(r.natural),number(r.mean_gross_r),number(r.mean_net_r),number(r.sum_net_r,2),
                    f'{int(r.max_initial_stop_streak)}/{int(r.max_net_loss_streak)}',f'{int(r.gross3r)}/{int(r.net3r)}',
                    number(r.random_mean_net_r),number(r.excess_net_r),number(r.p,4),int(r.matched_trades)])
            text+=table(['时段','自然笔数','每笔毛R','每笔净R','累计净R','完整SL/净亏最长','毛/净3R兑现','随机净R','超额R','p','足额匹配笔数'],rows)
    text+='\n## 1000U连续资金：固定1U与亏后翻倍\n\n初始价格风险1U，名义杠杆容量10倍，净亏后风险翻倍，整轮实际净回本才重置。容量不足保留债务等待，不能继续时停机；没有六次后自动清债。原始退出不固定止盈；其他三时序固定净1R退出。费用保本价为多单开仓价×1.002、空单×0.998，按0.01价格档向有利方向取整，价格再向有利方向越过一档才激活。下根生效组在收盘后安装，盘中组沿指定路径立即安装；保留原跟踪止损。亏损时成本可能令净亏超过价格风险1U。\n\n'
    rows=[]
    chosen_sessions=list(dict.fromkeys(['all','union',chosen]))
    for r in cash[(cash.period=='continuous_pre')&cash.session.isin(chosen_sessions)].itertuples():
        ledger=pd.read_csv(out/(r.period+'_'+r.arm+'_'+r.session+'_'+r.cash+'_ledger.csv.gz'))
        accepted=ledger[ledger.accepted]
        last=str(accepted.entry_time.iloc[-1])[:16] if len(accepted) else '无'
        rows.append([ARM[r.arm],LABEL[r.session],'固定1U' if r.cash=='fixed1' else '亏后翻倍',int(r.n_natural),
            number(r.final_balance,2),number(100*r.max_realized_drawdown,1)+'%',int(r.max_risk),int(r.n_capacity_rejected),
            f'{int(r.max_initial_stop_streak)}/{int(r.max_net_loss_streak)}',last,
            str(r.halt_time)[:16] if pd.notna(r.halt_time) else '未永久停机'])
    text+=table(['退出','时段','资金','自然笔数','期末U','现金回撤','最大已用风险U','容量拒单','完整SL/净亏最长','最后实际开仓UTC','停止UTC'],rows)
    text+='\n这是资金可持续性诊断，同一入场规则的固定1U账户作为资金管理对照。匹配随机入场收益见三个独立期间的同退出/时段表及controls.csv，不是模拟随机倍投账户，不能冒充连续资金随机对照。容量拒单不占仓，后续可能接受原本会被占仓挡掉的信号，所以有限资金账户笔数可能不同于不设容量的自然序列。资金早停后的低交易数和短连损不能代表覆盖到2026年4月。纽约盘中两组在2025-03-07均因下一档价格风险512U已超过剩余现金而停止；“保本打断连亏”没有清掉上一轮亏损。\n'
    text+='\n## 盘中保护时序敏感性\n\n先高后低/先低后高是两种OHLC路径假设，不能冒充真实tick或严格收益上下界。\n\n'
    rows=[]
    for r in joined[(joined.period!='continuous_pre')&joined.session.isin(['all','union',chosen])&joined.arm.isin(['ohlc','olhc'])].itertuples():
        rows.append([PERIOD[r.period],ARM[r.arm],LABEL[r.session],int(r.natural),number(r.mean_net_r),int(r.be),
            f'{int(r.max_initial_stop_streak)}/{int(r.max_net_loss_streak)}',number(r.random_mean_net_r),number(r.excess_net_r),number(r.p,4)])
    text+=table(['区间','时序','时段','自然笔数','每笔净R','保本次数','完整SL/净亏最长','随机净R','超额R','p'],rows)
    text+=f'''
## 数据、复现与验证

固定OKX ETH-USDT-SWAP 3m上下文482176根，2023-07-31 11:12至2026-04-30 23:57 UTC；交易开发从2023-08-01开始，保留前史预热。开发/2025/2026前4月每组从空仓开始，连续资金另算。正类率/val AUC/top-decile无预测模型不适用；以胜率、完整收益分布摘要和匹配随机入场代替。没有随机切分。

这是该配置第0次消耗holdout；没有拿9月数据选择时段。此前历史已用于其他研究，不宣称全新盲样本。研究代码与计划在`{audit['study_frozen_commit']}`冻结后运行；原receipt中的`{receipt['builder_commit']}`是运行结束时HEAD，其间仅提交了报告生成器，研究代码逐字节未变。config保留从上一研究继承的未使用arms/cash/permutation_draws字段，实际执行以冻结代码和PROJECT_PLAN为准；validation_receipt列出实际四组退出、两组资金参数，未运行无限杠杆组。

日历DST、边界、排除信号无影子仓位、保本/删失等13项合成检查通过。另核对96组完整自然序列、192份现金账本、72组匹配对照算术和744笔原始V8开发期基线逐笔一致；选择文件时间早于首份2025年产物。以上验证不等于原生Pine逐笔一致或盘内真实成交验证。

同币、UTC月、纽约开仓小时、周末状态、当前ATR比例相对于此前120根的三分位桶匹配9次随机入场，同方向/退出/成本；月份区块符号置换9999次。控制只匹配自然出场，保留删失、无效及缺配对数量；随机均值、超额R及p只按足额9次匹配的子集计算，而策略均值包含全部自然交易。p未做多重比较校正，开发排名后p不具有确认性含义。阈值单变量对照就是同退出的全天组。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --capture=no tests/evaluation/test_spike_ict_session_study.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m yoyo.evaluation.spike_ict_session_study
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/validate_spike_ict_results.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/report_spike_ict_sessions.py
```

结果目录拒绝覆盖。固定输入SHA、源证据和开发选择在实验目录。完整[自然交易摘要](../../experiments/active/{EXP.name}/results/summary.csv)、[资金账本摘要](../../experiments/active/{EXP.name}/results/cash.csv)、[匹配随机摘要](../../experiments/active/{EXP.name}/results/controls.csv)以及各序列逐笔CSV均可核对。运行后报告立即由md_to_html转换HTML。

## 风险与诚实声明

这次改变开仓时段，没有修正Pine参考框与Python次开成交的差异；之前18笔与20笔仍未完成原生图表逐项映射。当前时段方案只是此明确Python模型下的研究，尚未部署或实盘验证。无资金费、标记价格强平、滑点/延迟/最小张数重建；固定20bp成本不是当前账户实际费率的声明。冬季与历史版本的受保护指标时段尚未逐年原生校验。

下一步只有在每笔净收益、后期稳定性和资金可持续性同时支持时才考虑新的前向验证。若继续调整退出或实际费用预算，另立单变量实验；新holdout使用与实盘操作仍需owner明确授权。本轮不promote、不修改图表参数或交易执行器。
'''
    report=ROOT/'analysis/p1_spike_eth3m_ict_sessions_20260914.md'
    report.write_text(text)
    subprocess.run([str(ROOT/'.venv/bin/python'),'scripts/md_to_html.py',str(report),'--out-dir','analysis/html'],cwd=ROOT,check=True)
    print(report)


if __name__=='__main__':main()
