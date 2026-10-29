"""One explicit endpoint expansion with the V1 alarm/ranking policy unchanged.

Recover half the actual15minute drawdown, rather than return near the alarm
reference. Sources are only needed for previously unretained future windows;
features, eligibility and selections are reused. Save compact windows to avoid
another full source pass for future label audits. This is not a trade replay.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.alarm_laggard_selection import STEP, outcome_bounds, select_and_match
from yoyo.evaluation.alarm_laggard_statistics import summarize
from yoyo.evaluation.market_crash_alarm import save

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'experiments/active/exp-alarm-laggard-recovery-20261029-v2'
PARENT=ROOT/'experiments/active/exp-alarm-laggard-selection-20261029-v1'
_ALARMS=_CFG=None


def half_drawdown_bounds(times,low,close,alarm,reference,cfg):
    """Only labels use future lows/closes; both bounds contain exact path truth."""
    result=outcome_bounds(times,low,close,alarm,reference,cfg)
    end=alarm+cfg['outcomes']['deep_horizon_minutes']*60000
    recovery_end=alarm+cfg['outcomes']['recovery_horizon_minutes']*60000
    possible=(times<end)&(times+STEP>alarm)
    full=(times>=alarm)&(times+STEP<=end)
    possible_low=np.nanmin(low[possible]) if np.isfinite(low[possible]).any() else np.nan
    full_low=np.nanmin(low[full]) if np.isfinite(low[full]).any() else np.nan
    lower=False
    if result['deep_lower']:
        later=(times+STEP>end)&(times+STEP<=recovery_end)
        lower=bool((close[later]>=(reference+full_low)/2).any())
    first=np.flatnonzero(possible&(low<=reference*(1-cfg['outcomes']['deep_fraction'])))
    upper=False
    if len(first):
        later=(times>=times[first[0]])&(times<recovery_end)
        upper=bool((close[later]>=(reference+possible_low)/2).any())
    if result['future_censored'] and result['deep_upper']:
        upper=True
    result.update(joint_lower=int(lower),joint_upper=int(upper),
                  trough_possible=possible_low,trough_full=full_low)
    return result


def initialize(alarms,cfg):
    global _ALARMS,_CFG
    _ALARMS=alarms.set_index('alarm_id');_CFG=cfg


def process(job):
    from yoyo.evaluation.monday_ma6_retest import source_frame
    symbol,paths=job
    receipt=json.loads((PARENT/'symbols'/f'{symbol}.json').read_text())
    for source in receipt['sources']:
        stat=(ROOT/source['path']).stat()
        if stat.st_size!=source['bytes'] or stat.st_mtime_ns!=source['mtime_ns']:
            raise ValueError(f'Parent input changed: {source["path"]}')
    output=OUT/'symbols'/f'{symbol}.csv.gz'
    if output.exists(): return dict(symbol=symbol,cached=True)
    c=pd.read_csv(PARENT/'symbols'/f'{symbol}.csv.gz')
    if c.empty:
        c['trough_possible']=[];c['trough_full']=[]
        c.to_csv(output,index=False,compression='gzip')
        return dict(symbol=symbol,rows=0)
    a=_ALARMS.loc[c.alarm_id]
    times=a.feature_close_ms.to_numpy()[:,None]+np.arange(14)[None,:]*STEP
    cache=OUT/'windows'/f'{symbol}.npz'
    if cache.exists():
        w=np.load(cache);low=w['low'];close=w['close']
        if not np.array_equal(w['alarm_id'],c.alarm_id.to_numpy()): raise ValueError('Window cache identity changed')
    else:
        f,_=source_frame(symbol,paths)
        f.index=f.index.astype('int64')//10**6
        reference=f.close.reindex(a.feature_close_ms.to_numpy()-STEP).to_numpy()
        np.testing.assert_allclose(reference,c.reference.to_numpy(),rtol=1e-12,atol=0)
        low=f.low.reindex(times.ravel()).to_numpy().reshape(times.shape)
        close=f.close.reindex(times.ravel()).to_numpy().reshape(times.shape)
        np.savez_compressed(cache,alarm_id=c.alarm_id.to_numpy(),low=low,close=close)
    labels=[half_drawdown_bounds(times[i],low[i],close[i],int(a.alarm_ms.iloc[i]),float(c.reference.iloc[i]),_CFG) for i in range(len(c))]
    labels=pd.DataFrame(labels)
    for name in ['deep_lower','deep_upper','initial_touch_ambiguous','future_censored']:
        np.testing.assert_array_equal(labels[name].to_numpy(),c[name].to_numpy())
    for name in labels: c[name]=labels[name]
    c.to_csv(output,index=False,compression='gzip')
    return dict(symbol=symbol,rows=len(c),reference_parity=True,deep_label_parity=True)


def main():
    v2=json.loads((OUT/'config.json').read_text())
    cfg=json.loads((PARENT/'config.json').read_text())
    paths=['yoyo/evaluation/alarm_laggard_recovery.py',str((OUT/'config.json').relative_to(ROOT))]
    subprocess.run(['git','diff','--exit-code','HEAD','--',*paths],check=True,stdout=subprocess.DEVNULL)
    alarms=pd.read_csv(PARENT/'alarms.csv')
    jobs=json.loads((PARENT/'source_jobs.json').read_text())
    for name in ['symbols','windows']: (OUT/name).mkdir(exist_ok=True)
    checks=[]
    with ProcessPoolExecutor(v2['workers'],initializer=initialize,initargs=(alarms,cfg)) as pool:
        futures={pool.submit(process,job):job[0] for job in jobs}
        for i,future in enumerate(as_completed(futures),1):
            checks.append(future.result())
            if i%25==0: print(f'Additional future-window labels {i}/{len(jobs)}',flush=True)
    frames=[pd.read_csv(OUT/'symbols'/f'{s}.csv.gz') for s,_ in jobs]
    candidates=pd.concat([f for f in frames if not f.empty],ignore_index=True)
    candidates['not_deep']=candidates.not_deep.astype(bool)
    original=pd.read_csv(PARENT/'candidates.csv.gz')
    pd.testing.assert_frame_equal(candidates[['alarm_id','symbol']],original[['alarm_id','symbol']])
    feature_columns=['reference','lag','recovery','sigma','liquidity','beta','return30','low30','historical_tail_count']
    np.testing.assert_allclose(candidates[feature_columns],original[feature_columns],rtol=1e-12,atol=1e-14)
    np.testing.assert_array_equal(candidates.not_deep,original.not_deep)
    candidates.to_csv(OUT/'candidates.csv.gz',index=False,compression='gzip')
    records,selected=select_and_match(candidates,cfg)
    old=pd.read_csv(PARENT/'selections.csv.gz')
    keys=['alarm_id','rule','symbol']
    pd.testing.assert_frame_equal(selected.sort_values(keys).reset_index(drop=True),old.sort_values(keys).reset_index(drop=True))
    records.to_csv(OUT/'matched_alarm_records.csv',index=False)
    result=summarize(records,candidates,alarms,cfg,OUT)
    result['frozen_config']=v2
    result.update(experiment_id=v2['experiment_id'],parent=str(PARENT.relative_to(ROOT)),
                  builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                  selection_and_control_parity=True,feature_and_eligibility_parity=True,
                  exploratory_endpoint_expansion=True,raw_candidate_snapshots=len(candidates),
                  training_eligible=False,production_eligible=False)
    save(OUT/'source_checks.json',checks);save(OUT/'summary.json',result)
    print('Saved endpoint expansion; original candidates and selections identical.',flush=True)


if __name__=='__main__': main()
