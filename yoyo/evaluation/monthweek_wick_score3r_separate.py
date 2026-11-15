"""Three independent monthly-wick models, each fit and used within its chosen timeframe.

The frozen v1 rows supply close-time features and the original VectorBT 3R outcomes.
All training statistics, feature selection (constant removal only) and thresholds are
estimated within one timeframe. Earlier predictions are retained for comparison only.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import monthweek_wick_score3r as base

EXP = base.EXP.parent / 'exp-binance-monthweek-wick-score3r-20261116-v2'


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def score_timeframes(observations, models):
    """Route each row to its own model, preserving row order and returning p3r and band."""
    if observations.empty or not observations.index.is_unique:
        raise ValueError('Expected a nonempty table with unique row indices')
    clocks = pd.to_numeric(observations.tf, errors='raise')
    if not clocks.isin(models).all():
        raise ValueError('Each row needs a supported timeframe (1, 4, or 12)')
    parts = []
    for tf, group in observations.groupby(clocks, sort=False):
        model = models[tf]
        if model['timeframe_hours'] != tf or set(model['band_thresholds']) != {str(int(tf))}:
            raise ValueError('Wrong timeframe model')
        parts.append(base.score_rows(group.assign(tf=tf), model))
    return pd.concat(parts).reindex(observations.index)


def train():
    cfg = json.loads((EXP/'config.json').read_text())
    old = EXP.parent / cfg['parent']
    options = json.loads((old/'config.json').read_text())
    assert cfg['target'] == options['target'] == '3R'
    rows = pd.read_csv(old/'scores.csv.gz', float_precision='round_trip').rename(
        columns={'p3r': 'pooled_p3r', 'band': 'pooled_band'})
    assert rows.event_id.is_unique and set(rows.tf.unique()) == set(cfg['timeframes'])
    boundary = int(pd.Timestamp(options['train_until_exclusive']).timestamp()*1000)
    entered = rows.status.eq('entered')
    settled = rows.outcome.isin(['tp', 'sl'])
    pre = rows.close_ms < boundary
    assert set(rows.loc[settled, 'resolution']) <= {'h1','1m'}
    known = rows.exit_ms + np.where(rows.resolution.eq('1m'), 60_000, 3_600_000)
    eligible = pre & entered & settled & (known <= boundary)
    assert eligible.equals(rows.split.eq('train'))
    assert (~pre).equals(rows.split.eq('out_of_time'))

    models = {}
    for h in cfg['timeframes']:
        sample = rows.loc[eligible & rows.tf.eq(h)]
        counts = base.matrix(sample, base.FEATURES).nunique()
        if (counts == 0).any():
            raise ValueError('Unavailable feature in timeframe training set')
        names = counts[counts > 1].index.to_list()
        model = base.fit_model(sample, options, names)
        model.update(timeframe_hours=h, dropped_constants=counts[counts==1].index.to_list(),
                     train_hits=int(sample.outcome.eq('tp').sum()))
        models[h] = model

    scores = rows.join(score_timeframes(rows, models))
    results, checks = [], {}
    for h, model in models.items():
        test = scores.loc[~pre & entered & scores.tf.eq(h)]
        for period, group in [('2025+', test), *test.groupby('year')]:
            for band in ['all', *base.BANDS]:
                subset = group if band=='all' else group[group.band.eq(band)]
                results.append(dict(tf=h, period=str(period), band=band,
                                    **base.stats(subset, model['train_hit_rate'])))
        checks[str(h)] = base.inference(test, options)
        pooled = test[test.pooled_band.eq('top10')]
        checks[str(h)]['previous_top10'] = base.stats(pooled)
        checks[str(h)]['overlap_high_signals'] = int((test.band.eq('top10') & test.pooled_band.eq('top10')).sum())
        checks[str(h)]['train_n'] = model['train_n']
        checks[str(h)]['train_hits'] = model['train_hits']
        checks[str(h)]['train_p90'] = model['band_thresholds'][str(h)][2]

    for h, model in models.items():
        write_json(EXP/f'model_{h}h.json', model)
    cols = ['event_id','symbol','month','tf','side','close_ms','status','split','outcome','net_r',
            'p3r','band','pooled_p3r','pooled_band']
    scores[cols].to_csv(EXP/'scores.csv.gz', index=False, compression={'method':'gzip','mtime':0})
    pd.DataFrame(results).to_csv(EXP/'test_by_period_tf_band.csv', index=False)
    summary = dict(parent=cfg['parent'], target=cfg['target'], confirmed=len(rows),
                   entered=int(entered.sum()), train_n=int(eligible.sum()), test_n=int((~pre & entered).sum()),
                   parameters='L2 logistic, C=1, independently fit per timeframe',
                   tests=checks, cutoff=options['cutoff_exclusive'], production_eligible=False,
                   builder=subprocess.check_output(['git','log','-1','--format=%H','--',
                           __file__,str(EXP/'config.json')],cwd=base.ROOT,text=True).strip())
    write_json(EXP/'summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score', type=Path, metavar='FEATURES_CSV',
                        help='Score observed rows with their corresponding frozen model.')
    args = parser.parse_args()
    if args.score:
        observations = pd.read_csv(args.score, float_precision='round_trip')
        models = {h: json.loads((EXP/f'model_{h}h.json').read_text()) for h in (1,4,12)}
        result = score_timeframes(observations, models)
        keys = [k for k in ('event_id','tf','close_ms') if k in observations]
        print(observations[keys].join(result).to_csv(index=False), end='')
    else:
        train()
