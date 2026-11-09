"""Keep proven large winners on their daily EMA trend once a full 12H closes above twice entry."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_risk as source

correction = source.correction
parent = correction.parent
wallet = correction.wallet
native = correction.archive.native.engine
EXP = source.EXP.parent/'exp-cloud-close-doubled-runner-20261109-v1'
EXIT_FIELDS = ['exit_bar_ms','exit_phase','exit_reason','exit_price','exit_fee_rate','gap_flag','same_bar_ambiguous']


def adjust_symbol(children,source_spec):
    """Use only completed 12H close and last known UTC daily trend at each native H1 open."""
    hours,bars = parent.repair.market.frames(source_spec)
    q,times,_ = native._prepare_symbol(bars,hours)
    close_clock = bars.index.to_numpy()+12*wallet.HOUR
    closes = bars.close.to_numpy()
    sig = parent.daily_state(bars)
    daily = sig.reindex(times//parent.DAY*parent.DAY).to_numpy()
    updates = []
    for r in children[children.tp_r.eq(0)&children.side.eq(1)].itertuples():
        first,last = close_clock.searchsorted([r.entry_ms+12*wallet.HOUR,r.exit_bar_ms+1])
        hits = np.flatnonzero(closes[first:last]>=2*r.entry_price)
        if not len(hits):
            continue
        t = int(close_clock[first+hits[0]])
        if r.exit_bar_ms==t and r.exit_phase=='open':
            continue
        ix = int(times.searchsorted(t))
        if ix==len(times) or times[ix]!=t or not np.isfinite(daily[ix]):
            continue
        q_new = q.copy()
        active = (times>=t)&np.isfinite(daily)
        q_new[active,4] = np.nan
        q_new[active,5] = daily[active]
        a = int(times.searchsorted(r.entry_ms))
        e,why = native._safe_path_end(a,1,q_new,times)
        event = dict(event_id=r.event_id,parent_id=r.parent_id,symbol=r.symbol,source_row_number=0,week='',
                is_control=False,side=1,entry_ms=r.entry_ms,entry_price=r.entry_price,stop_price=r.stop_price,
                risk_distance=r.entry_price-r.stop_price,entry_i=a,end_i=e,path_upper_bound_reason=why)
        fills,_ = native._simulate_symbol([event],q_new,times,(0.,),1)
        new = fills[0]
        if new['exit_bar_ms']<t or not np.isclose(new['entry_price'],r.entry_price):
            raise ValueError(f'Runner was already closed before changing its exit for {r.event_id}')
        if new['exit_reason']=='ema_flip_open' and new['exit_bar_ms']>=t and new['exit_phase']=='open':
            new['exit_reason']='daily_ema_flip_open'
        updates.append(dict(event_id=r.event_id,symbol=r.symbol,activated_ms=t,entry_ms=r.entry_ms,
                            original_exit_ms=r.exit_bar_ms,original_exit_reason=r.exit_reason,
                            original_exit_price=r.exit_price,**{k:new[k] for k in EXIT_FIELDS}))
    return updates


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed doubled winner follow-through')
    pool,src,_,_ = correction.pool()
    _,quotes,_ = source.inputs()
    results = []
    for n,(sym,g) in enumerate(pool.groupby('symbol')):
        results.extend(adjust_symbol(g,src[sym]))
        if n%80==0:
            print('completed native markets',n,flush=True)
    updates = pd.DataFrame(results).set_index('event_id')
    updates.to_csv(EXP/'native_switches.csv.gz')
    # Unfilled 2R child follows its original runner after 12H; previously
    # settled 2R children stay closed even when the sibling changes its exit.
    live = pool.event_id.isin(updates.index)&~pool.exit_reason.str.startswith('take_profit')
    if not pool.loc[live,'exit_bar_ms'].eq(pool.loc[live,'event_id'].map(updates.original_exit_ms)).all():
        raise ValueError('Partial child and original runner have different outstanding exits')
    for k in EXIT_FIELDS:
        pool.loc[live,k] = pool.loc[live,'event_id'].map(updates[k])
    result = parent.run_wallet(pool,quotes,src,EXP/'account')
    held = pd.read_csv(EXP/'account/funded_1.5x.csv.gz',usecols=['event_id'])
    if result['new_or_uncertain_tail_exits']:
        print('Additional source-tail exits require administrative review',flush=True)
    result.update(eligible_activations=len(updates),funded_activations=int(held.event_id.isin(updates.index).sum()),
                  source='Corrected WAVES, LRC, ICX 360-name historical universe; historical funding excluded.')
    correction.archive.prices.safe_json(EXP/'summary.json',result)


if __name__=='__main__':
    main()
