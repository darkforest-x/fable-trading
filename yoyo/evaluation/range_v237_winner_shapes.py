"""Compare original V2.37 large realized winners with pre-1R initial stops.

Reuse frozen Nautilus outcomes, including attached same-week/risk controls.
Predictors read only primary-arrow OHLC, preceding5/20 native candles, and
completed5/15/60/240m context at entry. No outcomes enter feature extraction.
Reference frames are retrospective when pairing is later; practical filter
checks use publication-close entries, with the original5R/4ATR/H1 exits.
No classifier, execution engine, Pine change, or parameter promotion.
"""
from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_twoyear import close_frame, read
from yoyo.evaluation.pepe_range_exits import aggregate, inference
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
from yoyo.evaluation.range_management_study import holm

EXP = ROOT/'experiments/active/exp-range-v237-winner-shapes-20261022-v1'
CONFIG = EXP/'config.json'
OHLC = ['open', 'high', 'low', 'close']
EPS = 1e-7


def contexts(five):
    """Completed-bar aggregates only; no forward shifts or final weekly range."""
    frames = {5: five, **{n: aggregate(five, n) for n in (15, 60, 240)}}
    cache = {}
    for n, q in frames.items():
        a = atr(q).to_numpy(float)
        ma20, ma60 = q.close.rolling(20).mean(), q.close.rolling(60).mean()
        cache[n] = dict(frame=q, bars=q[OHLC].to_numpy(float), atr=a,
            ma20=ma20.to_numpy(), ma60=ma60.to_numpy(),
            slope20=ma20.diff(3).to_numpy())
    grid, names, _, developing = monday_levels(five, [0,.5,.618,.705,1.13,1.27,1.618],
        'America/New_York', bar_minutes=5)
    return cache, grid, names, developing


def features(five, rows, tick, context=None):
    """Windows end at entry or earlier; primary signal belongs to lane5/15.

    Primary-arrow morphology uses its own native candle and ATR at j-1.
    Context uses last completed aggregate at entry; key levels were known
    before the primary candle opened. No later matching arrow is a predictor.
    """
    cache, grid, names, developing = contexts(five) if context is None else context
    out=[]
    for r in rows.itertuples():
        e, lane, side = int(r.entry_i), int(r.lane), int(r.side)
        stamp=five.index[e]; q=cache[lane]; f=q['frame']; b=q['bars']
        j=int(f.index.get_indexer([pd.Timestamp(r.original_close_ns, tz='UTC')])[0])
        assert 21<=j and f.index[j]<=stamp
        o,h,l,c=b[j]; width=max(h-l,tick); body=abs(c-o)
        a=float(q['atr'][j-1]); assert a>0
        wick=min(o,c)-l if side==1 else h-max(o,c)
        prior=b[j-20:j]; recent=b[j-5:j]
        sweep=(l<prior[:,2].min() and min(o,c)>prior[:,2].min()) if side==1 else (
            h>prior[:,1].max() and max(o,c)<prior[:,1].max())
        old=int(five.index.get_indexer([f.index[j]-pd.Timedelta(minutes=lane-5)])[0])
        assert old>=0
        prices=grid[old]; bodylo,bodyhi=min(o,c),max(o,c)
        hits=((l<=prices)&(prices<bodylo)) if side==1 else ((bodyhi<prices)&(prices<=h))
        entryprices=grid[e]; advance=side*(entryprices-r.entry)
        ahead=advance[advance>tick/2]
        next_distance=float(ahead.min()/r.risk) if len(ahead) else np.nan
        low, high=entryprices[0],entryprices[1]
        location=(r.entry-low)/(high-low) if high>low else np.nan
        row=dict(event_id=r.event_id,mode=r.mode,lane=lane,
            arrow_wick_share=wick/width,arrow_wick_body=wick/max(body,tick),
            arrow_wick_atr=wick/a,arrow_range_atr=width/a,
            arrow_body_share=body/width,arrow_aligned_body=float(side*(c-o)>0),
            arrow_close_rejection=(c-l)/width if side==1 else (h-c)/width,
            arrow_sweep20=float(sweep),
            prior20_signed_atr=side*(b[j-1,3]-b[j-21,3])/a,
            compression5_20=float(np.mean(recent[:,1]-recent[:,2])/np.mean(prior[:,1]-prior[:,2])),
            prior20_efficiency=abs(b[j-1,3]-b[j-21,3])/max(np.abs(np.diff(b[j-21:j,3])).sum(),tick),
            risk_bp=r.risk/r.entry*10000,
            risk_atr5=r.risk/cache[5]['atr'][e],
            entry_break_previous=float(r.entry>five.high.iloc[e-1] if side==1 else r.entry<five.low.iloc[e-1]),
            entry_followthrough_atr=side*(r.entry-c)/a,
            entry_lag_minutes=(stamp-f.index[j]).total_seconds()/60,
            arrow_wick_key=float(hits.any()),
            arrow_wick_keys='|'.join(names[k] for k in np.flatnonzero(hits)),
            next_key_r=next_distance,monday_location=location,
            monday_developing=float(developing[e]))
        for n in (15,60,240):
            z=cache[n]; k=int(z['frame'].index.searchsorted(stamp,side='right')-1)
            assert k>=60 and z['frame'].index[k]<=stamp
            row[f'ma{n}_aligned']=float(side*(z['ma20'][k]-z['ma60'][k])>0)
            row[f'ma{n}_slope_atr']=side*z['slope20'][k]/z['atr'][k]
            row[f'ma{n}_distance_atr']=side*(r.entry-z['ma20'][k])/z['atr'][k]
        out.append(row)
    return pd.DataFrame(out)


