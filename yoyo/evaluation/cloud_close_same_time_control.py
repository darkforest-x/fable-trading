"""Match the funded 90-day V1.7 signal cohort to non-signals at the identical market instant."""
import gc
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_pair_uncertainty as uncertainty
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_slots as capacity


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-same-time-control-20261109-v1'


def observed_market(symbol,top):
    folder=(wallet.TOP if top else wallet.SOURCE)/'inputs'/symbol
    bars=engine._load_bars(folder/'bars12h.csv.gz','12h')
    records=bars.reset_index().to_dict('records')
    ema=engine._ema_points_12h(records)
    atr=pd.DataFrame(engine.atr_points(records)).atr14.shift(1).to_numpy()
    prior=100*atr/bars.close.shift(1).to_numpy()
    confirmed=bars.index.to_numpy()+leader.BAR
    history=leader.trailing_return(bars.reset_index()).reindex(confirmed).notna().to_numpy()
    side=np.sign(ema.ema20-ema.ema50).to_numpy()
    clock,_=wallet.quotes(symbol)
    available=history & ema.ready.to_numpy() & np.isfinite(prior) & (prior>0) & np.isin(confirmed,clock)
    return pd.DataFrame(dict(symbol=symbol,ts=confirmed[available],side=side[available],atr=prior[available]))


def pair(parents,market):
    keys=set(zip(parents.symbol,parents.entry_ms))
    open_keys=set(zip(market.symbol,market.ts))
    if not keys.issubset(open_keys):
        raise ValueError('Eligible parent lost at its own market time')
    non_signal=np.fromiter(((s,t) not in keys for s,t in zip(market.symbol,market.ts)),dtype=bool,count=len(market))
    choices=market[non_signal].sort_values('symbol').groupby(['ts','side'])
    ranked=parents.assign(crc=parents.symbol.map(lambda s:zlib.crc32(s.encode())))
    matched=[]
    for ts,signals in ranked.groupby('entry_ms',sort=True):
        used=set()
        for r in signals.sort_values(['entry_order','crc','symbol','event_id']).itertuples():
            key=(ts,r.side)
            if key not in choices.indices:
                continue
            options=choices.get_group(key)
            eligible=options[options.atr.between(.75*r.entry_order,1.25*r.entry_order)&~options.symbol.isin(used)]
            if eligible.empty:
                continue
            rng=np.random.default_rng(20261109+zlib.crc32(r.event_id.encode()))
            row=eligible.iloc[int(rng.integers(len(eligible)))]
            used.add(row.symbol)
            matched.append(dict(parent_id=r.parent_id,source_row_number=r.source_row_number,
                                signal_symbol=r.symbol,symbol=row.symbol,entry_ms=ts,side=r.side,
                                entry_order=r.entry_order,control_prior_atr=row.atr,
                                risk_fraction=r.side*(r.entry_price-r.stop_price)/r.entry_price))
    result=pd.DataFrame(matched)
    if (result.duplicated(['entry_ms','symbol']).any() or result.parent_id.duplicated().any()
            or result.signal_symbol.eq(result.symbol).any()):
        raise ValueError('Simultaneous control identity violation')
    return result


def new_paths(matches,tops):
    all_rows=[]
    for symbol,g in matches.groupby('symbol'):
        folder=(wallet.TOP if tops[symbol] else wallet.SOURCE)/'inputs'/symbol
        b12=engine._load_bars(folder/'bars12h.csv.gz','12h')
        h1=engine._load_bars(folder/'bars1h.csv.gz','1h')
        q,clock,_=engine._prepare_symbol(b12,h1)
        i=clock.searchsorted(g.entry_ms)
        if not np.array_equal(clock[i],g.entry_ms) or (q[i,5]*g.side.to_numpy()<=0).any():
            raise ValueError('Changed control entry clock or EMA')
        price=q[i,0]
        risk=price*g.risk_fraction.to_numpy()
        events=g.assign(entry_i=i,entry_price=price,stop_price=price-g.side*risk,
                        risk_distance=risk,event_id=g.parent_id+':same_time',week='',is_control=True)
        events['end_i']=[engine._safe_path_end(j,s,q,clock)[0] for j,s in zip(i,g.side)]
        rows,_=engine._simulate_symbol(events.to_dict('records'),q,clock,(0.,2.),8)
        part=pd.DataFrame(rows)
        part['entry_order']=part.parent_id.map(g.set_index('parent_id').entry_order)
        all_rows.append(part[['symbol','parent_id','event_id','side','entry_ms','exit_bar_ms','entry_price',
                              'exit_price','exit_reason','exit_phase','exit_fee_rate','tp_r','stop_price','entry_order']])
    return pd.concat(all_rows,ignore_index=True)


