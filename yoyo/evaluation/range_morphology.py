"""Diagnose candle morphology versus frozen V2.31 trade outcomes.

Features use15m OHLC only: touch and arrow candles, prior5/8/20bars,
ATR14 ending strictly before touch, and completed movement through entry.
Window right edges never exceed entry_i. Outcome ledgers are joined AFTER
feature extraction. Source entries/exits/fees are reused, never replayed.
Predetermined binary contrasts, week-cluster uncertainty, explicit risk/side/
calendar adjustment and cross-exit/time/coin checks prevent winner-only claims.
No classifier is trained and no admission/default configuration is changed.
"""
import argparse
import json
import subprocess

import numpy as np
import pandas as pd
from scipy.stats import fisher_exact

from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.pepe_range_exits import aggregate
from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.range_management_study import holm

CONFIG=ROOT/'experiments/active/exp-range-v231-morphology-20261021-v1/config.json'


def read(path):
    return pd.read_csv(path,float_precision='round_trip')


def extract_features(q,matches,tick):
    """OHLC windows: touch[j],arrow[a],history[j-20:j],close[e];a,j<=e.

    ATR is sampled at j-1. Five/20bar sweeps compare the touch wick to prior
    extrema, never a future-confirmed pivot. Engulfing requires opposite prior
    body and inclusive body enclosure. Same-direction rejection means the
    trade-side wick occupies at least half of that past candle. Key-cross
    history compares past bodies to the key already known at entry.
    """
    average=atr(q,14).to_numpy(float)
    o,h,l,c=(q[col].to_numpy(float) for col in ['open','high','low','close'])
    bottom,top=np.minimum(o,c),np.maximum(o,c)
    out=[]
    for r in matches.itertuples():
        j,a,e,side=int(r.touch_i),int(r.signal_i),int(r.entry_i),int(r.side)
        assert 21<=j<=e<len(q) and 0<=a<=e and side in (-1,1)
        assert abs(c[e]-r.entry)<=tick*1e-6
        width=h[j]-l[j];body=top[j]-bottom[j];scale=average[j-1]
        wick=bottom[j]-l[j] if side==1 else h[j]-top[j]
        opposite=h[j]-top[j] if side==1 else bottom[j]-l[j]
        depth=r.level-l[j] if side==1 else h[j]-r.level
        assert width>0 and wick>0 and scale>0 and 0<=depth<=wick+1e-7
        assert abs(r.stop-(l[j] if side==1 else h[j]))<tick*1e-6
        history=slice(j-20,j);recent=slice(j-5,j)
        sweep=lambda n: (l[j]<np.min(l[j-n:j])-tick*1e-6 if side==1 else h[j]>np.max(h[j-n:j])+tick*1e-6)
        prior_wicks=bottom[recent]-l[recent] if side==1 else h[recent]-top[recent]
        ranges=h-l
        efficient=abs(c[j-1]-c[j-21])/max(np.abs(np.diff(c[j-21:j])).sum(),tick)
        stamp=q.index[e];local=stamp.tz_convert('America/New_York')
        week=(local.tz_localize(None).normalize()-pd.Timedelta(days=local.dayofweek)).date().isoformat()
        risk=side*(r.entry-r.stop)
        out.append(dict(pair_id=int(r.pair_id),side=side,key=r.key,level=float(r.level),
            entry=float(r.entry),stop=float(r.stop),risk_bp=risk/r.entry*10000,
            entry_i=e,signal_i=a,touch_i=j,entry_time=stamp.isoformat(),
            touch_time=(q.index[j]-pd.Timedelta(minutes=15)).tz_convert('Asia/Shanghai').isoformat(),
            week=week,event_id=f'{side}:{j}',
            touch_open=float(o[j]),touch_high=float(h[j]),touch_low=float(l[j]),touch_close=float(c[j]),
            wick_points=wick,body_points=body,atr_before_touch=scale,
            wick_share=wick/width,wick_body=wick/max(body,tick),wick_atr=wick/scale,
            penetration=depth/wick,range_atr=width/scale,body_share=body/width,
            signed_body_share=side*(c[j]-o[j])/width,opposite_wick_share=opposite/width,
            close_rejection=(c[j]-l[j])/width if side==1 else (h[j]-c[j])/width,
            sweep5=int(sweep(5)),sweep20=int(sweep(20)),
            approach8_atr=-side*(c[j-1]-c[j-9])/scale,efficiency20=efficient,
            compression5_20=float(np.mean(ranges[recent])/np.mean(ranges[history])),
            rejections5=int(np.sum(prior_wicks/np.maximum(ranges[recent],tick)>=.5)),
            directional_engulf=int(side*(c[j]-o[j])>0 and side*(c[j-1]-o[j-1])<0
                and bottom[j]<=bottom[j-1] and top[j]>=top[j-1]),
            followthrough_atr=side*(c[e]-c[j])/scale,touch_entry_lag=e-j,
            touch_arrow_offset=j-a,arrow_body_share=side*(c[a]-o[a])/max(h[a]-l[a],tick),
            body_touches20=int(np.sum((bottom[history]<=r.level)&(top[history]>=r.level)))))
    return pd.DataFrame(out)


