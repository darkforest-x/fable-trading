"""Fit and evaluate the monthly wick 3R score on the frozen first-pierce cohort.

The feature module sets each row's clock to its own signal candle close. Only
settled pre-split labels known by the split can fit preprocessing, coefficients
and cutoffs. Each other time period receives predictions from that same fit.
Run with ~/.local/bin/vectorbt-python -m yoyo.evaluation.monthweek_wick_score3r.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import sklearn
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from yoyo.evaluation.monthweek_wick_score_features import FEATURES, build_features

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-binance-monthweek-wick-score3r-20261116-v1'
BANDS = ['below50', '50to80', '80to90', 'top10']


def save_json(name, value):
    (EXP / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def matrix(rows, columns):
    return rows[list(columns)].astype('float64').replace([np.inf, -np.inf], np.nan)


def score_rows(rows, spec):
    """Compute probability and training-relative band at candle close for any feature frame."""
    names = spec['features']
    x = matrix(rows, names).fillna(dict(zip(names, spec['median'])))
    z = (x.to_numpy() - np.asarray(spec['mean'])) / np.asarray(spec['scale'])
    p = expit(z @ np.asarray(spec['coef']) + spec['intercept'])
    bands = []
    for h, value in zip(rows.tf, p):
        cuts = spec['band_thresholds'][str(int(h))]
        bands.append(BANDS[np.searchsorted(cuts, value, side='right')])
    return pd.DataFrame({'p3r': p, 'band': bands}, index=rows.index)


def fit_model(train, cfg, features=FEATURES):
    x = matrix(train, features)
    med = x.median()
    if med.isna().any():
        raise ValueError(f'No train coverage for {list(med[med.isna()].index)}')
    scaler = StandardScaler().fit(x.fillna(med))
    z = scaler.transform(x.fillna(med))
    lr = LogisticRegression(C=1, max_iter=2000, solver='lbfgs', tol=1e-7)
    lr.fit(z, train.outcome.eq('tp'))
    if lr.n_iter_[0] >= lr.max_iter:
        raise RuntimeError('Fit did not converge')
    p = lr.predict_proba(z)[:, 1]
    cuts = {str(int(h)): np.quantile(p[train.tf.eq(h)], cfg['reference_quantiles']).tolist()
            for h in sorted(train.tf.unique())}
    spec = dict(method='3R first-hit logistic probability', features=list(features), median=med.tolist(),
                mean=scaler.mean_.tolist(), scale=scaler.scale_.tolist(), coef=lr.coef_[0].tolist(),
                intercept=float(lr.intercept_[0]), band_thresholds=cuts, bands=BANDS,
                band_quantiles=cfg['reference_quantiles'], train_until_exclusive=cfg['train_until_exclusive'],
                train_n=len(train), train_hit_rate=float(train.outcome.eq('tp').mean()),
                sklearn=sklearn.__version__)
    np.testing.assert_allclose(score_rows(train, spec).p3r, p, atol=1e-12, rtol=0)
    return spec


def stats(group, base_rate=None):
    closed = group[group.outcome.isin(['tp', 'sl'])]
    wins = closed.outcome.eq('tp')
    row = dict(signals=len(group), settled=len(closed), tp=int(wins.sum()),
               sl=int((~wins).sum()), censored=int(group.outcome.eq('censored').sum()),
               behind=int(group.outcome.eq('behind_entry').sum()), mean_net_r=closed.net_r.mean(),
               settled_win_pct=100*wins.mean(), hit_pct_all=100*wins.sum()/len(group) if len(group) else np.nan)
    if base_rate is not None and len(closed):
        row.update(predicted_pct=100*closed.p3r.mean(),
                   auc=roc_auc_score(wins, closed.p3r) if wins.nunique()==2 else np.nan,
                   brier=brier_score_loss(wins, closed.p3r),
                   constant_brier=brier_score_loss(wins, np.full(len(closed), base_rate)))
    return row


def by_scope(test, base_rate):
    rows = []
    for period, year_frame in [('2025+', test), *test.groupby('year')]:
        for scope, subset in [('all', year_frame), *year_frame.groupby('tf')]:
            for band in ['all', *BANDS]:
                group = subset if band=='all' else subset[subset.band.eq(band)]
                rows.append(dict(period=str(period), tf=scope, band=band, **stats(group, base_rate)))
    return pd.DataFrame(rows)


def inference(test, cfg):
    """Stratified random selection tests and NY-month-cluster uncertainty on closed 3R labels."""
    settled = test[test.outcome.isin(['tp', 'sl'])].copy()
    settled['selected'] = settled.band.eq('top10')
    rng = np.random.default_rng(cfg['seed'])
    draws = cfg['draws']
    sim = np.zeros(draws)
    monthly = {}
    for (month, _, _), group in settled.groupby(['month', 'tf', 'side']):
        acc = monthly.setdefault(month, np.zeros(3))
        k = int(group.selected.sum())
        if not k:
            continue
        values = group.net_r.to_numpy()
        acc += [group.loc[group.selected, 'net_r'].sum(), k, k*values.mean()]
        if k == len(values):
            sim += values.sum()
            continue
        # Ranks of iid numbers select k distinct events uniformly per replicate.
        for a in range(0, draws, 256):
            b = min(draws, a+256)
            ids = np.argpartition(rng.random((b-a, len(values))), k-1, axis=1)[:, :k]
            sim[a:b] += values[ids].sum(axis=1)
    m = np.array(list(monthly.values()))
    net, n, expected = m.sum(axis=0)
    sim /= n
    boot = m[rng.integers(0, len(m), size=(draws, len(m)))].sum(axis=1)
    boot = boot[boot[:, 1] > 0]
    intervals = [np.quantile(v/boot[:, 1], [.025, .975]).tolist()
                 for v in [boot[:, 0], boot[:, 0]-boot[:, 2]]]
    p = float((1+np.sum(sim >= net/n-1e-12))/(draws+1))
    return dict(settled_high=int(n), high_mean_net_r=float(net/n), matched_signal_mean_net_r=float(expected/n),
                excess_net_r=float((net-expected)/n), one_sided_p=p, months=len(m),
                high_month_bootstrap_95=intervals[0], excess_month_bootstrap_95=intervals[1],
                draws=draws, passes_primary=bool(net>0 and p<.01))


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    parent = EXP.parent / cfg['parent']
    parent_receipt = json.loads((parent/'receipt.json').read_text())
    assert parent_receipt['cutoff_exclusive'] == cfg['cutoff_exclusive']
    features = build_features()
    assert features.event_id.is_unique and len(features)==parent_receipt['confirmed']
    labels = pd.read_csv(parent/'outcomes.csv.gz')
    target = cfg['target']
    outcomes = labels[(labels.cohort=='signal') & (labels.target==target)]
    fields = ['event_id','outcome','resolution','exit_ms','net_r','entry_ms']
    rows = features.merge(outcomes[fields], on='event_id', how='left', validate='one_to_one')
    entered = rows.status.eq('entered')
    assert entered.equals(rows.outcome.notna()) and entered.sum()==parent_receipt['entries']
    assert (rows.loc[entered,'entry_ms']==rows.loc[entered,'close_ms']).all()
    cutoff = int(pd.Timestamp(cfg['train_until_exclusive']).timestamp()*1000)
    settled = rows.outcome.isin(['tp','sl'])
    assert set(rows.loc[settled,'resolution']) <= {'1m','h1'}
    available = rows.exit_ms + np.where(rows.resolution.eq('1m'), 60_000, 3_600_000)
    earlier = rows.close_ms < cutoff
    train = rows[earlier & settled & (available<=cutoff)]
    spec = fit_model(train, cfg)
    rows = rows.join(score_rows(rows, spec))
    rows['split'] = np.where(earlier, 'pre_split_not_fit', 'out_of_time')
    rows.loc[train.index, 'split'] = 'train'
    rows['year'] = pd.to_datetime(rows.close_ms, unit='ms', utc=True).dt.year
    test = rows[~earlier & entered].copy()
    metrics = by_scope(test, spec['train_hit_rate'])
    inf = inference(test, cfg)
    high = test[test.band.eq('top10')]
    paired = high[['event_id','net_r']].merge(
        labels[(labels.target==target)&(labels.cohort=='random')][['event_id','net_r']],
        on='event_id', validate='one_to_one', suffixes=('_signal','_control')).dropna()
    assert len(paired) > 0
    ladder = labels[labels.cohort.eq('signal')].merge(
        test[['event_id','band']], on='event_id', validate='many_to_one')
    ladder_rows = []
    for t, g in ladder.groupby('target', sort=False):
        for band, s in [('all',g),('top10',g[g.band=='top10'])]:
            ladder_rows.append(dict(target=t, band=band, **stats(s)))
    rows = rows.sort_values(['close_ms','event_id'])
    rows.to_csv(EXP/'scores.csv.gz', index=False, compression={'method':'gzip','mtime':0})
    metrics.to_csv(EXP/'test_by_period_tf_band.csv', index=False)
    pd.DataFrame(ladder_rows).to_csv(EXP/'test_other_targets.csv', index=False)
    save_json('model.json', spec)
    summary = dict(parent=cfg['parent'], confirmed=len(rows), entered=int(entered.sum()), features=list(FEATURES),
                   missing_features=matrix(rows, FEATURES).isna().sum().to_dict(), train_n=len(train),
                   train_wins=int(train.outcome.eq('tp').sum()), pre_split_entries=int((entered & earlier).sum()),
                   purged_unmatured=int((earlier & settled & (available>cutoff)).sum()),
                   pre_split_censored=int((earlier & rows.outcome.eq('censored')).sum()),
                   test_entered=len(test), test_censored=int(test.outcome.eq('censored').sum()),
                   inference=inf, paired_high_3r=dict(n=len(paired), high_mean=paired.net_r_signal.mean(),
                   random_clock_mean=paired.net_r_control.mean()),
                   git_commit=subprocess.check_output(['git','log','-1','--format=%H','--',
                       __file__,str(Path(__file__).with_name('monthweek_wick_score_features.py')),str(EXP/'config.json')],
                       cwd=ROOT,text=True).strip(), production_eligible=False)
    save_json('summary.json', summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score', type=Path, metavar='FEATURES_CSV',
                        help='Apply the frozen model to a CSV with tf and its named close-time features.')
    args = parser.parse_args()
    if args.score:
        observations = pd.read_csv(args.score)
        scores = score_rows(observations, json.loads((EXP/'model.json').read_text()))
        keys = [k for k in ('event_id', 'tf', 'close_ms') if k in observations]
        print(observations[keys].join(scores).to_csv(index=False), end='')
    else:
        main()
