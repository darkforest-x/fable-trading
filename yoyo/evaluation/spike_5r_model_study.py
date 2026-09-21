"""Frozen two-model study of realized net-R>5 on all original V9 long signals.

Source: receipt-bound spike_10r_dataset; all twenty feature columns/windows are
documented by spike_10r_features. At quarter Q, fit only [Q-12m,Q-3m) labels
fully available by Q-3m; score calibration [Q-3m,Q) without labels, then freeze
the same model and percentile thresholds for Q. Only long admission changes.
The owner explicitly approved this bounded offline experiment on 2026-09-21.
Training/prediction and outcome evaluation are separate receipt-bound phases.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss

from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation import spike_5r_models as models
from yoyo.evaluation import spike_5r_statistics as t
from yoyo.evaluation import spike_10r_serial as serial
from yoyo.evaluation.spike_six_filter_statistics import strict_bool
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-5r-model-20260921-v5')
START = pd.Timestamp('2025-10-01T00:00:00Z')
COVERAGES = (.01, .05, .1, .2)
MODEL_NAMES = ('logistic', 'lightgbm')
QUARTERS = ('2025-10-01', '2026-01-01', '2026-04-01', '2026-07-01')


def policy_name(name, coverage):
    return f'{name}_top{int(100*coverage)}'


def model_policies():
    return [policy_name(n, q) for n in MODEL_NAMES for q in COVERAGES]


def all_policies():
    return model_policies()+[policy_name('risk', q) for q in COVERAGES]+['prior_v21_risk_decile']


def dependencies():
    return [Path(__file__), Path(models.__file__), Path(t.__file__), Path(s.__file__), Path(serial.__file__),
            Path('yoyo/evaluation/spike_10r_features.py'),
            Path('yoyo/evaluation/spike_high_r_entry_report.py'),
            Path('yoyo/evaluation/spike_six_filter_statistics.py'),
            Path('yoyo/evaluation/spike_v8_six_filters.py'),
            Path('tests/evaluation/test_spike_5r_statistics.py'),
            Path('tests/evaluation/test_spike_5r_models.py'),
            Path('tests/evaluation/test_spike_5r_model_study.py'),
            EXP/'PROJECT_PLAN.md', EXP/'config.json', EXP/'AUTHORIZATION.json']


def frozen_config(dataset):
    cfg = json.loads((EXP/'config.json').read_text())
    auth = json.loads((EXP/'AUTHORIZATION.json').read_text())
    if (cfg['quarters'] != list(QUARTERS) or cfg['model_names'] != list(MODEL_NAMES)
            or cfg['nominal_coverages'] != list(COVERAGES) or cfg['fit_months'] != 9
            or cfg['calibration_months'] != 3 or cfg['development_months'] != 12
            or cfg['calibration_min_rows'] != 100 or cfg['round_trip_cost'] != .002
            or cfg['production_eligible'] or not cfg['training_eligible']
            or cfg['serial_trigger_min_hits'] != 10 or cfg['serial_trigger_min_recall'] != .1
            or cfg['target_r'] != 5 or cfg['label_column'] != models.LABEL_COLUMN
            or cfg['seed'] != 921401):
        raise ValueError('configuration differs from frozen study')
    pin = s.digest(dataset/'receipt.json')
    if (pin != cfg['dataset_receipt_sha256'] or pin != auth['dataset_receipt_sha256']
            or auth['target_r'] != 5 or auth['allowed_models'] != list(MODEL_NAMES)
            or auth['owner_response'] != '或者大于 5r'
            or auth['production_eligible']
            or s.digest(Path(auth['approved_proposal_path'])) != auth['approved_proposal_sha256']
            or s.digest(Path(auth['prior_training_authorization_path']))
            != auth['prior_training_authorization_sha256']):
        raise ValueError('scoped training authorization or dataset identity mismatch')
    if not _committed(dependencies()):
        raise ValueError('commit model study and tests before a market-data run')
    return cfg


def relabel_candidates(c: pd.DataFrame) -> pd.DataFrame:
    """Copy candidates and derive the nullable strict realized net-R>5 label.

    Only valid, uncensored candidates with a recorded outcome have a known
    label.  The immutable source ``label_gt10`` remains present and untouched;
    censored or otherwise unavailable outcomes stay ``<NA>``, never false.
    """
    required = ("valid_entry", "censored", "net_r")
    missing = [name for name in required if name not in c]
    if missing:
        raise ValueError(f"cannot derive label_gt5 without columns: {missing}")
    derived = c.copy()
    known = strict_bool(derived.valid_entry) & ~strict_bool(derived.censored)
    net_r = pd.to_numeric(derived.net_r, errors="coerce")
    known &= np.isfinite(net_r)
    labels = pd.Series(pd.NA, index=derived.index, dtype="boolean")
    labels.loc[known] = net_r.loc[known].gt(5).to_numpy()
    derived[models.LABEL_COLUMN] = labels
    return derived


def quarterly_blocks(c, quarter):
    """Only available_at defines unlabelled calibration/prediction membership."""
    quarter = pd.Timestamp(quarter)
    if quarter.tzinfo is None:
        quarter = quarter.tz_localize('UTC')
    cutoff = quarter-pd.DateOffset(months=3)
    train = models.training_mask(c, cutoff, history_months=9)
    cal = c.available_at.ge(cutoff) & c.available_at.lt(quarter)
    target = c.available_at.ge(quarter) & c.available_at.lt(quarter+pd.DateOffset(months=3))
    return train, cal, target, cutoff


def score_gate(calibration, target, coverage, minimum=100):
    """Historical unlabelled quantiles; ties enter, unknowns reject.

    Inputs are scores only, never outcomes. Actual target coverage can differ
    from the nominal past-score tail fraction due to ties and distribution drift.
    """
    history = np.asarray(calibration, float)
    history = history[np.isfinite(history)]
    target = np.asarray(target, float)
    if len(history) < minimum:
        return np.zeros(len(target), bool), np.nan
    threshold = float(np.quantile(history, 1-coverage))
    return np.isfinite(target) & (target >= threshold), threshold


def receipt_files(folder, name, extra):
    paths = sorted(p for p in folder.rglob('*') if p.is_file() and p.name != name)
    s.dump(folder/name, s.clean(dict(extra, files={str(p.relative_to(folder)):s.digest(p) for p in paths})))


def verify_output(folder, name):
    receipt = json.loads((folder/name).read_text())
    for filename, expected in receipt['files'].items():
        if s.digest(folder/filename) != expected:
            raise ValueError(f'frozen output drift: {filename}')
    for path, expected in receipt.get('dependencies', {}).items():
        if s.digest(Path(path)) != expected:
            raise ValueError(f'builder drift: {path}')
    return receipt


def train_predict(dataset, output):
    cfg = frozen_config(dataset)
    if output.exists():
        raise ValueError('refuse to overwrite model predictions')
    c, _, _ = s.load_candidates(dataset)
    c = relabel_candidates(c)
    closed=c.valid_entry & ~c.censored
    if not np.array_equal(c.loc[closed,models.LABEL_COLUMN].to_numpy(dtype=bool),c.loc[closed,'net_r'].gt(5).to_numpy()):
        raise ValueError('cached labels differ from strict realized net_r > 5')
    output.mkdir(parents=True)
    decision = c[['event_key','stream_key','available_at','timeframe_min']].copy()
    scores = decision.copy()
    scores['quarter'] = ''
    for name in (*MODEL_NAMES,'risk'):
        scores[name] = np.nan
    for name in all_policies():
        decision[name] = c.available_at.lt(START)  # Original warmup context for serial replay.
    folds, thresholds, calibrations = [], [], []
    for date in QUARTERS:
        q = pd.Timestamp(date, tz='UTC')
        train, cal, target, cutoff = quarterly_blocks(c,q)
        history = c.loc[train]
        if len(history) < 100 or history.net_r.gt(5).nunique() != 2:
            raise ValueError('insufficient mature training support')
        tag = f'{q.year}Q{q.quarter}'
        fold = output/'models'/tag
        fold.mkdir(parents=True)
        history[['event_key','available_at','entry_time','exit_time','timeframe_min','net_r']].to_csv(
            fold/'training_members.csv.gz', index=False, compression={'method':'gzip','mtime':0})
        cs = c.loc[cal,['event_key','available_at','timeframe_min']].copy()
        cs['quarter'] = tag
        scores.loc[target,'quarter'] = tag
        for name in (*MODEL_NAMES,'risk'):
            if name == 'risk':
                cp = -c.loc[cal,'reference_risk_fraction'].to_numpy(float)
                tp = -c.loc[target,'reference_risk_fraction'].to_numpy(float)
            else:
                fitted = models.fit_model(name, history)
                cp = models.predict_model(fitted, c.loc[cal])
                tp = models.predict_model(fitted, c.loc[target])
                models.save_model(fitted, fold/name)
            cs[name] = cp
            scores.loc[target,name] = tp
            for coverage in COVERAGES:
                admitted, threshold = score_gate(cp,tp,coverage,cfg['calibration_min_rows'])
                policy = policy_name(name,coverage)
                decision.loc[target,policy] = admitted
                thresholds.append(dict(quarter=tag,rule=policy,nominal_coverage=coverage,
                    calibration_start=cutoff,calibration_end=q,calibration_n=int(np.isfinite(cp).sum()),
                    threshold=threshold,target_candidates=int(target.sum()),admitted=int(admitted.sum())))
        calibrations.append(cs)
        mature = history.exit_time+pd.to_timedelta(history.timeframe_min,unit='min')
        folds.append(dict(quarter=tag,fit_start=q-pd.DateOffset(months=12),fit_end=cutoff,
            calibration_start=cutoff,calibration_end=q,predict_start=q,predict_end=q+pd.DateOffset(months=3),
            train_closed=len(history),train_gt5=int(history.net_r.gt(5).sum()),
            max_label_available_at=mature.max(),calibration_candidates=int(cal.sum()),
            target_candidates=int(target.sum()),training_event_hash=s.digest(fold/'training_members.csv.gz')))
        print(f'{tag}: trained {len(history)}, positives {history.net_r.gt(5).sum()}, predicted {target.sum()}',flush=True)
    matrix, _, _ = s.calibrate(c)
    decision.loc[c.available_at.ge(START),'prior_v21_risk_decile'] = matrix[c.available_at.ge(START),0]
    scores.to_csv(output/'scores.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    decision.to_csv(output/'decisions.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    pd.DataFrame(thresholds).to_csv(output/'thresholds.csv',index=False)
    pd.concat(calibrations,ignore_index=True).to_csv(output/'calibration_scores.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    s.dump(output/'folds.json',s.clean(folds))
    import lightgbm, sklearn
    receipt_files(output,'prediction_receipt.json',dict(
        dataset_receipt_sha256=s.digest(dataset/'receipt.json'),production_eligible=False,
        policy_names=all_policies(),model_policies=model_policies(),fit_labels_used_after_cutoff=False,
        calibration_labels_used=False,dependencies={str(p):s.digest(p) for p in dependencies()},
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        versions=dict(numpy=np.__version__,pandas=pd.__version__,sklearn=sklearn.__version__,lightgbm=lightgbm.__version__)))


def clocks(c):
    time = c.available_at
    yield 'oof', time.ge(START).to_numpy()
    yield 'last_two_quarters', time.ge('2026-04-01T00:00:00Z').to_numpy()
    for date in QUARTERS:
        q = pd.Timestamp(date,tz='UTC')
        yield f'{q.year}Q{q.quarter}', (time.ge(q) & time.lt(q+pd.DateOffset(months=3))).to_numpy()


def comparison(c, controls, decisions, serial_mode=False):
    rows=[]
    baseline = c.loc[c.rule.eq('original_all')] if serial_mode else c
    for period,clock in clocks(baseline):
        universe=baseline.loc[clock]
        for name in ['original_all',*all_policies()]:
            if serial_mode:
                part=c.loc[c.rule.eq(name)]
                selected=part.loc[dict(clocks(part))[period]]
            else:
                selected=c.loc[clock & (np.ones(len(c),bool) if name=='original_all' else decisions[name].to_numpy())]
            summary=t.metrics(selected,universe)
            if serial_mode:
                summary.update(t.serial_retention(selected,universe))
            rows.append(dict(period=period,rule=name,**summary,
                **t.control_statistics(selected,controls,period),**t.rate_interval(universe,selected)))
    table=pd.DataFrame(rows)
    for period in table.period.unique():
        mask=table.period.eq(period)&table.rule.isin(model_policies())
        for what in ('tail','net'):
            table.loc[mask,'random_'+what+'_holm_p']=s.holm(table.loc[mask,'random_'+what+'_p'])
        table.loc[mask,'historical_gate_passed']=(table.loc[mask,'precision_ci_low'].gt(0)&
            table.loc[mask,'random_tail_holm_p'].lt(.01)&table.loc[mask,'random_net_holm_p'].lt(.01)&
            table.loc[mask,'mean_net_bp'].gt(0)&table.loc[mask,'recall'].ge(.1))
    return table


def serial_trigger(table):
    part=table.loc[table.period.eq('oof')]
    base=float(part.loc[part.rule.eq('original_all'),'precision'].iloc[0])
    passed=part.rule.isin(model_policies()) & part.precision.gt(base) & part.gt5.ge(10) & part.recall.ge(.1)
    return dict(needed=bool(passed.any()),triggered_by=part.loc[passed,'rule'].tolist(),
                replay_policies=all_policies() if passed.any() else [],
                meaning='Exploratory serial check, not acceptance or selection of a best policy')


def aligned_decisions(c, folder):
    d=pd.read_csv(folder/'decisions.csv.gz')
    if d.event_key.duplicated().any() or set(d.event_key)!=set(c.event_key):
        raise ValueError('prediction universe mismatch')
    d=d.set_index('event_key').loc[c.event_key].reset_index()
    for name in all_policies():
        d[name]=strict_bool(d[name])
    return d


def verify_admissions(c, prediction):
    """Rebuild all masks from frozen scores and strictly earlier score cohorts."""
    d=aligned_decisions(c,prediction)
    scores=pd.read_csv(prediction/'scores.csv.gz')
    if scores.event_key.duplicated().any() or set(scores.event_key)!=set(c.event_key):
        raise ValueError('score universe mismatch')
    scores=scores.set_index('event_key').loc[c.event_key].reset_index()
    if not pd.to_datetime(scores.available_at,utc=True).equals(c.available_at.reset_index(drop=True)):
        raise ValueError('score availability clock mismatch')
    cal=pd.read_csv(prediction/'calibration_scores.csv.gz')
    if cal.duplicated(['quarter','event_key']).any():
        raise ValueError('duplicate calibration event')
    thresholds=pd.read_csv(prediction/'thresholds.csv')
    if len(thresholds)!=48 or thresholds.duplicated(['quarter','rule']).any():
        raise ValueError('threshold family mismatch')
    for date in QUARTERS:
        q=pd.Timestamp(date,tz='UTC')
        _,cm,tm,cutoff=quarterly_blocks(c,q)
        tag=f'{q.year}Q{q.quarter}'
        cp=cal.loc[cal.quarter.eq(tag)]
        if set(cp.event_key)!=set(c.loc[cm,'event_key']):
            raise ValueError('calibration is not the complete earlier three-month cohort')
        cp=cp.set_index('event_key').loc[c.loc[cm,'event_key']]
        if not np.array_equal(pd.to_datetime(cp.available_at,utc=True).to_numpy(),c.loc[cm,'available_at'].to_numpy()):
            raise ValueError('calibration clock mismatch')
        for name in (*MODEL_NAMES,'risk'):
            for coverage in COVERAGES:
                policy=policy_name(name,coverage)
                expected,value=score_gate(cp[name],scores.loc[tm.to_numpy(),name],coverage)
                meta=thresholds.loc[thresholds.quarter.eq(tag)&thresholds.rule.eq(policy)].iloc[0]
                if (not np.isclose(value,meta.threshold,rtol=1e-12,atol=1e-15,equal_nan=True)
                        or pd.Timestamp(meta.calibration_start)!=cutoff or pd.Timestamp(meta.calibration_end)!=q
                        or not np.array_equal(expected,d.loc[tm.to_numpy(),policy].to_numpy())):
                    raise ValueError('admission differs from earlier-score threshold')
    before=c.available_at.lt(START).to_numpy()
    if not d.loc[before,all_policies()].to_numpy().all():
        raise ValueError('pre-study serial context changed')
    matrix,_,_=s.calibrate(c)
    if not np.array_equal(d.loc[~before,'prior_v21_risk_decile'].to_numpy(),matrix[~before,0]):
        raise ValueError('single-feature prior baseline drift')
    return d,scores


def evaluate(dataset,prediction,output):
    cfg=frozen_config(dataset)
    receipt=verify_output(prediction,'prediction_receipt.json')
    if receipt['dataset_receipt_sha256']!=s.digest(dataset/'receipt.json'):
        raise ValueError('prediction dataset mismatch')
    if output.exists():
        raise ValueError('refuse to overwrite model evaluation')
    c,controls,_=s.load_candidates(dataset)
    d,scores=verify_admissions(c,prediction)
    table=comparison(c,controls,d)
    diagnostics,calibration,groups=[],[],[]
    for period,clock in clocks(c):
        for name in (*MODEL_NAMES,'risk'):
            score=scores[name].to_numpy(float)
            valid=clock & c.valid_entry.to_numpy() & ~c.censored.to_numpy() & np.isfinite(score)
            indices=np.flatnonzero(valid)
            y=c.iloc[indices].net_r.gt(5).to_numpy(int)
            p=score[indices]
            order=indices[np.argsort(-p,kind='stable')]
            top=c.iloc[order[:int(np.ceil(len(order)*.1))]]
            row=dict(period=period,score=name,scored=len(y),base_rate=float(y.mean()) if len(y) else np.nan,
                auc_gt5=s.auc_score(y,p),pr_auc=average_precision_score(y,p) if y.sum() else np.nan,
                **t.metrics(top,c.loc[clock]),**t.control_statistics(top,controls,period))
            if name!='risk' and len(y):
                row.update(mean_predicted_probability=float(p.mean()),brier=brier_score_loss(y,p),
                           logloss=log_loss(y,p,labels=[0,1]))
                bucket=np.minimum(np.digitize(p,cfg['probability_bins'],right=False)-1,len(cfg['probability_bins'])-2)
                for i,(low,high) in enumerate(zip(cfg['probability_bins'][:-1],cfg['probability_bins'][1:])):
                    take=bucket==i
                    calibration.append(dict(period=period,model=name,low=low,high=high,n=int(take.sum()),
                        positives=int(y[take].sum()),mean_prediction=float(p[take].mean()) if take.any() else np.nan,
                        observed_rate=float(y[take].mean()) if take.any() else np.nan))
            diagnostics.append(row)
    for name in ['original_all',*all_policies()]:
        mask=c.available_at.ge(START).to_numpy() & (np.ones(len(c),bool) if name=='original_all' else d[name].to_numpy())
        part=c.loc[mask & c.valid_entry.to_numpy() & ~c.censored.to_numpy()].copy()
        part['month']=part.available_at.dt.strftime('%Y-%m')
        for column in ('asset','timeframe_min','venue','month'):
            for key,g in part.groupby(column):
                groups.append(dict(rule=name,dimension=column,value=str(key),closed=len(g),gt5=int(g.net_r.gt(5).sum()),
                    precision=float(g.net_r.gt(5).mean()),mean_net_bp=float(g.net_return.mean()*1e4)))
    output.mkdir(parents=True)
    table.to_csv(output/'comparison.csv',index=False)
    pd.DataFrame(diagnostics).to_csv(output/'score_diagnostics.csv',index=False)
    pd.DataFrame(calibration).to_csv(output/'probability_calibration.csv',index=False)
    pd.DataFrame(groups).to_csv(output/'groups.csv',index=False)
    s.dump(output/'serial_trigger.json',serial_trigger(table))
    receipt_files(output,'evaluation_receipt.json',dict(prediction_receipt_sha256=s.digest(prediction/'prediction_receipt.json'),
        dataset_receipt_sha256=s.digest(dataset/'receipt.json'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),dependencies={str(p):s.digest(p) for p in dependencies()}))
    print(table.loc[table.period.eq('oof')].to_string(index=False))


def replay(dataset,prediction,evaluation,output,workers):
    frozen_config(dataset)
    pr=verify_output(prediction,'prediction_receipt.json')
    er=verify_output(evaluation,'evaluation_receipt.json')
    if (pr['dataset_receipt_sha256']!=s.digest(dataset/'receipt.json') or
            er['prediction_receipt_sha256']!=s.digest(prediction/'prediction_receipt.json')):
        raise ValueError('serial input identity mismatch')
    trigger=json.loads((evaluation/'serial_trigger.json').read_text())
    if trigger!=serial_trigger(pd.read_csv(evaluation/'comparison.csv')) or not trigger['needed']:
        raise ValueError('canonical serial trigger absent')
    deps=dependencies()+[Path(serial.engine.__file__),Path(serial.base.__file__),
        Path('yoyo/evaluation/spike_v9_full_replay.py'),Path('yoyo/evaluation/spike_v7_fast.py'),
        Path('yoyo/evaluation/spike_v8_replay.py')]
    if not _committed(deps):
        raise ValueError('commit execution dependencies before replay')
    if (output/'receipt.json').exists():
        raise ValueError('refuse to overwrite completed serial result')
    c,controls,_=s.load_candidates(dataset)
    d,_=verify_admissions(c,prediction)
    pin=serial.SOURCE/'statistics/full_v1/statistics_receipt.json'
    if s.digest(pin)!=json.loads((s.EXP/'config.json').read_text())['statistics_receipt_sha256']:
        raise ValueError('source stream identity drift')
    sources=json.loads(pin.read_text())['source_receipts']
    if len(sources)!=3531 or len({x['key'] for x in sources})!=3531:
        raise ValueError('source stream set incomplete')
    dep_hashes={str(p):s.digest(p) for p in deps}
    import hashlib
    identity=hashlib.sha256(json.dumps(dict(dependencies=dep_hashes,
        prediction=s.digest(prediction/'prediction_receipt.json'),evaluation=s.digest(evaluation/'evaluation_receipt.json')),
        sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True,exist_ok=True)
    by={key:part for key,part in d.groupby('stream_key')}
    tasks=[]
    for src in sources:
        part=by.get(src['key'],d.iloc[:0])
        gates={name:dict(zip(part.event_key,part[name])) for name in all_policies()}
        tasks.append((src,gates,str(output),identity))
    done=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for result in as_completed([pool.submit(serial.one,t) for t in tasks]):
            done.append(result.result())
            if len(done)%250==0:
                print(f'model serial {len(done)}/3531',flush=True)
    frames=[pd.read_csv(output/'streams'/r['key']/'trades.csv.gz') for r in sorted(done,key=lambda r:r['key'])]
    trades=pd.concat(frames,ignore_index=True)
    for name in ('entry_time','exit_time','available_at','signal_bar_open'):
        trades[name]=pd.to_datetime(trades[name],utc=True)
    for name in ('valid_entry','censored'):
        trades[name]=strict_bool(trades[name])
    if trades.duplicated(['rule','event_key']).any():
        raise ValueError('duplicate serial event')
    joined=trades.merge(c[['event_key','net_r','net_return','censored']],on='event_key',how='left',validate='many_to_one',suffixes=('','_candidate'))
    if (not np.allclose(joined.net_r,joined.net_r_candidate,equal_nan=True) or
            not np.allclose(joined.net_return,joined.net_return_candidate,equal_nan=True) or
            not joined.censored.eq(joined.censored_candidate).all()):
        raise ValueError('candidate/serial economic parity failed')
    table=comparison(trades,controls,None,serial_mode=True)
    table.to_csv(output/'comparison.csv',index=False)
    trades.to_csv(output/'trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    s.dump(output/'receipt.json',dict(complete=True,streams=len(done),identity=identity,
        dependencies=dep_hashes,baseline_parity=True,candidate_economic_parity=True,
        prediction_receipt_sha256=s.digest(prediction/'prediction_receipt.json'),
        evaluation_receipt_sha256=s.digest(evaluation/'evaluation_receipt.json'),source_statistics_sha256=s.digest(pin),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        stream_receipts={r['key']:s.digest(output/'streams'/r['key']/'completion.json') for r in done},
        files={n:s.digest(output/n) for n in ['comparison.csv','trades.csv.gz']}))
    print(table.loc[table.period.eq('oof')].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=['predict','evaluate','serial'],required=True)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prediction',type=Path)
    p.add_argument('--evaluation',type=Path)
    p.add_argument('--workers',type=int,default=6)
    a=p.parse_args()
    if a.phase=='predict': train_predict(a.dataset,a.output)
    elif a.phase=='evaluate': evaluate(a.dataset,a.prediction,a.output)
    else: replay(a.dataset,a.prediction,a.evaluation,a.output,a.workers)
