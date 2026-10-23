"""Audit open-before-high priority and replay only demonstrably affected fills.

The initial three-segment adapter could select an intrabar short stop after
the open had already gapped through its target. Raw results remain preserved.
This audit reads observed opens, selects that exact failure condition, and
re-executes only affected unique geometries with the corrected native adapter.
It is a bug repair, not another parameter search or a custom fill evaluator.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_vectorbt_research import EXP,ROOT,OUTCOME
from yoyo.evaluation import monday_15m_volume_rejection as volume
from yoyo.evaluation import monday_5m_wick_gold_clock as clock
from yoyo.evaluation import monday_5m_wick_gold as quality
from yoyo.evaluation.monday_vectorbt_engine import execute
from yoyo.evaluation.range_framework import dump,file_stamp


def audit(symbol,repair):
    dest=EXP/'symbols'/symbol
    raw_path=dest/'trades.csv.gz'
    if not raw_path.exists():return dict(symbol=symbol,suspects=0,new_short_first_segments=0)
    frame=pd.read_csv(raw_path,float_precision='round_trip')
    candidates=frame[frame.engine_origin.str.startswith('vectorbt') & frame.side.eq(-1) & frame.reason.eq(1) & frame.exit_path_segment.eq(0)].drop_duplicates('trade_key')
    receipt_path=dest/'open-priority-audit.json'
    if candidates.empty:
        out=dict(symbol=symbol,suspects=0,new_short_first_segments=0);dump(receipt_path,out);return out
    identity=json.loads((dest/'identity.json').read_text())
    paths=[p['path'] for p in identity['inputs']]
    for p,expected in zip(paths,identity['inputs']):
        assert file_stamp(p)==expected,'Source changed during the study'
    gold=None
    if symbol=='XAUUSD':
        raw,_=clock.read_minutes(paths);gold,_=quality.clean_minutes(raw)
        observed={int(k):v for k,v in gold.groupby('quality_segment',sort=True)}
    else:
        parts=[pd.read_csv(p,usecols=['ts','open'],float_precision='round_trip') for p in paths]
        opens=pd.concat(parts).sort_values('ts').drop_duplicates('ts')
        opens.index=pd.DatetimeIndex(pd.to_datetime(opens.pop('ts'),unit='ms',utc=True)).as_unit('ns')
        observed={0:opens}
    suspects=[]
    for r in candidates.itertuples():
        source=observed[int(r.source_segment)]
        i=int(r.exit_original_i)
        if pd.Timestamp(r.exit_interval_start)!=source.index[i]:raise AssertionError('Exit/source index mismatch')
        # Price comparison only selects already-computed fills for native repair.
        # It does not assign a fill, exit reason or PnL.
        if float(source.open.iloc[i])<=r.target_price:
            suspects.append(r.trade_key)
    out=dict(symbol=symbol,suspects=len(suspects),new_short_first_segments=len(candidates),trade_keys=suspects,
             source_ledger=file_stamp(raw_path))
    dump(receipt_path,out)
    if repair and suspects:
        corrected_path=dest/'trades-open-priority.csv.gz'
        fix_receipt=dest/'open-priority-repair.json'
        if fix_receipt.exists():
            previous=json.loads(fix_receipt.read_text())
            assert previous['trade_keys']==suspects and previous['source_ledger']==out['source_ledger']
            assert corrected_path.exists();return previous
        if symbol!='XAUUSD':
            complete=volume.load(paths);complete['quality_segment']=0;observed={0:complete}
        cfg=json.loads((EXP/'config.json').read_text());replacement={};native_fills=[]
        selected=candidates[candidates.trade_key.isin(suspects)]
        for segment,entries in selected.groupby('source_segment'):
            result,fills=execute(observed[int(segment)],entries,symbol,cfg)
            for row in result.to_dict('records'):
                assert row['reason']==4,'Native open-priority repair must confirm a target fill'
                replacement[row['trade_key']]={k:row[k] for k in OUTCOME}
            native_fills.append(fills)
        for key,values in replacement.items():
            mask=frame.trade_key.eq(key) & frame.engine_origin.str.startswith('vectorbt')
            for col,val in values.items():frame.loc[mask,col]=val
            frame.loc[mask,'engine_origin']='vectorbt_1.2.0_native_open_priority_repair'
        frame.to_csv(corrected_path,index=False)
        pd.concat(native_fills,ignore_index=True).to_csv(dest/'vectorbt-open-priority-fills.csv.gz',index=False)
        out.update(repaired=len(replacement),affected_parameter_rows=int(frame.trade_key.isin(suspects).sum()),
                   builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip())
        dump(fix_receipt,out)
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--repair',action='store_true');args=ap.parse_args()
    paths=[__file__]
    if args.repair:paths.append(str(ROOT/'yoyo/evaluation/monday_vectorbt_engine.py'))
    if subprocess.run(['git','diff','--quiet','HEAD','--',*paths],cwd=ROOT).returncode:raise RuntimeError('Commit audit/repair builder first')
    symbols=[p.parent.name for p in (EXP/'symbols').glob('*/done.json')]
    results=[]
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(audit,s,args.repair) for s in symbols]
        for fut in as_completed(futures):
            r=fut.result();results.append(r)
            if r['suspects']:print(json.dumps(r),flush=True)
    if args.repair:
        rows=[]
        for s in symbols:
            folder=EXP/'symbols'/s
            path=folder/'trades-open-priority.csv.gz'
            if not path.exists():path=folder/'trades.csv.gz'
            if path.exists():rows.append(pd.read_csv(path,float_precision='round_trip'))
        pd.concat(rows,ignore_index=True).to_csv(EXP/'all-trades-open-priority.csv.gz',index=False)
    dump(EXP/('open-priority-repair.json' if args.repair else 'open-priority-audit.json'),dict(symbols=len(results),
         suspects=sum(x['suspects'] for x in results),repaired=sum(x.get('repaired',0) for x in results),details=results))
    print(json.dumps(dict(symbols=len(results),suspects=sum(x['suspects'] for x in results))),flush=True)


if __name__=='__main__':main()
