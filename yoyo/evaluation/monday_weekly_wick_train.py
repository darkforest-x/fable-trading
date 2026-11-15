"""Separate 1h/4h/12h probability models for 3R after a confirmed weekly Monday wick.

The fixed census and features contain one row per event. No scoring feature reads
the next open or exit. Fit labels must be available by their development boundary,
using the end of the hour/minute in which the outcome was resolved. Versioned
models may score an already computed feature row via --score.
"""
import argparse
import json
from pathlib import Path
import subprocess
from time import perf_counter

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
import sklearn
from scipy.optimize import minimize
from scipy.special import expit, logit
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

from yoyo.evaluation import monthweek_wick_score3r as base
from yoyo.evaluation.monday_weekly_wick_features import CORE_FEATURES, FEATURES

EXP = base.ROOT / 'experiments/active/exp-binance-monday-wick-score3r-20261116-v1'
FEATURE_FILE = 'monday_weekly_wick_features.csv.gz'
LABEL_FILE = 'outcomes_3r.csv.gz'
HOUR = 3_600_000


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def year_start(year):
    return int(pd.Timestamp(f'{year}-01-01', tz='UTC').timestamp()*1000)


def load_rows():
    features = pd.read_csv(EXP/FEATURE_FILE)
    fields = ['event_id','outcome','exit_ms','resolution','net_r']
    ledger = pd.read_csv(EXP/LABEL_FILE)
    if 'target' in ledger:
        assert ledger.target.eq('3R').all()
    assert features.event_id.is_unique and ledger.event_id.is_unique
    assert set(ledger.event_id)==set(features.loc[features.status.eq('entered'), 'event_id'])
    rows = features.merge(ledger[fields], on='event_id', how='left', validate='one_to_one')
    assert set(rows.loc[rows.status.eq('entered'), 'outcome']) <= {'tp','sl','censored'}
    assert set(rows.tf)=={1,4,12}
    known = rows.outcome.isin(['tp','sl'])
    if not set(rows.loc[known,'resolution']).issubset({'h1','1m'}):
        raise ValueError('Unexpected exit time resolution')
    rows['known_ms'] = (rows.exit_ms + np.where(rows.resolution.eq('1m'),60_000,HOUR)).where(known)
    assert (rows.loc[known,'known_ms'] >= rows.loc[known,'close_ms']).all()
    return rows.sort_values(['close_ms','event_id']).reset_index(drop=True)


def candidate_model(candidate, seed):
    if candidate['family']=='logit':
        return make_pipeline(SimpleImputer(strategy='median'), StandardScaler(),
                             LogisticRegression(C=1, max_iter=1500, tol=1e-6))
    depth = candidate['depth']
    return lgb.LGBMClassifier(objective='binary', max_depth=depth, num_leaves=min(63,2**depth),
                              n_estimators=candidate['rounds'], min_child_samples=candidate['leaf_min'],
                              learning_rate=.04, reg_lambda=3., colsample_bytree=.85, subsample=.9,
                              subsample_freq=1, random_state=seed, n_jobs=3, verbosity=-1,
                              deterministic=True, force_col_wise=True)


def fit(rows, candidate, cutoff, seed):
    tr = rows[(rows.close_ms<cutoff) & rows.known_ms.le(cutoff)]
    if tr.tf.nunique()!=1 or tr.outcome.nunique()!=2:
        raise ValueError('Each fit requires two labels in exactly one timeframe')
    names = list(CORE_FEATURES if candidate['features']=='core' else FEATURES)
    counts = base.matrix(tr, names).nunique()
    names = counts.index[counts.gt(1)].tolist()
    model = candidate_model(candidate, seed)
    model.fit(base.matrix(tr, names), tr.outcome.eq('tp').astype(int))
    return dict(tf=int(tr.tf.iloc[0]), name=candidate['name'], estimator=model, features=names,
                train_n=len(tr), train_wins=int(tr.outcome.eq('tp').sum()), fit_until_utc_ms=cutoff)


def predict(bundle, rows):
    if not rows.tf.eq(bundle['tf']).all():
        raise ValueError('Timeframe does not match model')
    return bundle['estimator'].predict_proba(base.matrix(rows, bundle['features']))[:,1]


