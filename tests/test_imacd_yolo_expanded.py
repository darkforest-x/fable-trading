"""Expanded-cohort boundary, unchanged-candidate and restart safety checks."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import imacd_yolo_expanded as ex


@pytest.mark.parametrize("minutes", [60, 240])
def test_cohorts_purge_incomplete_followup_without_crossing_cut(minutes):
    times = pd.date_range(ex.CUT-pd.Timedelta(minutes=minutes*50), periods=50,
                          freq=f"{minutes}min", tz="UTC")
    bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=times)
    features = pd.DataFrame(dict(release_side=0, focus_start_i=-1, near_zero_bars=0), index=times)
    for p in (38, 39, 40, 48, 49):
        features.iloc[p] = [1, p-12, 12]
    c = ex.candidates(bars, features, "TEST", minutes, "pre_holdout")
    assert c.signal_i.tolist() == [38, 39, 40, 48]
    assert c.complete_followup.tolist() == [True, True, False, False]
    trace = ex.frozen.trace_for(c, bars, features.assign(md=1.))
    assert pd.to_datetime(trace.bar_open_at, utc=True).max() < ex.CUT


@pytest.mark.parametrize("minutes", [60, 240])
def test_same_holdout_candidates_as_frozen_small_pilot(minutes):
    times = pd.date_range(ex.CUT, periods=300, freq=f"{minutes}min", tz="UTC")
    bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=times)
    features = pd.DataFrame(dict(release_side=0, focus_start_i=-1, near_zero_bars=0), index=times)
    for p in (20, 50, 200, 298):
        features.iloc[p] = [-1, p-12, 12]
    old = ex.frozen.candidates(bars, features, "TEST", minutes)
    new = ex.candidates(bars, features, "TEST", minutes, "holdout_review")
    pd.testing.assert_frame_equal(old, new.drop(columns="fold"))


def test_resume_refuses_partial_changed_or_wrong_identity_shards(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "DATA", tmp_path)
    assert ex.load_completed("TEST", {"v": 1}) is None
    suffixes = ("candidates.csv", "decisions.csv", "proposals.csv.gz", "trace.csv.gz")
    files = {}
    for suffix in suffixes:
        p = tmp_path/f"TEST_{suffix}"
        p.write_text("fixed")
        files[p.name] = ex.digest(p)
    with pytest.raises(ValueError, match="incomplete shard"):
        ex.load_completed("TEST", {"v": 1})
    ex.atomic_json(tmp_path/"TEST_receipt.json", dict(identity={"v":1}, files=files))
    assert ex.load_completed("TEST", {"v":1})["files"] == files
    with pytest.raises(ValueError, match="identity mismatch"):
        ex.load_completed("TEST", {"v":2})
    (tmp_path/"TEST_candidates.csv").write_text("changed")
    with pytest.raises(ValueError, match="changed shard"):
        ex.load_completed("TEST", {"v":1})


@pytest.mark.parametrize("minutes", [60, 240])
def test_reject_unbounded_prefix(minutes):
    times = pd.date_range(ex.CUT, periods=20, freq=f"{minutes}min", tz="UTC")
    bars = pd.DataFrame(dict(close=100.), index=times)
    with pytest.raises(ValueError, match="truncate"):
        ex.candidates(bars, pd.DataFrame(), "TEST", minutes, "pre_holdout")


def test_summary_separates_immediate_delayed_and_direction():
    d = pd.DataFrame(dict(complete_followup=[True]*3+[False],
        status=["confirmed", "confirmed", "invalidated", "censored_end"],
        side=[1,-1,1,1], delay_bars=[0,3,np.nan,np.nan],
        displacement_bp=[0,40,np.nan,np.nan], symbol=["A","B","B","A"]))
    s = ex.stats_for(d)
    assert (s["arrows"],s["complete"],s["confirmed"],s["censored"]) == (4,3,2,1)
    assert (s["immediate"],s["delayed"],s["adverse_delays"]) == (1,1,1)
    assert s["delayed_displacement_median_bp"] == 40