def stop_bounds(bars, row):
    """MFE bounds before actual initial stop; entry candle is not a future path.

    Stop-candle open precedes either extreme. A gap-through stop exits at
    open; its later high/low cannot raise the upper bound. Otherwise the
    favorable stop-bar extreme is ambiguous, retained only in the upper bound.
    """
    if row.reason!=1:
        return np.nan,np.nan
    e,x,s=int(row.entry_i),int(row.exit_i),int(row.side)
    assert x>e
    prior=bars[e+1:x]
    value=prior[:,1]-row.entry if s==1 else row.entry-prior[:,2]
    lo=max(0.,float(value.max()/row.risk)) if len(value) else 0.
    opened=s*(bars[x,0]-row.entry)/row.risk
    if opened<=-1+EPS:
        return lo,lo
    lo=max(lo,opened)
    favorable=s*(bars[x,1 if s==1 else 2]-row.entry)/row.risk
    return lo,max(lo,float(favorable))


def cohort_counts(d):
    closed=d.reason.ne(0)
    return dict(n=len(d),closed=int(closed.sum()),open=int((~closed).sum()),
        winner3_net=int((closed&d.net_r.gt(3+EPS)).sum()),
        winner3_gross=int((closed&d.gross_r.gt(3+EPS)).sum()),
        early_stop_certain=int((d.reason.eq(1)&d.stop_mfe_upper.lt(1-EPS)).sum()),
        early_stop_ambiguous=int((d.reason.eq(1)&d.stop_mfe_lower.lt(1-EPS)&d.stop_mfe_upper.ge(1-EPS)).sum()),
        all_initial_stops=int(d.reason.eq(1).sum()))


def stats(d):
    z=d[d.reason.ne(0)]
    if z.empty:return dict(n=0)
    # Random marks at the data boundary remain separately identifiable.
    rz=z[z.random_reason.ne(0)]
    return dict(n=len(z),wins=int(z.net_r.gt(EPS).sum()),win_rate=float(z.net_r.gt(EPS).mean()),
        net_r=float(z.net_r.sum()),gross_r=float(z.gross_r.sum()),mean_r=float(z.net_r.mean()),
        mean_bp=float(z.net_notional_bp.mean()),winner3=int(z.net_r.gt(3+EPS).sum()),
        early_stop=int((z.reason.eq(1)&z.stop_mfe_upper.lt(1-EPS)).sum()),
        random_closed=len(rz),random_wins=int(rz.random_net_r.gt(EPS).sum()),
        random_win_rate=float(rz.random_net_r.gt(EPS).mean()) if len(rz) else None,
        random_net_r=float(rz.random_net_r.sum()),random_mean_r=float(rz.random_net_r.mean()) if len(rz) else None,
        net_without_best=float(z.net_r.sum()-z.net_r.max()))


