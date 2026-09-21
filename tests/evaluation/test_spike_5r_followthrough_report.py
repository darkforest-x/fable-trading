"""Past-only thresholds, parent comparisons and input authentication."""
import json
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_10r_search as search
from yoyo.evaluation.spike_5r_followthrough_report import (
    holm,load_authenticated_original,parent_comparison,past_threshold,period_mask,
)


def test_threshold_ignores_current_month_and_future_values():
    dates=pd.date_range('2025-01-01',periods=200,freq='12h',tz='UTC')
    f=pd.DataFrame(dict(available_at=dates,timeframe_min=60,x=np.arange(200,dtype=float)))
    before=past_threshold(f,'x',.5);current=f.available_at.ge('2025-04-01');f.loc[current,'x']=1e12
    after=past_threshold(f,'x',.5)
    assert before[current].equals(after[current])
    assert before[f.available_at.lt('2025-03-01')].isna().all()


def test_holm_is_monotone_and_keeps_unknown_non_significant():
    assert np.allclose(holm([.01,.04,np.nan]),[.03,.08,1.])


def test_cancelled_request_retains_its_period_coverage():
    f=pd.DataFrame(dict(valid_entry=[False,False],entry_time=pd.to_datetime([None,None],utc=True),
        exit_time=pd.to_datetime([None,None],utc=True),decision_at=pd.to_datetime(['2025-08-01','2025-10-01'],utc=True)))
    assert period_mask(f,'earlier').tolist()==[True,False]
    assert period_mask(f,'later').tolist()==[False,True]
    unresolved=pd.DataFrame(dict(valid_entry=[True],entry_time=pd.to_datetime(['2025-08-01'],utc=True),
        exit_time=pd.to_datetime([None],utc=True),decision_at=pd.to_datetime(['2025-08-01'],utc=True)))
    assert period_mask(unresolved,'crossing_or_unresolved').tolist()==[True]


def test_parent_comparison_keeps_arm_coverage_and_same_event_closed_pairs():
    at=pd.to_datetime(['2025-01-01','2025-02-01','2025-02-02'],utc=True)
    left=pd.DataFrame(dict(event_key=['a','b','c'],available_at=at,valid_entry=[True,True,False],
        censored=[False,False,True],net_r=[6.,1.,np.nan],net_return=[.03,.01,np.nan]))
    right=pd.DataFrame(dict(event_key=['a','b','c'],available_at=at,valid_entry=[True,True,True],
        censored=[False,False,False],net_r=[4.,7.,6.],net_return=[.01,0.,.02]))
    got=parent_comparison(left,right,'confirm1-vs-wait1')
    assert (got['left_events'],got['left_closed'],got['left_gt5'],got['right_events'],got['right_closed'],got['right_gt5'])==(3,2,1,3,3,2)
    assert got['paired_closed_events']==2
    assert got['paired_mean_net_delta_bp']==pytest.approx(150.)
    assert got['paired_net_month_blocks']==2


def test_authenticated_dataset_rejects_candidate_drift(tmp_path):
    dataset=tmp_path/'dataset';dataset.mkdir()
    candidates=pd.DataFrame({'event_key':[f'e{i}' for i in range(49207)]})
    candidate_path=dataset/'candidates.csv.gz';candidates.to_csv(candidate_path,index=False,compression='gzip')
    candidate_sha=search.digest(candidate_path)
    manifest=dict(complete=True,candidates=49207,candidate_sha256=candidate_sha)
    manifest_path=dataset/'manifest.json';manifest_path.write_text(json.dumps(manifest))
    manifest_sha=search.digest(manifest_path)
    receipt=dict(status='complete',manifest_sha256=manifest_sha,
        files={'candidates.csv.gz':candidate_sha,'manifest.json':manifest_sha})
    receipt_path=dataset/'receipt.json';receipt_path.write_text(json.dumps(receipt))
    expected=search.digest(receipt_path)
    assert len(load_authenticated_original(dataset,expected))==49207
    candidate_path.write_bytes(candidate_path.read_bytes()+b'drift')
    with pytest.raises(ValueError,match='candidates aggregate hash drift'):
        load_authenticated_original(dataset,expected)
