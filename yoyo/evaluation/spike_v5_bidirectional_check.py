"""One-source V5 directional software replay; no labels, fitting or returns.

The inherited long detector uses current/prior OHLCV and Pine-equivalent rolling
fields. Its short mirror uses prior12 range/density, current SMA/EMA20, three-bar
price/ATR and volume progress, MD/SB and current/prior ZLEMA. Per-side cooldowns
and parent ages are independent. The structural gate adds current 6-MA body
evidence and frozen parent boundaries; only closed observations are consumed.
The fixed reporting interval does not participate in any detection condition.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v5_pepe_regression import SOURCE, SOURCE_SHA, sha
from yoyo.evaluation.spike_burst_dataset import load_feature
from yoyo.evaluation.spike_burst_early_warning import detect as long_legacy, early_fields
from yoyo.evaluation.spike_burst_v5_structure import detect

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENT=ROOT/'experiments/active/exp-spike-v5-bidirectional-20260910-v1'


def short_legacy(frame):
    """Mirror V4's parent lifecycle using prior12 low, not today's future low."""
    fields=early_fields(frame)
    raw=frame.ready & frame.close.lt(fields.prog_prior_low) & frame.close.lt(frame[['s20','e20']].min(axis=1,skipna=False))
    quality=(frame.ready & fields.prog_recent_dense & fields.prog_advance.le(-1.5)
        & fields.prog_volume_ratio.ge(1.5) & frame.md.le(frame.sb)
        & frame.middle.lt(frame.middle.shift()))
    last=parent=None;previous=consumed=False;high=low=np.nan;rows=[]
    for i in range(len(frame)):
        if i and frame.index[i]-frame.index[i-1]!=pd.Timedelta(hours=1):
            last=parent=None;previous=consumed=False;high=low=np.nan
        condition=bool(raw.iloc[i]);event=False
        early=condition and not previous and (last is None or i-last>=12)
        previous=condition
        if early:
            last=parent=i;high=float(fields.prog_prior_high.iloc[i]);low=float(fields.prog_prior_low.iloc[i]);consumed=False
        if parent is not None and i-parent>3:
            parent=None;high=low=np.nan;consumed=False
        if parent is not None and not consumed and frame.close.iloc[i]<low and bool(quality.iloc[i]):
            event=True;consumed=True
        rows.append(dict(confirmed=event,parent_i=parent,parent_high=high,parent_low=low))
    return pd.DataFrame(rows,index=frame.index)


def both(frame):
    long=long_legacy(frame);short=short_legacy(frame)
    base=frame[['open','high','low','close','md','sb','atr','ropeHigh','ropeLow','ready']].copy()
    base['data_gap']=frame.index.to_series().diff().ne(pd.Timedelta(hours=1)).to_numpy()
    base.iloc[0,base.columns.get_loc('data_gap')]=False
    base['confirmed']=True
    outputs={}
    for side,events in [(1,long),(-1,short)]:
        x=base.copy();x['legacy_confirmed']=events.confirmed
        if side==1:
            x['legacy_parent_high']=long.frozen_parent_high
            x['legacy_parent_low']=[float(long.prog_prior_low.iloc[int(p)]) if np.isfinite(p) else np.nan for p in long.parent_i]
        else:
            x['legacy_parent_high']=short.parent_high;x['legacy_parent_low']=short.parent_low
        outputs[side]=detect(x,side=side)
    assert not (outputs[1].confirmed & outputs[-1].confirmed).any()
    return outputs


def main():
    pins={}
    for rel in ['yoyo/evaluation/spike_v5_bidirectional_check.py','yoyo/evaluation/spike_burst_v5_structure.py',
        'yoyo/evaluation/pine/spike_burst_v5.pine',str((EXPERIMENT/'PROJECT_PLAN.md').relative_to(ROOT))]:
        if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=(ROOT/rel).read_bytes():
            raise ValueError('Commit exact builder before replay: '+rel)
        pins[rel]=sha(ROOT/rel)
    frame=load_feature(SOURCE,SOURCE_SHA,60,pd.Timestamp('2026-09-09T00:00Z'))
    outputs=both(frame);start=pd.Timestamp('2026-08-14T00:00Z');rows=[]
    for side,result in outputs.items():
        for stamp,row in result.loc[result.confirmed & (result.index>=start)].iterrows():
            rows.append(dict(side=side,bar_open_utc=str(stamp),bar_open_beijing=str(stamp.tz_convert('Asia/Shanghai')),
                bar_close_beijing=str((stamp+pd.Timedelta(hours=1)).tz_convert('Asia/Shanghai')),
                close=float(frame.loc[stamp,'close']),legacy_open_utc=str(frame.index[int(row.legacy_i)]),
                wait_bars=int((stamp-frame.index[int(row.legacy_i)])/pd.Timedelta(hours=1)),
                parent_high=float(row.legacy_parent_high),parent_low=float(row.legacy_parent_low)))
    previous=json.loads((ROOT/'experiments/active/exp-spike-v5-pepe-1h-repair-20260910-v1/results/regression.json').read_text())
    long_times=[r['bar_open_beijing'] for r in rows if r['side']==1]
    assert long_times==[r['final_open_beijing'] for r in previous['final_events']]
    cut=pd.Timestamp('2026-08-20T00:00Z');prefix=both(frame.loc[:cut])
    for side in (1,-1):pd.testing.assert_frame_equal(prefix[side],outputs[side].loc[:cut])
    result=dict(schema='spike-v5-bidirectional-check-v1',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256=SOURCE_SHA,pins=pins,bar_minutes=60,reviewed_bars=624,events=sorted(rows,key=lambda r:r['bar_open_utc']),
        counts={'long':len(long_times),'short':sum(r['side']==-1 for r in rows)},
        inherited_long_times_unchanged=True,prefix_passed=True,holdout_consumption=1,economic_evaluation=False)
    out=EXPERIMENT/'results';out.mkdir(exist_ok=True,parents=True)
    (out/'regression.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['counts','events','inherited_long_times_unchanged','prefix_passed']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
