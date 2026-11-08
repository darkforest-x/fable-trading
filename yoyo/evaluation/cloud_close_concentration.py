"""Replay funded 12H accounts after excluding those names that contributed most through 2024."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import repair_io
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_side_exit as asymmetric
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-concentration-20261109-v1'


def main(output_dir=None):
    output_dir = repair_io.new_output_dir(
        output_dir, root=wallet.ROOT, historical_dir=EXP,
        artifacts=('account_metrics.csv','summary.json'), description=__doc__)
    cfg = json.loads((EXP/'config.json').read_text())
    paths = wallet.read_paths()
    paths = paths[~paths.is_control & paths.tp_r.isin([0,2])].copy()
    rank = leader.leadership().dropna()
    event_clock = pd.MultiIndex.from_frame(paths[['entry_ms','symbol']])
    paths = paths[event_clock.isin(rank.index)].copy()
    if paths.duplicated(['event_id','tp_r']).any() or not paths.groupby('event_id').size().eq(2).all():
        raise ValueError('Eligible native events must have one complete tp0/tp2 pair')
    source = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    source.index = pd.RangeIndex(1,len(source)+1)
    paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    rows, exclusions = [], {}
    for name in cfg['parents']:
        both = name == 'partial_both'
        folder = leader.EXP if both else asymmetric.EXP
        funded = pd.read_csv(folder/('funded_1.0.csv.gz' if both else 'funded_shorts_only.csv.gz'),dtype={'symbol':str})
        dev = funded[funded.exit_bar_ms + wallet.HOUR < wallet.SPLIT]
        ranked = dev.groupby('symbol').cash_pnl.sum().sort_index().sort_values(ascending=False,kind='stable')
        if not ranked.iloc[:max(cfg['counts'])].gt(0).all():
            raise ValueError('Fewer profitable contributors than the stress requests')
        exclusions[name] = {symbol:cash for symbol,cash in ranked.head(max(cfg['counts'])).items()}
        baseline = pd.read_csv(folder/'account_metrics.csv')
        if both:
            base = baseline[baseline.top_fraction.eq(1)].drop(columns='top_fraction')
        else:
            base = baseline[baseline.policy.eq('shorts_only')].drop(columns='policy')
        rows.extend(base.assign(parent=name,excluded=0).to_dict('records'))
        for count in cfg['counts']:
            selected = paths[~paths.symbol.isin(ranked.index[:count])]
            result, positions, equity = (capacity.run(selected,9,.75) if both else asymmetric.account(selected,-1))
            if not np.isclose(1000+positions.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8):
                raise ValueError('Excluded-asset account did not reconcile')
            rows.extend(dict(parent=name,excluded=count,period=p,**m) for p,m in result.items())
            gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(output_dir/'account_metrics.csv',index=False)
    summary = dict(experiment_id=cfg['id'], output_dir=str(output_dir),
                   eligible_actual_events=int(paths.event_id.nunique()),
                   development_contributors_usdt=exclusions)
    (output_dir/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))
    return dict(output_dir=str(output_dir), account_metrics=table, summary=summary)


if __name__=='__main__':
    main()
