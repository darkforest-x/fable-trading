"""Require a chart-native V1.7 H1 Stoch marker inside the confirmed 12H entry key."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import bb_stoch_parameter_replay as stoch
from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market
from yoyo.evaluation.signal_center_score import stop_pct

wallet = thirds.wallet
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-hourly-stoch-20261109-v1'


def scan(clock, prices, group):
    """Look up H1 high/low/close, 5/3/3 Stoch and default weekday in each just-closed 12H key."""
    frame = pd.DataFrame(prices, index=clock, columns=['open','high','low','close'])
    arrows = stoch._stoch_arrows(frame, stoch.ParamSpec())
    weekday = pd.to_datetime(clock,unit='ms',utc=True).tz_convert('America/New_York').weekday.to_numpy()
    timestamps = group.entry_ms.to_numpy()[:,None] + wallet.HOUR*np.arange(-21,0)
    locations = np.searchsorted(clock,timestamps).clip(0,len(clock)-1)
    complete = (clock[locations]==timestamps).all(axis=1)
    ix = locations[:,-12:]
    known = np.isfinite(arrows.d.to_numpy()[locations[:,-13:]]).all(axis=1)
    complete &= known
    long = group.side.to_numpy()[:,None] == 1
    cross = np.where(long,arrows.arrow_long.to_numpy()[ix],arrows.arrow_short.to_numpy()[ix])
    edge = group.extension.to_numpy()[:,None]
    extreme = np.where(long,prices[ix,2],prices[ix,1])
    outside = np.where(long,extreme<edge,extreme>edge) & ~np.isclose(extreme,edge,rtol=1e-12,atol=0)
    marked = cross & outside & (weekday[ix]>=1) & (weekday[ix]<=4)
    return pd.DataFrame({'covered':complete,'markers':np.where(complete,marked.sum(axis=1),-1)},index=group.index)


def screen(venue,paths):
    rows = paths[paths.tp_r.eq(0)].copy()
    parents = rows[~rows.is_control].set_index('parent_id')
    if venue=='OKX':
        signals = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['reference_entry'],float_precision='round_trip')
        signals.index = np.arange(1,len(signals)+1)
        levels = parents.source_row_number.map(signals.reference_entry)
    else:
        levels = parents.stop_price/(1-parents.side*parents.symbol.map(stop_pct))
    rows = rows[rows.parent_id.isin(parents.index)].copy()
    rows['extension'] = rows.parent_id.map(levels)
    if not (np.isfinite(rows.extension) & rows.extension.gt(0)).all():
        raise ValueError('Missing funded parent Monday boundary')
    quotes = wallet.quotes if venue=='OKX' else binance.quotes
    labels = pd.concat([scan(*quotes(symbol),group) for symbol,group in rows.groupby('symbol')])
    rows = rows.join(labels)
    cols = ['event_id','parent_id','symbol','side','entry_ms','is_control','extension','covered','markers']
    rows[cols].to_csv(EXP/f'events_{venue}.csv.gz',index=False)
    funded = paths[paths.event_id.isin(rows.loc[rows.markers.gt(0),'event_id'])]
    real = funded[~funded.is_control]
    control = funded[funded.is_control & funded.parent_id.isin(real.parent_id)]
    return real,control,dict(observed=int(rows.loc[~rows.is_control,'covered'].sum()),parents=len(parents),
                             passing=len(real)//2,matched=len(control)//2)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed H1 marker experiment exists')
    baseline = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    records = baseline[baseline.scenario.eq('actual')].assign(policy='normal_partial').to_dict('records')
    summary = {}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
        real,control,counts = screen(venue,paths)
        metrics,funded,equity = market.fund(venue,real,0.)
        funded.to_csv(EXP/f'funded_{venue}.csv.gz')
        equity.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        records.extend(dict(policy='hourly_marker',venue=venue,scenario='actual',period=p,**m) for p,m in metrics.items())
        summary[venue] = dict(**counts,development_pass=second.meets(metrics['dev']),full_pass=second.meets(metrics['all']),
                              full=metrics['all'])
        if summary[venue]['development_pass']:
            for tag,pool,slip in [('paired',real[real.parent_id.isin(control.parent_id)],0.),('control',control,0.),('stress',real,.0005)]:
                if pool.empty:
                    continue
                additional = market.fund(venue,pool,slip)[0]
                records.extend(dict(policy='hourly_marker',venue=venue,scenario=tag,period=p,**m) for p,m in additional.items())
        del paths,real,control,funded,equity
        gc.collect()
    pd.DataFrame(records).to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__=='__main__':
    main()