def calibration(prob, actual):
    """Fit monotone Platt scaling on OOF probabilities with labels known by the final cut."""
    z = logit(np.clip(np.asarray(prob),1e-8,1-1e-8))
    y = np.asarray(actual,dtype=float)
    def objective(params):
        a, b = params
        t = a+b*z
        error = expit(t)-y
        return (np.mean(np.logaddexp(0.,t)-y*t),
                np.array([error.mean(), (error*z).mean()]))
    result = minimize(objective,[0.,1.],method='L-BFGS-B',jac=True,bounds=[(None,None),(0,None)])
    if not result.success:
        raise RuntimeError(result.message)
    return result.x.tolist()


def calibrated(raw, params):
    a, b = params
    return expit(a + b*logit(np.clip(raw,1e-8,1-1e-8)))


def validate_candidates(rows, cfg, final_cut):
    records, predictions = [], {}
    for c in cfg['candidates']:
        parts = []
        for year in cfg['validation_years']:
            start = year_start(year)
            val = rows[rows.close_ms.between(start,year_start(year+1),inclusive='left') &
                       rows.status.eq('entered')].copy()
            bundle = fit(rows,c,start,cfg['seed'])
            val['raw'] = predict(bundle,val)
            parts.append(val[['event_id','raw','outcome','known_ms','net_r','close_ms']])
            cut = val.raw.quantile(.9)
            observed = val[val.known_ms.le(final_cut)]
            top = observed[observed.raw.ge(cut)]
            if top.empty:
                raise ValueError('Validation high band contains no mature outcomes')
            records.append(dict(tf=bundle['tf'], candidate=c['name'], year=year,
                                train=bundle['train_n'], features=len(bundle['features']),
                                validation_signals=len(val), validation_settled=len(observed),
                                high_settled=len(top), high_tp=int(top.outcome.eq('tp').sum()),
                                high_net_r=top.net_r.mean(), all_net_r=observed.net_r.mean(),
                                logloss=log_loss(observed.outcome.eq('tp'),observed.raw,labels=[False,True])))
        predictions[c['name']] = pd.concat(parts,ignore_index=True)
    tab = pd.DataFrame(records)
    lookup = {c['name']:i for i,c in enumerate(cfg['candidates'])}
    ranks = tab.groupby('candidate',sort=False).agg(net=('high_net_r','mean'),loss=('logloss','mean'))
    ranks['order'] = ranks.index.map(lookup)
    winner = ranks.sort_values(['net','loss','order'],ascending=[False,True,True]).index[0]
    return winner, tab, predictions


def economics(test, cfg):
    # The original inference routine groups by its period key and side/tf. Supply
    # NY weeks here; its bootstrap, matched drawings and CIs all use whole weeks.
    result = base.inference(test.assign(month=test.week),cfg)
    return {k.replace('month','week'):v for k,v in result.items()}


