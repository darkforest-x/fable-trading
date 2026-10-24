"""Restore order event time intervals from frozen native audit indices.

No fills or returns are computed. Crypto submission-to-resolution paths are
continuous by the preparer's execution-segment rule, so index offsets map to
five-minute close times. Gold uses the exact cleaned source index, preserving
scheduled closures. Native filled rows independently validate these mappings.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_ma6_retest import EXP,STEP,source_frame,finite
from yoyo.evaluation.range_framework import dump


def gold_indices():
    folder=EXP/'order-time-index';folder.mkdir(exist_ok=True)
    meta=folder/'gold-receipt.json';cache=folder/'gold-indices.npz'
    identity=json.loads((EXP/'symbols/XAUUSD/identity.json').read_text())['inputs']
    if cache.exists() and meta.exists() and json.loads(meta.read_text())['inputs']==identity:
        with np.load(cache) as data:return {int(k):pd.DatetimeIndex(data[k],tz='UTC') for k in data.files}
    f,_=source_frame('XAUUSD',[r['path'] for r in identity])
    parts={int(k):g.index for k,g in f.groupby('quality_segment',sort=True)}
    np.savez_compressed(cache,**{str(k):v.asi8 for k,v in parts.items()})
    dump(meta,finite(dict(inputs=identity,rows=len(f),segments=len(parts),purpose='Order audit timestamp mapping; no execution')))
    return parts


def enrich_orders(e,t,completed):
    """Return order rows with observed resolution intervals and actual amendments."""
    gold=gold_indices() if e.symbol.eq('XAUUSD').any() else {}
    x=e.copy().set_index('trade_id',drop=False)
    native=t[t.policy.eq('monday_full')].set_index('parent_id')
    validated=0
    for done in completed:
        for path in sorted(done.parent.glob('segment-*/native-audit.json')):
            seg=int(path.parent.name.split('-')[1]);audit=json.loads(path.read_text())
            amendments={}
            for u in audit.get('pending_amendments',[]):
                key=u['trade_id'].rsplit('-',1)[0]
                # Target names themselves contain underscores, not hyphens.
                if not u['trade_id'].endswith('-monday_full'):continue
                amendments.setdefault(key,[]).append(u)
            for event in audit.get('opportunities',[]):
                if not event['trade_id'].endswith('-monday_full'):continue
                key=event['trade_id'][:-len('-monday_full')]
                r=x.loc[key]
                def close_time(i):
                    if r.symbol=='XAUUSD':return gold[seg][int(i)]+STEP
                    return pd.Timestamp(r.submitted_at)+(int(i)-int(r.entry_i))*STEP
                resolution_i=int(event['fill_i'] if event['status']=='filled' else event.get('cancelled_i',event['expire_i']))
                close=close_time(resolution_i)
                x.loc[key,'resolution_interval_start']=str(close-STEP)
                x.loc[key,'resolution_interval_end']=str(close)
                x.loc[key,'resolution_i']=resolution_i
                if event['status']=='filled':
                    if pd.Timestamp(native.loc[key,'entry_time'])!=close:raise AssertionError(f'Native timestamp mismatch {key}')
                    validated+=1
                else:x.loc[key,'canceled_at_utc']=str(close)
                events=[]
                for u in amendments.get(key,[]):
                    events.append(dict(at=str(close_time(u['bar_i'])),entry=u['entry'],stop=u['stop']))
                x.loc[key,'actual_pending_amendments']=json.dumps(events,separators=(',',':'))
                x.loc[key,'actual_pending_amendment_count']=len(events)
    x['timestamp_note']='Resolution is a 5m interval; amendment at is a completed close. Cancelled_at is conservative interval end.'
    for column in ['submitted_at','resolution_interval_start','resolution_interval_end']:
        x[column+'_beijing']=pd.to_datetime(x[column],utc=True,format='mixed').dt.tz_convert('Asia/Shanghai').astype(str)
    dump(EXP/'order-time-audit.json',dict(native_filled_timestamps_matched=validated,order_rows=len(x),source='Frozen native-audit.json and exact/source-continuous indices'))
    return x.reset_index(drop=True)


if __name__=='__main__':
    print({key:len(value) for key,value in gold_indices().items()})
