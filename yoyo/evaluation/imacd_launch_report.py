"""Render independent startup/HTF checks and the preceding formation result.

Tables come from saved ledgers. Retrospective cases are selected with fixed
max/min outcome rules only after the policy is frozen. Only pre2026 price
prefixes are loaded for charts; no rule search, new exits or live mutation.
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
from . import imacd_launch_research as r
from .imacd_formation_report import fmt, table, style
from .imacd_startup_quality import build_features
from .imacd_formation_memory import add_formation_memory
from .imacd_launch_context import add_context, HIGHER
from .imacd_launch_verify import verify_saved

ROOT,EXP,DATA=r.ROOT,r.EXP,r.DATA
REPORT=ROOT/'analysis/p1_imacd_launch_context_20260908.md'
NAMES={'P00':'原始箭头','S01':'收盘突破前12根区间','H00':'仅高周期数据齐全','H01':'高周期主线同向'}
COLORS={'P00':'#96a6bb','S01':'#29c7b0','H00':'#a9a0be','H01':'#e8b264'}
TF={15:'15分钟',60:'1小时',240:'4小时'}
V2=ROOT/'experiments/active/exp-imacd-formation-memory-20260908-v2/results'


def decisions(summary):
    rows=[]
    for m in r.PERIODS:
        g=summary.loc[summary.fold.eq('validation')&summary.minutes.eq(m)].set_index('policy')
        b=g.loc['P00']
        for p in ('S01','H01'):
            q=g.loc[p]
            checks=dict(net_positive=q.portfolio_net_pct>0,net_improves=q.portfolio_net_pct>b.portfolio_net_pct,
                drawdown_not_worse=q.portfolio_mdd_pct<=b.portfolio_mdd_pct,
                matched_excess_improves=q.excess_bp>b.excess_bp,tail_profit_retained=q.tail_profit_pct>=80,
                increment_holm_under_001=q.incremental_p_holm_validation<.01)
            if p=='H01':
                known=g.loc['H00']
                checks.update(net_improves_over_same_coverage=q.portfolio_net_pct>known.portfolio_net_pct,
                    drawdown_not_worse_than_same_coverage=q.portfolio_mdd_pct<=known.portfolio_mdd_pct,
                    excess_not_worse_than_same_coverage=q.excess_bp>=known.excess_bp)
            rows.append(dict(minutes=m,policy=p,passed=all(checks.values()),
                checks={k:bool(v) for k,v in checks.items()},failed=[k for k,v in checks.items() if not v],
                holdout_consumptions=0,production_eligible=False))
    return rows


def charts(equity,summary,out):
    fig,axes=plt.subplots(3,1,figsize=(12,10),facecolor='#111820')
    for ax,m in zip(axes,r.PERIODS):
        style(ax)
        for p in r.POLICIES:
            g=equity.loc[equity.fold.eq('validation')&equity.minutes.eq(m)&equity.policy.eq(p)]
            ax.plot(pd.to_datetime(g.time,utc=True),(g.equity-1)*100,color=COLORS[p],lw=1.5,label=NAMES[p])
        ax.axhline(0,color='#74839b',lw=.6);ax.set_title(TF[m],color='#ecf1f7')
        ax.set_ylabel('组合净收益 %',color='#a8b3c4')
    axes[0].legend(ncol=2,facecolor='#111820',labelcolor='#dce6ed',fontsize=8)
    fig.suptitle('SPIKE · 分别检验启动突破与高周期同向 / 2025复核',color='#ecf1f7',fontsize=14)
    fig.tight_layout();fig.savefig(out/'equity_validation.png',dpi=150,facecolor=fig.get_facecolor());plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(13,4.8),facecolor='#111820')
    for ax,p in zip(axes,['S01','H01']):
        style(ax)
        g=summary.loc[summary.fold.eq('validation')&summary.policy.eq(p)].set_index('minutes').loc[r.PERIODS]
        x=np.arange(3)
        for shift,values,label,color in [(-.26,100-g.retained_pct,'总箭头削减','#879bb3'),
            (0,g.loss_removed_pct,'净亏损削减','#df929b'),(.26,g.tail_profit_pct,'大赢家正收益保留','#29c7b0')]:
            bars=ax.bar(x+shift,values,.24,color=color,label=label)
            for b,v in zip(bars,values):ax.text(b.get_x()+b.get_width()/2,v+1,f'{v:.1f}',ha='center',color='#dce6ed',fontsize=8)
        ax.set_xticks(x,[TF[m] for m in r.PERIODS]);ax.set_ylim(0,115)
        ax.set_title(NAMES[p],color='#ecf1f7',fontsize=12)
    axes[0].legend(facecolor='#111820',labelcolor='#dce6ed',fontsize=8)
    fig.suptitle('删掉的噪音与错过的大行情（%）',color='#ecf1f7',fontsize=13)
    fig.tight_layout();fig.savefig(out/'tradeoff.png',dpi=150,facecolor=fig.get_facecolor());plt.close(fig)


def case_chart(b,f,i,title,target):
    start,end=max(0,i-100),min(len(b),i+37)
    b,g=b.iloc[start:end],f.iloc[start:end];x=np.arange(len(b));k=i-start
    fig,axs=plt.subplots(3,1,figsize=(13,8),sharex=True,
        gridspec_kw={'height_ratios':[3.5,1.2,1.2]},facecolor='#111820')
    for ax in axs:
        style(ax);ax.axvline(k+.5,color='#dce3ec',ls='--',lw=.8)
        ax.axvspan(k+.5,len(b),color='#b3bbcc',alpha=.06)
        ax.axvspan(k-12-.5,k-.5,color='#e7aa54',alpha=.1)
    for j,v in enumerate(b.itertuples()):
        c='#31c9af' if v.close>=v.open else '#ed7f8a'
        axs[0].vlines(j,v.low,v.high,color=c,lw=.7)
        axs[0].add_patch(Rectangle((j-.31,min(v.open,v.close)),.62,max(abs(v.close-v.open),v.close*1e-6),color=c,lw=0))
    for name,color in zip(['sma20','ema20','sma60','ema60','sma120','ema120'],
        ['#4edac5','#77b9ad','#5078bd','#86a0c7','#8c8e94','#bcc2cd']):axs[0].plot(x,g[name],color=color,lw=.9)
    top,bottom=g.prior_box_high.iloc[k],g.prior_box_low.iloc[k]
    axs[0].hlines([top,bottom],k-12,k,color='#f1c477',linestyle='--',lw=1)
    axs[0].scatter([k],[b.close.iloc[k]],marker='D',s=35,color='#f1c477',zorder=6)
    axs[1].plot(x,g.md,color='#709bfa',lw=1.3);axs[1].plot(x,g.sb,color='#e7aa54',lw=1.1)
    axs[1].axhline(0,color='#adbccc',lw=.7);axs[1].set_ylabel('本周期IMACD',color='#a8b3c4',fontsize=8)
    axs[2].plot(x,g.htf_md,color='#b29fdb',lw=1.2,drawstyle='steps-post')
    axs[2].axhline(0,color='#adbccc',lw=.7);axs[2].set_ylabel('已确认日线md',color='#a8b3c4',fontsize=8)
    ticks=np.linspace(0,len(b)-1,7,dtype=int)
    axs[2].set_xticks(ticks,[b.index[j].tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M') for j in ticks])
    axs[2].set_xlabel('北京时间 · 金底/虚线价格：此前12根区间 · 垂直虚线右：后续复盘，不用于信号',color='#a8b3c4',fontsize=9)
    fig.suptitle(title,color='#ecf1f7',fontsize=13)
    fig.tight_layout();fig.savefig(target,dpi=150,facecolor=fig.get_facecolor());plt.close(fig)


def cases(events,out):
    g=events.loc[events.fold.eq('validation')&events.minutes.eq(240)]
    specifications=[('breakout_lost_winner','突破门错过的最大赢家',g.loc[~g.S01 & g.net_bp.gt(0)],False),
        ('breakout_removed_loss','突破门删除的最大亏损',g.loc[~g.S01 & g.net_bp.le(0)],True),
        ('higher_lost_winner','高周期已知但不同向：错过的最大赢家',g.loc[g.H00 & ~g.H01 & g.net_bp.gt(0)],False),
        ('higher_removed_loss','高周期已知但不同向：删除的最大亏损',g.loc[g.H00 & ~g.H01 & g.net_bp.le(0)],True)]
    outputs=[]
    for kind,title,subset,ascending in specifications:
        if subset.empty:outputs.append(dict(kind=kind,available=False,title=title));continue
        row=subset.sort_values(['net_bp','event_id'],ascending=[ascending,True]).iloc[0]
        paths=list((ROOT/'data/kline_deep').glob(f'okx_{row.symbol}_USDT_SWAP_15m_*.csv'))
        if len(paths)!=1:raise ValueError('ambiguous case source')
        raw=r.read_prefix(paths[0]);b=r.aggregate(raw,240);hb=r.aggregate(raw,1440)
        f=add_context(b,add_formation_memory(build_features(b)),hb,build_features(hb),240)
        i=int(row.signal_i)
        if not (b.index[i]==pd.Timestamp(row.signal_open_time) and np.isclose(b.close.iloc[i],row.signal_close_price)
                and bool(f.box_breakout.iloc[i])==bool(row.S01)
                and bool(f.htf_same_direction.iloc[i])==bool(row.H01)):
            raise AssertionError('case gate/price identity mismatch')
        target=out/f'case_{kind}.png';case_chart(b,f,i,title+' · '+row.symbol+' 4H',target)
        outputs.append(dict(kind=kind,title=title,available=True,event_id=row.event_id,symbol=row.symbol,
            signal_close_time=row.signal_close_time,net_bp=row.net_bp,near_zero_bars=int(row.near_zero_bars),
            S01=bool(row.S01),H00=bool(row.H00),H01=bool(row.H01),htf_md=None if pd.isna(row.htf_md) else row.htf_md,
            signal_close_price=row.signal_close_price,box_high=row.prior_box_high,box_low=row.prior_box_low,
            entry_price=row.entry_price,exit_price=row.exit_price,exit_time=row.exit_time,hold_bars=int(row.hold_bars),
            shown_future_bars=min(36,len(f)-i-1),path=str(target.relative_to(ROOT))))
    return outputs


def build():
    out=EXP/'results';manifest=json.loads((out/'manifest.json').read_text())
    builder=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    for name in ['imacd_launch_report.py','imacd_launch_verify.py','imacd_formation_report.py']:
        path=ROOT/'yoyo/evaluation'/name
        if subprocess.check_output(['git','show',f'HEAD:{path.relative_to(ROOT)}'],cwd=ROOT)!=path.read_bytes():
            raise RuntimeError('report dependencies not frozen')
    for file in manifest['files']:
        if r.digest(ROOT/file['path'])!=file['sha256']:raise AssertionError('source ledger hash differs')
    audit=verify_saved(DATA,out);(out/'ledger_verification.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    if not audit['passed']:raise AssertionError(audit['failed_checks'])
    e=pd.read_csv(DATA/'events.csv.gz');s=pd.read_csv(out/'summary.csv');eq=pd.read_csv(out/'equity_daily.csv');rank=pd.read_csv(out/'rankings.csv')
    v2=pd.read_csv(V2/'summary.csv');d=decisions(s)
    (out/'decision.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
    # Baseline rerun must agree before comparing different gates across studies.
    checkcols=['n','mean_net_bp','portfolio_net_pct','portfolio_mdd_pct','excess_bp']
    lhs=s.loc[s.policy.eq('P00')].sort_values(['fold','minutes'])
    rhs=v2.loc[v2.policy.eq('P00')].sort_values(['fold','minutes'])
    if not np.allclose(lhs[checkcols],rhs[checkcols],equal_nan=True):raise AssertionError('V2/V3 baseline changed')
    plt.rcParams.update({'font.family':['Arial Unicode MS','DejaVu Sans'],'axes.unicode_minus':False})
    charts(eq,s,out);examples=cases(e,out)
    (out/'cases.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2)+'\n')
    rows=[]
    for m in r.PERIODS:
        a=v2.loc[v2.fold.eq('validation')&v2.minutes.eq(m)].set_index('policy')
        b=s.loc[s.fold.eq('validation')&s.minutes.eq(m)].set_index('policy')
        for row,label in [(a.loc['P00'],'原始箭头'),(a.loc['P02'],'上一版：段内收拢＋位置'),
            (a.loc['Q02'],'新形成记忆＋位置'),(b.loc['S01'],NAMES['S01']),(b.loc['H01'],NAMES['H01'])]:
            rows.append([TF[m],label,int(row.n),fmt(row.portfolio_net_pct),fmt(row.portfolio_mdd_pct),fmt(row.tail_profit_pct)])
    text=['# SPIKE · 启动行情降噪：形成、突破、多周期分开验证',
        '按照Owner批注顺序，先完成形成过程＋价格位置V2，再独立检验启动突破S01和高周期同向H01。所有方法使用同一批原始箭头、相同退出和成本，没有叠加门或修改在线规则。',
        f'V3两个预指定候选共六个周期比较，通过冻结验证门 **{sum(x["passed"] for x in d)}/6**。V2形成＋位置为0/3。2025此前已观察，因此属于探索后的时间后段复核，不能称为全新盲样本外。新holdout消耗均为0。',
        '## 所有方向放在同一张表',table(['周期','规则','箭头数','组合净收益%','收盘最大回撤%','大赢家正收益保留%'],rows),
        '54个等初始现金份额，各币单仓、入场用当时权益1倍名义、数量持有期间固定；往返20bp代理。IMACD首次回零/反向确认后次开盘退出，没有保护止损或3R止盈。大赢家指原始箭头事后净收益前10%的正收益总量，仅用于评价漏掉多少尾部行情。',
        f'![各周期组合权益]({out/"equity_validation.png"})',
        '## 哪些箭头被删掉，代价是什么',
        '“净亏损箭头”是相同退出和成本下的经济代理，尚不是人工标注的形态噪音。高周期主线同向会同时滤掉数据不足和方向不同，因此下一节单独拆出覆盖效应。',
        f'![删减与尾部保留]({out/"tradeoff.png"})']
    val=s.loc[s.fold.eq('validation')]
    one=val.loc[val.minutes.eq(60)].set_index('policy')
    four=val.loc[val.minutes.eq(240)].set_index('policy')
    text+=['## 从结果可以确认什么',
        f"形成记忆修复V1误删，但V2仍保留约97%–99%的全部箭头，属于与现有蓄势条件高度重合的代理，缺少新增降噪。仅靠相对收窄不能实现Owner想要的精选。",
        f"1小时突破门有改善迹象：组合净{one.loc['P00','portfolio_net_pct']:.2f}%→{one.loc['S01','portfolio_net_pct']:.2f}%，回撤{one.loc['P00','portfolio_mdd_pct']:.2f}%→{one.loc['S01','portfolio_mdd_pct']:.2f}%。但仅保留{one.loc['S01','tail_profit_pct']:.2f}%的大赢家正收益，六比较Holm p={one.loc['S01','incremental_p_holm_validation']:.3f}；尚不足以宣称稳定有效。2023–2024同一规则还降低了1小时组合总收益，不能只报2025改善。",
        f"4小时原始箭头在本次两个历史分段均为正收益；2025突破过滤后{four.loc['P00','portfolio_net_pct']:.2f}%→{four.loc['S01','portfolio_net_pct']:.2f}%，高周期同向后{four.loc['H01','portfolio_net_pct']:.2f}%。降低回撤同时大幅削去尾部，统一强制过滤并不符合‘保留大趋势’目标。",
        '这些结果支持把早期启动与已经突破、顺高周期的阶段分开研究。阶段分类是下一步假设，不是已验证的新交易门或本轮已上线功能。']
    rows=[]
    for row in val.loc[val.policy.isin(['S01','H01'])].itertuples():
        rows.append([TF[row.minutes],NAMES[row.policy],fmt(100-row.retained_pct),fmt(row.loss_removed_pct),
            fmt(row.winner_retained_pct),fmt(row.win_pct),fmt(row.tail_profit_pct),fmt(row.incremental_p_holm_validation,4)])
    text.append(table(['周期','门','总箭头削减%','净亏损削减%','赢家数量保留%','剩余胜率%','大赢家正收益保留%','增量Holm p'],rows))
    rows=[]
    for m in r.PERIODS:
        part=val.loc[val.minutes.eq(m)].set_index('policy')
        for p in ['P00','H00','H01']:
            a=part.loc[p];rows.append([TF[m],NAMES[p],int(a.n),fmt(a.portfolio_net_pct),fmt(a.portfolio_mdd_pct),fmt(a.excess_bp)])
    text+=['## 高周期数据不足与方向过滤分开',
        table(['周期','覆盖/方向','箭头数','组合净收益%','收盘最大回撤%','匹配超额bp'],rows),
        'H00只要求高周期有独立340根预热；H01在同一覆盖上再要求md严格同向，零轴不通过。15m→1H、1H→4H、4H→1D。只用本根开盘时已确认的高周期；年轻品种缺日线预热会留现金，这不是看空或看多判断。随机对照保持原同币/月/波动/方向匹配，未额外匹配高周期状态，因此即使政策改善也不能把全部增益归因于IMACD形态本身。',
        '## 冻结的验收结果',
        table(['周期','候选','通过','未通过门'],[[TF[x['minutes']],NAMES[x['policy']],'是' if x['passed'] else '否',', '.join(x['failed']) or '无'] for x in d]),
        '每个候选要求组合净收益>0且超过P00、收盘回撤不增、匹配超额提升、尾部收益保留>=80%、六比较月度组合增量Holm p<.01；H01还要较同覆盖H00净收益改善、回撤不增、超额不差。通过也只支持后续独立时间段验收，不能直接上线。']
    for fold,title in [('development','2023–2024开发'),('validation','2025时间后段复核')]:
        rows=[]
        for a in s.loc[s.fold.eq(fold)].itertuples():
            rows.append([TF[a.minutes],NAMES[a.policy],int(a.n),int(a.matched_n),fmt(a.mean_gross_bp),fmt(a.mean_net_bp),
                fmt(a.win_pct),fmt(a.matched_case_net_bp),fmt(a.control_net_bp),fmt(a.excess_bp),fmt(a.p,4)])
        text+=['## '+title+'：病例与随机对照',table(['周期','规则','事件数','三控齐全','毛bp','净bp','胜率%','匹配病例净bp','随机净bp','超额bp','月符号p'],rows)]
    text+=['## 单特征基线与排序诊断',
        '只显示P00上的单特征；完整其他臂在rankings.csv。Top10%用整个分段的事前特征事后排序，边界不是线上可用的阈值。AUC的正类为净收益>0；高周期未知记录没有分数，不进入该单特征排序。匹配Top均值只比较三控齐全子集。']
    g=rank.loc[rank.fold.eq('validation')&rank.policy.eq('P00')]
    score_names={'strength_score':'启动强度','breakout_score':'区间突破距离','htf_score':'高周期同向强度'}
    text.append(table(['周期','单特征','有分数n','AUC','Top毛bp','Top净bp','Top胜率%','Top三控n','匹配Top净bp','随机Top净bp','Top超额bp','Top月p'],[
        [TF[a.minutes],score_names[a.score],int(a.n),fmt(a.auc,3),fmt(a.top_gross_bp),fmt(a.top_net_bp),fmt(a.top_win_pct),
         fmt(a.top_matched_n,0),fmt(a.top_matched_case_net_bp),fmt(a.top_control_net_bp),fmt(a.top_excess_bp),fmt(a.top_p,4)] for a in g.itertuples()]))
    text+=['## 被过滤的赢家和亏损，一起复盘',
        '固定选取2025 4H四类极值案例，包含两门各自删除的最大赢家和最大亏损。高周期案例仅限数据已知，排除缺数据影响。图显示前100根、后36根，右侧仅复盘；完整交易可能远超图示后续，净收益不是36根内收益。']
    for c in examples:
        if not c['available']:text.append(c['title']+'：集合为空。');continue
        text+=['### '+c['title'],
            f"{c['symbol']} · 确认 {pd.Timestamp(c['signal_close_time']).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M')} 北京时间；蓄势{c['near_zero_bars']}根。收盘{c['signal_close_price']:.9g}，前12根区间{c['box_low']:.9g}–{c['box_high']:.9g}；突破门={c['S01']}，高周期已知={c['H00']}，同向={c['H01']}。",
            f"完整neutral持有{c['hold_bars']}根，入场{c['entry_price']:.9g}、退出{c['exit_price']:.9g}，退出时钟{c['exit_time']}，净{c['net_bp']:.2f}bp。没有保护止损，这不是可直接兑现的实盘收益。",
            f"![{c['title']}]({ROOT/c['path']})"]
    text+=['## 数据、风险与诚实声明',
        f'固定54个既有历史品种，{sum(x["rows"] for x in manifest["sources"]):,}根15m原始价格记录，起点{min(x["start"] for x in manifest["sources"])}，结束收盘{max(x["end_close"] for x in manifest["sources"])}。原始候选{len(e):,}个，其中2025 {int(e.fold.eq("validation").sum()):,}个，全体经济正类率{100*e.net_bp.gt(0).mean():.2f}%。匹配对照数{audit["control_count"]:,}；所有来源SHA、覆盖和开始结束时间在manifest.json。',
        f'保存账本验证{len(audit["checks"])}项全部通过；V2/V3原始箭头的事件数、平均净收益、组合净收益/回撤、匹配超额逐组一致。V3有35个因果/高周期合成场景与7个账本损坏场景，42测试通过。未新增holdout、未训练、未promote、未修改线上。',
        '原始价格均在2026-01-01之前。最后一根2025 K线可能在2026-01-01 00:00标记，这是2025末根收盘，不是读取2026价格。高周期与本周期有独立热身，不能用340根小周期冒充340根大周期。',
        '没有人工失败标签，不能把净亏损全称假启动；没有样本外实盘100笔确认。54池存在选择、上市/幸存者偏差；2025此前观察，多个研究的选择误差不由本轮Holm全部抵消。月符号检验假定月块符号可交换，匹配随机不代表市场完全随机化。',
        '最大回撤是逐K收盘组合回撤，不含盘中保证金与爆仓；20bp只是统一费用代理，无真实滑点/资金费。未用3R止盈或保护止损，不能将长持收益除以信号K线极窄止损，冒称已兑现高R。保存的日采样只能核对完整回撤的下界；完整月边界NAV/控制波动桶未持久化，原始p和实际桶匹配不是独立账本复算，而由冻结实现与合成测试覆盖。',
        '高周期使用已确认值的依据见 [TradingView 官方说明](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。本研究坚持已有Pine/监控的local OPEN口径，没有用未来高周期确认补历史信号。',
        '## 复现与交付',
        f'研究冻结提交`{manifest["builder_commit"]}`；报告提交`{builder}`。已有账本禁止覆盖，下面研究命令用于同名产物尚不存在的环境；已有账本可只重建展示。',
        '```bash\ncd /Users/zhangzc/fable-trading\n.venv/bin/python -m pytest tests/test_imacd_launch_context.py tests/test_imacd_launch_verify.py -q\n.venv/bin/python -m yoyo.evaluation.imacd_launch_research\n.venv/bin/python -m yoyo.evaluation.imacd_launch_report\npython3 scripts/md_to_html.py analysis/p1_imacd_launch_context_20260908.md --out-dir analysis/html\n```',
        f'[形成过程＋价格位置完整报告]({ROOT/"analysis/html/p1_imacd_formation_memory_20260908.html"}) · [本轮冻结计划]({EXP/"PROJECT_PLAN.md"}) · [完整结果CSV]({out/"summary.csv"}) · [账本验证]({out/"ledger_verification.json"})',
        '下一步遵守单变量：若候选通过，本轮也不自动上线，需另作后续时间段验收；未通过则保留失败结果，不能临时换窗口或把两个门堆叠起来宣称改进。任何需要修改实际Pine默认或推送规则的版本都必须有清晰对应的验证结果。']
    REPORT.write_text('\n\n'.join(text)+'\n')
    subprocess.run([sys.executable,str(ROOT/'scripts/md_to_html.py'),str(REPORT),'--out-dir',str(ROOT/'analysis/html')],check=True)
    receipt=dict(report_builder_commit=builder,research_builder_commit=manifest['builder_commit'],generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        baseline_matches_v2=True,new_holdout_consumptions=0,live_changes=False,case_count=sum(c['available'] for c in examples),
        report_sha256=r.digest(REPORT),html_sha256=r.digest(REPORT.parent/'html'/REPORT.with_suffix('.html').name))
    (out/'report_receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(decisions=d,report=str(REPORT)),ensure_ascii=False,indent=2))


if __name__=='__main__':build()
