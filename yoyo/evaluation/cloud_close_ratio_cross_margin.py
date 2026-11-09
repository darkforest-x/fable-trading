"""Test the funding and margin of a relative portfolio when collateral offsets across its two real legs."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eth_btc_reclaim as pair

EXP=pair.EXP.parent/'exp-cloud-close-ratio-cross-margin-20261109-v1'


def cross_margin(legs,equity):
    """Use actual filled units and all held native H1 highs against the account's joint hourly NAV lower bound."""
    first=int(equity.index[0].value//1_000_000)-pair.H
    gross=np.zeros(len(equity))
    for symbol,part in legs.groupby('symbol'):
        ts,prices=pair.wallet.quotes(symbol)
        for row in part.itertuples():
            stamps=np.arange(row.entry_ms,row.exit_bar_ms+pair.H,pair.H)
            pos=ts.searchsorted(stamps)
            if np.any(pos>=len(ts)) or not np.array_equal(ts[pos],stamps):
                raise ValueError('Cannot bound shared margin without continuous native quotes')
            # Include even an opening exit for the full hour in this upper bound.
            gross[(stamps-first)//pair.H]+=abs(row.quantity)*prices[pos,1]
    held=gross>0
    floor=equity.lower_nav.to_numpy()
    required=(.10+.002)*gross
    buffer=floor-required
    worst=int(np.where(held)[0][np.argmin((buffer[held]/gross[held]))])
    return dict(valid=bool(np.all(buffer[held]>0)),held_hours=int(held.sum()),
                boundary_hours=int((held&(buffer<=0)).sum()),min_buffer_fraction_of_gross=float(buffer[worst]/gross[worst]),
                worst_hour_end=str(equity.index[worst]),worst_hour_nav_lower_usdt=float(floor[worst]),
                worst_hour_gross_upper_usdt=float(gross[worst]),worst_hour_requirement_usdt=float(required[worst]))



def exits_first(data,roster):
    """Simultaneous exits sell held longs before a short cover uses the released cash."""
    actions=data['actions']
    priority=np.ones(actions.shape,dtype=np.int8)
    for r in roster.itertuples():
        at=(r.exit_bar_ms-data['start'])//pair.H*3+(1 if r.exit_phase=='open' else 2)
        priority[at,r.slot]=0 if r.side==1 else 2
    return np.argsort(priority,axis=1,kind='stable')


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed shared margin scenario')
    source=EXP.parent/'exp-cloud-close-three-ratio-slots-20261109-v1'
    paths=pd.read_csv(source/'paths.csv.gz')
    if paths.event_id.nunique()!=398 or paths.slot.nunique()!=6:
        raise ValueError('Prior three-pair native leg roster changed')
    cases=dict(pair.fund(paths,paired,slip,directory=EXP,asset_label='asset_half',
                         pair_slots=3,margin_audit=cross_margin,order_sequence=exits_first)
               for paired in [True,False] for slip in [0.,.0005])
    (EXP/'summary.json').write_text(json.dumps(dict(funded_pairs=398,cases=cases,
        caveat='Shared cross-margin scenario on native trade-price H1 bounds. Actual venue mark history, tier changes, funding and exit depth not included.'),indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
