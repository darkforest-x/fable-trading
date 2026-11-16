"""Train time-split Monday-wick scores for realised net R to the near edge.

The frozen weekly first-breach cohort and its close-time causal features are
reused from ``exp-binance-monday-wick-score3r-20261116-v1``.  The target and
labels are specific to ``exp-binance-monday-wick-near-netr-20261116-v1``.
Each timeframe is fitted and selected independently.  Label maturity uses the
same exit-resolution purge as the original weekly-wick score builder.

Run with ``.venv/bin/python -u -m yoyo.evaluation.monday_weekly_wick_near_train``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from time import perf_counter

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from yoyo.evaluation import monthweek_wick_score3r as base
from yoyo.evaluation.monday_weekly_wick_features import FEATURES


ROOT = base.ROOT
EXP = ROOT / 'experiments/active/exp-binance-monday-wick-near-netr-20261116-v1'
PARENT = ROOT / 'experiments/active/exp-binance-monday-wick-score3r-20261116-v1'
FEATURE_FILE = PARENT / 'monday_weekly_wick_features.csv.gz'
BREACH_FILE = PARENT / 'first_breaches.csv.gz'
OUTCOME_FILE = EXP / 'outcomes_near.csv.gz'
MODEL_FEATURES = tuple(FEATURES) + ('near_net_r',)
SETTLED = ('tp', 'sl')
ELIGIBLE = ('tp', 'sl', 'censored')
RESOLUTION_MS = {
    'h1': 3_600_000,
    'h1_fallback': 3_600_000,
    '1m': 60_000,
    '1m_stop_first': 60_000,
}


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def year_start(year: int) -> int:
    return int(pd.Timestamp(f'{year}-01-01', tz='UTC').timestamp() * 1000)


def load_rows(cfg: dict) -> pd.DataFrame:
    """Join the frozen causal feature rows to near-edge outcomes and metadata.

    ``near_net_r`` is known at the signal close. Its entry proxy is the signal
    candle close, its stop is the signal wick tip extended 1% outward, and its
    target is the near Monday edge. It deliberately does not use the actual
    next-H1 entry from ``outcomes_near.csv.gz`` or ``first_breaches.csv.gz``.
    """
    features = pd.read_csv(FEATURE_FILE)
    breaches = pd.read_csv(BREACH_FILE)
    outcomes = pd.read_csv(OUTCOME_FILE)
    for name, frame in (('features', features), ('first_breaches', breaches), ('outcomes_near', outcomes)):
        if 'event_id' not in frame or frame.event_id.duplicated().any():
            raise ValueError(f'{name} must have unique event_id values')
    if not set(outcomes.outcome.dropna()).issubset(ELIGIBLE):
        raise ValueError('outcomes_near contains a status outside tp/sl/censored')
    if not set(outcomes.event_id).issubset(set(features.event_id)):
        raise ValueError('outcomes_near contains an event absent from frozen features')

    meta_cols = ['event_id', 'week_high', 'week_low', 'rail', 'tip', 'bar_close']
    metadata = breaches[meta_cols].copy()
    rows = features.merge(metadata, on='event_id', how='left', validate='one_to_one')
    if rows[['week_high', 'week_low', 'rail', 'tip', 'bar_close']].isna().any().any():
        raise ValueError('Some frozen feature rows lack first-breach close metadata')
    # These identity columns must agree across the frozen feature/metadata pair.
    check = features[['event_id', 'week', 'side', 'tf', 'close_ms']].merge(
        breaches[['event_id', 'week', 'side', 'tf', 'close_ms']],
        on='event_id', suffixes=('_feature', '_breach'), validate='one_to_one')
    if not np.array_equal(check.week_feature.astype(str), check.week_breach.astype(str)):
        raise ValueError('Frozen feature and breach metadata disagree on week')
    for col in ('side', 'tf', 'close_ms'):
        left = pd.to_numeric(check[f'{col}_feature'], errors='raise').to_numpy()
        right = pd.to_numeric(check[f'{col}_breach'], errors='raise').to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f'Frozen feature and breach metadata disagree on {col}')

    outcome_cols = ['event_id', 'outcome', 'net_r', 'exit_ms', 'resolution',
                    'target_price', 'entry', 'stop']
    missing = set(outcome_cols).difference(outcomes.columns)
    if missing:
        raise ValueError(f'outcomes_near missing fields: {sorted(missing)}')
    rows = rows.merge(outcomes[outcome_cols], on='event_id', how='left', validate='one_to_one')
    rows['close_ms'] = pd.to_numeric(rows.close_ms, errors='raise').astype('int64')
    rows['exit_ms'] = pd.to_numeric(rows.exit_ms, errors='coerce')
    rows['net_r'] = pd.to_numeric(rows.net_r, errors='coerce')
    rows['tf'] = pd.to_numeric(rows.tf, errors='raise').astype('int64')
    rows['side'] = pd.to_numeric(rows.side, errors='raise').astype('int64')
    if set(rows.tf.unique()) != {1, 4, 12}:
        raise ValueError(f'Unexpected timeframes: {sorted(rows.tf.unique())}')

    settled = rows.outcome.isin(SETTLED)
    duration = rows.resolution.map(RESOLUTION_MS)
    if duration[settled].isna().any():
        raise ValueError('Settled outcome has an unknown resolution')
    rows['known_ms'] = np.nan
    rows.loc[settled, 'known_ms'] = (
        rows.loc[settled, 'exit_ms'] + duration[settled]
    )
    if rows.loc[settled, 'known_ms'].isna().any():
        raise ValueError('Settled outcome lacks exit_ms')
    if (rows.loc[settled, 'known_ms'] < rows.loc[settled, 'close_ms']).any():
        raise ValueError('Outcome is known before its signal close')

    side = rows.side.astype('float64')
    proxy_entry = pd.to_numeric(rows.bar_close, errors='raise').astype('float64')
    tip = pd.to_numeric(rows.tip, errors='raise').astype('float64')
    stop = tip * (1.0 - side * 0.01)
    target = np.where(side.eq(1), rows.week_low.astype('float64'), rows.week_high.astype('float64'))
    risk = side * (proxy_entry - stop)
    entry_fee = proxy_entry * float(cfg['fees']['entry_taker'])
    target_fee = target * float(cfg['fees']['tp_exit_maker'])
    net_pnl = side * (target - proxy_entry) - entry_fee - target_fee
    with np.errstate(divide='ignore', invalid='ignore'):
        potential = net_pnl / risk
    potential[~np.isfinite(potential) | (proxy_entry <= 0) | (target <= 0) | (risk <= 0)] = np.nan
    rows['near_net_r'] = potential
    return rows.sort_values(['close_ms', 'event_id']).reset_index(drop=True)


def matrix(rows: pd.DataFrame, columns) -> pd.DataFrame:
    return rows[list(columns)].astype('float64').replace([np.inf, -np.inf], np.nan)


def mature_rows(rows: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    """Return settled labels whose exits were known no later than ``cutoff``."""
    return rows[
        rows.close_ms.lt(cutoff)
        & rows.outcome.isin(SETTLED)
        & rows.net_r.notna()
        & rows.known_ms.le(cutoff)
    ].copy()


def net_r_winner_cut(train: pd.DataFrame, quantile: float) -> float:
    if train.empty:
        raise ValueError('No mature training outcomes at this cut')
    return float(train.net_r.quantile(quantile))


def winner_labels(rows: pd.DataFrame, cut: float) -> pd.Series:
    return rows.net_r.ge(cut) & rows.net_r.gt(0)


def candidate_model(name: str, seed: int):
    if name == 'logit':
        return make_pipeline(
            SimpleImputer(strategy='median'),
            StandardScaler(),
            LogisticRegression(C=1, max_iter=1500, tol=1e-6),
        )
    if name == 'lgb_d3':
        depth, rounds, leaf_min = 3, 150, 50
    elif name == 'lgb_d5':
        depth, rounds, leaf_min = 5, 240, 75
    else:
        raise ValueError(f'Unsupported candidate: {name}')
    return lgb.LGBMClassifier(
        objective='binary', max_depth=depth, num_leaves=min(63, 2 ** depth),
        n_estimators=rounds, min_child_samples=leaf_min, learning_rate=.04,
        reg_lambda=3., colsample_bytree=.85, subsample=.9, subsample_freq=1,
        random_state=seed, n_jobs=3, verbosity=-1, deterministic=True,
        force_col_wise=True,
    )


def fit(rows: pd.DataFrame, candidate: str, cutoff: int, seed: int,
        winner_quantile: float, score_quantile: float) -> dict:
    """Fit one timeframe at a cut using only mature, pre-cut outcomes."""
    train = mature_rows(rows, cutoff)
    if train.tf.nunique() != 1 or train.outcome.nunique() != 2:
        raise ValueError('Each fit requires two settled labels in exactly one timeframe')
    label_cut = net_r_winner_cut(train, winner_quantile)
    y = winner_labels(train, label_cut).astype('int8')
    if y.nunique() != 2:
        raise ValueError(f'Winner threshold {label_cut} produces one class for {candidate}')
    estimator = candidate_model(candidate, seed)
    estimator.fit(matrix(train, MODEL_FEATURES), y)
    train_raw = estimator.predict_proba(matrix(train, MODEL_FEATURES))[:, 1]
    score_cut = float(np.quantile(train_raw, score_quantile))
    baseline_train = train.near_net_r.dropna()
    if baseline_train.empty:
        raise ValueError('No finite near_net_r values in mature training rows')
    baseline_cut = float(baseline_train.quantile(score_quantile))
    return dict(
        tf=int(train.tf.iloc[0]), candidate=candidate, estimator=estimator,
        features=list(MODEL_FEATURES), train_n=int(len(train)),
        train_winners=int(y.sum()), winner_net_r_cut=label_cut,
        winner_quantile=winner_quantile, score_quantile=score_quantile,
        score_threshold=score_cut,
        baseline_near_net_r_threshold=baseline_cut,
        train_until_exclusive_ms=int(cutoff), train_train_score_q90=float(score_cut),
        train_net_r_mean=float(train.net_r.mean()),
    )


def predict(bundle: dict, rows: pd.DataFrame) -> np.ndarray:
    if not rows.tf.eq(bundle['tf']).all():
        raise ValueError('Timeframe does not match fitted model')
    return bundle['estimator'].predict_proba(matrix(rows, bundle['features']))[:, 1]


def validation_folds(rows: pd.DataFrame, cfg: dict):
    records = []
    for tf in cfg['timeframes']:
        group = rows[rows.tf.eq(tf)].copy()
        for candidate in cfg['candidates']:
            for year in cfg['validation_years']:
                start, end = year_start(year), year_start(year + 1)
                bundle = fit(group, candidate, start, cfg['seed'], cfg['winner_quantile'],
                             cfg['score_quantile'])
                val = group[group.close_ms.ge(start) & group.close_ms.lt(end)].copy()
                val['raw_score'] = predict(bundle, val)
                val['band'] = np.where(val.raw_score.ge(bundle['score_threshold']), 'top10', 'rest')
                val['baseline_band'] = np.where(
                    val.near_net_r.ge(bundle['baseline_near_net_r_threshold']), 'top10', 'rest')
                # Keep candidate comparison strictly pre-2026: later outcomes
                # cannot make a historical validation year look settled.
                eval_rows = val[val.known_ms.le(int(pd.Timestamp(cfg['test_start']).timestamp()*1000))
                                & val.outcome.isin(SETTLED)].copy()
                high = eval_rows[eval_rows.band.eq('top10')]
                baseline_high = eval_rows[eval_rows.baseline_band.eq('top10')]
                record = dict(
                    tf=int(tf), candidate=candidate, year=int(year),
                    train_n=bundle['train_n'], train_winners=bundle['train_winners'],
                    winner_net_r_cut=bundle['winner_net_r_cut'],
                    train_score_q90=bundle['score_threshold'],
                    baseline_near_net_r_q90=bundle['baseline_near_net_r_threshold'],
                    validation_signals=len(val),
                    validation_eligible=int(val.outcome.isin(ELIGIBLE).sum()),
                    validation_settled=len(eval_rows),
                    high_signals=int(val.band.eq('top10').sum()),
                    high_eligible=int(val.loc[val.band.eq('top10'), 'outcome'].isin(ELIGIBLE).sum()),
                    high_settled=len(high),
                    high_winners=int(winner_labels(high, bundle['winner_net_r_cut']).sum()),
                    high_profitable=int(high.net_r.gt(0).sum()),
                    high_net_r=float(high.net_r.mean()) if len(high) else np.nan,
                    all_mean_net_r=float(eval_rows.net_r.mean()) if len(eval_rows) else np.nan,
                    baseline_high_settled=len(baseline_high),
                    baseline_high_mean_net_r=(float(baseline_high.net_r.mean())
                                              if len(baseline_high) else np.nan),
                    validation_logloss=(float(log_loss(
                        winner_labels(eval_rows, bundle['winner_net_r_cut']).astype('int8'),
                        np.clip(val.loc[eval_rows.index, 'raw_score'].to_numpy(), 1e-8, 1-1e-8),
                        labels=[0, 1])) if len(eval_rows) and winner_labels(
                            eval_rows, bundle['winner_net_r_cut']).nunique() == 2 else np.nan),
                )
                records.append(record)
    folds = pd.DataFrame(records)
    selections = {}
    order = {name: i for i, name in enumerate(cfg['candidates'])}
    for tf, group in folds.groupby('tf', sort=True):
        ranking = group.groupby('candidate').high_net_r.agg(['mean', 'count'])
        expected = len(cfg['validation_years'])
        if set(ranking.index) != set(cfg['candidates']) or not ranking['count'].eq(expected).all():
            raise ValueError(f'Each {tf}h candidate must have a nonempty score for all {expected} validation years')
        ranking = ranking.rename(columns={'mean': 'mean_high_net_r'})
        if ranking.mean_high_net_r.isna().any():
            raise ValueError(f'Each {tf}h candidate must have nonempty settled high-band outcomes in every validation year')
        ranking = ranking.drop(columns='count')
        ranking['declaration_order'] = [order[name] for name in ranking.index]
        ranking = ranking.sort_values(['mean_high_net_r', 'declaration_order'], ascending=[False, True])
        selections[str(int(tf))] = dict(
            selected=str(ranking.index[0]),
            candidates={name: dict(mean_high_net_r=float(row.mean_high_net_r))
                        for name, row in ranking.iterrows()},
        )
    return folds, selections


def stats(group: pd.DataFrame, winner_cut: float) -> dict:
    settled = group[group.outcome.isin(SETTLED) & group.net_r.notna()]
    eligible = group[group.outcome.isin(ELIGIBLE)]
    winners = winner_labels(settled, winner_cut)
    return dict(
        n_signals=int(len(group)), eligible=int(len(eligible)),
        settled=int(len(settled)), tp=int(settled.outcome.eq('tp').sum()),
        sl=int(settled.outcome.eq('sl').sum()),
        censored=int(group.outcome.eq('censored').sum()),
        winners=int(winners.sum()), profitable=int(settled.net_r.gt(0).sum()),
        mean_net_r=float(settled.net_r.mean()) if len(settled) else np.nan,
    )


def inference(group: pd.DataFrame, cfg: dict) -> dict:
    """Reuse the frozen weekly-wick matched random draw and week bootstrap."""
    settled = group[group.outcome.isin(SETTLED) & group.net_r.notna()].copy()
    if settled.empty or not settled.band.eq('top10').any():
        return dict(settled_high=0, high_mean_net_r=None, matched_signal_mean_net_r=None,
                    excess_net_r=None, one_sided_p=None, weeks=0, draws=cfg['draws'])
    settled = settled.assign(month=settled.week)
    result = base.inference(settled, cfg)
    # The shared helper names its period field ``months``. This experiment is
    # grouped by NY week, so keep the summary vocabulary aligned with the data.
    result['weeks'] = result.pop('months')
    result['week_bootstrap_95'] = result.pop('high_month_bootstrap_95')
    result['excess_week_bootstrap_95'] = result.pop('excess_month_bootstrap_95')
    return result


def test_one_timeframe(rows: pd.DataFrame, tf: int, selected: str,
                       cfg: dict, test_start: int, cutoff: int):
    group = rows[rows.tf.eq(tf)].copy()
    bundle = fit(group, selected, test_start, cfg['seed'], cfg['winner_quantile'],
                 cfg['score_quantile'])
    test = group[group.close_ms.ge(test_start) & group.close_ms.le(cutoff)].copy()
    test['raw_score'] = predict(bundle, test)
    test['band'] = np.where(test.raw_score.ge(bundle['score_threshold']), 'top10', 'rest')
    test['baseline_band'] = np.where(
        test.near_net_r.ge(bundle['baseline_near_net_r_threshold']), 'top10', 'rest')
    test['winner'] = pd.Series(pd.NA, index=test.index, dtype='boolean')
    settled = test.outcome.isin(SETTLED) & test.net_r.notna()
    test.loc[settled, 'winner'] = winner_labels(
        test.loc[settled], bundle['winner_net_r_cut']).to_numpy(dtype=bool)
    test['candidate'] = selected
    test['eligible'] = test.outcome.isin(ELIGIBLE)

    summary = dict(
        selected=selected, confirmed=int(len(group)), train_n=bundle['train_n'],
        train_winners=bundle['train_winners'], winner_net_r_cut=bundle['winner_net_r_cut'],
        train_score_q90=bundle['score_threshold'],
        baseline_near_net_r_q90=bundle['baseline_near_net_r_threshold'],
        test_start=cfg['test_start'], cutoff_exclusive=cfg['cutoff_exclusive'],
        all=stats(test, bundle['winner_net_r_cut']),
        high=stats(test[test.band.eq('top10')], bundle['winner_net_r_cut']),
        baseline_near_net_r=stats(test[test.baseline_band.eq('top10')], bundle['winner_net_r_cut']),
        selected_vs_matched_random=inference(test, cfg),
        baseline_vs_matched_random=inference(
            test.assign(band=test.baseline_band), cfg),
    )
    bundle.update(
        sklearn=sklearn.__version__, lightgbm=lgb.__version__,
        cutoff_exclusive=cfg['cutoff_exclusive'],
        baseline_definition='near Monday edge potential net R at signal close; actual next H1 entry is not used',
    )
    return bundle, test, summary


def main() -> None:
    cfg = json.loads((EXP / 'config.json').read_text())
    rows = load_rows(cfg)
    cutoff = int(pd.Timestamp(cfg['cutoff_exclusive']).timestamp() * 1000)
    test_start = int(pd.Timestamp(cfg['test_start']).timestamp() * 1000)
    if rows.close_ms.max() > cutoff:
        raise ValueError('Feature rows exceed cutoff_exclusive')
    if rows.close_ms.min() >= test_start:
        raise ValueError('No pre-2026 feature history available')
    if rows.outcome.dropna().nunique() and not set(rows.outcome.dropna()).issubset(ELIGIBLE):
        raise ValueError('Unexpected outcome label')

    tic = perf_counter()
    folds, selections = validation_folds(rows, cfg)
    folds.to_csv(EXP / 'validation_folds.csv', index=False)

    model_summary = {}
    ledgers = []
    for tf in cfg['timeframes']:
        selected = selections[str(int(tf))]['selected']
        bundle, test, result = test_one_timeframe(rows, int(tf), selected, cfg, test_start, cutoff)
        model_file = f'model_{int(tf)}h.joblib'
        joblib.dump(bundle, EXP / model_file, compress=3)
        reload = joblib.load(EXP / model_file)
        sample = test.iloc[:100]
        np.testing.assert_allclose(predict(reload, sample), sample.raw_score,
                                   rtol=0, atol=1e-12)
        result['model_file'] = model_file
        model_summary[str(int(tf))] = result
        ledgers.append(test)

    scored = pd.concat(ledgers, ignore_index=True).sort_values(
        ['close_ms', 'event_id'], kind='mergesort')
    wanted = [
        'event_id', 'symbol', 'week', 'tf', 'side', 'close_ms', 'status',
        'eligible', 'outcome', 'exit_ms', 'resolution', 'net_r',
        'target_price', 'entry', 'stop', 'near_net_r', 'raw_score', 'band',
        'baseline_band', 'winner', 'candidate',
    ]
    scored[wanted].to_csv(EXP / 'test_scores.csv.gz', index=False,
                          compression={'method': 'gzip', 'mtime': 0})
    write_json(EXP / 'model_summary.json', dict(
        parent=cfg['parent'], confirmed=int(len(rows)), eligible=int(rows.outcome.isin(ELIGIBLE).sum()),
        settled=int(rows.outcome.isin(SETTLED).sum()),
        feature_names=list(MODEL_FEATURES), timeframes=model_summary,
        selections=selections, validation_fits=len(cfg['validation_years'])
        * len(cfg['candidates']) * len(cfg['timeframes']),
        final_fits=len(cfg['timeframes']), baseline='near_net_r ranked independently; cutoff is the mature fit-sample q90',
        draws=cfg['draws'], seed=cfg['seed'], train_runtime='project .venv',
        sklearn=sklearn.__version__, lightgbm=lgb.__version__,
        model_elapsed_seconds=round(perf_counter() - tic, 2), production_eligible=False,
        builder=subprocess.check_output(
            ['git', 'log', '-1', '--format=%H', '--', __file__, str(EXP / 'config.json')],
            cwd=ROOT, text=True).strip(),
    ))
    print(json.dumps(model_summary, ensure_ascii=False, indent=2), flush=True)


def score_csv(path: Path) -> None:
    """Score a prepared close-time CSV with ``near_net_r`` and causal features."""
    observations = pd.read_csv(path)
    required = {'event_id', 'tf', 'near_net_r', *FEATURES}
    missing = required.difference(observations.columns)
    if missing:
        raise ValueError(f'Input lacks model columns: {sorted(missing)}')
    summary = json.loads((EXP / 'model_summary.json').read_text())
    results = []
    for tf, group in observations.groupby('tf', sort=False):
        selected = summary['timeframes'][str(int(tf))]['selected']
        bundle = joblib.load(EXP / summary['timeframes'][str(int(tf))]['model_file'])
        raw = predict(bundle, group)
        out = group[[c for c in ('event_id', 'symbol', 'week', 'tf', 'side', 'close_ms')
                     if c in group]].copy()
        out['raw_score'] = raw
        out['band'] = np.where(raw >= bundle['score_threshold'], 'top10', 'rest')
        out['baseline_band'] = np.where(
            group.near_net_r >= bundle['baseline_near_net_r_threshold'], 'top10', 'rest')
        out['candidate'] = selected
        results.append(out)
    print(pd.concat(results).sort_index().to_csv(index=False), end='')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score', type=Path,
                        help='Score a prepared CSV containing close-time model features and near_net_r.')
    args = parser.parse_args()
    with threadpool_limits(limits=3):
        if args.score:
            score_csv(args.score)
        else:
            main()