def returns(paths, fraction=.75):
    p=paths.copy()
    pnl=p.side*(p.exit_price-p.entry_price)-wallet.TAKER*p.entry_price-p.exit_fee_rate*p.exit_price
    p['weighted']=pnl/p.entry_price*np.where(p.tp_r.eq(2),fraction,1.-fraction)
    return p.groupby('parent_id').agg(net=('weighted','sum'),entry=('entry_ms','min'),last=('exit_bar_ms','max'))


def main():
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    original=wallet.read_paths()
    original=original[~original.is_control&original.tp_r.isin([0,2])].copy()
    original['entry_order']=original.source_row_number.map(src.prior_atr_pct)
    covered=leader.leadership().dropna().index
    original=original[pd.MultiIndex.from_frame(original[['entry_ms','symbol']]).isin(covered)]
    coverage=pd.read_csv(wallet.SOURCE/'input_coverage.csv',dtype={'symbol':str})
    tops=dict(zip(coverage.symbol,coverage.cohort.eq('top20_reused')))
    file=EXP/'simultaneous_controls.csv.gz'
    if not file.exists():
        market=pd.concat([observed_market(s,t) for s,t in tops.items()],ignore_index=True)
        matches=pair(original[original.tp_r.eq(0)],market)
        matches.to_csv(EXP/'matches.csv.gz',index=False)
        controls=new_paths(matches,tops)
        temp=file.with_suffix('.pending')
        controls.to_csv(temp,index=False,compression='gzip')
        temp.replace(file)
    else:
        controls=pd.read_csv(file,dtype={'symbol':str},keep_default_na=False)
    real=original[original.parent_id.isin(controls.parent_id)]
    if len(real)!=len(controls) or len(real)==0:
        raise ValueError('Signal/control legs are not paired')
    rows=[]
    for label,pool in [('matched_signals',real),('simultaneous_controls',controls)]:
        m,tr,eq=capacity.run(pool,9,.75)
        rows.extend(dict(policy=label,period=p,**v) for p,v in m.items())
        tr.to_csv(EXP/f'funded_{label}.csv.gz')
        eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    ab=returns(real).add_suffix('_signal').join(returns(controls).add_suffix('_control'),how='inner')
    if not ab.entry_signal.eq(ab.entry_control).all():
        raise ValueError('Matched pair trades have different opening instants')
    stats={}
    for period,a,b in [('all','2020-01-01','2026-11-09'),('dev','2020-01-01','2025-01-01'),
                       ('2025','2025-01-01','2026-01-01'),('2026','2026-01-01','2026-11-09')]:
        lo,hi=[pd.Timestamp(x,tz='UTC').value//1_000_000 for x in (a,b)]
        subset=ab[ab.entry_signal.ge(lo)&ab.last_signal.lt(hi)&ab.last_control.lt(hi)]
        difference=subset.net_signal-subset.net_control
        ci,weeks=uncertainty.draws_by_week(difference,subset.entry_signal)
        stats[period]=dict(pairs=len(subset),weeks=weeks,mean_difference_pp=100*difference.mean(),ci95_pp=ci)
    result=dict(matched=len(ab),unmatched=len(original)//2-len(ab),uncertainty=stats)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False),stats,flush=True)


if __name__=='__main__':
    main()
