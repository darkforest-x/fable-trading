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


def finalize():
    """Combine native subhourly bounds and frozen funded outcomes without changing orders."""
    summary_path = EXP/'summary.json'
    summary = json.loads(summary_path.read_text())
    okx = json.loads((EXP/'okx_refinement.json').read_text())
    bn = json.loads((EXP/'binance_refinement.json').read_text())
    sensitivity = json.loads((EXP/'largest_event_sensitivity.json').read_text())
    table = pd.read_csv(EXP/'account_metrics.csv')
    actual = table[table.scenario.eq('actual') & table.policy.eq('trend_efficiency')].set_index(['venue','period'])
    bounds = {
        'OKX': {'all': (okx['observed_lower_pct'],okx['refined_upper_pct']),
                'dev': (okx['dev_observed_pct'],okx['dev_upper_pct'])},
        'Binance': {p: (bn['periods'][name]['observed_lower_pct'],bn['periods'][name]['refined_upper_pct'])
                    for p,name in [('all','full'),('dev','development')]},
    }
    for venue,periods in bounds.items():
        record = summary[venue]
        source_nav = okx['terminal_usdt'] if venue=='OKX' else bn['baseline_h1_metrics']['full']['final_usdt']
        terminal = float(actual.loc[(venue,'all'),'final_usdt'])
        if not np.isclose(source_nav,terminal,rtol=0,atol=1e-5):
            raise ValueError(f'{venue} drawdown evidence came from a different account')
        for period,(lower,upper) in periods.items():
            if not 0 <= lower <= upper <= 100:
                raise ValueError(f'{venue} invalid drawdown interval')
            row = actual.loc[(venue,period)].to_dict()
            field = 'full' if period=='all' else period
            record.setdefault(f'{field}_hourly_only_pass',record[f'{field}_pass'])
            record[f'{field}_drawdown_pct'] = [lower,upper]
            record[f'{field}_pass'] = second.meets(dict(row,dd_bound_pct=upper))
        record['terminal_usdt'] = terminal
        record['net_profit_multiple'] = (terminal-1000)/1000
        record['terminal_replacing_only_ada_exit_usdt'] = sensitivity[venue]['metrics']['all']['final_usdt']
        record['meets_target'] = bool(record['full_pass'] and record['dev_pass'] and terminal>1001000)
    summary['both_venues_pass_risk_and_payoff'] = all(summary[v]['full_pass'] and summary[v]['dev_pass'] for v in bounds)
    summary['both_venues_reach_target'] = all(summary[v]['meets_target'] for v in bounds)
    summary_path.write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--finalize',action='store_true')
    args = parser.parse_args()
    if args.finalize:
        finalize()
    else:
        main()
