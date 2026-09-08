"""Source-backed V2 report from frozen pre2026 ledgers and diagnostic charts.

All policy decisions use saved outcomes. Raw OHLCV is read only by the frozen
pre2026 prefix loader to plot four retrospectively selected cases; it does not
change features, thresholds, entry times or saved outcomes. Future chart bars
are explicitly separated from the decision window. No live API calls occur.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from . import imacd_formation_research as research
from .imacd_formation_memory import add_formation_memory
from .imacd_startup_quality import build_features

ROOT, EXP, DATA = research.ROOT, research.EXP, research.DATA
REPORT = ROOT / 'analysis/p1_imacd_formation_memory_20260908.md'
NAMES = {'P00':'原始箭头', 'P02':'上一版：段内收拢＋位置',
         'Q01':'新形成记忆', 'Q02':'新形成记忆＋位置'}
COLORS = {'P00':'#96a6bb','P02':'#d48c96','Q01':'#b59ddd','Q02':'#29c7b0'}
TF = {15:'15分钟',60:'1小时',240:'4小时'}


def fmt(value, digits=2):
    return '—' if pd.isna(value) else f'{float(value):,.{digits}f}'


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']
                     + ['| '+' | '.join(map(str,row))+' |' for row in rows])


def decisions(summary):
    """Use only the preregistered Q02 gates, with no post-result selection."""
    result=[]
    for minutes in research.PERIODS:
        part=summary.loc[(summary.fold=='validation') & (summary.minutes==minutes)].set_index('policy')
        b,q=part.loc['P00'],part.loc['Q02']
        checks=dict(net_positive=q.portfolio_net_pct>0,
            net_improves=q.portfolio_net_pct>b.portfolio_net_pct,
            drawdown_not_worse=q.portfolio_mdd_pct<=b.portfolio_mdd_pct,
            matched_excess_improves=q.excess_bp>b.excess_bp,
            tail_profit_retained=q.tail_profit_pct>=80,
            increment_holm_under_001=q.incremental_p_holm_validation<.01)
        result.append(dict(minutes=minutes,checks={k:bool(v) for k,v in checks.items()},
            passed=all(checks.values()), failed=[k for k,v in checks.items() if not v],
            holdout_consumptions=0, production_eligible=False))
    return result


def style(ax):
    ax.set_facecolor('#111820')
    ax.tick_params(colors='#a8b3c4',labelsize=8)
    ax.grid(alpha=.13)
    ax.spines[['top','right']].set_visible(False)


def equity_plot(equity, target):
    fig,axes=plt.subplots(3,1,figsize=(12,10),facecolor='#111820')
    for ax,minutes in zip(axes,research.PERIODS):
        style(ax)
        for p in research.POLICIES:
            g=equity.loc[(equity.fold=='validation')&(equity.minutes==minutes)&(equity.policy==p)]
            ax.plot(pd.to_datetime(g.time,utc=True), (g.equity-1)*100,
                    color=COLORS[p],lw=1.7 if p=='Q02' else 1.1,label=NAMES[p])
        ax.axhline(0,color='#74839b',lw=.6)
        ax.set_title(TF[minutes],color='#ecf1f7')
        ax.set_ylabel('组合净收益 %',color='#a8b3c4')
    axes[0].legend(ncol=2,facecolor='#111820',labelcolor='#dce6ed',fontsize=8)
    fig.suptitle('SPIKE · 同一批箭头 / 同一退出 / 2025 时间后段复核',color='#ecf1f7',fontsize=14)
    fig.tight_layout()
    fig.savefig(target,dpi=150,facecolor=fig.get_facecolor())
    plt.close(fig)


def tradeoff_plot(summary,target):
    g=summary.loc[(summary.fold=='validation')&summary.policy.eq('Q02')].set_index('minutes').loc[research.PERIODS]
    fig,ax=plt.subplots(figsize=(10,4.5),facecolor='#111820')
    style(ax)
    x=np.arange(3)
    for offset,values,label,color in [(-.26,100-g.retained_pct,'总箭头削减','#879bb3'),
        (0,g.loss_removed_pct,'净亏损箭头削减','#df929b'),
        (.26,g.tail_profit_pct,'前10%大赢家正收益保留','#29c7b0')]:
        bars=ax.bar(x+offset,values,.24,label=label,color=color)
        for b,v in zip(bars,values):
            ax.text(b.get_x()+b.get_width()/2,b.get_height()+1,f'{v:.1f}%',ha='center',color='#dce6ed',fontsize=9)
    ax.set_xticks(x,[TF[m] for m in research.PERIODS]);ax.set_ylim(0,115)
    ax.legend(loc='upper left',facecolor='#111820',labelcolor='#dce6ed',fontsize=8)
    ax.set_title('少发信号的代价：同时看删掉的亏损与丢掉的大行情',color='#ecf1f7',fontsize=13)
    fig.tight_layout();fig.savefig(target,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)


def case_plot(bars,f,i,title,target):
    start,end=max(0,i-150),min(len(bars),i+37)
    b,g=bars.iloc[start:end],f.iloc[start:end]
    split=i-start;x=np.arange(len(b))
    fig,axs=plt.subplots(3,1,figsize=(14,8.5),sharex=True,
        gridspec_kw={'height_ratios':[3.5,1.25,1.5]},facecolor='#111820')
    for ax in axs:
        style(ax)
        ax.axvspan(max(0,split-132),split-12-.5,color='#728cb8',alpha=.1)
        ax.axvspan(split-12-.5,split-.5,color='#e7aa54',alpha=.16)
        ax.axvline(split+.5,color='#dce3ec',ls='--',lw=.8)
        ax.axvspan(split+.5,len(b),color='#b3bbcc',alpha=.06)
    for j,r in enumerate(b.itertuples()):
        color='#31c9af' if r.close>=r.open else '#ed7f8a'
        axs[0].vlines(j,r.low,r.high,color=color,lw=.8)
        axs[0].add_patch(Rectangle((j-.31,min(r.open,r.close)),.62,
            max(abs(r.close-r.open),r.close*1e-6),color=color,lw=0))
    for col,color in zip(['sma20','ema20','sma60','ema60','sma120','ema120'],
        ['#4edac5','#77b9ad','#5078bd','#86a0c7','#8c8e94','#bcc2cd']):
        axs[0].plot(x,g[col],color=color,lw=.9,label=col)
    axs[0].scatter([split],[b.close.iloc[split]],marker='D',s=35,color='#f1c477',zorder=6)
    axs[0].legend(ncol=6,fontsize=7,facecolor='#111820',labelcolor='#cdd6e4',loc='upper left')
    axs[1].plot(x,g.md,color='#709bfa',lw=1.2,label='IMACD')
    axs[1].plot(x,g.sb,color='#e7aa54',lw=1.1,label='Signal')
    axs[1].axhline(0,color='#c5cad3',lw=.7)
    axs[2].plot(x,g.rope_high-g.rope_low,color='#788999',lw=.7,label='当根六MA宽度')
    axs[2].plot(x,g.formation_memory_recent_width,color='#e7aa54',lw=1.3,label='前12根中位宽度')
    axs[2].plot(x,g.formation_memory_background_width,color='#a1b9e4',lw=1.3,label='更早120根中位宽度')
    axs[2].legend(fontsize=7,ncol=3,facecolor='#111820',labelcolor='#cdd6e4',loc='upper left')
    axs[2].set_ylabel('原始价格单位',color='#a8b3c4',fontsize=8)
    ticks=np.linspace(0,len(b)-1,7,dtype=int)
    axs[2].set_xticks(ticks,[b.index[j].tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M') for j in ticks])
    axs[2].set_xlabel('北京时间 · 蓝底：背景120根 / 金底：近期12根 / 虚线右：仅复盘，未用于信号',color='#a8b3c4',fontsize=9)
    fig.suptitle(title,color='#ecf1f7',fontsize=13)
    fig.tight_layout();fig.savefig(target,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)


def cases(events,results):
    """Choose favorable AND adverse cases by fixed saved-outcome ordering."""
    g=events.loc[(events.fold=='validation')&(events.minutes==240)]
    groups=[('rescued_winner','上一版误删、新版保留的最大赢家',g.loc[~g.P02 & g.Q02 & g.net_bp.gt(0)],False),
        ('lost_winner','新版仍然误删的最大赢家',g.loc[~g.Q02 & g.net_bp.gt(0)],False),
        ('removed_loss','新版过滤掉的最大亏损',g.loc[~g.Q02 & g.net_bp.le(0)],True),
        ('retained_loss','新版仍然放过的最大亏损',g.loc[g.Q02 & g.net_bp.le(0)],True)]
    outputs=[]
    for kind,label,subset,ascending in groups:
        if subset.empty:
            outputs.append(dict(kind=kind,label=label,available=False));continue
        row=subset.sort_values(['net_bp','event_id'],ascending=[ascending,True]).iloc[0]
        paths=list((ROOT/'data/kline_deep').glob(f'okx_{row.symbol}_USDT_SWAP_15m_*.csv'))
        if len(paths)!=1:raise ValueError('ambiguous case source')
        b=research.aggregate(research.read_prefix(paths[0]),int(row.minutes))
        f=add_formation_memory(build_features(b));i=int(row.signal_i)
        if not (b.index[i]==pd.Timestamp(row.signal_open_time)
                and np.isclose(b.close.iloc[i],row.signal_close_price)
                and np.isclose(f.formation_memory_ratio.iloc[i],row.formation_memory_ratio)):
            raise AssertionError('case identity or feature parity failed')
        target=results/f'case_{kind}.png'
        case_plot(b,f,i,f'{label} · {row.symbol} 4H · 新形成比 {row.formation_memory_ratio:.3f}',target)
        outputs.append(dict(kind=kind,label=label,available=True,event_id=row.event_id,
            symbol=row.symbol,signal_close_time=row.signal_close_time,
            entry_time=row.entry_open_time,exit_time=row.exit_time,entry_price=row.entry_price,
            exit_price=row.exit_price,net_bp=row.net_bp,old_ratio=row.contraction_ratio,
            memory_ratio=row.formation_memory_ratio,proximity_atr=row.proximity_atr,
            near_zero_bars=int(row.near_zero_bars),old_kept=bool(row.P02),new_kept=bool(row.Q02),
            shown_future_bars=min(36,len(b)-i-1),full_holding_bars=int(row.hold_bars),
            path=str(target.relative_to(ROOT)),sha256=research.digest(target)))
    return outputs


def build():
    results=EXP/'results'
    manifest=json.loads((results/'manifest.json').read_text())
    watched=[Path(__file__),ROOT/'yoyo/evaluation/imacd_formation_verify.py']
    builder=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for p in watched:
        blob=subprocess.check_output(['git','show',f'HEAD:{p.relative_to(ROOT)}'],cwd=ROOT)
        if blob!=p.read_bytes():raise RuntimeError(f'report code not frozen: {p}')
    for item in manifest['files']:
        if research.digest(ROOT/item['path'])!=item['sha256']:raise AssertionError('ledger hash mismatch')
    events=pd.read_csv(DATA/'events.csv.gz')
    summary=pd.read_csv(results/'summary.csv')
    equity=pd.read_csv(results/'equity_daily.csv')
    rankings=pd.read_csv(results/'rankings.csv')
    from .imacd_formation_verify import verify_saved
    verification=verify_saved(DATA,results)
    (results/'ledger_verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2)+'\n')
    if not verification['passed']:raise AssertionError(verification)
    decision=decisions(summary)
    (results/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2)+'\n')
    plt.rcParams.update({'font.family':['Arial Unicode MS','DejaVu Sans'],'axes.unicode_minus':False})
    equity_plot(equity,results/'equity_validation.png')
    tradeoff_plot(summary,results/'signal_tradeoff.png')
    examples=cases(events,results)
    (results/'cases.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2)+'\n')
    val=summary.loc[summary.fold=='validation']
    success=sum(d['passed'] for d in decision)
    rows=[]
    for m in research.PERIODS:
        part=val.loc[val.minutes==m].set_index('policy')
        for p in research.POLICIES:
            r=part.loc[p]
            rows.append([TF[m],NAMES[p],int(r.n),fmt(r.portfolio_net_pct),fmt(r.portfolio_mdd_pct),
                         fmt(r.tail_profit_pct),fmt(r.incremental_p_holm_validation,4)])
    sections=['# SPIKE · 均线形成记忆与价格位置：第二轮验证',
        f'源码先冻结后运行。Q02 通过预先约定验证门的周期：**{success}/3**。这是2025已观察历史的时间后段复核，不是新的盲测。本轮未评估2026或消耗holdout，尚无上线结论。',
        '## 原始箭头、上一版、新版同表',
        table(['周期','规则','箭头数','组合净收益%','收盘最大回撤%','大赢家正收益保留%','候选增量Holm p'],rows),
        '组合为54个等初始现金份额、每币单仓、1倍入场名义、20bp成本代理。Q01是形成单项诊断，唯一预指定候选为Q02；没有根据成绩换臂。收益与回撤是组合统计，不能换算为截图的小止损R倍数。',
        f'![2025组合权益]({results/"equity_validation.png"})',
        '## 是否真正减少低质量箭头',
        '这里的“亏损”严格指相同neutral退出及20bp成本下净收益不大于0，不是人工确认的形态噪音。少发通知不等于信号质量提高。',
        f'![信号取舍]({results/"signal_tradeoff.png"})']
    diagnostic=[]
    for m in research.PERIODS:
        p=val.loc[val.minutes==m].set_index('policy');q=p.loc['Q02'];b=p.loc['P00'];old=p.loc['P02']
        diagnostic.append([TF[m],fmt(100-q.retained_pct),int(q.loss_count),fmt(q.loss_removed_pct),
            fmt(q.winner_retained_pct),fmt(q.win_pct),fmt(q.tail_profit_pct),
            fmt(q.portfolio_net_pct-old.portfolio_net_pct)])
    sections.append(table(['周期','总箭头削减%','剩余净亏损数','净亏损削减%','净赢家数量保留%','剩余胜率%','大赢家正收益保留%','较上一版净收益变化pp'],diagnostic))
    sections+=['## 判定与单变量归因',
        table(['周期','通过','未通过的预定门'],[[TF[d['minutes']],'是' if d['passed'] else '否',', '.join(d['failed']) or '无'] for d in decision]),
        '预定门：净收益为正且超过原始箭头、收盘回撤不增加、匹配超额提升、前10%大赢家正收益保留至少80%、三个Q02周期比较的月度组合增量Holm p<0.01。诊断臂不能替代候选；统计不显著时不宣称过滤有效。']
    ablations=[]
    for m in research.PERIODS:
        p=val.loc[val.minutes==m].set_index('policy')
        for before,after,change in [('P02','Q02','只替换形成窗口'),('Q01','Q02','只加价格位置')]:
            a,b=p.loc[before],p.loc[after]
            ablations.append([TF[m],change,int(b.n-a.n),fmt(b.portfolio_net_pct-a.portfolio_net_pct),fmt(b.portfolio_mdd_pct-a.portfolio_mdd_pct)])
    sections.append(table(['周期','比较','箭头变化','净收益变化pp','回撤变化pp'],ablations))
    sections+=['## 全部时期与匹配随机对照',
        '每个箭头匹配同币、同周期、同日历月、此前240根ATR/close因果五分位、同md方向的最多3个随机入场。排除所有原始箭头、无重复使用。只有3控齐全才计算均值。对照与病例退出/成本完全相同；完整配对数量在表中，均值差只在同一匹配子集比较。']
    for fold,label in [('development','2023–2024开发'),('validation','2025时间后段复核')]:
        rows=[]
        for r in summary.loc[summary.fold==fold].itertuples():
            rows.append([TF[r.minutes],NAMES[r.policy],int(r.n),int(r.matched_n),fmt(r.mean_gross_bp),fmt(r.mean_net_bp),
                fmt(r.win_pct),fmt(r.matched_case_net_bp),fmt(r.control_net_bp),fmt(r.excess_bp),fmt(r.p,4)])
        sections.extend(['### '+label,table(['周期','规则','事件数','三控齐全','平均毛bp','平均净bp','胜率%','匹配病例净bp','随机净bp','超额bp','月符号p'],rows)])
    sections+=['## 单特征基线：事前特征的事后排序诊断',
        '只列原始P00事件上的单特征排序，其他臂完整结果保存在rankings.csv。Top10%在整个分段排序后才确定，只用于诊断，不是线上分位门，也没有据此新选参数。全Top收益和匹配Top收益分别列示，避免未匹配赢家放大表面超额。']
    ranks=rankings.loc[rankings.fold.eq('validation') & rankings.policy.eq('P00')]
    score_names={'strength_score':'释放强度','contraction_score':'原段内收拢','proximity_score':'价格位置','memory_score':'新形成记忆'}
    sections.append(table(['周期','单特征','AUC','Top毛bp','Top净bp','Top胜率%','Top三控齐全','匹配Top净bp','随机Top净bp','Top超额bp','Top月符号p'],[
        [TF[r.minutes],score_names[r.score],fmt(r.auc,3),fmt(r.top_gross_bp),fmt(r.top_net_bp),fmt(r.top_win_pct),
         int(r.top_matched_n),fmt(r.top_matched_case_net_bp),fmt(r.top_control_net_bp),fmt(r.top_excess_bp),fmt(r.top_p,4)] for r in ranks.itertuples()]))
    sections+=['## 成功与失败案例一起看',
        '以下四类案例按2025 4H保存账本的净收益极值机械选择，包含有利和不利结果，不用于改门。图只显示信号前150根和后36根；完整交易退出可能远在图外，表中净收益来自完整neutral持有，不是图内36根的收益。']
    for c in examples:
        if not c['available']:
            sections.append(c['label']+'：该子集为空。');continue
        sections+=['### '+c['label'],
            f"{c['symbol']}，信号确认 {pd.Timestamp(c['signal_close_time']).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')} 北京时间；蓄势 {c['near_zero_bars']} 根。旧形成比 {c['old_ratio']:.3f}，新形成比 {c['memory_ratio']:.3f}，价格位置 {c['proximity_atr']:.3f} ATR；旧保留={c['old_kept']}，新保留={c['new_kept']}。",
            f"完整持有 {c['full_holding_bars']} 根，入场 {c['entry_price']:.9g}，退出 {c['exit_price']:.9g}，净收益 {c['net_bp']:.2f}bp；退出时间 {c['exit_time']}。这不是带初始硬止损的实盘收益。",
            f"![{c['label']}]({ROOT/c['path']})"]
    source_rows=sum(s['rows'] for s in manifest['sources'])
    time_start=min(s['start'] for s in manifest['sources']);time_end=max(s['end_close'] for s in manifest['sources'])
    sections+=['## 数据统计、验证与风险和诚实声明',
        f'实际54个固定历史品种，读取 {source_rows:,} 根15分钟原始记录，来源范围 {time_start} 至 {time_end}；按完整UTC聚合三个周期。有效原始箭头 {len(events):,} 个，2025 {int(events.fold.eq("validation").sum()):,} 个，经济正类率（净收益>0）{100*events.net_bp.gt(0).mean():.2f}%。新holdout消耗：每臂0次。',
        f'独立保存账本检查 passed={verification["passed"]}；具体断言见ledger_verification.json。85个形成/时钟/会计合成测试在数据运行前通过，独立保存账本验证测试另外记录。输入及结果SHA保存在manifest.json。',
        '均线当前比历史窄不等于绝对密集：一直很宽但不再扩大也可能通过；中位数会忽略窗口内短促交叉或分离。价格位置门为带外距离，处在宽带内也记为0。因此本轮只验一个固定代理，不能把失败扩大成“所有均线密集无效”，也不能把通过解释成完整形态已被识别。',
        '54品种来自既有研究池，有历史选择、上市与幸存者偏差，不是全OKX历史全集。2025已经在V1观察；本轮属于探索后的时间后段复核，Holm只校正本轮三个候选周期，不抵消跨研究反复试验。月聚类符号检验假定月度增量符号可交换，不是随机临床试验式的因果证据。',
        '沿用主线回零/反向后次开盘退出，无保护止损、无3R止盈；20bp只是既有往返成本代理，未含真实逐笔费用、滑点、资金费、盘中回撤/爆仓。最大回撤按全量每根收盘计算，显示曲线仅抽样且保留真实观察时间。',
        '控制组波动桶没有逐事件持久化，因此独立账本验证不能重算实际波动桶匹配；该条件由冻结匹配实现及合成测试覆盖。任何只读图示或后续高点都不能替代初始止损下可存活的持仓收益。',
        '已收盘确认及高周期未确认值的差异参考 [TradingView 官方重绘说明](https://www.tradingview.com/pine-script-docs/concepts/repainting/)。本轮没有加入高周期信息，后续若检验必须只使用当时已收盘的高周期。',
        '## 复现与下一步',
        f'研究源码冻结提交：`{manifest["builder_commit"]}`；报告源码提交：`{builder}`。新运行在没有同名结果的干净产物目录中执行，runner拒绝覆盖历史账本；已有结果只运行report可重新生成展示。',
        '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest tests/test_imacd_formation_memory.py tests/test_imacd_formation_research.py tests/test_imacd_startup_quality.py tests/test_imacd_startup_accounting.py tests/test_imacd_startup_research.py -q\n.venv/bin/python -m yoyo.evaluation.imacd_formation_research\n.venv/bin/python -m pytest tests/test_imacd_formation_verify.py -q\n.venv/bin/python -m yoyo.evaluation.imacd_formation_report\npython3 scripts/md_to_html.py analysis/p1_imacd_formation_memory_20260908.md --out-dir analysis/html\n```',
        '通过验证门才有理由单独安排后续时间段验收；未通过则保留失败记录，定位遗漏赢家与残留亏损的共同点，不把Q01或旧P02悄悄换成生产候选。启动当根质量、多周期背景、趋势退出是分别的下一项研究，本轮没有混入或宣称完成。无训练、模型promote、在线阈值、Pine、spike、TG/Bark或执行变更。',
        f'[冻结计划]({EXP/"PROJECT_PLAN.md"}) · [完整汇总CSV]({results/"summary.csv"}) · [验证收据]({results/"ledger_verification.json"})']
    REPORT.write_text('\n\n'.join(sections)+'\n')
    subprocess.run([sys.executable,str(ROOT/'scripts/md_to_html.py'),str(REPORT),'--out-dir',str(ROOT/'analysis/html')],check=True)
    receipt=dict(report_builder_commit=builder,research_builder_commit=manifest['builder_commit'],
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),case_count=sum(c['available'] for c in examples),
        new_holdout_consumptions=0,live_changes=False,
        report_source_sha256=research.digest(REPORT),html_sha256=research.digest(REPORT.parent/'html'/REPORT.with_suffix('.html').name))
    (results/'report_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(decisions=decision,report=str(REPORT)),ensure_ascii=False,indent=2))


if __name__=='__main__':
    build()
