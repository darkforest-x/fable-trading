"""Validate the near-edge ledger and export ex-post winners from model training only.

Read the existing 3R roster to verify that only the exit target changed.
Use the end of the resolving bar to date label availability. R uses the
original cash risk before fees. P&L per 100 risk units uses constant initial
risk and is independent of the coin price.
"""
from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-binance-monday-wick-near-netr-20261116-v1'
PARENT = EXP.parent / 'exp-binance-monday-wick-score3r-20261116-v1'


def verify_ledger(labels, events):
    events = events.loc[events.status.eq('entered')].copy()
    events['near'] = np.where(events.side.eq(1), events.week_low, events.week_high)
    eligible = events.loc[events.side * (events.near - events.entry) > 1e-12 * events.entry]
    eligible = eligible.set_index('event_id')
    rows = labels.loc[labels.outcome.isin(['tp','sl','censored'])].copy().set_index('event_id')
    if not rows.index.is_unique or not eligible.index.is_unique or set(rows.index) != set(eligible.index):
        raise ValueError('Near-target executions must reconcile to the eligible frozen entries')
    e = eligible.loc[rows.index]
    for col, expected in [('entry',e.entry), ('stop',e.stop), ('target_price',e.near),
                          ('close_ms',e.close_ms), ('side',e.side), ('tf',e.tf)]:
        np.testing.assert_allclose(rows[col], expected, rtol=1e-12, atol=0)
    if (rows.loc[rows.outcome.eq('censored'),'net_r'].notna()).any():
        raise ValueError('Unresolved outcomes cannot have realized net R')
    done = rows[rows.outcome.isin(['tp','sl'])]
    risk = (done.entry - done.stop).abs()
    exit_rate = np.where(done.outcome.eq('tp'), .0002, .0005)
    net = (done.side * (done.exit_price - done.entry) - done.entry * .0005 - done.exit_price * exit_rate) / risk
    np.testing.assert_allclose(done.net_r, net, rtol=1e-8, atol=1e-8)
    rows['known_ms'] = rows.exit_ms + np.where(rows.resolution.str.startswith('1m', na=False), 60000, 3600000)
    return rows.reset_index()


def export_winners():
    cfg = json.loads((EXP/'config.json').read_text())
    labels = pd.read_csv(EXP/'outcomes_near.csv.gz')
    events = pd.read_csv(PARENT/'first_breaches.csv.gz')
    rows = verify_ledger(labels, events)
    cutoff = pd.Timestamp(cfg['test_start']).value // 1000000
    train = rows[rows.outcome.isin(['tp','sl']) & rows.close_ms.lt(cutoff) & rows.known_ms.le(cutoff)].copy()
    train['winner_cutoff_net_r'] = train.groupby('tf').net_r.transform('quantile', q=cfg['winner_quantile'])
    win = train[train.net_r.gt(0) & train.net_r.ge(train.winner_cutoff_net_r)].copy()
    win['signal_close_utc'] = pd.to_datetime(win.close_ms, unit='ms', utc=True)
    win['resolved_bar_open_utc'] = pd.to_datetime(win.exit_ms, unit='ms', utc=True)
    win['net_per_100_risk'] = 100 * win.net_r
    fields = ['tf','symbol','week','side','signal_close_utc','resolved_bar_open_utc',
              'entry','stop','target_price','exit_price','winner_cutoff_net_r','net_r','net_per_100_risk','event_id']
    win = win.sort_values(['tf','net_r','event_id'], ascending=[True,False,True])
    win[fields].to_csv(EXP/'training_big_winners.csv', index=False)
    print('All near targets, eligibility and net fees verified. Training big winners:')
    print(win.groupby('tf').net_r.agg(['count','min','median','max']).to_string())


def compare_score_to_target_distance():
    """Resample the same NY weeks for both frozen filters, retaining overlap."""
    scores = pd.read_csv(EXP/'test_scores.csv.gz')
    cfg = json.loads((EXP/'config.json').read_text())
    results = {}
    for tf, group in scores[scores.outcome.isin(['tp','sl'])].groupby('tf'):
        model = group.band.eq('top10')
        simple = group.baseline_band.eq('top10')
        totals = group.assign(
            model_net=group.net_r*model, model_count=model.astype(int),
            simple_net=group.net_r*simple, simple_count=simple.astype(int),
        ).groupby('week')[['model_net','model_count','simple_net','simple_count']].sum().to_numpy()
        rng = np.random.default_rng(cfg['seed'])
        sample = rng.integers(0, len(totals), size=(cfg['draws'],len(totals)))
        summed = totals[sample].sum(axis=1)
        summed = summed[(summed[:,1]>0) & (summed[:,3]>0)]
        differences = summed[:,0]/summed[:,1] - summed[:,2]/summed[:,3]
        results[str(int(tf))] = dict(
            model_n=int(model.sum()), distance_n=int(simple.sum()),
            overlap=int((model & simple).sum()),
            mean_net_r_difference=float(group[model].net_r.mean()-group[simple].net_r.mean()),
            paired_week_bootstrap_95=np.quantile(differences,[.025,.975]).tolist(),
            weeks=len(totals), draws=cfg['draws'],
        )
    (EXP/'baseline_comparison.json').write_text(json.dumps(results,indent=2)+'\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', action='store_true', help='Compare both frozen 2026 score filters.')
    if parser.parse_args().compare:
        compare_score_to_target_distance()
    else:
        export_winners()