def flag(d,spec):
    x=d[spec['feature']]
    return x.ge(spec['threshold']) if spec['op']=='ge' else x.le(spec['threshold'])


def chronological_masks(d,midpoint):
    entry=pd.to_datetime(d.entry_time,utc=True);exited=pd.to_datetime(d.exit_time,utc=True)
    cut=pd.Timestamp(midpoint)
    return (entry<cut)&(exited<cut)&d.reason.ne(0), (entry>=cut)&d.reason.ne(0)


def select_rule(d,cfg):
    first,_=chronological_masks(d,cfg['midpoint']);base=d[first]
    choices=[]
    for cut in cfg['cuts']:
        g=base[flag(base,cut)]
        if len(g)>=max(cfg['minimum_first'],cfg['minimum_retention']*len(base)) and (g.net_r>3+EPS).sum()>=5:
            choices.append((cut,stats(g)))
    if not choices:return None
    return sorted(choices,key=lambda x:(-x[1]['win_rate'],-x[1]['mean_r'],x[0]['id']))[0][0]


def bootstrap_delta(d,keep,seed):
    """Week-cluster ratio bootstrap, selected versus all, same later sample."""
    week,group=np.unique(d.week,return_inverse=True);n=len(week)
    a=np.asarray(keep,float);y=d.net_r.gt(EPS).to_numpy(float)
    values=np.column_stack([np.bincount(group,weights=v,minlength=n) for v in (a,a*y,np.ones(len(d)),y)])
    rng=np.random.default_rng(seed)
    weights=rng.multinomial(n,np.full(n,1/n),size=5000)
    sampled=np.einsum('ij,jk->ik',weights,values,optimize=False)
    ok=sampled[:,0]>0;draw=sampled[ok,1]/sampled[ok,0]-sampled[ok,3]/sampled[ok,2]
    observed=float(y[a==1].mean()-y.mean())
    # Centered cluster bootstrap approximates a two-sided null for selection
    # frozen on a disjoint earlier period; known history is not fresh OOS.
    p=float((1+np.sum(np.abs(draw-observed)>=abs(observed)))/(1+len(draw)))
    return dict(delta_win=observed,delta_win95=np.quantile(draw,[.025,.975]).tolist(),p=p,weeks=n)