def test_model(rows, c, oof, cfg, final_cut):
    mature = oof[oof.known_ms.le(final_cut)]
    scale = calibration(mature.raw,mature.outcome.eq('tp'))
    threshold = float(oof.raw.quantile(.9))
    bundle = fit(rows,c,final_cut,cfg['seed'])
    bundle.update(calibration=scale, raw_high_threshold=threshold,
                  p3r_at_high_threshold=float(calibrated(threshold,scale)), oof_scored=len(oof),
                  oof_settled=len(mature), sklearn=sklearn.__version__, lightgbm=lgb.__version__)
    test = rows[rows.close_ms.ge(final_cut)].copy()
    raw = predict(bundle,test)
    test['raw_score'] = raw
    test['p3r'] = calibrated(raw,scale)
    test['band'] = np.where(raw>=threshold,'top10','rest')
    eligible = test[test.status.eq('entered')]
    train_rate = bundle['train_wins']/bundle['train_n']
    periods = pd.to_datetime(eligible.close_ms,unit='ms',utc=True)
    quarters = 'Q'+periods.dt.quarter.astype(str)
    metrics = []
    for label,sub in [('2026',eligible), *eligible.groupby(quarters)]:
        for band in ['all','top10','rest']:
            g = sub if band=='all' else sub[sub.band==band]
            metrics.append(dict(tf=bundle['tf'], candidate=c['name'], period=label,band=band,
                                **base.stats(g,train_rate)))
    return bundle, test, metrics, economics(eligible,cfg)


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    rows = load_rows()
    cut = int(pd.Timestamp(cfg['test_start']).timestamp()*1000)
    assert rows.close_ms.max()<=int(pd.Timestamp(cfg['cutoff_exclusive']).timestamp()*1000)
    candidates = {c['name']:c for c in cfg['candidates']}
    summary, validations, tables, scored, oof_chosen = {}, [], [], [], []
    tic = perf_counter()
    for tf in cfg['timeframes']:
        group = rows[rows.tf.eq(tf)]
        started = perf_counter()
        winner, scores, oof = validate_candidates(group,cfg,cut)
        validations.append(scores)
        summary[str(tf)] = dict(selected=winner, confirmed=len(group), models={})
        for name in dict.fromkeys(['logit_core',winner]):
            bundle,test,metrics,checks = test_model(group,candidates[name],oof[name],cfg,cut)
            # Persist a separate calibrated model per clock. Baseline also has its
            # own validation-derived scale and threshold for an honest comparison.
            path = EXP/f'model_{tf}h_{name}.joblib'
            joblib.dump(bundle,path,compress=3)
            sample = test.iloc[:100]
            np.testing.assert_allclose(predict(joblib.load(path),sample),sample.raw_score,rtol=0,atol=1e-12)
            tables.extend(metrics)
            summary[str(tf)]['models'][name] = {
                **{k:v for k,v in bundle.items() if k!='estimator'}, 'file':path.name,'inference':checks}
            cols = ['event_id','symbol','week','tf','side','close_ms','status','outcome','net_r','p3r','raw_score','band']
            scored.append(test[cols].assign(candidate=name,selected=name==winner))
        oof_chosen.append(oof[winner][['event_id','close_ms','raw','known_ms','outcome','net_r']].assign(tf=tf,candidate=winner))
        summary[str(tf)]['elapsed_seconds'] = round(perf_counter()-started,2)
        print(f'{tf}h selected {winner}; validation and final fit completed in {perf_counter()-started:.1f}s',flush=True)
    pd.concat(validations,ignore_index=True).to_csv(EXP/'validation_folds.csv',index=False)
    pd.concat(oof_chosen,ignore_index=True).to_csv(EXP/'selected_validation_scores.csv.gz',index=False,compression='gzip')
    pd.DataFrame(tables).to_csv(EXP/'test_metrics.csv',index=False)
    pd.concat(scored,ignore_index=True).to_csv(EXP/'test_scores.csv.gz',index=False,compression='gzip')
    write_json(EXP/'model_summary.json',dict(confirmed=len(rows),entered=int(rows.status.eq('entered').sum()),
               test_start=cfg['test_start'], cutoff=cfg['cutoff_exclusive'], timeframes=summary,
               validation_fits=len(cfg['candidates'])*len(cfg['validation_years'])*len(cfg['timeframes']),
               final_fits=sum(len(info['models']) for info in summary.values()),
               train_runtime='project .venv', sklearn=sklearn.__version__, lightgbm=lgb.__version__,
               model_elapsed_seconds=round(perf_counter()-tic,2), production_eligible=False,
               builder=subprocess.check_output(['git','log','-1','--format=%H','--',__file__,str(EXP/'config.json')],cwd=base.ROOT,text=True).strip()))


def score_csv(path):
    observations = pd.read_csv(path)
    summary = json.loads((EXP/'model_summary.json').read_text())
    if not observations.tf.isin([1,4,12]).all():
        raise ValueError('Unsupported timeframe')
    results = []
    for tf, g in observations.groupby('tf'):
        choice=summary['timeframes'][str(int(tf))]
        bundle=joblib.load(EXP/choice['models'][choice['selected']]['file'])
        raw=predict(bundle,g)
        fields=[k for k in ['event_id','symbol','tf','close_ms'] if k in g]
        results.append(g[fields].assign(p3r=calibrated(raw,bundle['calibration']),
                                        high=raw>=bundle['raw_high_threshold']))
    print(pd.concat(results).sort_index().to_csv(index=False),end='')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--score',type=Path)
    args=parser.parse_args()
    with threadpool_limits(limits=3):
        if args.score:
            score_csv(args.score)
        else:
            main()