def mask_for(frame,spec):
    x=frame[spec['feature']];threshold=spec['threshold']
    return {'ge':x>=threshold,'gt':x>threshold,'le':x<=threshold,'eq':x==threshold}[spec['op']]


def join_outcomes(features,ledger):
    """Outcome columns have explicit prefixes and cannot enter feature extraction."""
    assert not ledger.duplicated(['pair_id','population']).any()
    result=features.copy()
    fields=['entry','stop','side','entry_i','net_r','gross_r','fee_r','net_notional_bp','exit_i','segment']
    for pop,prefix in [('signal','out_'),('random','random_')]:
        subset=ledger[ledger.population==pop].set_index('pair_id')
        assert set(subset.index)==set(features.pair_id)
        aligned=subset.loc[features.pair_id]
        if pop=='signal':
            np.testing.assert_allclose(aligned[['entry','stop','side']],features[['entry','stop','side']],rtol=0,atol=1e-7)
        for col in fields:
            result[prefix+col]=aligned[col].to_numpy()
        np.testing.assert_allclose(result[prefix+'net_r'],result[prefix+'gross_r']-result[prefix+'fee_r'],rtol=0,atol=1e-8)
    result['net_win']=(result.out_net_r>1e-9).astype(int)
    result['gross_win']=(result.out_gross_r>1e-9).astype(int)
    return result


def wilson(k,n):
    if n==0: return [None,None]
    z=1.959963984540054;p=k/n;d=1+z*z/n
    center=(p+z*z/(2*n))/d
    half=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [max(0,float(center-half)),min(1,float(center+half))]


def group_stats(d):
    if d.empty: return dict(n=0,wins=0,win_rate=None,net_r=None,mean_bp=None)
    n=len(d);wins=int(d.net_win.sum());best=d.out_net_r.max()
    return dict(n=n,events=int(d.event_id.nunique()),weeks=int(d.week.nunique()),wins=wins,
        win_rate=wins/n,win_wilson95=wilson(wins,n),gross_wins=int(d.gross_win.sum()),
        net_r=float(d.out_net_r.sum()),gross_r=float(d.out_gross_r.sum()),mean_r=float(d.out_net_r.mean()),
        mean_bp=float(d.out_net_notional_bp.mean()),median_bp=float(d.out_net_notional_bp.median()),
        random_wins=int((d.random_net_r>1e-9).sum()),random_net_r=float(d.random_net_r.sum()),
        random_mean_bp=float(d.random_net_notional_bp.mean()),
        excess_mean_bp=float((d.out_net_notional_bp-d.random_net_notional_bp).mean()),
        median_risk_bp=float(d.risk_bp.median()),mean_fee_r=float(d.out_fee_r.mean()),
        net_r_without_best=float(d.out_net_r.sum()-best))


def cluster_design(d,cfg):
    weeks,groups=np.unique(d.week,return_inverse=True)
    rng=np.random.default_rng(cfg['seed'])
    bootstrap=rng.multinomial(len(weeks),np.full(len(weeks),1/len(weeks)),size=cfg['bootstrap_weeks'])
    signs=rng.choice([-1.,1.],size=(cfg['wild_score_draws'],len(weeks)))
    risk=np.log(d.risk_bp.to_numpy(float));risk=(risk-risk.mean())/max(risk.std(),1e-12)
    half=(pd.to_datetime(d.entry_time,utc=True)>=pd.Timestamp(cfg['midpoint'])).astype(float)
    controls=np.column_stack([np.ones(len(d)),d.side,half,risk])
    return groups,bootstrap,signs,controls


