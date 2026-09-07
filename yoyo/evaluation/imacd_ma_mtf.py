"""Frozen IMACD plus six-MA formation and closed-timeframe confluence study.

Source contract: exp-imacd-ma-mtf-20260907-v3/PROJECT_PLAN.md. OHLC through
decision only; SMA/EMA20/60/120, prior12 width/ATR and pair flips reuse the
existing dense_l1 feature module. Formation memory is trailing34, waiting at
most9 bars. Higher/lower states are visible only at their closing timestamps.
Future bars are used solely by the inherited V2 neutral-exit outcome engine.
No fitting, production preset changes, protective stops, orders or leverage.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.layers.l2_judgment.pine_dense_start import add_six_ma_dense_start_features
from .imacd_indicator_audit import ROOT, aggregate
from .imacd_profit_mechanism import features, exit_arrays, outcome, distribution, inference, rank_baseline, single_position, FOLDS

OUT=ROOT/'experiments/active/exp-imacd-ma-mtf-20260907-v3'
DATA=ROOT/'data/imacd_ma_mtf_v3'
V2=ROOT/'experiments/active/exp-imacd-profit-mechanism-20260907-v2'
TF=[15,30,60,120,240,360,720]
HIGH={15:60,30:120,60:240,120:360,240:1440,360:1440,720:1440}
LOW={15:None,30:15,60:15,120:30,240:60,360:60,720:240}
POLICIES=['P00_base','P01_width','P02_dense_now','P03_dense_recent','P04_release','P05_direction',
          'P06_htf_md','P07_htf_sh','P08_htf_either','P09_htf_ma','P10_htf_ltf','P11_wait_htf','P12_wait_ltf']
PARENTS=dict(zip(POLICIES,[None,'P00_base','P01_width','P02_dense_now','P03_dense_recent','P04_release',
                            'P04_release','P04_release','P04_release','P08_htf_either','P08_htf_either','P08_htf_either','P11_wait_htf']))
SEED=20260908
END=pd.Timestamp('2026-07-01',tz='UTC')
ZERO_POLICIES=['P00_base','P04_release','P08_htf_either','P10_htf_ltf','P11_wait_htf','P12_wait_ltf',
               'P13_htf_zero','P14_zero_ltf','P15_wait_zero','P16_wait_zero_ltf']
PARENTS.update(P13_htf_zero='P08_htf_either',P14_zero_ltf='P13_htf_zero',
               P15_wait_zero='P13_htf_zero',P16_wait_zero_ltf='P15_wait_zero')
WAIT_POLICIES=['P11_wait_htf','P12_wait_ltf','P15_wait_zero','P16_wait_zero_ltf']
LOW_POLICIES=['P10_htf_ltf','P12_wait_ltf','P14_zero_ltf','P16_wait_zero_ltf']


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def formation_features(b):
    """OHLC<=t; formation [t-12,t-1], memory34, six-MA slopes t-3..t."""
    f=b.join(features(b))
    for length in (20,60,120):
        f[f'sma{length}']=b.close.rolling(length).mean()
        f[f'ema{length}']=b.close.ewm(span=length,adjust=False).mean()
    f=add_six_ma_dense_start_features(f)
    f['width']=f.dense_pre_bandwidth_atr_mean_12<=3.
    f['dense_now']=f.width & (f.dense_pre_pairwise_cross_count_12>=2)
    f['dense_recent']=f.dense_now.rolling(34,min_periods=34).max().eq(1)
    f['ma_score']=-f.dense_pre_bandwidth_atr_mean_12
    f['sma60_slope']=f.sma60.diff()
    return f


def align_closed(source,source_minutes,target_index,target_minutes):
    """Last source bar with close<=target decision close; no open-time join."""
    close_ns=(source.index+pd.Timedelta(minutes=source_minutes)).asi8
    decision_ns=(target_index+pd.Timedelta(minutes=target_minutes)).asi8
    ix=np.searchsorted(close_ns,decision_ns,side='right')-1
    valid=ix>=0
    if len(source):valid &= source.eligible.to_numpy()[np.maximum(ix,0)]
    fields=['md','sh','sma60_slope']
    result=pd.DataFrame(index=target_index)
    for col in fields:
        a=np.full(len(ix),np.nan);a[valid]=source[col].to_numpy()[ix[valid]];result[col]=a
    result['source_i']=np.where(valid,ix,-1)
    result['source_close_ns']=np.where(valid,close_ns[np.maximum(ix,0)],-1)
    result['known']=valid
    assert (result.loc[valid,'source_close_ns'].to_numpy()<=decision_ns[valid]).all()
    return result


def all_frames():
    old=json.loads((V2/'manifest.json').read_text())
    bars={};fs={};sources=[]
    for symbol in ['BTC','ETH']:
        row=next(x for x in old['sources'] if f'kline_deep/okx_{symbol}_' in x['path'])
        p=ROOT/row['path'];assert sha(p)==row['sha256'];sources.append(row)
        raw=pd.read_csv(p);raw.index=pd.DatetimeIndex(pd.to_datetime(raw.open_time,utc=True))
        raw=raw[['open','high','low','close']].astype(float)
        raw=raw.loc[raw.index+pd.Timedelta(minutes=15)<=END]
        for minutes in TF+[1440]:
            b=aggregate(raw,minutes,15)
            assert not b.index.has_duplicates and b.index.to_series().diff().iloc[1:].eq(pd.Timedelta(minutes=minutes)).all()
            bars[symbol,minutes]=b;fs[symbol,minutes]=formation_features(b)
        for minutes in TF:
            f=fs[symbol,minutes]
            h=align_closed(fs[symbol,HIGH[minutes]],HIGH[minutes],f.index,minutes).add_prefix('h_')
            if LOW[minutes]:l=align_closed(fs[symbol,LOW[minutes]],LOW[minutes],f.index,minutes).add_prefix('l_')
            else:
                l=pd.DataFrame(index=f.index,columns=['l_md','l_sh','l_sma60_slope']);l['l_source_i']=-1;l['l_source_close_ns']=-1;l['l_known']=False
            fs[symbol,minutes]=f.join(h).join(l)
    return bars,fs,sources


def side_masks(f,side):
    """All gates at closed t, with no outcomes or future-dependent selection."""
    suffix='long' if side==1 else 'short'
    release=(f.dense_rope_upper<f.close) if side==1 else (f.dense_rope_lower>f.close)
    recent=f.dense_recent
    direction=(f[f'dense_current_alignment_{suffix}']>=6)&(f[f'dense_pre_cross_imbalance_{suffix}_12']>=-1)&(
        f[f'dense_signed_mean_slope_atr_{suffix}_3']>0)&((f[f'dense_slope_coherence_{suffix}_3']>=2/3)|(f.dense_atr_release_ratio_8>=1))
    hm=(f.h_md*side)>0;hs=(f.h_sh*side)>0;support=hm|hs;lm=(f.l_md*side)>0
    base=pd.Series(True,index=f.index)
    masks=[base,f.width,f.dense_now,recent,recent&release,recent&release&direction,
           recent&release&hm,recent&release&hs,recent&release&support,
           recent&release&support&(f.h_sma60_slope*side>0),recent&release&support&lm,
           release&support,release&support&lm]
    out={p:m.fillna(False).to_numpy(bool) for p,m in zip(POLICIES,masks)}
    zero_support=f.h_md.eq(0)|support
    for p,m in [('P13_htf_zero',recent&release&zero_support),
                ('P14_zero_ltf',recent&release&zero_support&lm),
                ('P15_wait_zero',release&zero_support),
                ('P16_wait_zero_ltf',release&zero_support&lm)]:
        out[p]=m.fillna(False).to_numpy(bool)
    return out


def select_requests(f,anchors,last,policy,masks):
    """One request per departure anchor; wait cancels on a neutral/reverse md."""
    md=f.md.to_numpy();recent=f.dense_recent.to_numpy();chosen=[]
    for anchor in anchors:
        side=int(np.sign(md[anchor]))
        if policy in WAIT_POLICIES:
            if not recent[anchor]:continue
            for j in range(anchor,min(anchor+9,last-1)+1):
                if md[j]*side<=0:break
                if masks[side][policy][j]:chosen.append((int(anchor),j,side));break
        elif masks[side][policy][anchor]:chosen.append((int(anchor),int(anchor),side))
    return chosen


def key_arrays(f):
    """Month, causal vol bucket, md zone and last closed higher md zone."""
    hz=np.where(f.h_known,np.sign(f.h_md).fillna(0),-2).astype(int)
    return list(zip(f.index.strftime('%Y-%m'),f.volbin.fillna(-1).astype(int),np.sign(f.md).astype(int),hz))


def match_union(f,indexes,entries):
    """One outcome-blind matching map for the union of actual entry decisions."""
    keys=key_arrays(f);pool={};entryset=set(entries);rng=np.random.default_rng(SEED)
    for i in indexes:
        if i not in entryset and f.departure.iloc[i]==0:pool.setdefault(keys[i],[]).append(int(i))
    for values in pool.values():rng.shuffle(values)
    out={}
    for i in sorted(entryset):
        values=pool.get(keys[i],[]);n=min(3,len(values));out[i]=[values.pop() for _ in range(n)]
    return out


def nominate(discovery):
    """Nominate from 2023/2024 outcomes only; callers cannot pass later rows."""
    assert discovery.fold.eq('discovery').all()
    years=pd.to_datetime(discovery.entry_time,utc=True).dt.year
    assert years.isin([2023,2024]).all()
    d=discovery.assign(year=years);rows=[]
    for (symbol,minutes),g in d.groupby(['symbol','minutes']):
        candidates=[]
        for policy,q in g.groupby('policy'):
            if policy=='P00_base' or len(q)<12:continue
            y=q.groupby('year').net_bp.agg(['size','mean'])
            if len(y)!=2 or y['size'].min()<4 or y['mean'].min()<=0 or not(q.excess_bp.mean()>0):continue
            candidates.append((float(y['mean'].min()),float(q.net_bp.mean()),policy,len(q)))
        if candidates:
            z=sorted(candidates,key=lambda x:(-x[0],-x[1],x[2]))[0]
            rows.append(dict(symbol=symbol,minutes=int(minutes),policy=z[2],n=z[3],worst_year_mean_bp=z[0],mean_net_bp=z[1],basis='2023/2024 only'))
    return pd.DataFrame(rows,columns=['symbol','minutes','policy','n','worst_year_mean_bp','mean_net_bp','basis'])


def ledger_record(symbol,minutes,fold,policy,anchor,j,side,b,f,result):
    r=f.iloc[j]
    return dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,parent=PARENTS[policy],
        event_id=f'{symbol}_{minutes}_{fold}_departure_{anchor}',anchor_i=anchor,signal_i=j,
        anchor_time=b.index[anchor].isoformat(),signal_time=b.index[j].isoformat(),entry_time=b.index[j+1].isoformat(),
        exit_bar_time=b.index[result['exit_i']].isoformat(),month=b.index[j].strftime('%Y-%m'),side=side,
        delay_bars=j-anchor,zero_before=int(f.zero_before.iloc[anchor]),anchor_dense=bool(f.dense_recent.iloc[anchor]),
        width_at_anchor=float(f.dense_pre_bandwidth_atr_mean_12.iloc[anchor]),crosses_at_anchor=float(f.dense_pre_pairwise_cross_count_12.iloc[anchor]),
        width_at_entry=float(r.dense_pre_bandwidth_atr_mean_12),ma_score=float(r.ma_score),strength=float(side*r.md/r.atr),
        h_md=float(r.h_md),h_sh=float(r.h_sh),h_i=int(r.h_source_i),h_close_ns=int(r.h_source_close_ns),
        l_md=float(r.l_md),l_i=int(r.l_source_i),l_close_ns=int(r.l_source_close_ns),**result)


def run(variant='primary'):
    assert variant in ('primary','neutral_extension')
    output=OUT if variant=='primary' else OUT/variant
    data=DATA if variant=='primary' else DATA/variant
    policies=POLICIES if variant=='primary' else ZERO_POLICIES
    output.mkdir(parents=True,exist_ok=True);data.mkdir(parents=True,exist_ok=True)
    bars,fs,sources=all_frames();allrows=[];controls=[];summaries=[];portfolios=[];coverage=[]
    for symbol in ['BTC','ETH']:
        for minutes in TF:
            b=bars[symbol,minutes];f=fs[symbol,minutes];ex=exit_arrays(f);masks={side:side_masks(f,side) for side in [-1,1]}
            for fold,start,end in FOLDS:
                valid=np.flatnonzero((b.index>=pd.Timestamp(start,tz='UTC'))&(b.index+pd.Timedelta(minutes=minutes)<=pd.Timestamp(end,tz='UTC')))
                last=int(valid[-1]);idx=[int(i) for i in valid if i<last and f.eligible.iloc[i] and pd.notna(f.volbin.iloc[i])]
                anchors=[i for i in idx if f.departure.iloc[i]!=0]
                requests={p:select_requests(f,anchors,last,p,masks) if not (LOW[minutes] is None and p in LOW_POLICIES) else [] for p in policies}
                union=sorted({j for rows in requests.values() for _,j,_ in rows});matches=match_union(f,idx,union);cache={}
                def resolved(i,side):
                    key=(i,side)
                    if key not in cache:cache[key]=outcome(b,ex,i,side,'neutral',last)
                    return cache[key]
                baseline={anchor:resolved(j,side)['net_bp'] for anchor,j,side in requests['P00_base']}
                profits=sum(max(v,0) for v in baseline.values());losses=-sum(min(v,0) for v in baseline.values())
                for policy,rs in requests.items():
                    rows=[]
                    for anchor,j,side in rs:
                        r=ledger_record(symbol,minutes,fold,policy,anchor,j,side,b,f,resolved(j,side))
                        cc=matches[j];returns=[]
                        for c in cc:
                            o=resolved(c,side);returns.append(o['net_bp'])
                            controls.append(dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,event_id=r['event_id'],signal_i=j,control_i=c,side=side,**o))
                        r['control_n']=len(cc);r['control_mean_net_bp']=float(np.mean(returns)) if len(cc)==3 else np.nan
                        r['excess_bp']=r['net_bp']-r['control_mean_net_bp'];r['anchor_baseline_net_bp']=baseline[anchor]
                        rows.append(r)
                    q=pd.DataFrame(rows);allrows.extend(rows)
                    common=dict(symbol=symbol,minutes=minutes,fold=fold,policy=policy,parent=PARENTS[policy],base_n=len(anchors),
                                available=not(LOW[minutes] is None and policy in LOW_POLICIES),higher_minutes=HIGH[minutes],lower_minutes=LOW[minutes])
                    if not len(q):summaries.append(dict(**common,n=0,matched_n=0));continue
                    stats=distribution(q.net_bp);ranks=rank_baseline(q.assign(strength=q.ma_score));ranks={'ma_'+k:v for k,v in ranks.items()}
                    pm,accepted=single_position(b,q);portfolios.extend(accepted)
                    # Fixed-notional paths below zero are diagnostic only, not feasible accounts.
                    kept={a for a,j,side in rs};keptprofit=sum(max(baseline[a],0) for a in kept)
                    removedloss=-sum(min(v,0) for a,v in baseline.items() if a not in kept)
                    summ=dict(**common,**stats,**ranks,**inference(q.excess_bp,q.month),
                              mean_gross_bp=float(q.gross_bp.mean()),matched_n=int(q.excess_bp.notna().sum()),
                              matched_case_mean_net_bp=float(q.loc[q.excess_bp.notna(),'net_bp'].mean()),controls_mean_net_bp=float(q.control_mean_net_bp.mean()),
                              median_hold_bars=float(q.hold_bars.median()),mean_delay_bars=float(q.delay_bars.mean()),
                              anchor_profit_retained_pct=100*keptprofit/profits if profits else np.nan,
                              baseline_loss_filtered_pct=100*removedloss/losses if losses else np.nan,
                              actual_profit_on_prior_winners_bp=float(q.loc[q.anchor_baseline_net_bp>0,'net_bp'].sum()),
                              boundary_marks=int(q.exit_kind.eq('boundary_mark').sum()),
                              **{'portfolio_'+k:v for k,v in pm.items()})
                    summaries.append(summ)
                coverage.append(dict(symbol=symbol,minutes=minutes,fold=fold,start=b.index[idx[0]].isoformat(),end=(b.index[last]+pd.Timedelta(minutes=minutes)).isoformat(),bars=len(idx),anchors=len(anchors),union_entries=len(union)))
            print(f'{symbol} {minutes}m completed',flush=True)
    events=pd.DataFrame(allrows);control=pd.DataFrame(controls);summary=pd.DataFrame(summaries)
    nominations=nominate(events[events.fold=='discovery'].copy())
    summary['nominated_on_discovery']=[any((nominations.symbol==r.symbol)&(nominations.minutes==r.minutes)&(nominations.policy==r.policy)) for r in summary.itertuples()]
    selected=summary[(summary.fold=='replication')&summary.p.notna()].sort_values('p')
    summary.loc[selected.index,'p_holm_replication']=np.maximum.accumulate(np.minimum(1,selected.p.to_numpy()*(len(selected)-np.arange(len(selected)))))
    # Exact inherited strategy parity includes all wins, losses and boundary marks.
    old=pd.read_csv(ROOT/'data/imacd_profit_mechanism_v2/events.csv')
    old=old[(old.policy=='departure_neutral')&old.minutes.isin(TF)].set_index('event_id').sort_index()
    new=events[events.policy=='P00_base'].set_index('event_id').sort_index()
    assert old.index.equals(new.index)
    for col in ['entry_i','exit_i','entry_price','exit_price','net_bp','mfe_bp','mae_bp']:
        assert np.allclose(old[col],new[col],rtol=0,atol=1e-7),col
    outputs=[]
    for name,frame,folder in [('events',events,data),('controls',control,data),('portfolio',pd.DataFrame(portfolios),data),
                              ('summary',summary,output),('nominations',nominations,output),('coverage',pd.DataFrame(coverage),output)]:
        p=folder/(name+'.csv');frame.to_csv(p,index=False);outputs.append(dict(path=str(p.relative_to(ROOT)),rows=len(frame),sha256=sha(p),size_bytes=p.stat().st_size))
    manifest=dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),sources=sources,outputs=outputs,
                  variant=variant,policies=policies,parents={p:PARENTS[p] for p in policies},base_parity_rows=len(new),base_parity_columns=7,
                  seed=SEED,round_trip_cost_bp=20,holdout_uses=({'P00_base_inherited_V2_C_total':2,**{p:1 for p in policies[1:]}} if variant=='primary'
                      else {'P00_base_inherited_V2_C_total':3,**{p:(2 if p in POLICIES else 1) for p in policies[1:]}}),
                  owner_authorization='OKX all periods/all dates; explicitly add usual MA density and multitimeframe confluence; explore independently.',
                  higher_timeframe_clock='source close timestamp <= decision close; eligible source warmup340',
                  production_eligible=False,training_eligible=False,raw_kline_writes=False)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print('V2 baseline parity passed;',len(new),'rows;',len(events),'V3 events; discovery nominations',len(nominations),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--neutral-extension',action='store_true')
    args=parser.parse_args();run('neutral_extension' if args.neutral_extension else 'primary')
