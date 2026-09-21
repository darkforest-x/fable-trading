"""Recompute serial metrics from immutable trades with identity-based tail recall.

Original V3 serial_v1 economic paths stay intact. Its 'recall' was a ratio of
winner counts, which can include newly opened winners. This statistics-only
revision separates retained/lost/gained original events; no price engine runs.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import pandas as pd
from yoyo.evaluation import spike_10r_search as s
from yoyo.evaluation.spike_10r_serial import serial_retention
from yoyo.evaluation.spike_six_filter_statistics import strict_bool
from yoyo.evaluation.spike_v8_six_filters import _committed


def run(source,dataset,output):
    deps=[Path(__file__),Path('yoyo/evaluation/spike_10r_serial.py'),Path('tests/evaluation/test_spike_10r_serial.py'),Path(s.__file__)]
    if not _committed(deps):
        raise ValueError('commit corrected reporter before rebuilding metrics')
    if output.exists():
        raise ValueError('refuse to overwrite corrected statistics')
    r=json.loads((source/'receipt.json').read_text())
    if not r['complete'] or r['streams']!=3531 or not r['candidate_economic_parity']:
        raise ValueError('source serial replay incomplete')
    for name,sha in r['files'].items():
        if s.digest(source/name)!=sha:
            raise ValueError('serial aggregate drift')
    for key,sha in r['stream_receipts'].items():
        folder=source/'streams'/key
        if s.digest(folder/'completion.json')!=sha:
            raise ValueError('serial stream receipt drift')
        stream=json.loads((folder/'completion.json').read_text())
        if not stream['baseline_parity'] or stream['identity']!=r['identity']:
            raise ValueError('serial stream identity mismatch')
        for name,expected in stream['files'].items():
            if s.digest(folder/name)!=expected:
                raise ValueError('serial leaf drift')
    _,controls,_=s.load_candidates(dataset)
    t=pd.read_csv(source/'trades.csv.gz')
    for name in ('available_at','entry_time','exit_time','signal_bar_open'):
        t[name]=pd.to_datetime(t[name],utc=True)
    for name in ('valid_entry','censored'):
        t[name]=strict_bool(t[name])
    baseline=t.loc[t.rule.eq('original_all')]
    rows=[]
    for period,clock in s.periods(baseline):
        universe=baseline.loc[clock]
        for name,part in t.groupby('rule'):
            selected=part.loc[dict(s.periods(part))[period]]
            rows.append(dict(period=period,rule=name,**(s.metrics(selected,universe) | serial_retention(selected,universe)),
                **s.control_statistics(selected,controls,period),**s.rate_interval(universe,selected)))
    comparison=pd.DataFrame(rows)
    later=comparison.period.eq('later') & ~comparison.rule.eq('original_all')
    for what in ('tail','net'):
        comparison.loc[later,'random_'+what+'_holm_p']=s.holm(comparison.loc[later,'random_'+what+'_p'])
    comparison.loc[later,'historical_gate_passed']=(comparison.loc[later,'precision_ci_low'].gt(0) &
        comparison.loc[later,'random_tail_holm_p'].lt(.01) & comparison.loc[later,'random_net_holm_p'].lt(.01) &
        comparison.loc[later,'mean_net_bp'].gt(0) & comparison.loc[later,'recall'].ge(.1))
    output.mkdir(parents=True)
    comparison.to_csv(output/'comparison.csv',index=False)
    s.dump(output/'receipt.json',dict(source_serial_receipt_sha256=s.digest(source/'receipt.json'),
        dataset_receipt_sha256=s.digest(dataset/'receipt.json'),source_streams=r['streams'],
        price_paths_recomputed=False,correction='winner count ratio is not event-identity recall',
        dependencies={str(p):s.digest(p) for p in deps},
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),files={'comparison.csv':s.digest(output/'comparison.csv')}))
    print(comparison.loc[later,['rule','closed','gt10','precision','retained_gt10','lost_gt10','gained_gt10','recall']].to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    run(a.source,a.dataset,a.output)
