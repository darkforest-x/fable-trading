"""Render saved three-arm profit-protection results; never read market history."""
import json
from pathlib import Path
import pandas as pd

EXP=Path(__file__).resolve().parent
ROOT=EXP.parents[2]
D=EXP/'delivery_v1'
NAMES={'v1_native':'原版V1（多头）','v8':'V8（多空）','v1_common':'统一退出V1（补充）'}
LABELS={'baseline':'原退出','be05':'0.5R保本','tier':'分档锁利'}
PERIODS={'full':'完整两年','earlier':'前一年','later':'后一年'}
TF={'all':'合计','30':'30m','60':'1H','240':'4H'}


def f(x,n=2): return '—' if pd.isna(x) else f'{x:,.{n}f}'


def tbl(head,rows):
    return '\n'.join(['| '+' | '.join(head)+' |','| '+' | '.join(['---']*len(head))+' |']+
                     ['| '+' | '.join(map(str,row))+' |' for row in rows])


def main():
    p=pd.read_csv(D/'three_arm_summary.csv',dtype={'timeframe_min':str})
    c=pd.read_csv(D/'comparison_summary.csv',dtype={'timeframe_min':str})
    m=pd.read_csv(D/'independent_metrics.csv',dtype={'timeframe_min':str})
    trades=pd.read_csv(D/'all_outcomes.csv.gz')
    full=p.query("period=='full' and timeframe_min=='all' and side_group=='all'")
    comp=c.query("period=='full' and timeframe_min=='all' and side_group=='all'")
    get=lambda system,rule:full.loc[(full.system==system)&(full.rule==rule)].iloc[0]
    pair=lambda system,left,right:comp.loc[(comp.system==system)&(comp.from_rule==left)&(comp.to_rule==right)].iloc[0]
    summary=[]
    for system in ('v1_native','v8'):
        a,b,t=[get(system,rule) for rule in ('baseline','be05','tier')]
        summary.append(f"{NAMES[system]}：相同入场、三组均已结束的{int(t.closed):,}笔，原退出 {f(a.total_r)}R → 单独保本 {f(b.total_r)}R → 分档 {f(t.total_r)}R。分档相较原退出 {f(t.total_r-a.total_r)}R，相较单独保本 {f(t.total_r-b.total_r)}R。")
    sections=['# V1 / V8：0.5R保本＋1.5R锁0.5R，三组对照',
        '研究日期：2026-09-14。实验键：`exp-spike-v1-v8-tier-lock-20260914-v1`。历史研究，未上线。',
        '## 结论先看',*summary,
        '**累计R是每笔初始风险单位的事件加总，不是账户收益率。** 不能把这些R直接换算为本金或复利收益。以下主表固定同一入场并要求三组退出都已观察，避免未结束交易和重入次数混淆。',
        tbl(['系统','规则','共同已结束','累计净R','平均净R','净胜率','PF(R)','实际≥10R','事件回撤R'],[
            [NAMES[r.system],LABELS[r.rule],int(r.closed),f(r.total_r),f(r.mean_r,4),f(r.win_rate*100)+'%',f(r.pf_r,3),int(r.realized_ge10),f(r.event_drawdown_r)] for _,r in full.iterrows()]),
        '原版V1保留原生只做多与信号参考保护路径；统一退出V1用另一套通用ATR和反向退出，是补充敏感性，不能替代原版V1。V8保留多空和已冻结的入场规则。',
        '## 分周期：原退出、单独保本、分档锁利',
        tbl(['系统','周期','共同已结束','原净R','单独BE净R','分档净R','分档减原R','分档减BE R','净胜率 原/BE/分档'],[
            [NAMES[system],TF[tf],int(g.loc['tier'].closed),f(g.loc['baseline'].total_r),f(g.loc['be05'].total_r),f(g.loc['tier'].total_r),f(g.loc['tier'].total_r-g.loc['baseline'].total_r),f(g.loc['tier'].total_r-g.loc['be05'].total_r),
             ' / '.join(f(g.loc[rule].win_rate*100)+'%' for rule in ('baseline','be05','tier'))]
            for (system,tf),raw in p.query("period=='full' and timeframe_min!='all' and side_group=='all' and system!='v1_common'").groupby(['system','timeframe_min']) for g in [raw.set_index('rule')]]),
        '## 第二档究竟救回了什么、牺牲了什么',
        '下面分别以原退出或上一轮BE05作为对照。各自使用双方共同已结束的事件，数量可能不同于三方共同样本。“减轻原亏损”不是新增盈利单；锁定的0.5R是扣费前价差。',
        tbl(['系统','比较','双方已结束','减轻原亏损R','削减原盈利R','其他变化R','总变化R','原兑现≥10R保留','每笔变化95%周块区间'],[
            [NAMES[r.system],LABELS[r.from_rule]+'→'+LABELS[r.to_rule],int(r.joint_closed),f(r.rescue_delta_r),f(r.harmed_delta_r),f(r.delta_r-r.rescue_delta_r-r.harmed_delta_r),f(r.delta_r),
             f'{int(r.retained_original_ge10)}/{int(r.original_realized_ge10)}',f'[{f(r.mean_delta_weekly_ci_low,4)}, {f(r.mean_delta_weekly_ci_high,4)}]']
            for _,r in comp.query("to_rule=='tier'").iterrows()]),
        '达到10R的峰值不等于实际兑现10R，尾部保留只看原先已经兑现的净R≥10交易。周块区间使用整周重采样2000次、固定seed14092026，保留同周跨币跨市场相关性；不能作为全新样本验证或保证。',
        '## 前后年份与方向',
        '2025-09-10 00:00 UTC切分；前一年要求三组退出都早于切点，后一年按切点后入场。跨切点事件仅留在完整两年表，所以前后段不一定相加等于总表。',
        tbl(['系统','阶段','规则','共同已结束','累计净R','净胜率','实际≥10R'],[
            [NAMES[r.system],PERIODS[r.period],LABELS[r.rule],int(r.closed),f(r.total_r),f(r.win_rate*100)+'%',int(r.realized_ge10)]
            for _,r in p.query("period!='full' and timeframe_min=='all' and side_group=='all' and system!='v1_common'").iterrows()]),
        tbl(['V8方向','规则','共同已结束','累计净R','净胜率'],[
            ['多头' if r.side_group=='long' else '空头',LABELS[r.rule],int(r.closed),f(r.total_r),f(r.win_rate*100)+'%']
            for _,r in p.query("system=='v8' and period=='full' and timeframe_min=='all' and side_group!='all'").iterrows()]),
        '## 独立账本：未结束与串行重入',
        tbl(['系统','方式','规则','入场/结束/未结束','累计净R','净胜率','PF(名义收益)','事件回撤R'],[
            [NAMES[r.system],r['mode'],LABELS[r.rule],f'{int(r.events)}/{int(r.closed)}/{int(r.censored)}',f(r.total_r),f(r.win_rate*100)+'%',f(r.pf_nominal,3),f(r.event_drawdown_r)]
            for _,r in m.query("period=='full' and timeframe_min=='all' and side_group=='all'").iterrows()]),
        'fixed锁住原入场集合；serial允许提前退出后因后续原信号重新入场。原生V1本来是事件账本，只做fixed，没有人为发明一个串行账户。事件回撤按退出时间累计R计算，不含账户保证金、仓位并发、复利或资金约束。',
        '## 锁利阶段与实际费用',
        '多头有利最高价、空头有利最低价达到0.5个实际初始R，收盘确认后从下一根将保护推到精确开仓价；达到1.5R则从下一根推到开仓价±0.5R。两档可以在同一根触发，更紧的原止损继续保留。先检查旧保护与跳空，再更新阶段；不根据最终MFE回头判断。',
        '新第二档多头向下取整、空头向上取整到最小报价单位；原第一档和原跟踪公式保持原样。跳空按较差开盘退出。保本仍扣0.20%往返名义成本；锁0.5R的净R约为0.5−0.20%/初始止损百分比，还可能受tick与gap影响，因此并不保证净盈利0.5R。',
        '## 数据范围与审计',
        '现有 Binance／OKX／Gate 30m／1H／4H 历史池，2024-09-10含至2026-09-10不含UTC。原生V1冻结6185个入场；共同执行与V8覆盖3531条流。之前的baseline和BE05只读取已完成、SHA绑定的逐笔结果；新分档只增加第二档，没有改入场参数，没有另搜阈值。16项本轮相关合成与统计口径测试通过。',
        '首次实现错误地将第一档开仓价也量化，可能因二进制floor/ceil漂移一跳。父审查后保留精确actual entry，仅第二档新价位量化。共同结果full_tier_v1保留但不使用；原生首次全量在250流/751事件后中断，过程已记录。修复后新目录full_tier_v2与native_v1/full_exact_entry为本报告来源。fixed gap优先于预定reverse时的退出归因字段也已修正，填单价未因此改变。',
        'Owner已允许任意历史日期研究。本轮是新的固定档位配置，但重复用了上轮历史；样本、首次全量和修复重跑都是重复读取，配置编号1不等于只消耗一次数据。保留样本与失败记录，不声称新盲测。',
        '## 风险与诚实声明',
        '- 数据池来自现有缓存，不是历史全部上市和退市合约的完整普查。跨所跨周期事件相关，不能当成独立账户交易。',
        '- 原V1旧研究盈利高度集中于RAVE，剔除诊断仍亏；本次增加退出档位不自动解决入场收益集中度。不得据事后结果制作获利币白名单。',
        '- 费用沿用0.20%统一假设，完整资金费率、盘口冲击与逐笔成交未覆盖。本次是收盘更新保护，不等于盘中触价立刻改单。',
        '- AUC、top-decile和随机入场alpha检验不适用于同一入场的退出干预，未计算、不编造；对照是同一事件的原退出和上一档规则。',
        '- 没有修改Pine、Bark、Telegram、线上监控、ACTIVE、自动化或真实仓位。未将任何本次配置标记为可实盘。',
        '## 复现与逐笔数据',
        '在仓库根目录运行；既有缓存需通过SHA验证，重跑应使用新的空输出目录。',
        '```bash\n.venv/bin/python -m pytest tests/test_spike_native_v1_tier_lock.py tests/test_spike_v1_v8_tier_lock.py tests/test_spike_tier_lock_report.py -q\n.venv/bin/python -m yoyo.evaluation.spike_v1_v8_tier_lock --official --output experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/common/results/reproduction\n.venv/bin/python -m yoyo.evaluation.spike_native_v1_tier_lock --output experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/native_v1/reproduction\n.venv/bin/python -m yoyo.evaluation.spike_tier_lock_report --common experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/common/results/full_tier_v2 --native experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/native_v1/full_exact_entry/tier_paired_trade_ledger.csv.gz --native-receipt experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/native_v1/full_exact_entry/receipt.json --output experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/delivery_reproduction\n.venv/bin/python experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/render_report.py\n.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v1_v8_tier_lock_20260914.md --out-dir analysis/html\n```',
        '报告读取原delivery_v1；重跑生成delivery_reproduction后按事件键核对原表，避免覆盖。',
        '[三方逐笔配对CSV.gz](../../experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/delivery_v1/three_arm_trades.csv.gz) · [三组统计CSV](../../experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/delivery_v1/three_arm_summary.csv) · [两两差异与区间CSV](../../experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/delivery_v1/comparison_summary.csv) · [独立账本CSV](../../experiments/active/exp-spike-v1-v8-tier-lock-20260914-v1/delivery_v1/independent_metrics.csv)',
        '[上轮0.5R保本研究](p1_spike_v1_v8_be05_20260914.html) · [上轮Notion记录](https://app.notion.com/p/3db8856479af817e8bb8d93773c5a171)',
        '## 下一步决策',
        '按原版、周期和方向分别评估新增第二档的增益与尾部损失；不因胜率提高就自动上线，也不据这次单配置对照宣称最优。任何不同激活时机或净费用锁利均属于新实验。']
    stage=trades.loc[trades.rule=='tier']
    rows=[]
    for (system,mode),g in stage.groupby(['system','mode']):
        rows.append([NAMES[system],mode,len(g),int(g.stage1_armed.sum()),int(g.stage2_armed.sum())])
    sections.extend(['## 阶段实际触发次数',tbl(['系统','方式','入场','到第一档','到第二档'],rows),
        '次数统计只表示已完成K线观察到相应MFE并启用保护，不能直接当成该档成交或获利笔数。common逐笔表另外保留exit_protection_source用于区分锁利、原跟踪和反向退出。'])
    out=ROOT/'analysis/p1_spike_v1_v8_tier_lock_20260914.md'
    out.write_text('\n\n'.join(sections)+'\n',encoding='utf-8');print(out)


if __name__=='__main__': main()
