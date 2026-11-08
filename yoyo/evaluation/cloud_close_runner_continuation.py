"""Describe the observed continuation value of fully running historical 12H positions after 2R."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_efficient_trend as study

EXP = study.EXP


def describe(frame):
    changed = frame[frame.gain.abs().gt(1e-10)].copy()
    up = changed[changed.gain.gt(0)].sort_values('gain',ascending=False)
    down = changed[changed.gain.lt(0)]
    return dict(funded=len(frame),after_target_diverged=len(changed),full_exit_better=len(up),partial_better=len(down),
                identical=len(frame)-len(changed),mean_gain_pp=float(100*frame.gain.mean()),
                median_gain_after_divergence_pp=float(100*changed.gain.median()) if len(changed) else None,
                top_1_share_of_positive_pct=float(100*up.gain.head(1).sum()/up.gain.sum()) if len(up) else None,
                top_10_share_of_positive_pct=float(100*up.gain.head(10).sum()/up.gain.sum()) if len(up) else None,
                top_ten_switched_to_full_by_live_rule=int(up.head(10).switched.sum()),
                biggest=[dict(symbol=r.symbol,entry_utc=pd.Timestamp(r.entry_ms,unit='ms',tz='UTC').isoformat(),gain_pp=float(100*r.gain))
                         for r in up.head(5).itertuples()])


def main():
    dest = EXP/'continuation_labels.json'
    if dest.exists():
        raise FileExistsError('Historical continuation labels already summarized')
    result = {}
    for venue in ('OKX','Binance'):
        base = study.repair.EXP/'thirds' if venue=='OKX' else study.thirds.EXP
        funded = pd.read_csv(base/f'funded_{venue}.csv.gz',dtype={'symbol':str}).set_index('event_id')
        choices = pd.read_csv(EXP/f'decisions_{venue}.csv.gz',usecols=['event_id','choice']).set_index('event_id')
        paths = study.thirds.okx_paths() if venue=='OKX' else study.binance.assembled(set(study.binance.patch.affected_tickers()))
        paths = paths[~paths.is_control & paths.event_id.isin(funded.index)].copy()
        # Source native fills already fix every exit price and fee. This is an outcome label only.
        paths['net'] = (paths.side*(paths.exit_price-paths.entry_price)-study.thirds.wallet.TAKER*paths.entry_price
                        -paths.exit_fee_rate*paths.exit_price)/paths.entry_price
        legs = paths.pivot(index='event_id',columns='tp_r',values='net').reindex(funded.index)
        if not np.allclose((legs[0]+2*legs[2])/3,funded.net_return,rtol=1e-8,atol=1e-10):
            raise ValueError('Original unit returns do not reconcile with the funded parent')
        funded['gain'] = legs[0]-funded.net_return
        funded['switched'] = choices.loc[funded.index,'choice'].eq('full_runner')
        years = pd.to_datetime(funded.entry_ms,unit='ms',utc=True).dt.year
        result[venue] = {label:describe(funded.loc[mask]) for label,mask in [('all',years.notna()),('through_2024',years.le(2024)),
                                                                            ('2025',years.eq(2025)),('2026',years.eq(2026))]}
    result['units'] = 'Percentage-point change in equal-initial-notional net return if the original fully held runner replaces the two-thirds partial. Frozen native paths and the actual funded roster only. No account NAV simulation, entry ranking, decision-time feature or future selection rule uses these realized labels.'
    dest.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    for venue in ('OKX','Binance'):
        print(venue,{k:v for k,v in result[venue]['all'].items() if k!='biggest'})


if __name__=='__main__':
    main()
