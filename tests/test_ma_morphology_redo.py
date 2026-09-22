"""Regression checks for morphology/background separation and split protection."""
from copy import deepcopy
from pathlib import Path
import json

import pandas as pd

from yoyo.datasets.ma_morphology_redo import in_split, protection_intervals, read_interval


def plan():
    return {'splits':{'train_end_exclusive':'2026-01-01T00:00:00Z',
        'validation_end_exclusive':'2026-05-01T00:00:00Z','test_end_exclusive':'2026-09-21T16:00:00Z'}}


def test_background_cannot_cross_train_label_safety_boundary():
    p=plan()
    assert in_split(pd.Timestamp('2025-12-30T10:00Z'),pd.Timestamp('2025-12-30T12:00Z'),'train',p)
    assert not in_split(pd.Timestamp('2025-12-31T10:00Z'),pd.Timestamp('2025-12-31T12:00Z'),'train',p)
    assert not in_split(pd.Timestamp('2025-12-31T23:00Z'),pd.Timestamp('2026-01-01T01:00Z'),'val',p)


def test_every_candidate_protected_even_if_losing_or_unknown(tmp_path,monkeypatch):
    from yoyo.datasets import ma_morphology_redo as m
    monkeypatch.setattr(m,'ROOT',tmp_path)
    base={'canonical_asset':'BTC','core_start_time':'2025-01-01T10:00:00Z',
        'core_end_time':'2025-01-01T11:00:00Z','bar_minutes':15}
    paths={'candidate_ledger':'pool.jsonl','manual_rows':'manual.jsonl','reference_exclusion':'refs.json'}
    (tmp_path/'manual.jsonl').write_text('')
    (tmp_path/'refs.json').write_text('{"events":[]}')
    p={'negative_sampling':{'protection_hours':4},'inputs':{k:{'path':v} for k,v in paths.items()}}
    results=[]
    for outcome,retained in [('TP',True),('SL',False),('TIMEOUT',False),('UNKNOWN',False)]:
        (tmp_path/'pool.jsonl').write_text(json.dumps({**base,'profit':{'outcome':outcome,'retained':retained}})+'\n')
        results.append(protection_intervals(p))
    assert all(x==results[0] for x in results)
    assert results[0]['BTC']==[(pd.Timestamp('2025-01-01T06:00Z'),pd.Timestamp('2025-01-01T15:15Z'))]


def test_interval_reader_retains_source_indexes_and_clips_future(tmp_path):
    file=tmp_path/'source.csv'
    times=pd.date_range('2025-01-01',periods=6,freq='min',tz='UTC')
    pd.DataFrame({'ts':times.as_unit('ms').asi8,'open':[1]*6,'high':[2]*6,
        'low':[0.5]*6,'close':[1.5]*6,'volume':[10]*6}).to_csv(file,index=False)
    f=read_interval(file,times[1],times[4])
    assert f['_source_i'].tolist()==[1,2,3]
    assert f['open_time'].tolist()==list(times[1:4])
