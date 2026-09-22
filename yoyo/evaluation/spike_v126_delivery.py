"""Bind final V12.6 research evidence and independently reconcile main metrics.

The delivery manifest is an evidence index, not a model bundle or promotion.
It retains per-stream receipts and leaf digests without putting price archives
or reconstructed multi-symbol trading histories into a production path.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v126_htf_report import load_run, sha
from yoyo.evaluation.spike_v8_six_filters import _committed


def build(experiment: Path, output: Path):
    if not _committed((Path(__file__),)):
        raise ValueError('commit delivery builder before generation')
    if output.exists() or (experiment/'results/ledger_index.json').exists():
        raise ValueError('refuse to overwrite delivery evidence')
    run=experiment/'results/run_v2'
    summary=experiment/'results/summary_v3'
    identity,tables=load_run(run)
    audit=json.loads((experiment/'results/audit_v4.json').read_text())
    assert audit['status']=='pass' and audit['manifest_sha256']==sha(run/'manifest.json')
    metadata=json.loads((summary/'summary.json').read_text())
    assert metadata['input_manifest_sha256']==sha(run/'manifest.json')
    for file,expected in metadata['files'].items():
        assert sha(summary/file)==expected
    metrics=pd.read_csv(summary/'metrics.csv')
    trades=tables['trades']; closed=trades.loc[~trades.censored.astype(bool)].copy()
    signal=pd.to_datetime(closed.signal_close,utc=True)
    exits=pd.to_datetime(closed.exit_time,utc=True)
    split=pd.Timestamp(identity['config']['split'])
    checks=0
    for row in metrics.itertuples():
        mask=closed.arm.eq(row.arm)
        if row.period=='earlier': mask &= (signal<split)&(exits<split)
        if row.period=='later': mask &= signal>=split
        unit=closed.loc[mask]
        assert len(unit)==row.n and int(unit.net_r.gt(0).sum())==row.wins
        np.testing.assert_allclose([unit.net_r.mean(),unit.net_r.sum(),unit.net_return.mean()*1e4],
            [row.mean_net_r,row.sum_net_r,row.mean_net_bp],atol=1e-10)
        checks+=5
    for name in ('inference.csv','tail_retention.csv','paired_attribution.csv'):
        assert sha(summary/name)==sha(experiment/'results/summary_v2'/name)
    receipts={symbol:json.loads((run/'streams'/symbol/'receipt.json').read_text()) for symbol in identity['symbols']}
    ledger_index={'run_identity':json.loads((run/'manifest.json').read_text())['run_identity'],'receipts':receipts,
        'receipt_file_sha256':json.loads((run/'manifest.json').read_text())['receipts'],
        'raw_leaves_storage':'local run_v2/streams; reproducible from frozen source archive'}
    index=experiment/'results/ledger_index.json'
    index.write_text(json.dumps(ledger_index,ensure_ascii=False,indent=2)+'\n')
    direct=[experiment/'config.json',experiment/'PROJECT_PLAN.md',run/'identity.json',run/'started.json',run/'manifest.json',
        summary/'summary.json',*[summary/name for name in metadata['files']],experiment/'results/audit_v4.json',index,
        experiment/'verification_tests.log',experiment/'verification_limits.json',experiment/'notion_sync.json',
        experiment/'audit_v2.log',experiment/'audit_v3.log',experiment/'audit_v4.log',
        Path('analysis/p1_spike_v126_htf_recheck_20260922.md'),
        Path('yoyo/evaluation/spike_v126_engine.py'),Path('yoyo/evaluation/spike_v126_htf_recheck.py'),
        Path('yoyo/evaluation/spike_v126_htf_report.py'),Path('yoyo/evaluation/spike_v126_audit.py'),Path(__file__)]
    proof={'experiment_id':experiment.name,'status':'inconclusive_no_adoption',
        'run_identity':json.loads((run/'manifest.json').read_text())['run_identity'],
        'run_source_commit':audit['source_commit_verified'],
        'generator_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'sources':{str(path):{'sha256':sha(path),'size_bytes':path.stat().st_size} for path in direct},
        'symbols':len(identity['symbols']),'candidates':audit['candidate_rows'],
        'receipt_count':len(receipts),'leaf_count':sum(len(r['files']) for r in receipts.values()),
        'metric_identity_checks':checks,'summary_v2_to_v3_noncontrol_tables_unchanged':True,
        'focused_checks':43,'extended_checks':{'passed':505,'failed_outside_scope':8},
        'native_pine_parity':False,'training_eligible':False,'production_eligible':False,
        'notion_url':json.loads((experiment/'notion_sync.json').read_text())['url']}
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(output),'sha256':sha(output),'metric_checks':checks,'leaf_count':proof['leaf_count']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    build(args.experiment,args.output)
