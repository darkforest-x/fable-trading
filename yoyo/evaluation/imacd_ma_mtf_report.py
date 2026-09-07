"""Independent ledger audit and source-backed IMACD formation report.

Reads frozen primary/neutral-extension ledgers; no new entry, exit, matching,
threshold or model selection. Raw OHLC and closed source clocks verify saved
trades. Future price is explicitly confined to outcomes and hindsight plots.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from .imacd_ma_mtf import ROOT,OUT,DATA,TF,HIGH,LOW,FOLDS,all_frames,sha,side_masks,select_requests,key_arrays,nominate
from .imacd_profit_report import equity,independent_md,table,num,pc

REPORT=ROOT/'analysis/p0_imacd_ma_mtf_20260907.md'
FIG=ROOT/'analysis/figures/imacd_ma_mtf_20260907'
FN={'discovery':'2023–24','replication':'2025','exposed':'2026上半年'}
PN={'P00_base':'原零轴启动','P01_width':'六MA宽度','P02_dense_now':'宽度＋交织',
    'P03_dense_recent':'近期密集','P04_release':'密集＋离带','P05_direction':'再加方向块',
    'P06_htf_md':'高周期主线同向','P07_htf_sh':'高周期动量同向','P08_htf_either':'高周期主线或动量同向',
    'P09_htf_ma':'再加高周期MA斜率','P10_htf_ltf':'再加小周期同向','P11_wait_htf':'最多等9根高周期确认',
    'P12_wait_ltf':'等待＋小周期','P13_htf_zero':'允许高周期零轴','P14_zero_ltf':'允许零轴＋小周期',
    'P15_wait_zero':'允许零轴＋等待','P16_wait_zero_ltf':'允许零轴＋等待＋小周期'}
KEY=['symbol','minutes','fold','policy']


def load(variant):
    folder=OUT if variant=='primary' else OUT/variant
    m=json.loads((folder/'manifest.json').read_text());frames={}
    for row in m['outputs']:
        p=ROOT/row['path'];assert sha(p)==row['sha256'];d=pd.read_csv(p);assert len(d)==row['rows'];frames[p.stem]=d
    return m,frames


def audit(m,frames,bars,fs):
    e,c,p,s=[frames[n] for n in ['events','controls','portfolio','summary']]
    assert not e.duplicated(['event_id','policy']).any()
    q=e.join(c.groupby(['event_id','policy']).net_bp.agg(['count','mean']),on=['event_id','policy'])
    assert q.control_n.eq(q['count'].fillna(0)).all()
    exact=q.control_n.eq(3)
    assert np.allclose(q.loc[exact,'control_mean_net_bp'],q.loc[exact,'mean'])
    assert q.loc[~exact,'control_mean_net_bp'].isna().all()
    assert np.allclose(q.loc[exact,'excess_bp'],q.loc[exact,'net_bp']-q.loc[exact,'mean'])
    sides=c.merge(e[['event_id','policy','side']],on=['event_id','policy'],validate='many_to_one',suffixes=('','_event'))
    assert sides.side.eq(sides.side_event).all()
    paths=[];mfe_checked=0;scalar={}
    for symbol in ['BTC','ETH']:
        for minutes in TF:
            b=bars[symbol,minutes];f=fs[symbol,minutes];md=f.md.to_numpy();oo=b.open.to_numpy();cc=b.close.to_numpy()
            if minutes==240:
                scalar[symbol]=float(np.abs(independent_md(b)-md).max());assert scalar[symbol]<1e-7
            masks={side:side_masks(f,side) for side in (-1,1)}
            es=e[(e.symbol==symbol)&(e.minutes==minutes)];cs=c[(c.symbol==symbol)&(c.minutes==minutes)]
            for fold,start,end in FOLDS:
                valid=np.flatnonzero((b.index>=pd.Timestamp(start,tz='UTC'))&(b.index+pd.Timedelta(minutes=minutes)<=pd.Timestamp(end,tz='UTC')))
                last=int(valid[-1]);anchors=[int(i) for i in valid if i<last and f.eligible.iloc[i] and pd.notna(f.volbin.iloc[i]) and f.departure.iloc[i]!=0]
                for policy in m['policies']:
                    g=es[(es.fold==fold)&(es.policy==policy)]
                    if not len(g):continue
                    expected=select_requests(f,anchors,last,policy,masks)
                    assert expected==list(g[['anchor_i','signal_i','side']].itertuples(index=False,name=None))
                a=es[es.fold==fold];u=cs[cs.fold==fold]
                # Shared controls across policies are intentional; reuse across distinct decisions is forbidden.
                pairs=u[['signal_i','control_i']].drop_duplicates()
                assert not pairs.control_i.duplicated().any()
                assert not set(pairs.control_i)&set(a.signal_i)
                assert f.departure.iloc[pairs.control_i.to_numpy(int)].eq(0).all()
            for frame,control in [(es,False),(cs,True)]:
                for fold,g in frame.groupby('fold'):
                    i=g.control_i.to_numpy(int) if control else g.signal_i.to_numpy(int)
                    en=g.entry_i.to_numpy(int);ex=g.exit_i.to_numpy(int);side=g.side.to_numpy(int)
                    natural=g.exit_kind.eq('natural').to_numpy()
                    assert np.array_equal(en,i+1)
                    xp=np.where(natural,oo[ex],cc[ex])
                    assert np.allclose(oo[en],g.entry_price,rtol=0,atol=1e-8)
                    assert np.allclose(xp,g.exit_price,rtol=0,atol=1e-8)
                    assert np.allclose(side*(xp/oo[en]-1)*10000-20,g.net_bp,rtol=0,atol=1e-7)
                    assert np.allclose(g.gross_bp-g.net_bp,20)
                    start,end=next((a,z) for name,a,z in FOLDS if name==fold)
                    last=int(np.flatnonzero(b.index+pd.Timedelta(minutes=minutes)<=pd.Timestamp(end,tz='UTC'))[-1])
                    assert (b.index[en]>=pd.Timestamp(start,tz='UTC')).all()
                    for direction in (-1,1):
                        mask=side==direction;seq=np.flatnonzero(md*direction<=0)
                        nextbar=np.r_[seq,len(b)][np.searchsorted(seq,i[mask],side='right')]
                        assert np.array_equal(np.minimum(nextbar+1,last),ex[mask])
                        assert np.array_equal(nextbar+1<=last,natural[mask])
                    if not control:
                        assert np.array_equal(pd.to_datetime(g.entry_time,utc=True),b.index[en])
                        assert np.array_equal(pd.to_datetime(g.signal_time,utc=True),b.index[i])
                        ai=g.anchor_i.to_numpy(int)
                        assert (md[ai-1]==0).all() and np.array_equal(np.sign(md[ai]),side)
                        assert (i-ai<=9).all() and (i>=ai).all()
                        for prefix,period in [('h',HIGH[minutes]),('l',LOW[minutes])]:
                            if period is None:continue
                            source=fs[symbol,period];source_close=(source.index+pd.Timedelta(minutes=period)).asi8
                            decision=(b.index[i]+pd.Timedelta(minutes=minutes)).asi8
                            expected=np.searchsorted(source_close,decision,side='right')-1
                            assert np.array_equal(expected,g[prefix+'_i'])
                            assert np.array_equal(source_close[expected],g[prefix+'_close_ns'])
                            assert (source_close[expected]<=decision).all()
                            assert np.allclose(source.md.iloc[expected],g[prefix+'_md'])
                        for r in g.iloc[np.unique(np.linspace(0,len(g)-1,min(8,len(g))).astype(int))].itertuples():
                            held=b.iloc[r.entry_i:r.exit_i+(r.exit_kind=='boundary_mark')]
                            hi=max(r.entry_price,r.exit_price,float(held.high.max()));lo=min(r.entry_price,r.exit_price,float(held.low.min()))
                            best=(hi/r.entry_price-1 if r.side==1 else 1-lo/r.entry_price)*10000
                            worst=(lo/r.entry_price-1 if r.side==1 else 1-hi/r.entry_price)*10000
                            assert abs(best-r.mfe_bp)<1e-7 and abs(worst-r.mae_bp)<1e-7;mfe_checked+=1
            keys=key_arrays(f)
            assert all(keys[j]==keys[k] for j,k in cs[['signal_i','control_i']].drop_duplicates().itertuples(index=False,name=None))
            for (fold,policy),g in p[(p.symbol==symbol)&(p.minutes==minutes)].groupby(['fold','policy']):
                row=s[(s.symbol==symbol)&(s.minutes==minutes)&(s.fold==fold)&(s.policy==policy)].iloc[0]
                z=equity(b,g)
                assert abs(z['fixed_notional_return_pct']-row.portfolio_fixed_notional_return_pct)<1e-7
                assert abs(z['close_marked_drawdown_pct']-row.portfolio_close_marked_drawdown_pct)<1e-7
                paths.append(dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,**z))
    keyed=s.set_index(KEY);auc_checked=0
    for key,g in e.groupby(KEY):
        r=keyed.loc[key];assert len(g)==r.n
        assert abs(g.net_bp.mean()-r.mean_net_bp)<1e-7
        assert abs(g.net_bp.gt(0).mean()*100-r.win_pct)<1e-7
        assert abs(g.loc[g.excess_bp.notna(),'net_bp'].mean()-r.matched_case_mean_net_bp)<1e-7 or g.excess_bp.notna().sum()==0
        # Independent pairwise probability checks suspiciously high AUC values.
        if key[1]==240:
            positive=g.loc[g.net_bp>0,'ma_score'].to_numpy();negative=g.loc[g.net_bp<=0,'ma_score'].to_numpy()
            if len(positive) and len(negative):
                delta=positive[:,None]-negative[None,:]
                auc=float(((delta>0)+.5*(delta==0)).mean())
                assert abs(auc-r.ma_strength_auc)<1e-12;auc_checked+=1
            ranked=g.sort_values(['ma_score','event_id'],ascending=[False,True]).head(max(1,int(np.ceil(len(g)/10))))
            assert abs(ranked.net_bp.mean()-r.ma_score_top_decile_net_bp)<1e-7
    nominated=nominate(e[e.fold=='discovery'].copy())
    pd.testing.assert_frame_equal(nominated,frames['nominations'],check_dtype=False,atol=1e-7)
    paths=pd.DataFrame(paths)
    return dict(events=len(e),controls=len(c),fully_matched=int(exact.sum()),unmatched=int((~exact).sum()),summary_rows=len(s),
        all_prices_clocks_first_legal_exits_verified=True,all_matching_keys_verified=True,
        controls_reused_across_distinct_decisions=False,causal_candidate_membership_verified=True,
        saved_discovery_nominations_reproduced=True,scalar_imacd_max_error=scalar,
        independent_pairwise_auc_cells=auc_checked,mfe_mae_cases=mfe_checked,paths_crossing_zero=int(paths.crossed_zero.sum())),paths


def result_table(s):
    rows=[]
    for r in s.itertuples():
        rows.append([r.symbol,f'{r.minutes}m',FN[r.fold],r.policy.split('_')[0],int(r.n),pc(r.mean_net_bp),
            num(r.win_pct)+'%',num(r.pf),int(r.matched_n),pc(r.matched_case_mean_net_bp),pc(r.controls_mean_net_bp),
            pc(r.excess_bp),num(r.p,4)])
    return table(['币','周期','时期','配置','n','全部净均值','胜率','PF','匹配n','匹配信号净均值','随机净均值','匹配超额','原始p'],rows)


def case_chart(primary,ext,bars,fs,eid,title,filename):
    r=primary[(primary.event_id==eid)&(primary.policy=='P00_base')].iloc[0]
    b=bars[r.symbol,int(r.minutes)];f=fs[r.symbol,int(r.minutes)]
    left=max(0,int(r.anchor_i)-max(60,int(r.zero_before)+8));right=min(len(b),int(r.exit_i)+13)
    g=b.iloc[left:right];ff=f.iloc[left:right];x=g.index
    fig,axes=plt.subplots(3,1,figsize=(13,9.5),height_ratios=[2.5,1,1],sharex=True)
    fig.subplots_adjust(left=.085,right=.985,top=.82,bottom=.13,hspace=.23)
    fig.text(.085,.955,title,fontsize=17,fontweight='bold')
    labels=[]
    for frame,p in [(primary,'P02_dense_now'),(primary,'P08_htf_either'),(ext,'P13_htf_zero'),(ext,'P15_wait_zero')]:
        q=frame[(frame.event_id==eid)&(frame.policy==p)]
        labels.append(p[:3]+(' 拒绝' if q.empty else f' {q.net_bp.iloc[0]/100:+.2f}%'))
    fig.text(.085,.91,'同一零轴启动：'+'  /  '.join(labels),fontsize=12)
    fig.text(.085,.87,f'anchor UTC {r.anchor_time[:16]} · 六MA前12根宽度/ATR {r.width_at_anchor:.2f} · 交织 {int(r.crosses_at_anchor)} 次 · 下根开盘执行',fontsize=11)
    axes[0].plot(x,g.close,color='#28313b',lw=1.15,label='4h close')
    for length,color in [(20,'#c27527'),(60,'#247daa'),(120,'#8871a5')]:
        for kind,ls in [('sma','-'),('ema','--')]:
            axes[0].plot(x,ff[f'{kind}{length}'],color=color,ls=ls,lw=1,label=f'{kind.upper()}{length}')
    axes[0].scatter(b.index[int(r.entry_i)],r.entry_price,color='#28313b',marker='^' if r.side==1 else 'v',s=70,label='原入场')
    axes[0].scatter(b.index[int(r.exit_i)],r.exit_price,color='#a7453b',s=40,label='回零退出')
    axes[1].bar(x,ff.md,width=.13,color='#3d86ab',label='4h md');axes[1].plot(x,ff.sb,color='#a35b46',label='4h sb')
    # Aligned daily values become visible at the signal bar CLOSE, never its open.
    visible=x+pd.Timedelta(minutes=int(r.minutes))
    axes[2].step(visible,ff.h_md,where='post',color='#286d94',label='已收盘 1d md')
    axes[2].step(visible,ff.h_sh,where='post',color='#b37330',ls='--',label='已收盘 1d sh')
    for ax in axes:
        ax.axvspan(b.index[int(r.anchor_i)-int(r.zero_before)],b.index[int(r.anchor_i)],color='#78828c',alpha=.11)
        ax.axvline(b.index[int(r.entry_i)],color='#28313b',lw=1)
        ax.axvline(b.index[int(r.exit_i)],color='#a7453b',lw=1,ls=':')
        ax.grid(axis='y',alpha=.18)
    for ax in axes[1:]:ax.axhline(0,color='#78828c',lw=.6)
    axes[0].legend(ncol=5,fontsize=8,loc='upper left');axes[1].legend(loc='upper left',fontsize=9);axes[2].legend(loc='upper left',fontsize=9)
    axes[0].set_ylabel('USDT');axes[1].set_ylabel('4h IMACD');axes[2].set_ylabel('1d 已知状态')
    fig.text(.085,.035,'阴影=4h主线零轴区间。图含事后走势，用于解释已知案例；不是未见样本。每笔减20bp，无资金费率/杠杆。',fontsize=10,color='#646f78')
    fig.savefig(FIG/filename,dpi=145);plt.close(fig)


def write_report(primary,ext,qa,paths):
    a=primary['summary'];z=ext['summary'];e=primary['events'];xe=ext['events']
    combined=pd.concat([a.assign(stage='primary'),z.assign(stage='neutral_extension')],ignore_index=True)
    valid=combined[(combined.fold=='replication')&combined.p.notna()].sort_values('p')
    combined.loc[valid.index,'p_holm_both_stages']=np.maximum.accumulate(np.minimum(1,valid.p.to_numpy()*(len(valid)-np.arange(len(valid)))))
    combined.to_csv(OUT/'combined_summary.csv',index=False)
    # Selected cases are hindsight diagnostics, never new candidates.
    caseids=['ETH_240_replication_departure_7232','ETH_240_replication_departure_7657',
        'ETH_240_replication_departure_6718','ETH_240_exposed_departure_8869','ETH_240_exposed_departure_9545',
        'BTC_240_discovery_departure_4534']
    cases=[]
    for eid in caseids:
        r=e[(e.event_id==eid)&(e.policy=='P00_base')].iloc[0]
        row=dict(event_id=eid,symbol=r.symbol,anchor_utc=r.anchor_time,side=r.side,zero_before=r.zero_before,
                 width=r.width_at_anchor,crosses=r.crosses_at_anchor,h_md=r.h_md,h_sh=r.h_sh)
        for frame,policy in [(e,'P00_base'),(e,'P02_dense_now'),(e,'P08_htf_either'),(xe,'P13_htf_zero'),(xe,'P15_wait_zero')]:
            q=frame[(frame.event_id==eid)&(frame.policy==policy)]
            row[policy+'_net_bp']=q.net_bp.iloc[0] if len(q) else np.nan
            row[policy+'_delay']=q.delay_bars.iloc[0] if len(q) else np.nan
        cases.append(row)
    cases=pd.DataFrame(cases);cases.to_csv(OUT/'case_trace.csv',index=False)
    tails=[]
    for (symbol,fold,policy),q in e[(e.minutes==240)&e.policy.isin(['P00_base','P02_dense_now','P08_htf_either'])].groupby(['symbol','fold','policy']):
        x=q.net_bp.sort_values();tails.append(dict(symbol=symbol,fold=fold,policy=policy,n=len(q),mean_net_bp=x.mean(),
            without_best_bp=x.iloc[:-1].mean(),median_bp=x.median(),best_bp=x.max(),worst_bp=x.min(),
            matched_case_bp=q.loc[q.excess_bp.notna(),'net_bp'].mean(),control_bp=q.control_mean_net_bp.mean()))
    tails=pd.DataFrame(tails);tails.to_csv(OUT/'tail_sensitivity.csv',index=False)
    focused=a[(a.symbol=='ETH')&(a.minutes==240)&a.policy.isin(['P00_base','P02_dense_now'])]
    riskrows=[]
    for r in focused.itertuples():
        riskrows.append([FN[r.fold],r.policy[:3],int(r.portfolio_trades),num(r.portfolio_fixed_notional_return_pct)+'%',
            num(r.portfolio_close_marked_drawdown_pct)+'%',num(r.anchor_profit_retained_pct)+'%',num(r.baseline_loss_filtered_pct)+'%',
            pc(r.matched_case_mean_net_bp)+' / '+pc(r.controls_mean_net_bp)])
    nom=[]
    for stage,fr in [('主实验',primary),('追加探索',ext)]:
        for r in fr['nominations'].itertuples():
            ss=fr['summary'];q=ss[(ss.symbol==r.symbol)&(ss.minutes==r.minutes)&(ss.policy==r.policy)].set_index('fold')
            nom.append([stage,r.symbol,f'{r.minutes}m',r.policy[:3],
                *[f'{pc(q.loc[f,"mean_net_bp"])} / n{int(q.loc[f,"n"])}' for f in FN],
                pc(q.loc['replication','matched_case_mean_net_bp']),pc(q.loc['replication','controls_mean_net_bp']),
                pc(q.loc['replication','excess_bp']),num(q.loc['replication','p'],4)])
    feature_rows=[]
    for r in focused.itertuples():
        feature_rows.append([FN[r.fold],r.policy[:3],num(r.ma_strength_auc,3),int(r.ma_score_top_decile_n),
            pc(r.ma_score_top_decile_gross_bp),pc(r.ma_score_top_decile_net_bp),num(r.ma_score_top_decile_win_pct)+'%',pc(r.ma_score_top_decile_excess_bp)])
    trace=[]
    for r in cases.to_dict('records'):
        trace.append([r['symbol'],r['anchor_utc'][:16],int(r['zero_before']),num(r['width']),int(r['crosses']),num(r['h_md']),num(r['h_sh']),
            *['拒绝' if pd.isna(r[p+'_net_bp']) else pc(r[p+'_net_bp'])+f'（等{int(r[p+"_delay"])}根）' for p in ['P00_base','P02_dense_now','P08_htf_either','P13_htf_zero','P15_wait_zero']]])
    usage=[]
    for stage in ['primary','neutral_extension']:
        m=json.loads(((OUT if stage=='primary' else OUT/stage)/'manifest.json').read_text())
        usage.append(f'- {stage}：builder `{m["builder_commit"]}`；holdout累计次数 `{m["holdout_uses"]}`。')
    text=f'''# IMACD＋六均线密集＋多周期：找到有效改进，也找到过滤大趋势的原因

2026-09-07 · OKX BTC/ETH USDT永续 · 研究V3 · 固定34/9与20bp往返成本

## 回答你的核心问题

加入你们常用的SMA/EMA20、60、120密集判断后，**ETH4小时出现了实质改善**：2025年单次净均值从0.88%提高到2.24%，2026上半年从2.05%提高到3.04%。在这两个窗口里，密集条件保留了全部原始盈利交易，分别过滤约27%和20%的原始亏损金额。早期2023–24也由0.07%提高到0.46%，但优势明显较弱。

这支持继续研究“零轴蓄势→均线密集→启动→拿住趋势”。此前若把所有金叉当入场、反向交叉就出场，确实没有完整表达这个意图。V2已经拆开了退出差异；本轮补上密集与多周期，未改成本或退出救结果。

多周期有一个具体陷阱：**要求大周期已经同向，会拒绝正在从大周期零轴区间发动的小级别趋势。** ETH 2026年1月这笔约29.80%的空头趋势，4小时已启动，但当时已收盘日线md=0。它被“日线md或sh必须同向”拒绝，允许日线中性后才能进入。不过，中性放行在2023–24并没普遍增益，不能把一个成功案例升格为万能规则。

这里所有“净”均指价格收益减0.20%，没有资金费率；不是杠杆账户收益，也不是保证未来盈利。匹配随机对照后增益缩小，全部比较经多重校正尚未过项目p<0.01门槛。研究已找到可解释的获利结构，尚未证明能稳定赚大钱。

## 1. 数据、时钟与分段

两份与V2相同SHA256的OKX15m历史，使用完整UTC聚合K线。指标预热从2022年开始；交易观察2023-01-01至2026-07-01之前。主实验原始零轴启动{int(a[a.policy=='P00_base'].n.sum()):,}次；所有13配置共{len(e):,}事件行，复用同一anchor会重复出现，不能相加作独立样本。追加阶段{len(xe):,}事件行。

2023–24用于规则内提名，2025作按时间复核，2026上半年为已暴露历史复核。V2看过这些历史，第二阶段又看过主实验后才提出，所以没有把任何一段包装成全新盲测。当前已授权任意日期；未访问实盘账户。

| 信号周期（分钟） | 高周期背景（分钟） | 低周期确认（分钟） |
|---|---|---|
'''+table(['分钟','高周期分钟','低周期分钟'],[[t,HIGH[t],LOW[t] or '缺长样本，不做该门'] for t in TF]).split('\n',2)[2]+f'''

本轮覆盖15m、30m、1h、2h、4h、6h、12h七种信号周期；日线作为背景。1m/3m/5m、日线独立策略的V2结果仍在[上一版报告]({ROOT}/analysis/html/p0_imacd_profit_mechanism_20260907.html)，本轮没有为小周期拼接短数据冒充多年验证。

信号收盘确定、下根开盘成交；回零/反向退出状态出现后下根开盘退出。分段末未退出的仓位按末根收盘记账，并单列boundary_marks。高/低周期一律取收盘时间不晚于信号决策时刻的最后一根，经过至少340根预热。Pine多周期值的历史/实时确认时机必须一致，参考[TradingView官方多周期文档](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。图中日线状态按其可见时刻绘制。

## 2. 把“密集、共振、拿住”拆成可验证的规则

- **IMACD零轴**：DEMA34(hlc3)进入SMMA34高低通道时，md被定义为精确0。这是相对通道状态，不能单独证明价格横盘。md首次离零才是本轮anchor；信号线交叉不是等价事件。
- **六均线密集**：复用仓内dense_l1：前12根六MA最大最小带宽/ATR14均值≤3，并且15对均线前12根交织≥2次。不是肉眼截图的自动等价物；这是现成数值代理，没有新增阈值搜索。
- **启动**：有当前密集、近期34根密集、价格离开六MA带三层；完整方向块也单列，避免把排列尚未完成的早期启动一刀切掉。
- **高周期**：分别比较md同向、sh同向、任一同向、再加SMA60斜率，以及追加的md=0中性允许。sh同向只说明相对信号线的动量，不能称趋势已经翻转。
- **小周期**：最近已收盘低周期md同向。这里只测试一个末端状态，不等于“低周期连续回踩后再起”等完整序列。
- **等待**：anchor当时已密集，最多等9根首次满足确认；期间md回零/反向取消。不能事后用新密集补原anchor资格。
- **拿住趋势**：本轮统一用md回零/反向退出，保持V2 C。回撤、未实现利润回吐和失败启动都保留，没有按已知大赢家定制退出。

全部父子规则见[冻结计划]({OUT}/PROJECT_PLAN.md)。主实验P00–P12先提交builder再跑；P13–P16是看过主实验后追加的探索，输出单独保存。P01→P02只加交织，P08→P13只加高周期零轴允许；没有改生产预设。

## 3. 最明确的改进：ETH4h六均线密集

{result_table(focused)}

匹配对照=同币、同周期、同月、同因果波动五桶、同本周期md区域、同已闭合高周期md区域的随机时点，相同side、退出和成本；每笔3个，不放回。只有完整匹配才计算超额；“全部均值”与“匹配信号均值”分母不同，不能拿全部均值直接减随机均值。

{table(['时期','配置','单仓笔数','固定初始名义累计收益','收盘标记最大回撤','原赢家利润保留','原亏损金额过滤','匹配信号/随机单次净均值'],riskrows)}

2025从30笔减少到26笔，固定初始名义累计价格净收益从26.53%到58.34%，回撤从31.20%到23.74%；2026上半年从28.76%到36.49%。这是同额、不复利、单仓研究账本，不是收益率预测。密集的价值体现在少做了部分亏损，而不是大赢家入场价格被改得更漂亮。

但是2026上半年密集信号均值3.04%，匹配随机均值2.72%，超额只有0.32个百分点。这说明那段市场状态本身也有收益，不能把全部3.04%归功于指标。2025对应超额1.04个百分点，原始p=0.2305。

## 4. 多周期：支持、中性、等待必须分清

下表来自同一追加阶段，父子共用匹配池，便于比较；主实验所有原结果保持原样。

{result_table(z[(z.symbol=='ETH')&(z.minutes==240)&z.policy.isin(['P08_htf_either','P13_htf_zero','P15_wait_zero','P16_wait_zero_ltf'])])}

2025高周期严格支持P08净均值3.42%，但2026上半年变成−0.80%，因为仅留下约28%的原始赢家利润。允许高周期零轴P13后，该窗口回到+1.22%；再允许最多9根等待P15为+2.60%。然而P15在2023–24为负，不存在“越共振越赚钱”的单调规律。2025/2026 ETH4h加低周期末根同向没有改变这两组交易；在其他周期仍有差异。

这是三个不同功能：大周期判断背景是否许可，本周期决定启动，小周期决定执行时机。**功能分工是目前由案例支持的研究解释；尚未验证成一套可泛化的盈利规则。** 原有“至少md/sh一项同向”与“允许md=0”都应保留对照，不能只留下某年漂亮的一条。

## 5. 六个真实案例：同样查看赢家与失败

以下案例因V2已知结果与本轮机制问题选出，属于事后解释，不计算额外胜率，不用于调阈值。单位是每笔减20bp的价格收益；拒绝表示没有进入该策略账本。

{table(['币','anchor UTC','零轴根数','宽度/ATR','交织','日md','日sh','P00','P02密集','P08支持','P13零轴允许','P15等待'],trace)}

![2026年1月日线零轴案例]({FIG}/eth_202601_neutral.png)

ETH 2026-01-20 20:00 UTC信号，于2026-01-21 00:00开盘2938.29做空，主线回零退出2056.86，约+29.80%。前12根宽度/ATR约2.46、交织5次。日线md=0而sh为正：这是高周期中性被旧支持门拒绝的具体例子。

![2025年4月趋势案例]({FIG}/eth_202504_trend.png)

2025年4月ETH大趋势在原反向交叉退出中几乎没赚到，主线回零退出约+41.02%。本轮的任务是查看形成和多周期是否保住它，而不是把已知涨幅倒灌成买入条件。图中六均线展开是趋势持续的表现，不等于启动当时必须完全排列整齐。

![2025年1月失败启动]({FIG}/eth_202501_failure.png)

2025年1月失败空头也画出来：零轴横盘很久并不保证启动成功。宽度、交织和日线状态在同一张轨迹表列出，失败退出也按原规则算，不能因为它不符合“赚大钱”的预期就删除。

2026年5月另一大空头在anchor日线md仍小幅为正，允许零轴也不能即时放行；等2根4h确认后进入，价格净收益约19.15%，保住了该趋势。这说明等待有时有价值，但该例不能决定9根是最佳长度。

BTC 2024年1月那笔约55.19%大赢家，启动前带宽/ATR为3.33，恰好不满足现成≤3的密集代理，被本轮过滤。没有为了保住这一笔临时放宽阈值。这也是为什么不能把ETH的改善直接搬给BTC：既要数过滤了多少亏损，也要数误删了多少趋势利润。

## 6. BTC/ETH各周期：发现段提名全部跟踪

提名只使用2023和2024：各年至少4笔、合计至少12笔、两年净均值都正、匹配超额正，按较差年份均值排序。每币每周期最多一个；没有合格项则不提名。该选择程序没有读取2025/2026，但这批历史整体早已被研究看过；追加阶段更不能叫新的盲测。

{table(['阶段','币','周期','配置','2023–24净均值/n','2025净均值/n','2026H1净均值/n','2025匹配信号','2025随机','2025超额','原始p'],nom)}

BTC4h P07、BTC12h P04和ETH12h P09是主实验中三段总均值都为正的提名，但后两者交易极少。ETH12h P09在2025只有3笔，不能把极高均值当稳定性；部分其他提名在2025或2026转负。ETH4h P02虽然三大窗口都改善，但没有通过2023和2024分别考察的提名条件，它是探索线索，不能冒称预先锁定的验证胜者。

短周期组合并没有普遍修复手续费损耗；下方完整表同时列正负结果。日线做背景也不是所有信号周期的唯一正确搭配，本轮没有继续搜索周期比例来追最佳曲线。

## 7. 单特征、尾部依赖和统计门

单特征分数=负的前12根带宽/ATR，越密分数越高。无机器学习训练，val AUC在这里仅指2025以净收益>0为标签的单特征AUC；top10是各段离线排名诊断，不能用于当时已知的实时阈值。

{table(['时期','配置','密集分数AUC','top10 n','top10毛均值','top10净均值','top10胜率','top10匹配超额'],feature_rows)}

{table(['币','时期','配置','n','全部净均值','删最大赢家后均值','中位数','最大赢家','最大亏损','原匹配信号均值','原随机均值'],[[r.symbol,FN[r.fold],r.policy[:3],r.n,pc(r.mean_net_bp),pc(r.without_best_bp),pc(r.median_bp),pc(r.best_bp),pc(r.worst_bp),pc(r.matched_case_bp),pc(r.control_bp)] for r in tails.itertuples()])}

一个比V2更积极的变化：ETH4h加入密集后，即使删掉最大赢家，2025和2026上半年的单次均值仍分别为+0.69%和+0.61%；原始P00删掉最大赢家则都转负。这说明本轮改善并非仅把一个偶然大赚留下。但2023–24删最大赢家后仍为负，稳定性还不够。

删最大赢家是脆弱性诊断，不是要求趋势策略不依赖趋势。应同时看到：趋势利润本就集中，而样本里独立大趋势太少，未来还能否重复并未证实。表中2025 AUC曾超过0.7，已用独立正负样本两两比较重算，并验证特征前缀不变性；高AUC与top10匹配超额为负可以同时存在，因此它不是成功标准。

月聚类bootstrap与符号置换针对匹配超额；它是观察性基准，不是随机化交易试验。主实验2025全家族Holm最小值{num(a.p_holm_replication.min(),5)}；把两阶段全部{len(valid)}个非空2025检验更保守地合并校正后最小值{num(combined.p_holm_both_stages.min(),5)}，没有p<0.01的确认项。长持仓跨月、同币同周期反复规则仍有依赖；校正也不能消除研究者看过历史带来的选择偏差。

## 8. 完整结果与审计

主实验和追加阶段各自完整CSV（包含每单元AUC、top10、匹配分母、边界记账、仓位阻塞、回撤与提名），以及账本哈希：

- [主实验汇总]({OUT}/summary.csv)；[追加汇总]({OUT}/neutral_extension/summary.csv)；[两阶段合并统计]({OUT}/combined_summary.csv)。
- [主manifest]({OUT}/manifest.json)；[追加manifest]({OUT}/neutral_extension/manifest.json)；[案例逐项轨迹]({OUT}/case_trace.csv)。
- [逐价与时钟审计]({OUT}/audit_qa.json)；[固定名义路径偿付审计]({OUT}/portfolio_solvency_audit.csv)。

审计主实验{qa['primary']['events']:,}事件/{qa['primary']['controls']:,}对照、追加{qa['neutral_extension']['events']:,}事件/{qa['neutral_extension']['controls']:,}对照的全部入/出价、20bp成本、首个合法退出、匹配键、闭合周期时钟；P00每阶段与V2的7,499笔×7字段精确复核。共享配置两阶段价格/收益不变，随机对照池变化单独保留。两阶段共有{int(paths.crossed_zero.sum())}条固定名义路径穿零，这些行只是数学诊断，不能被解释成可执行账户曲线。

数据统计：覆盖表见[主coverage]({OUT}/coverage.csv)；每周期各年候选数、正类率（净赢比例）和验证n见完整表。没有分类训练集；不虚构模型AUC。19个相关单元测试全部通过；连同登记契约共34通过、1个既有失败：另一个exp-btcusdtp-owner-k1k2-genuine-flow-20260907-v36条目的holdout登记与固定集合不一致。该问题在本轮前已存在，没有替它补授权或绕过测试。见[交付检查]({OUT}/delivery_qa.json)。三张PNG已实际查看；HTML完成表格、图片嵌入及本地链接检查，未声称浏览器视觉验收。

## 9. Notion中与你这个方向直接对应的记录

你在[交易系统—更新中](https://app.notion.com/p/2ac8856479af8063b17bc02256e564a9)已经明确写了“回测Impulse MACD [LazyBear] + 均线密集”，所以本轮组合有你的原始研究依据。

[Stochastic＋Vix＋ImpulseMACD回测](https://app.notion.com/p/2258856479af80d5bbf3dd45c59278d2)及其[bonk1h案例](https://app.notion.com/p/2278856479af809fbeeeebe17910a3e6)可以帮助理解图形语境，但选出的截图不是BTC/ETH完整交易账本。[2025下半年记录](https://app.notion.com/p/2358856479af8047bcfccab5ea9a2f0e)还涉及趋势、MACD、成交量和结构；本輪没有用本次数据顺便筛成交量参数，也没有回写或发布到Notion。

## 10. 风险与诚实声明

1. 原始IMACD代码是指标，不含完整执行规则；本轮规则是根据你的“零轴启动、拿住趋势、密集与多周期”方向构建的研究定义，不能称与你所有主观交易动作完全一致。
2. 20bp统一成本没有历史资金费率、跳价下单深度、保护止损与强平模型。固定初始名义单仓收益不等于复利账户收益，最大回撤只按收盘标记，会低估盘中风险。
3. 大量交易有同一市场背景，低频尤其缺独立趋势。2–4笔大赚不能证明多年可重复。ETH的改进不能自动迁移BTC或所有周期。
4. 2023–24提名仍受此前研究暴露影响；2026H1明确为暴露样本。追加中性门是看过一笔大赢家后提出，其探索身份在计划与报告保留。
5. 原因解释区分“公式必然”“账本观察”“待检验假设”。密集改善和被挡赢家是观察事实；用高周期背景许可、低周期执行分工的解释还需要独立检验。

## 11. 复现与holdout账本

在本仓已有固定依赖的.venv运行；不要安装/降级主环境。manifest记录原K线SHA256；缺原数据时停止，不自动换交易所。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/test_imacd_ma_mtf.py tests/test_imacd_profit_mechanism.py tests/test_imacd_indicator_audit.py -q
.venv/bin/python -m yoyo.evaluation.imacd_ma_mtf
.venv/bin/python -m yoyo.evaluation.imacd_ma_mtf --neutral-extension
.venv/bin/python -m yoyo.evaluation.imacd_ma_mtf_report
```

原构建先落commit再执行；重新跑价格回放会增加对应配置holdout消耗，不能沿用旧次数冒充首次。仅检查已保存账本不另计新评估。

{chr(10).join(usage)}

Owner授权为OKX、每周期、任何时间段，并明确要求加入常用密集/多周期自主探索。本研究所有产物training_eligible=false、production_eligible=false，未修改rawK线、生产阈值、ACTIVE或执行器。

## 12. 下一步取舍

已经完成本轮两阶段探索，最值得保留的简单候选是ETH4h“零轴启动＋当前六MA密集＋回零退出”；高周期中性许可作为独立假设保留，不能宣称替代后全面更好。要检验“拿大钱”，接下来有价值的是锁定有限候选、观察未参与选择的新信号与完整资金费率/仓位账本，而不是继续筛已知赢家。

研究候选可归档；接入前向、调整止损/成本、训练、promote或实盘操作都仍需Owner另行决策。本轮不自动进行这些动作。

## 附录：全部周期的原始结果

配置缩写对应：{'; '.join(p[:3]+' '+PN[p] for p in PN)}。

### 主实验：所有非空单元

{result_table(a[a.n>0])}

### 追加阶段：所有非空单元

{result_table(z[z.n>0])}
'''
    REPORT.write_text(text)
    subprocess.run(['python3','scripts/md_to_html.py',str(REPORT),'--out-dir','analysis/html'],cwd=ROOT,check=True)


def main():
    bars,fs,sources=all_frames();qa={};loaded={};allpaths=[]
    for variant in ['primary','neutral_extension']:
        m,frames=load(variant);qa[variant],paths=audit(m,frames,bars,fs);loaded[variant]=frames
        allpaths.append(paths.assign(stage=variant));print(variant,'audit passed',flush=True)
    primary=loaded['primary'];ext=loaded['neutral_extension']
    common=set(primary['events'].policy)&set(ext['events'].policy)
    a=primary['events'][primary['events'].policy.isin(common)].set_index(['event_id','policy']).sort_index()
    b=ext['events'][ext['events'].policy.isin(common)].set_index(['event_id','policy']).sort_index()
    assert a.index.equals(b.index)
    for col in ['signal_i','entry_i','exit_i','entry_price','exit_price','net_bp','mfe_bp','mae_bp']:
        assert np.allclose(a[col],b[col],atol=1e-7,rtol=0)
    qa['shared_policy_price_parity_rows']=len(a);qa['source_hashes_verified']=sources
    paths=pd.concat(allpaths,ignore_index=True);paths.to_csv(OUT/'portfolio_solvency_audit.csv',index=False)
    (OUT/'audit_qa.json').write_text(json.dumps(qa,ensure_ascii=False,indent=2)+'\n')
    FIG.mkdir(parents=True,exist_ok=True)
    plt.rcParams.update({'font.family':['Arial Unicode MS','DejaVu Sans'],'axes.unicode_minus':False,
        'axes.spines.top':False,'axes.spines.right':False,'font.size':10})
    for eid,title,file in [('ETH_240_exposed_departure_8869','大趋势可能从高周期零轴开始','eth_202601_neutral.png'),
        ('ETH_240_replication_departure_7232','密集之后展开：检查能否保住趋势','eth_202504_trend.png'),
        ('ETH_240_replication_departure_6718','长时间零轴也会出现失败启动','eth_202501_failure.png')]:
        case_chart(primary['events'],ext['events'],bars,fs,eid,title,file)
    write_report(primary,ext,qa,paths)
    print('Report and audit complete',flush=True)


if __name__=='__main__':main()