def main():
    cfg=json.loads(CONFIG.read_text());source=ROOT/cfg['source']
    if (EXP/'summary.json').exists():
        print('Reuse '+str(EXP/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    totals=[];profiles=[];cut_rows=[];selected=[];checks=[];stamps=[]
    for spec in cfg['symbols']:
        coin=spec['symbol'];path=source/coin
        five=close_frame(read(path/'candles.csv.gz'));allrows=read(path/'results.csv.gz')
        real=allrows[allrows.population.eq('signal')].copy()
        f=features(five,real,spec['tick'])
        # Complete prefix recomputation, including higher-timeframe and Monday
        # context; one prefix per mode/lane. Outcome fields never enter f.
        count=0
        for (mode,lane),group in real.groupby(['mode','lane']):
            r=group.iloc[[len(group)//2]];end=int(r.entry_i.iloc[0])+1
            got=features(five.iloc[:end],r,spec['tick'])
            expected=f[(f['mode']==mode)&(f.lane==lane)&(f.event_id==r.event_id.iloc[0])]
            pd.testing.assert_frame_equal(got.reset_index(drop=True),expected.reset_index(drop=True))
            count+=1
        d=real.merge(f,on=['event_id','mode','lane'],validate='one_to_one')
        bars=five[OHLC].to_numpy()
        bounds=[stop_bounds(bars,r) for r in d.itertuples()]
        d[['stop_mfe_lower','stop_mfe_upper']]=bounds
        random=allrows[allrows.population.eq('random')][['event_id','mode','lane','net_r','reason']]
        d=d.merge(random.rename(columns={'net_r':'random_net_r','reason':'random_reason'}),
            on=['event_id','mode','lane'],validate='one_to_one')
        assert len(d)==len(real) and not d.duplicated(['event_id','mode','lane']).any()
        np.testing.assert_allclose(d.net_r,d.gross_r-d.fee_r,atol=1e-7,rtol=0)
        assert (d.loc[d.reason.eq(1),'stop_mfe_upper']>=d.loc[d.reason.eq(1),'stop_mfe_lower']).all()
        d['cohort']=np.select([d.reason.eq(0),d.net_r.gt(3+EPS),
            d.reason.eq(1)&d.stop_mfe_upper.lt(1-EPS),
            d.reason.eq(1)&d.stop_mfe_lower.lt(1-EPS)&d.stop_mfe_upper.ge(1-EPS)],
            ['open','winner_gt3R','stop_before1R','stop_order_ambiguous'],default='other')
        d['symbol']=coin
        d.to_csv(EXP/f'{coin}_all_features.csv.gz',index=False)
        for cohort in ['winner_gt3R','stop_before1R']:
            d[d.cohort.eq(cohort)].to_csv(EXP/f'{coin}_{cohort}.csv',index=False)
        numeric=[x for x in f.select_dtypes(include=[np.number]) if x!='lane']
        for (mode,lane),g in d.groupby(['mode','lane']):
            identity=dict(symbol=coin,mode=mode,lane=int(lane))
            totals.append(dict(**identity,**cohort_counts(g),baseline=stats(g)))
            win=g[g.cohort.eq('winner_gt3R')];bad=g[g.cohort.eq('stop_before1R')]
            for feature in numeric:
                profiles.append(dict(**identity,feature=feature,n_winner=len(win),n_stop=len(bad),
                    winner_median=float(win[feature].median()),stop_median=float(bad[feature].median()),
                    winner_mean=float(win[feature].mean()),stop_mean=float(bad[feature].mean())))
            first,later=chronological_masks(g,cfg['midpoint'])
            for period,mask in [('all',g.reason.ne(0)),('first',first),('later',later)]:
                base=g[mask]
                cut_rows.append(dict(**identity,period=period,rule='all',**stats(base)))
                for cut in cfg['cuts']:
                    subset=base[flag(base,cut)]
                    cut_rows.append(dict(**identity,period=period,rule=cut['id'],**stats(subset),
                        retained=len(subset)/len(base),winner_retained=int(subset.net_r.gt(3+EPS).sum())/max(1,int(base.net_r.gt(3+EPS).sum()))))
            # Only causal entries can support an executable filter conclusion.
            if mode=='causal':
                chosen=select_rule(g,cfg)
                if chosen:
                    base=g[later];keep=flag(base,chosen);sub=base[keep]
                    common=sub[sub.random_reason.ne(0)]
                    pair=common[['net_r','random_net_r','week']].rename(columns={'random_net_r':'net_r_random'})
                    dedup=base.drop_duplicates(['entry_i','side','entry','stop'])
                    selected.append(dict(**identity,rule=chosen['id'],label=chosen['label'],
                        earlier=stats(g[first&flag(g,chosen)]),earlier_all=stats(g[first]),
                        later=stats(sub),later_all=stats(base),
                        retained=len(sub)/len(base),
                        later_dedup=stats(dedup[flag(dedup,chosen)]),later_all_dedup=stats(dedup),
                        inference=bootstrap_delta(base,keep,cfg['seed']),
                        paired_random=inference(pair,cfg['seed']),
                        purged_cross_boundary=int(((pd.to_datetime(g.entry_time,utc=True)<pd.Timestamp(cfg['midpoint']))&~first).sum())))
        checks.append(dict(symbol=coin,rows=len(d),prefix_checks=count,
            source_bars=len(five),feature_missing={c:int(d[c].isna().sum()) for c in numeric if d[c].isna().any()},
            outcome_and_cost_identities=True))
        stamps.extend(file_stamp(path/name) for name in ['candles.csv.gz','results.csv.gz'])
        print(coin,'completed',len(d),'occurrences',flush=True)
    adjusted=holm([x['inference']['p'] for x in selected])
    excess=holm([x['paired_random']['p_one_sided'] for x in selected])
    for row,p,q in zip(selected,adjusted,excess):
        row['inference']['p_holm']=p;row['paired_random']['p_holm']=q
    pd.DataFrame(profiles).to_csv(EXP/'shape_profiles.csv',index=False)
    pd.DataFrame(cut_rows).to_csv(EXP/'filter_comparison.csv',index=False)
    dump(EXP/'summary.json',dict(config=cfg,cohorts=totals,selected=selected))
    dump(EXP/'validation.json',dict(checks=checks,inputs=stamps,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        native_replays=0,training=False,pine_changed=False))
    print(json.dumps(selected,ensure_ascii=False),flush=True)


def exports():
    """Readable separated ledgers and gross-outcome sensitivity from cache only."""
    cfg=json.loads(CONFIG.read_text());result=[];files=[];examples=[]
    for spec in cfg['symbols']:
        coin=spec['symbol'];d=read(EXP/f'{coin}_all_features.csv.gz')
        for col,source in [('signal_time_shanghai','signal_ns'),('publication_time_shanghai','publication_ns')]:
            d[col]=pd.to_datetime(d[source],unit='ns',utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
        for col in ['entry_time','exit_time']:
            d[col+'_shanghai']=pd.to_datetime(d[col],utc=True).dt.tz_convert('Asia/Shanghai').astype(str)
        cols=['event_id','signal_time_shanghai','publication_time_shanghai','entry_time_shanghai','exit_time_shanghai',
            'side','entry','stop','exit_price','gross_r','fee_r','net_r','reason','stop_mfe_lower','stop_mfe_upper',
            'arrow_wick_share','arrow_close_rejection','arrow_sweep20','prior20_signed_atr','compression5_20',
            'risk_bp','ma15_aligned','ma60_aligned','ma240_aligned','arrow_wick_keys','retrospective']
        for (mode,lane),g in d.groupby(['mode','lane']):
            for cohort in ['winner_gt3R','stop_before1R']:
                z=g[g.cohort.eq(cohort)].sort_values('signal_ns')
                file=EXP/f'{coin}_{lane}m_{mode}_{cohort}.csv'
                z[cols].to_csv(file,index=False)
                files.append(dict(path=str(file.relative_to(ROOT)),rows=len(z)))
            for side in [0,-1,1]:
                z=g if not side else g[g.side.eq(side)]
                bad=z[z.cohort.eq('stop_before1R')]
                for measure in ['net_r','gross_r']:
                    win=z[z.reason.ne(0)&z[measure].gt(3+EPS)]
                    metrics={}
                    for key in ['arrow_wick_share','arrow_close_rejection','arrow_sweep20',
                        'prior20_efficiency','risk_bp']:
                        metrics[key]=dict(winner_quartiles=win[key].quantile([.25,.5,.75]).tolist(),
                            stop_quartiles=bad[key].quantile([.25,.5,.75]).tolist(),
                            winner_mean=float(win[key].mean()),stop_mean=float(bad[key].mean()))
                    result.append(dict(symbol=coin,mode=mode,lane=int(lane),side=side,measure=measure,
                        winners=len(win),losers=len(bad),winner_net_r=float(win.net_r.sum()),
                        loser_net_r=float(bad.net_r.sum()),late_published_winners=int(win.retrospective.sum()),
                        late_published_losers=int(bad.retrospective.sum()),features=metrics))
        if coin=='BTC':
            for r in d[d.event_id.eq('5-216356')].to_dict('records'):
                examples.append({k:r[k] for k in ['event_id','mode','entry_time_shanghai','publication_time_shanghai',
                    'entry','stop','risk_bp','reason','gross_r','net_r','arrow_wick_share','arrow_close_rejection',
                    'arrow_sweep20','ma15_aligned','ma60_aligned','ma240_aligned']})
    dump(EXP/'supplement.json',dict(profiles=result,files=files,screenshot_at_frozen_cutoff=examples,
        source='Cached feature and Nautilus ledgers; no new bars or replays',
        export_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


if __name__=='__main__':
    exports() if '--exports' in sys.argv else main()
