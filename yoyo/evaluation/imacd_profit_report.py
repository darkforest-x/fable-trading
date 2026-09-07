"""Audit saved IMACD V2 ledgers and explain zero-band launch/trend-holding.

No new strategy, fitting, matching, cost or exit selection is performed here.
Price inputs are the hashed read-only OKX files in the frozen V2 manifest.
Plots show future outcomes explicitly for hindsight explanation. Group reports
use the predeclared zero-run bins, never a future-return entry feature.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from .imacd_indicator_audit import ROOT, aggregate
from .imacd_profit_mechanism import OUT, DATA_OUT, FOLDS, features, distribution, inference, rank_baseline

FIG = ROOT/'analysis/figures/imacd_profit_20260907'
REPORT = ROOT/'analysis/p0_imacd_profit_mechanism_20260907.md'
POLICIES = ['cross_signal','departure_signal','departure_neutral','departure_opposite']
PN = dict(zip(POLICIES,['A 原交叉进出','B 零轴启动/反向交叉退出','C 零轴启动/主线回零退出','D 零轴启动/主线反向退出']))
FN = {'discovery':'2023–2024','replication':'2025','exposed':'2026上半年'}
BLUE='#2563a6'; ORANGE='#d07828'; INK='#29333d'; GRAY='#78828c'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])


def num(x, digits=2):
    return 'N/A' if x is None or pd.isna(x) else f'{x:.{digits}f}'


def pc(x):
    return num(x/100)+'%' if x is not None and pd.notna(x) else 'N/A'


def load_bars(manifest, summary):
    raw={}; cache={}
    for src in manifest['sources']:
        path=ROOT/src['path']
        assert digest(path)==src['sha256'], f'source changed: {path}'
        parts=path.name.split('_');symbol=parts[1];base=int(parts[4][:-1])
        d=pd.read_csv(path)
        d.index=pd.DatetimeIndex(pd.to_datetime(d.open_time,utc=True))
        raw[symbol,base]=d[['open','high','low','close']].astype(float)
    end=pd.Timestamp('2026-07-01',tz='UTC')
    for symbol,minutes in summary[['symbol','minutes']].drop_duplicates().itertuples(index=False,name=None):
        base=minutes if minutes<15 else 15
        b=raw[symbol,base];b=b.loc[b.index+pd.Timedelta(minutes=base)<=end]
        cache[symbol,minutes]=aggregate(b,minutes,base)
    return cache


def independent_md(b):
    """Scalar independent recurrence, no imported indicator implementation."""
    out=[]; h=[]; l=[]; e1=e2=hi=lo=None
    for k,r in enumerate(b.itertuples()):
        s=(r.high+r.low+r.close)/3
        e1=s if e1 is None else (2/35)*s+(33/35)*e1
        e2=e1 if e2 is None else (2/35)*e1+(33/35)*e2
        h.append(r.high);l.append(r.low)
        if k==33:hi=sum(h)/34;lo=sum(l)/34
        elif k>33:hi=(33*hi+r.high)/34;lo=(33*lo+r.low)/34
        mi=2*e1-e2
        out.append(0. if hi is None else mi-hi if mi>hi else mi-lo if mi<lo else 0.)
    return np.asarray(out)


def equity(b,q):
    """Independently mark a saved nonoverlapping fixed-notional trade ledger."""
    cash=1.;path=[1.];last=-1
    for r in q.sort_values('entry_i').itertuples():
        assert r.entry_i>=last
        stop=r.exit_i+(r.exit_kind=='boundary_mark')
        path.extend(cash+r.side*(b.close.iloc[r.entry_i:stop].to_numpy()/r.entry_price-1)-.001)
        cash+=r.net_bp/10000;path.append(cash);last=stop
    p=np.asarray(path); peak=np.maximum.accumulate(p)
    return dict(fixed_notional_return_pct=(cash-1)*100,
                close_marked_drawdown_pct=float(np.max((peak-p)/peak)*100),
                minimum_equity=float(p.min()), crossed_zero=bool(p.min()<=0))


def audit(e,c,p,s,cache):
    qa={'event_rows':len(e),'control_rows':len(c),'portfolio_rows':len(p),'summary_rows':len(s)}
    assert not e.duplicated(['event_id','policy']).any()
    controls=c.groupby(['event_id','policy']).net_bp.agg(['mean','count'])
    j=e.join(controls,on=['event_id','policy'])
    assert (j.control_n==j['count'].fillna(0)).all()
    exact=j.control_n==3
    assert np.allclose(j.loc[exact,'control_mean_net_bp'],j.loc[exact,'mean'])
    assert j.loc[~exact,'control_mean_net_bp'].isna().all()
    assert np.allclose(j.loc[exact,'excess_bp'],j.loc[exact,'net_bp']-j.loc[exact,'mean'])
    qa['fully_matched_events']=int(exact.sum());qa['unmatched_events']=int((~exact).sum())
    fcache={}; scalar_errors={}; paths=[]; mfe_checks=0
    for (symbol,minutes),b in cache.items():
        f=features(b);fcache[symbol,minutes]=f
        if minutes==240:
            diff=float(np.abs(independent_md(b)-f.md).max())
            assert diff<1e-7;scalar_errors[symbol]=diff
        oo=b.open.to_numpy();cc=b.close.to_numpy();md=f.md.to_numpy();sh=f.sh.to_numpy()
        for frame,is_control in [(e,False),(c,True)]:
            sub=frame[(frame.symbol==symbol)&(frame.minutes==minutes)]
            for (fold,policy),g in sub.groupby(['fold','policy']):
                i=g.control_i.to_numpy(int) if is_control else g.signal_i.to_numpy(int)
                en=g.entry_i.to_numpy(int);ex=g.exit_i.to_numpy(int);side=g.side.to_numpy(int)
                natural=g.exit_kind.eq('natural').to_numpy()
                assert (en==i+1).all() and (ex>=en).all()
                ep=oo[en];xp=np.where(natural,oo[ex],cc[ex])
                assert np.allclose(ep,g.entry_price,rtol=0,atol=1e-8)
                assert np.allclose(xp,g.exit_price,rtol=0,atol=1e-8)
                assert np.allclose(side*(xp/ep-1)*10000-20,g.net_bp,rtol=0,atol=1e-7)
                assert np.allclose(g.gross_bp-g.net_bp,20)
                start,end=[(a,z) for name,a,z in FOLDS if name==fold][0]
                end=pd.Timestamp(end,tz='UTC')
                last=int(np.flatnonzero(b.index+pd.Timedelta(minutes=minutes)<=end)[-1])
                assert (ex<=last).all() and (b.index[en]>=pd.Timestamp(start,tz='UTC')).all()
                # Independently locate the first legal future exit state.
                for direction in (1,-1):
                    mask=side==direction
                    if not mask.any():continue
                    if policy.endswith('_signal'):
                        prev=np.r_[np.nan,sh[:-1]]
                        valid=(sh<0)&(prev>=0) if direction==1 else (sh>0)&(prev<=0)
                    else:valid=md*direction<=0 if policy.endswith('_neutral') else md*direction<0
                    seq=np.flatnonzero(valid);z=np.searchsorted(seq,i[mask],side='right')
                    future=np.r_[seq,len(b)][z]
                    expected=np.minimum(future+1,last)
                    assert np.array_equal(expected,ex[mask])
                    assert np.array_equal(future+1<=last,natural[mask])
                if not is_control:
                    assert np.array_equal(pd.to_datetime(g.signal_time,utc=True),b.index[i])
                    assert np.array_equal(pd.to_datetime(g.entry_time,utc=True),b.index[en])
                    assert np.array_equal(pd.to_datetime(g.exit_bar_time,utc=True),b.index[ex])
                    if policy.startswith('departure'):
                        assert (md[i-1]==0).all() and np.array_equal(np.sign(md[i]),side)
                        run=[]
                        for ix in i:
                            z=ix-1
                            while z>=0 and md[z]==0:z-=1
                            run.append(ix-z-1)
                        assert np.array_equal(run,g.zero_before)
                    # Check representative MFE/MAE against held raw prices.
                    for r in g.iloc[np.unique(np.linspace(0,len(g)-1,min(5,len(g))).astype(int))].itertuples():
                        stop=r.exit_i+(r.exit_kind=='boundary_mark')
                        held=b.iloc[r.entry_i:stop]
                        hi=max(r.entry_price,r.exit_price,float(held.high.max()))
                        lo=min(r.entry_price,r.exit_price,float(held.low.min()))
                        best=(hi/r.entry_price-1 if r.side==1 else 1-lo/r.entry_price)*10000
                        worst=(lo/r.entry_price-1 if r.side==1 else 1-hi/r.entry_price)*10000
                        assert abs(best-r.mfe_bp)<1e-7 and abs(worst-r.mae_bp)<1e-7
                        mfe_checks+=1
        qs=p[(p.symbol==symbol)&(p.minutes==minutes)]
        for (fold,policy),g in qs.groupby(['fold','policy']):
            row=s[(s.symbol==symbol)&(s.minutes==minutes)&(s.fold==fold)&(s.policy==policy)].iloc[0]
            z=equity(b,g)
            assert abs(z['fixed_notional_return_pct']-row.portfolio_fixed_notional_return_pct)<1e-7
            assert abs(z['close_marked_drawdown_pct']-row.portfolio_close_marked_drawdown_pct)<1e-7
            paths.append(dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,**z))
        # Matching is checked at source timestamps, not on outcome columns.
        sub=c[(c.symbol==symbol)&(c.minutes==minutes)].merge(
            e[['event_id','policy','signal_i','side']],on=['event_id','policy'],validate='many_to_one',suffixes=('','_event'))
        ix=sub.control_i.to_numpy(int);tx=sub.signal_i.to_numpy(int)
        assert (sub.side==sub.side_event).all()
        assert np.array_equal(b.index[ix].strftime('%Y-%m'),b.index[tx].strftime('%Y-%m'))
        assert np.array_equal(f.volbin.iloc[ix].to_numpy(),f.volbin.iloc[tx].to_numpy())
        assert np.array_equal(f.zone.iloc[ix].to_numpy(),f.zone.iloc[tx].to_numpy())
        for (fold,policy),g in sub.groupby(['fold','policy']):
            assert not g.control_i.duplicated().any()
            kind='departure' if policy.startswith('departure') else 'cross'
            assert (f[kind].iloc[g.control_i.to_numpy(int)]==0).all()
    for key,g in e.groupby(['symbol','minutes','fold','policy']):
        row=s.set_index(['symbol','minutes','fold','policy']).loc[key]
        assert len(g)==row.n and abs(g.net_bp.mean()-row.mean_net_bp)<1e-7
        assert int(g.net_bp.gt(0).sum())==round(row.win_pct*len(g)/100)
    dep=e[e.policy.str.startswith('departure')]
    assert (dep.groupby('event_id').policy.nunique()==3).all()
    for col in ['signal_i','entry_i','entry_price','side','zero_before']:
        assert (dep.groupby('event_id')[col].nunique()==1).all()
    control_maps=c[c.policy.str.startswith('departure')].groupby(['event_id','control_i']).policy.nunique()
    assert (control_maps==3).all()
    paths=pd.DataFrame(paths);paths.to_csv(OUT/'portfolio_solvency_audit.csv',index=False)
    qa.update(source_hashes_verified=True,all_entry_exit_prices_verified=True,first_legal_exit_verified=True,
              all_matching_keys_verified=True,control_reuse_within_policy=False,same_departure_mother_group=True,
              independent_scalar_md_max_error=scalar_errors,mfe_mae_cases_checked=mfe_checks,
              close_marked_paths_crossing_zero=int(paths.crossed_zero.sum()),
              matching_inference_is_observational=True)
    return qa,paths,fcache


def derived_tables(e,cache):
    out=[];paired=[]
    g=e[(e.minutes==240)&(e.policy=='departure_neutral')]
    for symbol,rule in [('BTC','2–8根'),('ETH','≥9根')]:
        z=g[(g.symbol==symbol)&(g.zero_before.between(2,8) if symbol=='BTC' else g.zero_before>=9)]
        for fold,_,_ in FOLDS:
            q=z[z.fold==fold];x=q.net_bp.sort_values()
            out.append(dict(symbol=symbol,fold=fold,rule=rule,**distribution(x),**rank_baseline(q),
                **equity(cache[symbol,240],q),**inference(q.excess_bp,q.month),
                matched_n=int(q.excess_bp.notna().sum()),matched_case_mean_net_bp=float(q.loc[q.excess_bp.notna(),'net_bp'].mean()),
                controls_mean_net_bp=float(q.control_mean_net_bp.mean()),
                mean_without_best_bp=float(x.iloc[:-1].mean()),
                median_hold_days=float(q.hold_bars.median()*240/1440),
                worst_mae_bp=float(q.mae_bp.min()),
                boundary_marks=int(q.exit_kind.eq('boundary_mark').sum())))
    for symbol in ['BTC','ETH']:
        q=e[(e.minutes==240)&(e.symbol==symbol)&e.policy.str.startswith('departure')]
        for fold,_,_ in FOLDS:
            z=q[q.fold==fold].pivot(index='event_id',columns='policy',values='net_bp')
            for old,new in [('departure_signal','departure_neutral'),('departure_neutral','departure_opposite')]:
                diff=z[new]-z[old]
                paired.append(dict(symbol=symbol,fold=fold,old=old,new=new,n=len(diff),mean_delta_bp=float(diff.mean()),
                                   improved_pct=float(diff.gt(0).mean()*100),median_delta_bp=float(diff.median())))
    cohorts=pd.DataFrame(out);pairs=pd.DataFrame(paired)
    cohorts.to_csv(OUT/'candidate_cohorts.csv',index=False);pairs.to_csv(OUT/'paired_exits.csv',index=False)
    return cohorts,pairs


def setup_charts():
    plt.rcParams.update({'font.family':['Arial Unicode MS','PingFang SC','DejaVu Sans'],
        'font.size':11,'axes.unicode_minus':False,'axes.spines.top':False,'axes.spines.right':False,
        'text.color':INK,'axes.labelcolor':INK,'xtick.color':INK,'ytick.color':INK,
        'figure.facecolor':'white','axes.facecolor':'white','savefig.facecolor':'white'})
    FIG.mkdir(parents=True,exist_ok=True)


def case_chart(e,cache,fcache,eid,filename,description):
    q=e[(e.event_id==eid)&e.policy.str.startswith('departure')].set_index('policy')
    r=q.loc['departure_neutral']; b=cache[r.symbol,int(r.minutes)]; f=fcache[r.symbol,int(r.minutes)]
    left=max(0,int(r.signal_i)-max(60,int(r.zero_before)+12));right=min(len(b)-1,int(q.exit_i.max())+12)
    g=b.iloc[left:right+1];ff=f.iloc[left:right+1];x=mdates.date2num(g.index.to_pydatetime());w=int(r.minutes)/1440*.67
    fig,(ax,ai)=plt.subplots(2,1,figsize=(13,7.8),height_ratios=[2.4,1],sharex=True)
    fig.subplots_adjust(left=.085,right=.98,bottom=.15,top=.80,hspace=.08)
    fig.text(.085,.955,description,fontsize=18,fontweight='bold')
    fig.text(.085,.912,f'OKX {r.symbol}USDT 永续 · 4小时 · 零轴持续 {int(r.zero_before)} 根 · '+('做多' if r.side==1 else '做空')+' · 日期 UTC',fontsize=11)
    labels=[]
    for key,letter in [('departure_signal','B'),('departure_neutral','C'),('departure_opposite','D')]:
        z=q.loc[key];labels.append(f'{letter} {dict(B="反向交叉",C="主线回零",D="主线反向")[letter]} {z.net_bp/100:+.2f}%')
    fig.text(.085,.873,'同一入场，三种退出：  '+'   /   '.join(labels),fontsize=12)
    for xx,row in zip(x,g.itertuples()):
        ax.plot([xx,xx],[row.low,row.high],color=GRAY,lw=.55)
        ax.add_patch(Rectangle((xx-w/2,min(row.open,row.close)),w,max(abs(row.close-row.open),row.close*.00004),
                               facecolor='white' if row.close>=row.open else '#9099a2',edgecolor=GRAY,lw=.55))
    zero_start=b.index[int(r.signal_i)-int(r.zero_before)]
    for a in (ax,ai):
        a.axvspan(zero_start,b.index[int(r.signal_i)],color=GRAY,alpha=.12)
        a.axvline(b.index[int(r.entry_i)],color=INK,lw=1.3)
        a.grid(axis='y',alpha=.15)
    ax.scatter(b.index[int(r.entry_i)],r.entry_price,marker='^' if r.side==1 else 'v',color=INK,s=65,zorder=5,label='入场（下根开盘）')
    styles={'departure_signal':(ORANGE,':','B'), 'departure_neutral':(BLUE,'--','C'),'departure_opposite':(GRAY,'-.','D')}
    for k,(col,ls,lab) in styles.items():
        z=q.loc[k]
        for a in (ax,ai):a.axvline(b.index[int(z.exit_i)],color=col,ls=ls,lw=1.5)
        ax.scatter(b.index[int(z.exit_i)],z.exit_price,color=col,marker='o',s=32,zorder=6,label=f'{lab}退出')
    colors=np.where(ff.md>=0,BLUE,'white')
    ai.bar(x,ff.md,width=w,color=colors,edgecolor=BLUE,linewidth=.55,label='md 主线')
    ai.plot(x,ff.sb,color=ORANGE,lw=1.2,label='sb 信号线')
    ai.axhline(0,color=INK,lw=.8);ai.set_ylabel('IMACD\nUSDT');ax.set_ylabel('价格 / USDT')
    ax.legend(loc='upper left',ncol=4,fontsize=9,frameon=False)
    ai.legend(loc='upper left',ncol=2,fontsize=9,frameon=False)
    ai.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=5,maxticks=9))
    ai.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    fig.text(.085,.06,f'灰带为入场前已知零轴状态；后续走势用于事后解释。C 持仓最大浮盈 {r.mfe_bp/100:.2f}%，最大逆行 {r.mae_bp/100:.2f}%。',fontsize=10)
    fig.text(.085,.025,'来源：已保存的 OKX 15m 数据聚合；固定34/9，下一根开盘成交，往返扣0.20%；未计资金费率。样本按结果选取，不代表胜率。',fontsize=9)
    path=FIG/filename;fig.savefig(path,dpi=150);plt.close(fig)
    return dict(event_id=eid,path=str(path.relative_to(ROOT)),description=description)


def exit_chart(s):
    fig,ax=plt.subplots(figsize=(12,6.5));fig.subplots_adjust(left=.10,right=.98,bottom=.19,top=.79)
    fig.text(.10,.935,'同一批零轴启动，收益会被退出规则改写',fontsize=19,fontweight='bold')
    fig.text(.10,.882,'ETHUSDT 永续 · 4小时 · 每次信号平均净收益 / % · 固定34/9，往返扣0.20%',fontsize=12)
    folds=list(FN);x=np.arange(3);width=.24
    for i,(policy,col,hatch,lab) in enumerate([('departure_signal',ORANGE,'','B 反向交叉退出'),('departure_neutral',BLUE,'','C 主线回零退出'),('departure_opposite',GRAY,'//','D 主线反向退出')]):
        q=s[(s.symbol=='ETH')&(s.minutes==240)&(s.policy==policy)].set_index('fold').loc[folds]
        values=q.mean_net_bp.to_numpy()/100
        ax.bar(x+(i-1)*width,values,width=.22,color=col,hatch=hatch,label=lab)
        for xx,v in zip(x+(i-1)*width,values):ax.text(xx,v+(.08 if v>=0 else -.08),f'{v:+.2f}%',ha='center',va='bottom' if v>=0 else 'top',fontsize=11)
    ax.set_xticks(x,[f'{FN[f]}\nn={int(s[(s.symbol=="ETH")&(s.minutes==240)&(s.policy=="departure_neutral")&(s.fold==f)].iloc[0].n)}' for f in folds])
    ax.axhline(0,color=INK,lw=1);ax.set_ylim(-2.05,2.65);ax.set_ylabel('每次信号平均净收益 / %');ax.grid(axis='y',alpha=.14)
    ax.legend(ncol=3,loc='upper left',frameon=False,fontsize=10)
    fig.text(.10,.07,'2023–2024年提前交叉退出反而更好；2025与2026上半年，回零退出保留了更多尾部利润。',fontsize=11)
    fig.text(.10,.025,'来源：完整启动事件账本；三种退出逐笔配对。均值不是账户收益；D可能重叠，不能将全部事件收益直接累加。',fontsize=9)
    path=FIG/'eth4h_exit_comparison.png';fig.savefig(path,dpi=150);plt.close(fig)
    return str(path.relative_to(ROOT))


def write_report(s,g,cohorts,pairs,qa,paths,cases,manifest,e):
    sections=[]
    def add(x):sections.append(x.strip())
    add('''# IMACD：零轴横盘后启动，利润怎样留在手里

研究日期：2026-09-07。OKX BTCUSDT/ETHUSDT USDT永续；原参数34/9；长期样本2023-01-01至2026-06-30；不是当前行情信号。

## 这次发现的核心

**找到了与你“零轴横盘后启动、拿住大趋势”相符的盈利机制线索：少数启动会发展成大波段，过程中反向金死叉先发生，价格趋势却继续；过早按反向交叉离场，会把这部分利润截掉。**

最直接的例子：ETH4h 2025-04-23同一入场，反向交叉退出净−0.32%，主线回零退出净+41.02%；2025-07-03同一入场，分别−3.62%与+35.48%。这些是按已定义规则和下一根开盘计算的结果，不是拿最高点当收益。

**值得继续盯住的候选：ETH4h连续≥9根零轴后启动、BTC4h连续2–8根零轴后启动，持有至主线回零。** 两组在三个时间段均录得正均值，但大部分交易仍亏损，随机对照后的优势没有通过严格多重检验。这是一条值得保留的研究方向，尚不足以认定“已验证能赚大钱”。

## 先把形态定义到可以重现

1. “零轴横盘”指主线md精确等于0，代表零滞后均线处于SMMA高低通道内；它不是价格必然窄幅横盘，也不等于多条均线已经密集。
2. “启动”指上一根md=0，本根md首次变正/负；本根收盘后决定多/空，下一根开盘进入。同一次脱离零轴不重复追每个箭头。
3. “拿住”分别测试三种退出：B首次反向交叉；C主线回零或越过零轴反向；D主线真正反向。退出状态在收盘确认，下一根开盘执行。
4. 保持原34/9，不调参、不叠加过滤器、不加TP/SL。20bp往返成本固定。先固定同一批入场，再只改退出，才能看清收益差异。

为什么会出现这个机制：信号线sb是md的9根均值；md从快速扩张转为缓慢扩张时，可以下穿sb，但仍在零轴上方。此时交叉表达的是推动速度相对近期均值放慢，不必然代表整个上行段结束。空头对称。

连续≥9根md=0还有一个代码层面的含义：在启动前，sb及sh也已归零，旧的信号线记忆消失。2–8根的短归零则可能保留前一段方向记忆。这个差别为两个币种不同的结果提供解释线索，但本实验尚未检验前一段方向，不能把解释当结论。''')
    add('## 同一入场，只改退出\n\nETH4h完整零轴启动母群；单位为每次事件平均净收益，不是账户收益。配对三分支样本完全一致。')
    rows=[]
    for fold in FN:
        q=s[(s.symbol=='ETH')&(s.minutes==240)&(s.fold==fold)].set_index('policy')
        rows.append([FN[fold],int(q.loc['departure_neutral','n'])]+[pc(q.loc[p,'mean_net_bp']) for p in POLICIES[1:]]+[pc(q.loc['departure_neutral','excess_bp'])])
    add(table(['时段','启动数','B 交叉退出','C 回零退出','D 反向退出','C 相对匹配随机超额'],rows))
    add('![同入场退出比较](figures/imacd_profit_20260907/eth4h_exit_comparison.png)')
    add('2025年C相对B每次增加2.44个百分点，2026上半年增加2.95个百分点；但2023–2024年减少0.93个百分点。因此“多拿总更好”不成立。D进一步等到反向，ETH4h三个时间段的事件均值都低于C：持有规则也需要结束边界。')
    add('## 找到的两组候选形态\n\n两组来自预先声明的零带根数分组，报告看完全部周期与分组后重点展示，属于探索性选择。三段同号不等于三段都从未见过，更不等于独立终验。')
    rows=[]
    for r in cohorts.itertuples():
        rows.append([f'{r.symbol}4h {r.rule}',FN[r.fold],r.n,pc(r.mean_net_bp),pc(r.median_net_bp),num(r.win_pct)+'%',num(r.pf),pc(r.controls_mean_net_bp),pc(r.excess_bp),num(r.p,4)])
    add(table(['形态','时段','n','每次净均值','净中位数','胜率','PF','随机对照均值','超额均值','月聚类p（未校正）'],rows))
    add('PF=全部正收益之和/全部负收益绝对值之和。这里随机对照与信号使用相同退出算法、同成本。完整匹配样本数见候选CSV；缺少3个对照的事件不参与超额推断。')
    rows=[]
    for symbol in ['BTC','ETH']:
        for name in ['2_to_8','9_plus']:
            q=g[(g.symbol==symbol)&(g.minutes==240)&(g.policy=='departure_neutral')&(g.feature=='zero_before')&(g['group']==name)].set_index('fold')
            rows.append([symbol,'2–8根' if name=='2_to_8' else '≥9根']+[f'{pc(q.loc[f,"mean_net_bp"])} (n={int(q.loc[f,"n"])}) / 对照 {pc(q.loc[f,"controls_mean_net_bp"])}' for f in FN])
    add(table(['币种4h','零带长度','2023–24 净均值/对照','2025 净均值/对照','2026H1 净均值/对照'],rows))
    add('**不能把“横得越久、爆发越强”一概而论。** ETH偏长零带，BTC偏短零带，是本次更值得继续追踪的差异。BTC长零带三段净均值都负。这个发现不证明币种有永恒属性，也没有排除少数历史行情驱动结果。')
    add('## 大钱来自少数大波段：这对执行意味着什么')
    rows=[]
    for r in cohorts.itertuples():
        rows.append([f'{r.symbol}4h {r.rule}',FN[r.fold],pc(r.mean_net_bp),pc(r.mean_without_best_bp),pc(r.rest_realized_mean_bp),pc(r.worst_net_bp),pc(r.worst_mae_bp),num(r.median_hold_days)+'天'])
    add(table(['形态','时段','全部净均值','删除最大赢家后均值','删除事后前10%后均值','最差单次净收益','最差持仓逆行','持仓中位数'],rows))
    add('''ETH候选在三个时段里只去掉最大赢家，均值就全部转负。BTC候选2025年也如此。这里删除赢家只是依赖度诊断，绝不能用事后赢家名单制定入场。

这类系统的关键能力是：连续假启动后仍能执行下一次有效启动；进入大波段后容忍正常回撤；在已定义的结束条件出现时兑现。若把止盈设得很近或每次出现反向交叉就走，会改变盈利来源。反过来，容忍回撤不等于任何亏损都死扛：本研究没有保护止损，出现过超过10%的单笔亏损，不能直接照搬成杠杆实盘。''')
    add('## 实际K线：两个2025赢家、一个BTC赢家、一个同形态失败\n\n每张图明确显示已知零带和事后价格路径。图按结果挑选用于解释，完整胜负分母在前表与逐笔账本。日期均UTC。')
    for case in cases:
        add('### '+case['description'])
        q=e[(e.event_id==case['event_id'])&e.policy.str.startswith('departure')].set_index('policy')
        rows=[]
        for policy in POLICIES[1:]:
            r=q.loc[policy]
            rows.append([PN[policy],r.entry_time[:16],num(r.entry_price),r.exit_bar_time[:16],num(r.exit_price),pc(r.net_bp)])
        add(table(['规则','入场UTC','入场价','退出UTC','退出价','净收益'],rows))
        add(f'![{case["description"]}](figures/imacd_profit_20260907/{Path(case["path"]).name})')
    add('ETH 2025-01-27失败样本此前连续62根（约10.3天）md=0，向下启动后价格迅速反抽，等回零离场净亏约12.26%。它和赢家属于同一长零带候选，证明“贴零后启动”只是筛选条件，不能保证突破方向正确。')
    add('## 各周期真正告诉了什么\n\n下表全部使用C：零轴启动→回零退出。括号n；每次净均值与匹配随机均值并列，避免把市场方向收益当指标优势。15m及以上使用同一长历史；不按全样本收益挑一个所谓最优周期。')
    rows=[]
    for symbol in ['BTC','ETH']:
        for minutes in [15,30,60,120,240,360,720,1440]:
            q=s[(s.symbol==symbol)&(s.minutes==minutes)&(s.policy=='departure_neutral')].set_index('fold')
            rows.append([symbol,f'{minutes}m' if minutes<60 else f'{minutes//60}h']+[f'{pc(q.loc[f,"mean_net_bp"])} (n={int(q.loc[f,"n"])}) / {pc(q.loc[f,"controls_mean_net_bp"])}' for f in FN])
    add(table(['币种','周期','2023–24 净/随机','2025 净/随机','2026H1 净/随机'],rows))
    add('15m/30m的均值大多不足以覆盖20bp，频繁切换对这套持有逻辑不利。4h出现可解释的形态条件；BTC12h/1d全母群三段正均值，但信号少，而且BTC日线2025随机对照更好，不能据绝对盈利宣布优势。ETH日线2025只有3次信号，其大收益不能外推。6h/12h/1d可作为低频研究对象，尚无足够证据用来给4h信号加过滤；多周期共振本轮未回测。')
    rows=[]
    for r in s[(s.minutes<15)&(s.policy=='departure_neutral')].itertuples():
        rows.append([r.symbol,f'{r.minutes}m',r.observed_start[:16],r.observed_end_close[:16],r.n,pc(r.mean_net_bp),pc(r.controls_mean_net_bp),pc(r.excess_bp)])
    add(table(['币种','周期','开始UTC','结束UTC','n','每次净均值','随机均值','超额'],rows))
    add('小周期缓存范围较短。5m在2025仅有12月下旬，不能称2025全年验证。BTC1m缺失；周线可用总根数不够340根预热；没有伪造这些结果。当前覆盖1m（仅ETH）、3m、5m、15m、30m、1h、2h、4h、6h、12h、1d；未声称覆盖TradingView所有自定义周期。')
    add('''## 和你的Notion怎样连接\n\n最直接相关的是[交易系统-更新中](https://app.notion.com/p/2ac8856479af8063b17bc02256e564a9)里“回测Impulse MACD [LazyBear] + 均线密集”的待办。当前找出的零轴启动，是把这个想法中IMACD的一半写成了可执行规则；尚未把均线密集量化，因此没有把它称为对你完整系统的验证。

[CM_Williams_Vix_Fix交易系统](https://app.notion.com/p/2248856479af80d8b0fdcb7360750e01)及[Stochastic + Vix + Impulse MACD策略回测](https://app.notion.com/p/2258856479af80d5bbf3dd45c59278d2)把IMACD列入组合；对应[bonk1h](https://app.notion.com/p/2278856479af809fbeeeebe17910a3e6)的几笔截图收益不能替代BTC/ETH的完整账本。[2025下半年计划](https://app.notion.com/p/2358856479af8047bcfccab5ea9a2f0e)的趋势、动量、成交量、结构分层，可以作为下一轮研究背景，不能直接当作本轮已测试条件。

笔记里“三根没按预期走就退出”、Vix体系里的1:1后保本，与本次“忍受回调、拿到回零”可能产生冲突。它们属于不同体系或候选规则，不能不经同母群比较就全部叠加。本轮只检索引用，没有修改Notion。''')
    add('''## 数据、对照与统计口径\n\n完整母群包括全部符合启动定义的信号，盈利标签为已定义退出后扣20bp收益>0；不是未来最高涨幅标签。分段按时间：2023–2024 discovery、2025 replication、2026H1 exposed。2026及过往资料已经暴露给研究，不声称独立未见终验。Owner允许所有日期，本轮四个策略配置各第1次消耗holdout；分组是同一轮预声明诊断，不能重新宣称是未使用配置。独立价格核对与数值修复没有新增交易配置或抽取新样本。

每条信号找3个同币、同周期、同月、同ATR/close滚动240根百分位五桶、同md区域的随机时点，赋予相同多空方向；每个入场家族不重复使用控制时点。选择控制时不看未来；三个退出分支复用同一批控制。对照使用同一退出算法、20bp和边界记账。少于3个控制保留交易但不计算该事件的超额。

超额=信号净收益−三个对照平均净收益。按月聚类bootstrap区间与单侧符号置换p，承认月间独立近似。控制窗口可能互相重叠或与事件重叠，因此这是一种匹配观察研究，不是随机试验。没有宣称事件级独立。2025全部72个可比较单元做Holm校正（包括短样本5m），最小校正p=0.28125，没有任何一个达到项目p<0.01终验门槛。候选子组的p未再校正，只作描述。

单特征基线固定为入场方向×md/ATR14强度；给出AUC、强度最高10%样本的毛/净收益、胜率与匹配超额。这里AUC衡量强度与该退出下盈利标签的关系；没有训练L2，所以不是模型val AUC。2025作为时间验证段，完整指标见附表。按验证段强度排名取前10%是离线诊断，未构造可上线阈值。收益最高的事后10%只用于尾部依赖诊断，与这个因果强度排名严格区分。''')
    add(f'数据规模：{len(s)}个币种×周期×时段×规则单元；{len(e):,}条事件反事实（同一启动含三个退出版本，不是独立交易数）；{qa["control_rows"]:,}条控制反事实；完整3对照事件{qa["fully_matched_events"]:,}条，缺口{qa["unmatched_events"]:,}条。原始数据2022年开始用于长周期预热，长样本评估从2023年开始；每个周期至少340根热身。')
    rows=[]
    for fold in FN:
        for symbol in ['BTC','ETH']:
            q=e[(e.symbol==symbol)&(e.minutes==240)&(e.fold==fold)&(e.policy=='departure_neutral')]
            rows.append([symbol+'4h',FN[fold],len(q),num(q.net_bp.gt(0).mean()*100)+'%',int(q.exit_kind.eq('boundary_mark').sum()),int(q.excess_bp.notna().sum())])
    add(table(['核心母群','时间段','候选数','盈利正类率','边界强制记账','完整对照数'],rows))
    add('## 单仓路径与风险\n\n候选C各交易互不重叠。以下仅用每笔固定初始本金名义金额的研究路径，累计百分比是收益相加，非复利、非杠杆账户收益；回撤按已收盘价标记，盘中可能更大。不同时间段重置权益，不拼接跨年持仓。')
    rows=[]
    for r in cohorts.itertuples():
        rows.append([f'{r.symbol}4h {r.rule}',FN[r.fold],num(r.fixed_notional_return_pct)+'%',num(r.close_marked_drawdown_pct)+'%',num(r.minimum_equity,3),r.boundary_marks])
    add(table(['候选','时段','固定初始名义收益和','收盘标记最大回撤','最低权益/初始权益','边界记账数'],rows))
    add(f'全规则基线中有{qa["close_marked_paths_crossing_zero"]}个单仓路径的固定名义账面权益穿过0。原始摘要保留这些负结果用于审计，但它们不能当作可实施账户表现；完整标记在portfolio_solvency_audit.csv。两组展示候选是否穿零也在候选CSV逐项列明。这里没有保证金、逐仓/全仓、杠杆或清算引擎。')
    add('''## 风险与诚实声明\n\n- **已找到机制，尚未证明可持续赚钱。** 全母群退出优势跨期不恒定，子组选择是探索，严格对照与多重检验未达终验门槛。
- **尾部依赖非常强。** 不能错过少数大赢家，但事前也不能知道哪次是大赢家；“拿住”的代价是多数假启动和较大回撤。
- **20bp只是费用筛查。** 固定为既有约定，未建真实资金费率账本、滑点/价差与流动性模型；跨多周持仓尤其不能忽略资金费率。参见[OKX资金费率机制](https://www.okx.com/en-us/help/perps-funding-fee-mechanism)。
- **数据是已有OKX缓存。** 本机公开历史接口返回403，未换交易所；当前结论不覆盖7月以后新行情。源文件哈希固定，原始K线只读。
- **交易时钟已审计，尚未声称TradingView逐根原生parity。** 标量实现与向量实现核对；真实图表起算历史、日线时区仍需与用户图表设置一致。本轮均使用UTC聚合，日线对应UTC00:00。
- **无保护止损与仓位模型。** 因而本轮不是可直接照抄的资金管理方案，也没有实盘收益承诺。
- **边界平仓单列。** 分段最后一根close只是评估记账，真实策略是否平仓取决于退出信号，不是自然交易出口。''')
    add(f'''## 已完成的验证与来源\n\n全部事件和控制的入场/退出价格、下根开盘时钟、首次合法退出、匹配月份/波动桶/区域、不复用控制、三分支同母群、收益公式与摘要算术均通过重新核对。BTC/ETH4h另用独立标量递推核对md。MFE/MAE抽查{qa['mfe_mae_cases_checked']}条持仓路径，未使用退出开盘后的高低价。全部单仓路径独立逐bar重算并标记穿零。

主实验builder先提交：`59137e5d11f0afb0669f79711be93dfb29f8039a`。本机BLAS对有限输入发出浮点警告，修复为显式乘积求和后从保存账本刷新推断：`cd4c4b5`；事件/对照/单仓账本哈希完全不变，全部p最大变化0。保留first_run原始摘要与修复证据。此修复没有调整策略或利润结果。

原作者说明与公式背景：[LazyBear原指标](https://www.tradingview.com/script/qt6xLfLi-Impulse-MACD-LazyBear/)、[TradingView DEMA](https://www.tradingview.com/support/solutions/43000589132-double-exponential-moving-average-ema/)。收盘信号时钟依据用户提供Pine逻辑并参考[TradingView重绘说明](https://www.tradingview.com/pine-script-docs/v5/concepts/repainting/)。这些来源支持指标语义，不为本次盈利数字背书。

上一版V1是交叉分布审计，没有交易收益，因此经济指标N/A；本轮新增A原箭头策略作为同数据/同时钟/同成本经济基线，完整四规则同表对照如下。''')
    add('## 完整结果附表：基线、全部周期与失败结果\n\n全部为每事件统计；D事件可重叠，不作账户收益累加。净均值单位%，AUC为固定强度单特征；Top10为强度最高10%的毛/净均值。p为同组匹配超额的月聚类单侧置换，p-H为2025全72单元Holm；其他段N/A。CSV还含区间、单仓统计及尾部依赖。')
    rows=[]
    for r in s.itertuples():
        rows.append([r.symbol,str(r.minutes)+'m',FN[r.fold],{'cross_signal':'A','departure_signal':'B','departure_neutral':'C','departure_opposite':'D'}[r.policy],r.n,pc(r.mean_net_bp),num(r.win_pct),num(r.pf),pc(r.controls_mean_net_bp),pc(r.excess_bp),num(r.p,4),num(r.p_holm_replication,4),num(r.strength_auc,3),pc(r.score_top_decile_gross_bp)+' / '+pc(r.score_top_decile_net_bp),num(r.score_top_decile_win_pct),pc(r.score_top_decile_excess_bp)])
    add(table(['币','周期','时段','规则','n','净均值','胜率%','PF','随机均值','超额','p','p-H','强度AUC','强度Top10毛/净','Top10胜率%','Top10超额'],rows))
    add('''## 下一步应该沿什么线索研究\n\n1. 首选核验“均线密集＋零轴启动”是否能保留大赢家、减少假启动，分别在ETH4h长零带与BTC4h短零带上加同一个因果形态条件；先固定定义再回放。这直接承接Notion待办。新增阈值由Owner决定。
2. 对照已经找到的大赢家与最大失败，验证“上一段趋势方向、回零后是否同向再启动”这一条单变量，避免把短零带和长零带误当同一种压缩。
3. 再用同一批入场检查退出后的收益回吐与真实资金费率；若加入结构止损、分批兑现、ATR障碍或改变20bp，需Owner确定规则后单独登记，不能看到结果后回改。
4. 保留当前候选记录，未来新增未见时段进行前向检验；本轮没有上线、下单、训练或修改ACTIVE。

## 复现与交付文件

运行前必须具有manifest所列7个原始OKX CSV且哈希一致；没有这些数据不能从结果CSV伪造重建。所有原始输入只读，研究账本写入data/imacd_profit_mechanism_v2，不入git。最初运行使用冻结builder；当前代码重建会直接使用修复后的推断，不应再次调用只允许执行一次的--refresh-statistics归档命令。

```bash
cd /Users/zhangzc/fable-trading
git show 59137e5d11f0afb0669f79711be93dfb29f8039a:yoyo/evaluation/imacd_profit_mechanism.py
.venv/bin/python -m pytest tests/test_imacd_indicator_audit.py tests/test_imacd_profit_mechanism.py -q
.venv/bin/python -m yoyo.evaluation.imacd_profit_mechanism
.venv/bin/python -m yoyo.evaluation.imacd_profit_report
python3 scripts/md_to_html.py analysis/p0_imacd_profit_mechanism_20260907.md --out-dir analysis/html
```

严格重建应在允许的同一仓工作目录保存本次交付备份后执行，命令会重写同名研究产物；不需要更改原始缓存、分支、生产配置或依赖。''')
    rows=[]
    for name in ['summary.csv','groups.csv','candidate_cohorts.csv','paired_exits.csv','portfolio_solvency_audit.csv','manifest.json','audit_qa.json','statistics_refresh_qa.json']:
        rows.append([name,f'[打开](../experiments/active/exp-imacd-profit-mechanism-20260907-v2/{name})'])
    add(table(['交付','路径（仓内）'],rows))
    add('逐笔账本：`data/imacd_profit_mechanism_v2/events.csv`、`controls.csv`、`portfolio_trades.csv`。图表契约、配置计划与数据哈希保存在本实验目录。')
    REPORT.write_text('\n\n'.join(sections)+'\n')
    # Owner requires immediate Markdown-to-HTML conversion after source completion.
    subprocess.run(['python3','scripts/md_to_html.py',str(REPORT),'--out-dir','analysis/html'],cwd=ROOT,check=True)


def main():
    manifest=json.loads((OUT/'manifest.json').read_text())
    e=pd.read_csv(DATA_OUT/'events.csv');c=pd.read_csv(DATA_OUT/'controls.csv');p=pd.read_csv(DATA_OUT/'portfolio_trades.csv')
    s=pd.read_csv(OUT/'summary.csv');g=pd.read_csv(OUT/'groups.csv')
    for f in manifest['output_files']:assert digest(ROOT/f['path'])==f['sha256']
    cache=load_bars(manifest,s)
    qa,paths,fcache=audit(e,c,p,s,cache);print('All ledger/clock/control audits passed.',flush=True)
    cohorts,pairs=derived_tables(e,cache)
    assert not cohorts.crossed_zero.any()
    setup_charts();exit_chart(s)
    choices=[('ETH_240_replication_departure_7232','eth4h_20250423.png','ETH：反向交叉时尚未走出主升段'),
             ('ETH_240_replication_departure_7657','eth4h_20250702.png','ETH：启动后先回撤，随后走出大波段'),
             ('BTC_240_discovery_departure_4534','btc4h_20240129.png','BTC：短零带启动后的趋势延伸'),
             ('ETH_240_replication_departure_6718','eth4h_20250127_failure.png','失败对照：长时间贴零后也会假启动')]
    cases=[case_chart(e,cache,fcache,*choice) for choice in choices]
    qa['builder_commit']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    qa['cases']=cases;qa['all_candidate_equity_paths_positive']=True
    (OUT/'audit_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n')
    write_report(s,g,cohorts,pairs,qa,paths,cases,manifest,e)
    print(str(REPORT),flush=True)


if __name__=='__main__':main()
