"""Frozen six-way ETH feature/training comparison with purged quarterly folds.

Original twenty causal features, equal-week training weights, native netR
labels and strict positive score gate remain the baseline. Each candidate
changes one feature group or training parameter. No early stopping, fitted
calibrator, test-selected combinations or extra native simulation. Persist
each trained model and exact training event IDs before evaluating returns.
"""
from __future__ import annotations

import json
import pickle
import subprocess
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from yoyo.evaluation.btc_eth_repeat_fit import weights
from yoyo.evaluation.major_1618_v2_models import FINAL
from yoyo.evaluation.eth_1618_features import EXP, PRIOR, config, read, dump


def training_rows(data, start, arm):
    """Label maturity at cutoff; rolling variant also bounds whole-week start."""
    expiry = pd.to_datetime(data.expiry_dt, utc=True)
    week = pd.to_datetime(data.week_start, utc=True)
    keep = data.complete & (expiry <= start)
    if arm == 'window_52w':
        keep &= week >= start-pd.DateOffset(weeks=52)
    return data[keep].copy()


def training_weights(train, start, arm):
    """Equal total NY-week weight; optional26-week label-age half-life only."""
    w = weights(train)
    if arm == 'decay_26w':
        age = (start-pd.to_datetime(train.expiry_dt, utc=True)).dt.total_seconds().to_numpy()/(86400*7)
        assert (age >= 0).all()
        w *= np.exp2(-age/26.)
        w /= w.mean()
    return w


def main():
    cfg = config(); d = read('data.csv.gz')
    oldpred = pd.read_csv(PRIOR/'ETH_predictions.csv.gz', float_precision='round_trip')
    predictions = []; receipts = []
    for target in cfg['targets']:
        cohort = d[d.target.eq(target)].copy()
        for fold in cfg['folds']:
            start = pd.Period(fold, freq='Q').start_time.tz_localize('America/New_York')
            week = pd.to_datetime(cohort.week_start, utc=True)
            test = cohort[(week >= start) & (week < start+pd.DateOffset(months=3))].copy()
            path = PRIOR/'models/ETH'/target/fold/f'{FINAL}.pkl'
            with path.open('rb') as handle:
                baseline = pickle.load(handle)
            original = oldpred[oldpred.target.eq(target)&oldpred.fold.eq(fold)&oldpred.arm.eq(FINAL)]
            assert set(original.trade_id) == set(test.trade_id)
            base_columns = baseline['features']
            baseline_train = training_rows(cohort, start, 'original_model')
            assert set(baseline_train.event_id) == set(baseline['record']['train_event_ids'])
            base_score = baseline['model'].predict(test[base_columns])
            np.testing.assert_allclose(base_score, original.set_index('trade_id').loc[test.trade_id].score, atol=1e-12, rtol=0)
            original_row = test.copy()
            original_row['arm'] = 'original_model'; original_row['fold'] = fold
            original_row['score'] = base_score; original_row['score_kind'] = 'net_r'
            original_row['admit'] = base_score > 0
            predictions.append(original_row)
            for arm in cfg['arms']:
                directory = EXP/'models'/target/fold
                directory.mkdir(parents=True, exist_ok=True)
                model_path = directory/f'{arm}.pkl'
                cached = directory/f'{arm}_predictions.csv.gz'
                if model_path.exists() and cached.exists():
                    with model_path.open('rb') as handle:
                        bundle = pickle.load(handle)
                    predictions.append(pd.read_csv(cached, float_precision='round_trip'))
                    receipts.append(bundle['record']); continue
                train = training_rows(cohort, start, arm)
                assert len(train) >= cfg['minimum_history']['rows'] and train.week.nunique() >= cfg['minimum_history']['weeks']
                assert not set(train.week) & set(test.week)
                columns = base_columns+cfg['feature_groups'].get(arm, [])
                w = training_weights(train, start, arm)
                params = dict(cfg['lightgbm'])
                if arm == 'leaves_3': params['num_leaves'] = 3
                reuse = arm == 'window_52w' and train.event_id.tolist() == baseline_train.event_id.tolist()
                if reuse:
                    model = baseline['model']
                else:
                    model = LGBMRegressor(**params)
                    model.fit(train[columns].replace([np.inf, -np.inf], np.nan), train.net_r, sample_weight=w)
                score = model.predict(test[columns].replace([np.inf, -np.inf], np.nan))
                assert np.isfinite(score).all()
                rec = dict(target=target, fold=fold, arm=arm, train_n=len(train), train_weeks=int(train.week.nunique()),
                           train_positive=float(train.label.mean()), train_net_r_mean=float(train.net_r.mean()),
                           label_expiry_max=str(pd.to_datetime(train.expiry_dt, utc=True).max()),
                           train_week_min=str(train.week.min()), train_week_max=str(train.week.max()),
                           test_n=len(test), test_complete_n=int(test.complete.sum()), features=columns,
                           train_event_ids=train.event_id.tolist(), weights_min=float(w.min()), weights_max=float(w.max()),
                           weight_sum=float(w.sum()), effective_weight_n=float(w.sum()**2/(w*w).sum()),
                           params=params, baseline_model_path=str(path), reused_identical_baseline=bool(reuse),
                           feature_gain=model.booster_.feature_importance(importance_type='gain').tolist())
                result = test.copy(); result['arm'] = arm; result['fold'] = fold
                result['score'] = score; result['score_kind'] = 'net_r'; result['admit'] = score > 0
                with model_path.open('wb') as handle:
                    pickle.dump(dict(model=model, features=columns, record=rec), handle)
                result.to_csv(cached, index=False)
                predictions.append(result); receipts.append(rec)
            print(target, fold, 'complete', flush=True)
    pd.concat(predictions, ignore_index=True).to_csv(EXP/'predictions.csv.gz', index=False)
    dump(EXP/'model_receipts.json', [{k:v for k,v in r.items() if k!='train_event_ids'} for r in receipts])
    dump(EXP/'fit_receipt.json', dict(models_fitted=sum(not r['reused_identical_baseline'] for r in receipts),
         identical_models_reused=sum(r['reused_identical_baseline'] for r in receipts), original_models_reused=24,
         no_new_native_positions=True, builder_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()))


if __name__ == '__main__':
    main()
