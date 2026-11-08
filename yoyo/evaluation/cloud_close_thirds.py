"""Fund the same two-thirds partial and one-third original cloud runner on both venues."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT/'experiments/active/exp-cloud-close-thirds-20261109-v1'


def okx_paths():
    paths = wallet.read_paths()
    ranked = pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(leader.leadership().index)
    paths = paths[paths.tp_r.isin([0,2]) & ranked].copy()
    parent = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    parent.index = pd.RangeIndex(1,len(parent)+1)
    paths['entry_order'] = paths.source_row_number.map(parent.prior_atr_pct).fillna(np.inf)
    return paths


def run_venue(venue, paths, output_dir):
    real = paths[~paths.is_control]
    ctl = paths[paths.is_control & paths.parent_id.isin(real.parent_id)]
    matched = real[real.parent_id.isin(ctl.parent_id)]
    counts = dict(actual=len(real)//2, pairs=len(ctl)//2)
    rows = []
    for name, events, slip in [('actual',real,0.),('paired',matched,0.),('control',ctl,0.),('stress',real,.0005)]:
        if venue == 'OKX':
            metrics, trades, equity = capacity.run(events,9,2/3,slip)
        else:
            metrics, trades, equity = scaleout.simulate(repaired.prepare(events),2/3,slip)
        rows.extend(dict(venue=venue,scenario=name,period=p,**m) for p,m in metrics.items())
        if name == 'actual':
            if not np.isclose(1000+trades.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8):
                raise ValueError(f'{venue}: funded cash does not reconcile')
            trades.to_csv(output_dir/f'funded_{venue}.csv.gz')
            equity.to_csv(output_dir/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        gc.collect()
    return pd.DataFrame(rows), counts


def write_results(table, counts, output_dir):
    table.to_csv(output_dir/'account_metrics.csv',index=False)
    qualifies = {}
    for period in ['dev','all']:
        selected = table[table.period.eq(period)&table.scenario.eq('actual')]
        qualifies[period] = dict(zip(selected.venue,
            selected.apply(wallet.qualifies,axis=1) & selected.payoff_usdt.ge(2.5) & selected.trades.ge(100)))
    (output_dir/'summary.json').write_text(json.dumps(dict(fraction=2/3,counts=counts,qualifies=qualifies),
                                                       indent=2,allow_nan=False)+'\n')


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen experiment exists. Use cloud_close_eligibility_repair for corrected OKX results.')
    tables, counts = [], {}
    for venue in ['OKX','Binance']:
        paths = okx_paths() if venue == 'OKX' else repaired.assembled(set(repaired.patch.affected_tickers()))
        table, counts[venue] = run_venue(venue,paths,EXP)
        tables.append(table)
    write_results(pd.concat(tables,ignore_index=True),counts,EXP)


if __name__=='__main__':
    main()
