"""Committed replay of preliminary real-feature/source causality checks.

The original /tmp review preceded script commit and was preliminary. This
formal audit freezes one deterministic A/B/C/D example, cuts history at its
known decision close, checks all54 features and checks saved native payloads.
It never tunes thresholds or evaluates new signal variants.
"""
from pathlib import Path
import subprocess
import sys
import json,gzip,hashlib
import pandas as pd
import numpy as np
root=Path(__file__).resolve().parents[3]
experiment=Path(__file__).resolve().parent
sources=(Path(__file__).resolve(),root/'yoyo/evaluation/altseason_engine.py',root/'yoyo/data/altcoin_features.py')
for source in sources:
    committed=subprocess.check_output(['git','show','HEAD:'+str(source.relative_to(root))],cwd=root)
    if committed!=source.read_bytes():raise ValueError('Commit exact audit/engine/features source before formal audit: '+str(source))
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
sys.path.insert(0,str(root))
from yoyo.evaluation.altseason_engine import build_features,candidate_events,BAR_COLUMNS
base=experiment/'results/markets'
input_hashes={}
cases=[('gate','BICO_USDT',60,'focus_sma60'),('binance','AKEUSDT',60,'dense_sma60'),('binance','BANKUSDT',240,'pullback_sma60'),('gate','FONE_USDT',60,'young_breakout_sma20')]
results=[]
for venue,symbol,minutes,arm in cases:
    folder=base/venue/symbol;event_file=folder/'events.csv.gz'
    input_hashes[str(event_file.relative_to(root))]=hashlib.sha256(event_file.read_bytes()).hexdigest()
    events=pd.read_csv(event_file)
    e=events.loc[events.arm.eq(arm)&events.minutes.eq(minutes)&events.valid.eq(True)].sort_values('event_id').iloc[0]
    full=pd.read_pickle(e.features_path);p60=Path(e.features_path.replace('_240_','_60_'));hourly=pd.read_pickle(p60)
    close=full.index[int(e.decision_i)]+pd.Timedelta(minutes=minutes)
    prefix=hourly.loc[hourly.index<close,list(BAR_COLUMNS)].copy();prefix.attrs.update(minutes=60,period_seconds=3600)
    rebuilt=build_features(prefix)[minutes]
    cols=[c for c in rebuilt if c in full]
    errors=[]
    for c in cols:
        x=full.loc[rebuilt.index,c];y=rebuilt[c]
        if not np.allclose(x,y,equal_nan=True,rtol=1e-10,atol=1e-12):errors.append(c)
    events_prefix=candidate_events(rebuilt,minutes)
    exists=((events_prefix.decision_i==int(e.decision_i))&(events_prefix.arm==arm)).any()
    coverage=json.loads((folder/'coverage.json').read_text());rawp=Path(coverage['identity']['source_path']);raw=pd.read_csv(rawp)
    raw.index=pd.to_datetime(raw.ts,unit='ms',utc=True)
    intersect=hourly.index.intersection(raw.index)
    for c in BAR_COLUMNS:
        if not np.allclose(hourly.loc[intersect,c],raw.loc[intersect,c],equal_nan=True,rtol=1e-10,atol=1e-12):errors.append('source:'+c)
    metadata=coverage['source_manifest'];pages=metadata['pages'];native=[]
    datadir=rawp.parents[2]
    for page in pages:
        p=datadir/page['relative_body_path']
        input_hashes[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
        payload=json.loads(gzip.decompress(p.read_bytes()))
        records=payload if isinstance(payload,list) else payload.get('body',payload.get('data'))
        native.extend(records)
    byts={int(row[0]) if venue=='binance' else int(row['t'])*1000:row for row in native}
    for at in (0,len(raw)//2,len(raw)-1):
        rr=raw.iloc[at];n=byts[int(rr.ts)]
        vals=[n[x] for x in (1,2,3,4,5,7)] if venue=='binance' else [n[x] for x in ('o','h','l','c','v','sum')]
        for c,value in zip(BAR_COLUMNS,vals):
            if not np.isclose(float(rr[c]),float(value),rtol=1e-10,atol=1e-12):errors.append('native:'+c)
    for source in (Path(e.features_path),p60,rawp,folder/'coverage.json'):
        input_hashes[str(source.relative_to(root))]=hashlib.sha256(source.read_bytes()).hexdigest()
    results.append(dict(venue=venue,symbol=symbol,minutes=minutes,arm=arm,decision=close.isoformat(),source_sha_ok=hashlib.sha256(rawp.read_bytes()).hexdigest()==coverage['identity']['source_sha256'],prefix_feature_columns=len(cols),prefix_bars=len(rebuilt),prefix_candidate_retained=bool(exists),native_payload_points=3,errors=errors))
failures=sum(len(r['errors'])+int(not r['source_sha_ok'])+int(not r['prefix_candidate_retained']) for r in results)
output=experiment/'results/audit_prefix.json'
output.write_text(json.dumps(dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),code_commit=commit,
    code_sha256={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
    input_sha256=input_hashes,
    prior_review='The uncommitted /tmp inspection was preliminary; this is the formal committed replay.',
    selection='First event_id-sorted valid event for frozen BICO Gate A1H, AKE Binance B1H, BANK Binance C4H, FONE Gate D1H.',
    cases=results,failures=failures),indent=2)+'\n')
print(json.dumps(dict(output=str(output),cases=len(results),failures=failures)))
if failures:raise SystemExit(1)