def association(d,flag,design,cfg):
    """Week-cluster score under a no-feature linear probability null.

    Residualize feature and binary outcome against intercept, side, fixed
    calendar half and log risk-bp. Sign-flip whole-week null score sums; this
    is an exploratory cluster approximation, not evidence of causal impact.
    Bootstrap raw win-rate differences by week, preserving within-week trades.
    """
    groups,boot,signs,controls=design
    x=np.asarray(flag,float);y=d.net_win.to_numpy(float);g=signs.shape[1]
    n1=int(x.sum());n0=len(x)-n1;k1=int((x*y).sum());k0=int(((1-x)*y).sum())
    if not n1 or not n0:
        return dict(delta_win=None,bootstrap95=[None,None],fisher_p=1.,adjusted_delta=None,wild_p=1.,inferable=False)
    counts=np.column_stack([np.bincount(groups,weights=x,minlength=g),np.bincount(groups,weights=1-x,minlength=g)])
    wins=np.column_stack([np.bincount(groups,weights=x*y,minlength=g),np.bincount(groups,weights=(1-x)*y,minlength=g)])
    # Direct reductions avoid platform BLAS exception-flag artifacts on small
    # integer-count products; all bootstrap totals are finite and auditable.
    bn=np.einsum('ij,jk->ik',boot,counts,optimize=False)
    bw=np.einsum('ij,jk->ik',boot,wins,optimize=False)
    assert np.isfinite(bn).all() and np.isfinite(bw).all()
    valid=(bn>0).all(axis=1)
    dif=bw[valid,0]/bn[valid,0]-bw[valid,1]/bn[valid,1]
    ci=np.quantile(dif,[.025,.975]).tolist()
    x0=x-controls@np.linalg.lstsq(controls,x,rcond=None)[0]
    y0=y-controls@np.linalg.lstsq(controls,y,rcond=None)[0]
    den=float(x0@x0);observed=float(x0@y0)
    score=np.bincount(groups,weights=x0*y0,minlength=g)
    inferable=min(n1,n0)>=cfg['minimum_group'] and g>=8 and den>1e-10
    simulated=np.einsum('ij,j->i',signs,score,optimize=False)
    assert np.isfinite(simulated).all()
    p=float((1+np.sum(np.abs(simulated)>=abs(observed)-1e-12))/(len(signs)+1)) if inferable else 1.
    return dict(delta_win=k1/n1-k0/n0,bootstrap95=ci,
        fisher_p=float(fisher_exact([[k1,n1-k1],[k0,n0-k0]])[1]),
        adjusted_delta=observed/den if den>1e-10 else None,wild_p=p,inferable=inferable)


