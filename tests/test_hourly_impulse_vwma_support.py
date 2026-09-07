"""Synthetic full-clock parity and unknown accounting; no study I/O."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_vwma import add_reference_features
from yoyo.evaluation import hourly_impulse_vwma_support as study

FOLDS = (("synthetic", "2024-01-03T00:00:00Z", "2024-01-08T00:00:00Z"),)


def bars():
    n = 100
    p = 100 + np.sin(np.arange(n)/3)
    return pd.DataFrame(dict(open_time=pd.date_range("2024-01-01T00:00:00Z",periods=n,freq="h"),
                             open=p-.3,high=p+.5,low=p-.5,close=p+.3,volume=1.))


def run(data=None):
    data = bars() if data is None else data
    a,b = [add_reference_features(data,k) for k in ("SMA","VWMA")]
    ae,be = [study.accepted_entries(f,FOLDS) for f in (a,b)]
    return study.build_support(a,b,ae,be,FOLDS)


def test_full_grid_both_sides_exact_embargo():
    grid = study.opportunity_grid(FOLDS)
    assert len(grid)==96 and grid.event_id.is_unique
    assert grid.decision_time.min()==pd.Timestamp(FOLDS[0][1])
    assert grid.decision_time.max()==pd.Timestamp(FOLDS[0][2])-pd.Timedelta(hours=73)
    assert (grid.decision_time-grid.signal_time).eq(pd.Timedelta(hours=1)).all()
    assert grid.groupby("decision_time").direction.apply(set).map(lambda x:x=={-1,1}).all()


@pytest.mark.parametrize("folds", [FOLDS+FOLDS, (("x","2024-01-01T00:00:00Z","2024-01-02T00:00:00Z"),),
                                     FOLDS+(("different",FOLDS[0][1],FOLDS[0][2]),)])
def test_invalid_grid(folds):
    with pytest.raises(ValueError):study.opportunity_grid(folds)


def test_equal_volume_no_op_and_counts_preserve_zero_groups():
    tables,summary=run()
    assert summary["status"]=="no_entry_change"
    assert summary["overlap"]["sma_only"]==summary["overlap"]["vwma_only"]==0
    c=tables["counts"]
    assert (c.accepted+c.abstain+c.unknown).equals(c.total)
    assert len(c)==2*2*(1+1+1+2)
    assert summary["economic_acceptance"] is False


def test_missing_hours_are_unknown_not_deleted():
    data=bars().drop(index=60)
    tables,summary=run(data)
    assert summary["population"]==96
    ledger=tables["opportunities"]
    missing=ledger.loc[ledger.signal_time.eq(pd.Timestamp("2024-01-03T12:00:00Z"))]
    assert len(missing)==2 and missing.shape_reason.eq("missing_signal_hour").all()
    assert missing.overlap.eq("any_unknown").all()


@pytest.mark.parametrize("shape,known,expected", [("not_qualified",False,"abstain"),("qualified",False,"unknown"),
                                                  ("unknown",True,"unknown"),("qualified",True,"accepted")])
def test_three_valued_and(shape,known,expected):
    row=SimpleNamespace(reference_known=known,reference_reason="invalid_volume",close=101,ma=100,atr=1,cross_count24=0)
    assert study._state(row,shape,"why","id",{"id"},1)[0]==expected


def test_bad_volume_does_not_shrink_sma_eligibility():
    data=bars();data.loc[60,"volume"]=np.nan
    tables,_=run(data)
    clean,_=run()
    assert tables["sma_entries"].event_id.tolist()==clean["sma_entries"].event_id.tolist()
    ledger=tables["opportunities"]
    assert ledger.loc[ledger.vwma_reference_reason.eq("invalid_volume"),"sma_reference_known"].all()


def test_common_feature_mutation_rejected():
    data=bars();a=add_reference_features(data,"SMA");b=add_reference_features(data,"VWMA")
    ae=study.accepted_entries(a,FOLDS);be=study.accepted_entries(b,FOLDS)
    b.loc[50,"atr"]+=1
    with pytest.raises(AssertionError):study.build_support(a,b,ae,be,FOLDS)


def test_parity_checks_values_not_just_count():
    frame=pd.DataFrame([{c:0 for c in study.ENTRY_COLUMNS+["fold"]}])
    frame["event_id"]="test";frame["signal_time"]="2024-01-03T00:00:00Z";frame["decision_time"]="2024-01-03T01:00:00Z"
    study.assert_original_parity(frame,frame)
    altered=frame.copy();altered["ma"]=.1
    with pytest.raises(AssertionError):study.assert_original_parity(frame,altered)


def test_all_clocks_independent_of_accepted_sample():
    a=add_reference_features(bars(),"SMA");entries=study.accepted_entries(a,FOLDS)
    fake=entries.iloc[:0].copy()
    tables,_=study.build_support(a,a,fake,fake,FOLDS)
    assert len(tables["opportunities"])==len(study.opportunity_grid(FOLDS))
