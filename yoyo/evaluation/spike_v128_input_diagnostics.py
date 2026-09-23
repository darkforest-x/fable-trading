"""Explain ex-post feature-readiness exclusions without changing signal rules.

Inspect the frozen full evaluation window only for streams whose ready count is
zero. This is an input-quality audit, not a causal asset-selection feature or a
claim about listing/delisting status. Timestamp completeness is not tradability.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(run, output):
    run, output = Path(run), Path(output)
    identity = json.loads((run/'identity.json').read_text())
    manifest = json.loads((run/'manifest.json').read_text())
    if not manifest['complete']:
        raise ValueError('completed replay required')
    cfg = identity['config']
    source = Path(identity['input_manifest'])
    if digest(source) != identity['input_manifest_sha256']:
        raise ValueError('input manifest changed')
    inputs = {row['symbol']:row for row in json.loads(source.read_text())['streams']}
    zero = {}
    for key, sha in manifest['receipts'].items():
        path=run/'streams'/key/'receipt.json'
        if digest(path)!=sha:
            raise ValueError('receipt changed')
        receipt=json.loads(path.read_text()); summary=receipt['summary']
        if summary['valid_ready_window_bars']==0:
            zero.setdefault(receipt['symbol'],[]).append(receipt['minutes'])
    start=pd.Timestamp(cfg['start']).value//10**6; end=pd.Timestamp(cfg['end']).value//10**6
    rows=[]
    for symbol in sorted(zero):
        item=inputs[symbol]; path=Path(item['path'])
        if digest(path)!=item['sha256']:
            raise ValueError('frozen series changed')
        frame=pd.read_csv(path)
        frame=frame.loc[(frame.ts>=start)&(frame.ts<end)]
        flat=(frame[['open','high','low','close']].max(axis=1)==frame[['open','high','low','close']].min(axis=1))
        constant=frame.close.nunique()==1
        zero_volume=frame.volume.eq(0)
        rows.append({'symbol':symbol,'zero_ready_timeframes':','.join(map(str,sorted(zero[symbol]))),
                     'rows':len(frame),'flat_ohlc_rows':int(flat.sum()),'zero_volume_rows':int(zero_volume.sum()),
                     'unique_closes':int(frame.close.nunique()),'close_changes':int(frame.close.diff().iloc[1:].ne(0).sum()),
                     'flat_zero_input':bool(len(frame)>0 and constant and flat.all() and zero_volume.all()),
                     'source_sha256':item['sha256']})
    output.mkdir(parents=True,exist_ok=False)
    pd.DataFrame(rows).to_csv(output/'zero_ready_inputs.csv',index=False)
    receipt={'run_manifest_sha256':digest(run/'manifest.json'),'input_manifest_sha256':digest(source),
             'start':cfg['start'],'end':cfg['end'],'zero_ready_symbols':len(rows),
             'flat_zero_input_symbols':sum(row['flat_zero_input'] for row in rows),
             'other_zero_ready_symbols':sum(not row['flat_zero_input'] for row in rows),
             'audit_builder_sha256':digest(__file__),
             'files':{'zero_ready_inputs.csv':digest(output/'zero_ready_inputs.csv')}}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(); print(json.dumps(build(args.run,args.output),indent=2))
