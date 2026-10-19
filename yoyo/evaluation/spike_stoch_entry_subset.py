"""Freeze an owner-limited completed subset without selecting on returns.

Selection uses completed symbol receipt modification time, then symbol name.
Only finished four-timeframe streams qualify. Original interrupted outputs
remain untouched; the analysis directory references selected streams by link.
This operational completion subset is not a random or representative universe.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def run(source, output, count):
    source=Path(source).resolve();output=Path(output)
    assert count>0 and not output.exists()
    subprocess.run(['git','ls-files','--error-unmatch',__file__],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['git','diff','--exit-code','HEAD','--',__file__],check=True)
    files=sorted(source.glob('streams/*/receipt.json'),key=lambda p:(p.stat().st_mtime_ns,p.parent.name))
    assert len(files)>=count
    identity=json.loads((source/'identity.json').read_text())
    selected=files[:count];receipts=[]
    for p in selected:
        r=json.loads(p.read_text())
        assert r['symbol']==p.parent.name
        assert sorted(x['timeframe_min'] for x in r['streams'])==sorted(identity['config']['timeframes'])
        for m in identity['config']['timeframes']:assert (p.parent/f'{m}m.csv.gz').stat().st_size>0
        receipts.append(r)
    output.mkdir(parents=True);(output/'streams').mkdir()
    for p in selected:(output/'streams'/p.parent.name).symlink_to(p.parent,target_is_directory=True)
    note=dict(generated_at=datetime.now(timezone.utc).isoformat(),reason='Owner requested 460 is enough after progress update; full generation stopped before return inspection.',
        source=str(source),source_complete=False,source_exit_code=143,
        selection='First completed symbol receipts by mtime_ns, then symbol; no return-based selection.',
        requested_count=count,completed_at_stop=len(files),excluded_completed_symbols=[p.parent.name for p in files[count:]],
        selected=[dict(symbol=p.parent.name,receipt_mtime_ns=p.stat().st_mtime_ns) for p in selected],
        limitation='Operational completion subset; approximately source dispatch order, not random or representative; later-dispatched symbols omitted.',
        subset_builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(output/'subset_receipt.json',note)
    dump(output/'identity.json',identity|dict(subset=True,symbols=[r['symbol'] for r in receipts],subset_receipt='subset_receipt.json'))
    dump(output/'receipt.json',dict(complete=True,scope='owner_limited_completed_subset',full_universe_complete=False,errors=[],receipts=receipts))
    print(json.dumps(dict(output=str(output),selected=len(receipts),excluded_completed=len(files)-count)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);p.add_argument('--count',type=int,default=460)
    a=p.parse_args();run(a.source,a.output,a.count)
