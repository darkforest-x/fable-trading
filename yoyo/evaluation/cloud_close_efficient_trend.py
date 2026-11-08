"""Select the original V1.7 cloud runner on a historically persistent, side-aligned trend."""
import argparse
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_exit_rotation as rotation
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = thirds.wallet.ROOT/'experiments/active/exp-cloud-close-efficient-trend-20261109-v1'


def efficiency(bars):
    """Use only 21 consecutive pre-key close observations ending twelve hours before funding."""
    prices = np.log(bars.close)
    path_length = prices.diff().abs().rolling(20,min_periods=20).sum()
    continuous = bars.index.to_series().diff(20).eq(20*leader.BAR)
    net = prices.diff(20).div(path_length.where(path_length.gt(0))).where(continuous)
    net.index = net.index + 2*leader.BAR
    return net


def inputs(venue, paths):
    events = paths.loc[paths.tp_r.eq(0),['event_id','parent_id','symbol','side','entry_ms','is_control']].copy()
    series = []
    for symbol,group in events.groupby('symbol'):
        feature = efficiency(market.market_bars(venue,symbol))
        series.append(pd.Series(feature.reindex(group.entry_ms).to_numpy()*group.side.to_numpy(),index=group.index))
    events['directional_efficiency'] = pd.concat(series).reindex(events.index)
    events['choice'] = np.where(events.directional_efficiency.gt(.5),'full_runner','partial_2_3')
    return events,rotation._rotated_paths(paths,events)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Frozen trend-efficiency experiment already evaluated')
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = old[old.scenario.eq('actual')].assign(policy='normal_partial').to_dict('records')
    summary = {}
    for venue in ('OKX','Binance'):
        if venue=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
        events,new = inputs(venue,paths)
        events.to_csv(EXP/f'decisions_{venue}.csv.gz',index=False)
        true = events[~events.is_control]
        full_ids = set(true[true.choice.eq('full_runner')].event_id)
        actual = new[~new.is_control]
        control = new[new.is_control & new.parent_id.isin(actual.parent_id)]
        original,_ = thirds.wallet.admit(paths[~paths.is_control & paths.tp_r.eq(0)],9)
        summary[venue] = dict(real=len(true),full_runner_events=len(full_ids),unknown=int(true.directional_efficiency.isna().sum()))
        for tag,sample,slip in [('actual',actual,0.),('paired',actual[actual.parent_id.isin(control.parent_id)],0.),
                                ('control',control,0.),('stress',actual,.0005)]:
            metrics,tr,eq = market.fund(venue,sample,slip)
            rows.extend(dict(venue=venue,policy='trend_efficiency',scenario=tag,period=p,**m) for p,m in metrics.items())
            if tag=='actual':
                if set(original.event_id)!=set(tr.index):
                    raise ValueError('Changing only a native partial exit changed the funded event roster')
                summary[venue].update(funded_full_runner=len(full_ids&set(tr.index)),dev_pass=second.meets(metrics['dev']),
                                      full_pass=second.meets(metrics['all']))
                tr.to_csv(EXP/f'funded_{venue}.csv.gz')
                eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
            del tr,eq
            gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')][['venue','policy','scenario','final_usdt','trades','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
