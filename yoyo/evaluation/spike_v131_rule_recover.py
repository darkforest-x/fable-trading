"""Finish a receipt-bound interrupted batch without discarding valid streams.

Only the observed cached-censor None/NaN conversion failure is admitted. Rerun
each failed symbol in a fresh directory, retain all failed outputs, then copy
complete streams into an explicitly composed result with per-symbol lineage.
The numerical signal/feature/exit definitions do not change during recovery.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess

from yoyo.evaluation.spike_v131_rule_study import run,CONFIG
from yoyo.evaluation.spike_v131_entry_stages import dump,read
from yoyo.evaluation.spike_v131_excursions import provenance
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python

EXPECTED_ERROR='TypeError("unsupported operand type(s) for -: \'NoneType\' and \'float\'")'


def recovery_symbols(primary):
    receipt=json.loads((Path(primary)/'receipt.json').read_text())
    assert not receipt['complete'] and receipt['errors']
    assert all(e['error']==EXPECTED_ERROR for e in receipt['errors']),receipt['errors']
    symbols=[e['symbol'] for e in receipt['errors']]
    assert len(symbols)==len(set(symbols))
    return symbols


def compose(primary,repair,output):
    primary,repair,output=map(Path,(primary,repair,output))
    pi=json.loads((primary/'identity.json').read_text());ri=json.loads((repair/'identity.json').read_text())
    pr=json.loads((primary/'receipt.json').read_text());rr=json.loads((repair/'receipt.json').read_text())
    expected=set(recovery_symbols(primary))
    assert pi['builder_commit'].startswith('724682b4f8')
    assert rr['complete'] and not rr['errors']
    assert set(ri['symbols'])==expected and ri['subset'] and not pi['subset']
    for key in ['config','manifest','metadata','btc']:assert pi[key]==ri[key],key
    assert set(r['symbol'] for r in rr['receipts'])==expected
    assert set(r['symbol'] for r in pr['receipts'])==set(pi['symbols'])-expected
    assert len(pr['receipts'])+len(rr['receipts'])==len(pi['symbols'])
    # Check every selected complete receipt before creating the composed output.
    chosen=[]
    for root,identity,receipt in [(primary,pi,pr),(repair,ri,rr)]:
        for r in receipt['receipts']:
            source=root/'streams'/r['symbol']
            assert json.loads((source/'receipt.json').read_text())==r
            assert r['detectors_and_features_recomputed']
            assert r['input']==provenance(r['input']['path'])
            assert sorted(x['timeframe_min'] for x in r['streams'])==sorted(pi['config']['timeframes'])
            for stream in r['streams']:
                assert len(read(source/f"{stream['timeframe_min']}m.csv.gz",['cohort']))==stream['rows']
            chosen.append((source,r,identity['builder_commit']))
    output.mkdir(parents=True,exist_ok=False)
    sources=sorted(set([Path(__file__),CONFIG,*_local_transitive_python((Path(__file__),))]),key=str)
    commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    identity=pi|dict(generated_at=datetime.now(timezone.utc).isoformat(),builder_commit=commit,
        code=[provenance(p) for p in sources],generation_runs=[str(primary/'identity.json'),str(repair/'identity.json')],
        composition='Primary completed streams plus rerun of every cached-censor type failure; no incomplete stream accepted.')
    dump(output/'identity.json',identity)
    receipts=[]
    for source,r,builder in chosen:
        destination=output/'streams'/r['symbol'];shutil.copytree(source,destination)
        final=r|dict(generated_in=str(source),generation_commit=builder)
        dump(destination/'receipt.json',final);receipts.append(final)
    dump(output/'receipt.json',dict(complete=True,errors=[],receipts=receipts,
        recovery=dict(primary=str(primary),repair=str(repair),failed_symbols=sorted(expected))))
    print(json.dumps(dict(composed=len(receipts),repaired=len(expected),output=str(output))),flush=True)


def recover(primary,repair,output,workers):
    # Refuse uncommitted builders before either repair or composition.
    sources=sorted(set([Path(__file__),CONFIG,*_local_transitive_python((Path(__file__),))]),key=str)
    for p in sources:subprocess.run(['git','ls-files','--error-unmatch',str(p)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',*map(str,sources)],check=True)
    symbols=recovery_symbols(primary)
    prior=json.loads((Path(primary)/'reuse.json').read_text())['sources']
    run(repair,workers,symbols,[Path(r['source']) for r in prior])
    compose(primary,repair,output)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('primary',type=Path);p.add_argument('repair',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();recover(a.primary,a.repair,a.output,a.workers)