def prepare(cfg,dest):
    source=ROOT/cfg['source'];inputs=[];counts={}
    for spec in cfg['symbols']:
        coin=spec['symbol'];p=source/coin
        matches=read(p/'signal_matches.csv');candidates=read(p/'candidates.csv')
        raw=read(ROOT/cfg['candles']/coin/'candles.csv.gz')
        raw.index=pd.DatetimeIndex(pd.to_datetime(raw.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
        q=aggregate(raw,15)
        features=extract_features(q,matches,spec['tick'])
        assert len(features)==spec['expected'] and features.pair_id.is_unique
        actual=candidates[candidates.population=='signal'].set_index('pair_id').loc[features.pair_id]
        np.testing.assert_array_equal(raw.index[actual.entry_i.to_numpy(int)].asi8,q.index[features.entry_i.to_numpy(int)].asi8)
        numeric=features.select_dtypes(include=[np.number])
        assert np.isfinite(numeric.to_numpy()).all()
        # Whole-input prefixes and arbitrary future corruption must leave
        # every feature unchanged for representative early/middle/late events.
        prefix_checks=0
        for at in sorted(set(np.linspace(0,len(matches)-1,6,dtype=int))):
            selected=matches.iloc[[at]];end=int(selected.entry_i.iloc[0])+1
            want=features.iloc[[at]].reset_index(drop=True)
            got=extract_features(q.iloc[:end],selected,spec['tick'])
            pd.testing.assert_frame_equal(want,got)
            prefix_checks+=1
        features.to_csv(dest/f'{coin}_features.csv',index=False)
        counts[coin]=dict(signals=len(features),events=int(features.event_id.nunique()),weeks=int(features.week.nunique()),
            duplicate_touch_events=int(features.duplicated('event_id').sum()),prefix_checks=prefix_checks,
            numeric_missing=int(numeric.isna().sum().sum()),volume_available='volume' in raw)
        inputs.extend(file_stamp(p/name) for name in ['signal_matches.csv','candidates.csv'])
        inputs.append(file_stamp(ROOT/cfg['candles']/coin/'candles.csv.gz'))
    dump(dest/'preparation.json',dict(counts=counts,inputs=inputs,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


def scopes(d,cfg):
    time=pd.to_datetime(d.entry_time,utc=True)
    return dict(all=np.ones(len(d),bool),first_half=time<pd.Timestamp(cfg['midpoint']),
        second_half=time>=pd.Timestamp(cfg['midpoint']),recent2m=time>=pd.Timestamp(cfg['recent_start']),
        long=d.side==1,short=d.side==-1)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Reuse completed result: '+str(dest/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and hypotheses before analysis')
    if not (dest/'preparation.json').exists(): prepare(cfg,dest)
    if args.prepare_only:
        print((dest/'preparation.json').read_text());return
    source=ROOT/cfg['source'];previous=json.loads((source/'summary.json').read_text())
    arms=list(dict.fromkeys(r['arm'] for r in previous['rows']))
    cuts=cfg['hypotheses']+cfg['context_cuts'];bins=[];tests=[];baselines=[];profiles=[];sources=[]
    for spec in cfg['symbols']:
        coin=spec['symbol'];features=read(dest/f'{coin}_features.csv')
        for arm in arms:
            path=source/coin/arm/'trades.csv';ledger=read(path)
            d=join_outcomes(features,ledger);sources.append(file_stamp(path))
            np.testing.assert_allclose(d.out_fee_r,cfg['cost']*10000/d.risk_bp,rtol=0,atol=1e-7)
            for period,valid in scopes(d,cfg).items():
                part=d[valid]
                baselines.append(dict(symbol=coin,arm=arm,period=period,**group_stats(part)))
                for cut in cuts:
                    flag=mask_for(part,cut)
                    for value in [True,False]:
                        bins.append(dict(symbol=coin,arm=arm,period=period,cut=cut['id'],value=value,
                            **group_stats(part[flag==value])))
            if arm==cfg['primary_arm']:
                design=cluster_design(d,cfg)
                for cut in cuts:
                    flag=mask_for(d,cut)
                    tests.append(dict(symbol=coin,cut=cut['id'],label=cut['label'],
                        primary=cut in cfg['hypotheses'],yes=group_stats(d[flag]),no=group_stats(d[~flag]),
                        **association(d,flag,design,cfg)))
                for outcome in [0,1]:
                    part=d[d.net_win==outcome]
                    for field in list(dict.fromkeys(c['feature'] for c in cuts)):
                        profiles.append(dict(symbol=coin,win=outcome,feature=field,n=len(part),
                            median=float(part[field].median()),q25=float(part[field].quantile(.25)),q75=float(part[field].quantile(.75))))
                d.to_csv(dest/f'{coin}_annotated_trades.csv',index=False)
        print(coin+' completed18cached exits; no execution replay',flush=True)
    primary=[r for r in tests if r['primary']]
    for r,p,f in zip(primary,holm([r['wild_p'] for r in primary]),holm([r['fisher_p'] for r in primary])):
        r['holm_p']=float(p);r['fisher_holm_p']=float(f)
    pd.DataFrame(bins).to_csv(dest/'groups.csv',index=False)
    pd.DataFrame(baselines).to_csv(dest/'baselines.csv',index=False)
    pd.DataFrame(profiles).to_csv(dest/'winner_loser_profiles.csv',index=False)
    dump(dest/'summary.json',dict(config=cfg,associations=tests,
        baseline=[r for r in baselines if r['arm']==cfg['primary_arm']],
        sources=sources,prepared=json.loads((dest/'preparation.json').read_text()),
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        limitations='Small selected observational cohort; known history, not untouched validation. Multiple cuts overlap and are not additive. Conditional random references inherit signal-selected groups, not random shape matching. Week independence and linear nuisance adjustment are approximations. No volume data, no post-entry predictor, no new trade rule.'))
    print(json.dumps([dict(symbol=r['symbol'],cut=r['cut'],delta=r['delta_win'],p=r['wild_p'],holm=r.get('holm_p'))
        for r in sorted(primary,key=lambda r:r['wild_p'])[:10]],ensure_ascii=False,indent=2))


if __name__=='__main__': main()
