"""Synthetic F price adjudication, unchanged V3 clocks and public-time causality."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_v3_price_acceptance as gate


def frame(n=90):
    f = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n)+400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100.+np.arange(n)*.01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    f.attrs["minutes"] = 60
    return f


def bar(f, i, close=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., max(101., close+.2),
                                                                              min(99., close-.2), close, volume]


def setup(next_close=102., n=90):
    f = frame(n)
    bar(f, 30)
    if n > 31:
        bar(f, 31, next_close)
    return f


@pytest.mark.parametrize("close,status", [(102., "accepted"), (101., "rejected"), (100.5, "rejected")])
def test_next_close_strict_frozen_boundary(close, status):
    f = setup(close)
    s = gate.detect(f)
    assert s.v3_early.iloc[30] and s.frozen_parent_high.iloc[30] == 101.
    assert s.candidate_status.iloc[30] == "pending"
    assert not s.early.iloc[30] and not s.confirmed.iloc[30]
    assert s.candidate_status.iloc[31] == status
    assert s.early.iloc[31] == (status == "accepted")
    assert s.delay_bars.iloc[31] == 1


def test_candidate_high_does_not_replace_frozen_prior_twelve_high():
    f = setup()
    f.loc[f.index[30], "high"] = 150.
    s = gate.detect(f)
    assert s.prog_prior_high.iloc[31] == 150.
    assert s.frozen_parent_high.iloc[31] == 101.
    assert s.early.iloc[31]


def test_acceptance_allows_intrabar_low_below_boundary_and_lower_than_candidate_close():
    f = setup()
    s = gate.detect(f)
    assert f.low.iloc[31] < s.frozen_parent_high.iloc[31] < f.close.iloc[31] < f.close.iloc[30]
    assert s.early.iloc[31]


@pytest.mark.parametrize("column,value", [("volume", np.nan), ("atr", np.nan), ("ready", False),
    ("s20", 1000.), ("e20", 1000.), ("md", -100.), ("sb", 100.), ("ropeHigh", 1000.)])
def test_next_bar_has_no_extra_quality_or_ready_gate(column, value):
    f = setup()
    f.loc[f.index[31], column] = value
    assert gate.detect(f).early.iloc[31]


@pytest.mark.parametrize("column,value", [("open", np.nan), ("high", np.inf), ("low", np.nan),
    ("close", np.nan), ("low", -1.), ("high", 101.), ("low", 101.)])
def test_incomplete_or_invalid_next_ohlc_is_unknown_not_rejected(column, value):
    f = setup()
    f.loc[f.index[31], column] = value
    s = gate.detect(f)
    assert s.candidate_status.iloc[31] == "unknown"
    assert s.reason.iloc[31] == "invalid_next_ohlc"
    assert not s.early.iloc[31] and not s.confirmed.iloc[31]
    assert pd.isna(s.acceptance_i.iloc[31])


def test_end_pending_is_not_backfilled_and_registry_unknown_is_explicit():
    f = setup(n=31)
    s = gate.detect(f)
    before = s.copy(deep=True)
    r = gate.candidates(f, s)
    pd.testing.assert_frame_equal(before, s)
    assert s.candidate_status.iloc[-1] == "pending"
    assert r.candidate_status.tolist() == ["unknown"]
    assert r.reason.tolist() == ["no_next_bar_at_sample_end"]
    assert pd.isna(r.decision_time.iloc[0]) and pd.isna(r.decision_i.iloc[0])
    assert r.expected_decision_time.iloc[0] == f.index[-1] + 2 * gate.HOUR
    assert r.asof_time.iloc[0] == f.index[-1] + gate.HOUR
    assert r.registry_only.all()


def test_gap_unknown_never_retries_at_next_observed_bar():
    f = setup()
    bar(f, 32, 105.)
    f = f.drop(f.index[31])
    s = gate.detect(f)
    assert s.candidate_status.iloc[30] == "pending"
    assert s.candidate_status.iloc[31] == "unknown"
    assert s.reason.iloc[31] == "missing_next_hour"
    assert s.decision_time.iloc[31] == f.index[31] + gate.HOUR
    assert not s.early.iloc[30:42].any()
    r = gate.candidates(f, s).iloc[0]
    assert r.candidate_status == "unknown" and r.reason == "missing_next_hour"
    assert pd.isna(r.acceptance_i)


def test_rejection_does_not_change_original_candidates_or_original_cooldown():
    f = setup(100.)
    bar(f, 35, 110.)
    bar(f, 42, 120.)
    bar(f, 43, 111.)
    original = gate.v3.detect(f)
    s = gate.detect(f)
    assert np.flatnonzero(s.v3_early).tolist() == [30, 42]
    assert s.v3_cooldown_blocked.iloc[35]
    assert not s.early.iloc[35]
    assert np.flatnonzero(s.early).tolist() == [43]
    for col in original:
        target = "v3_" + col if col in gate.SOURCE_COLUMNS else col
        pd.testing.assert_series_equal(original[col], s[target], check_names=False)


@pytest.mark.parametrize("age", [0, 1, 2, 3])
def test_original_child_clock_and_publication_clock_remain_distinct(age):
    f = frame()
    for i in range(30, 34):
        bar(f, i, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    bar(f, 30 + age, 104., 600.)
    if age == 0:
        f.loc[f.index[30], "high"] = 110.
    s = gate.detect(f)
    raw_child, public_child = 30 + age, max(31, 30 + age)
    assert np.flatnonzero(s.v3_confirmed).tolist() == [raw_child]
    assert np.flatnonzero(s.early).tolist() == [31]
    assert np.flatnonzero(s.confirmed).tolist() == [public_child]
    assert s.parent_i.iloc[public_child] == 30
    assert s.acceptance_i.iloc[public_child] == 31
    assert s.original_child_i.iloc[public_child] == raw_child
    assert s.original_child_time.iloc[public_child] == f.index[raw_child] + gate.HOUR
    assert s.original_child_close.iloc[public_child] == f.close.iloc[raw_child]
    assert s.publication_i.iloc[public_child] == public_child
    assert s.publication_time.iloc[public_child] == f.index[public_child] + gate.HOUR
    assert s.publication_close.iloc[public_child] == f.close.iloc[public_child]
    assert s.confirm_age.iloc[public_child] == age
    assert s.publication_delay_bars.iloc[public_child] == public_child - raw_child
    if age > 1:
        assert pd.isna(s.original_child_i.iloc[31])
        assert not s.confirmed.iloc[31]


def test_public_acceptance_does_not_require_any_original_child():
    f = setup()
    f["md"] = -1.
    s = gate.detect(f)
    assert not s.v3_confirmed.any() and not s.confirmed.any()
    assert np.flatnonzero(s.early).tolist() == [31]


def test_late_original_child_cannot_revive_rejected_candidate():
    f = frame()
    bar(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    bar(f, 31, 100.)
    bar(f, 32, 104.)
    s = gate.detect(f)
    assert s.v3_confirmed.iloc[32]
    assert not s.early.any() and not s.confirmed.any()
    assert s.candidate_status.iloc[32] == "rejected"
    assert s.original_child_i.iloc[32] == 32  # Research provenance, not a public upgrade.
    assert pd.isna(s.child_publication_i.iloc[32])
    assert pd.isna(s.publication_i.iloc[32])


def test_invalid_next_bar_unknown_cannot_revive_on_later_original_child():
    f = frame()
    bar(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    bar(f, 31, 101.1, 100.)
    f.loc[f.index[31], "open"] = np.nan
    bar(f, 32, 104.)
    s = gate.detect(f)
    assert s.v3_confirmed.iloc[32]
    assert s.candidate_status.iloc[32] == "unknown"
    assert not s.early.any() and not s.confirmed.any()


def test_prefix_invariance_all_columns_and_input_unchanged():
    f = setup()
    bar(f, 44, 107.)  # Rejected by next baseline close.
    bar(f, 60, 109.)
    bar(f, 61, 108.)
    bar(f, 89, 200.)  # Pending at the sample end.
    original = f.copy(deep=True)
    s = gate.detect(f)
    for end in (1, 29, 30, 31, 32, 33, 44, 45, 46, 61, 62, 89, 90):
        pd.testing.assert_frame_equal(gate.detect(f.iloc[:end]), s.iloc[:end])
    pd.testing.assert_frame_equal(f, original)
    changed = f.copy()
    changed.loc[changed.index[34:], ["open", "high", "low", "close", "volume"]] = [200., 220., 150., 210., 900.]
    pd.testing.assert_frame_equal(gate.detect(changed).iloc[:34], s.iloc[:34])


def test_gap_prefix_invariance_including_pending_to_unknown_transition():
    f = setup().drop(setup().index[31])
    s = gate.detect(f)
    for end in (31, 32, 33, 44):
        pd.testing.assert_frame_equal(gate.detect(f.iloc[:end]), s.iloc[:end])


def test_public_price_and_next_economic_open_are_never_original_candidate_entry():
    f = setup()
    f.loc[f.index[32], ["open", "high", "low", "close"]] = [103., 104., 102., 103.5]
    s = gate.detect(f)
    i = int(np.flatnonzero(s.early)[0])
    assert i == 31 and s.acceptance_close.iloc[i] == 102.
    assert s.candidate_close.iloc[i] == 104.
    assert s.acceptance_time.iloc[i] == f.index[31] + gate.HOUR
    assert f.index[i+1] == s.acceptance_time.iloc[i]
    assert f.open.iloc[i+1] == 103.  # Executor must use t+2, not t+1 open=100.


def test_registry_all_candidates_and_cross_window_clocks_without_implicit_filtering():
    f = setup(n=45)
    bar(f, 44, 107.)
    s = gate.detect(f)
    r = gate.candidates(f, s)
    assert r.candidate_i.tolist() == [30., 44.]
    assert r.candidate_status.tolist() == ["accepted", "unknown"]
    start, end = f.index[31] + gate.HOUR, f.index[44] + gate.HOUR
    # A pre-window original candidate can publish at the window's exact start.
    assert r.candidate_time.iloc[0] < start == r.acceptance_time.iloc[0]
    public = s.loc[(s.index + gate.HOUR >= start) & (s.index + gate.HOUR < end)]
    assert public.early.sum() == 1
    # Last candidate's absent next close stays unknown; no forced rejection.
    assert r.expected_decision_time.iloc[-1] == end + gate.HOUR
    assert r.reason.iloc[-1] == "no_next_bar_at_sample_end"


def test_registry_reuses_supplied_state_without_recomputing_detector(monkeypatch):
    f = setup()
    s = gate.detect(f)
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected second detection")
    monkeypatch.setattr(gate.v3, "detect", forbidden)
    r = gate.candidates(f, s)
    assert r.acceptance_i.tolist() == [31.]
    assert r.original_child_i.tolist() == [30.]
    assert r.publication_i.tolist() == [31.]
    assert r.child_publication_i.tolist() == [31.]


def test_registry_empty_schema_and_alignment_guard():
    f = frame()
    s = gate.detect(f)
    assert gate.candidates(f, s).empty
    assert list(gate.candidates(f, s).columns) == list(gate.REGISTRY_COLUMNS)
    with pytest.raises(ValueError, match="aligned"):
        gate.candidates(f.iloc[:-1], s)


def test_public_metadata_is_absent_on_nonpublic_bars_and_no_reference_suppression():
    f = setup()
    bar(f, 42, 107.)
    bar(f, 43, 106.)
    f["trend_side"] = 1
    f["replay_trend_side"] = 1
    s = gate.detect(f)
    assert np.flatnonzero(s.early).tolist() == [31, 43]
    nonpublic = s.loc[~s.early & ~s.confirmed]
    assert nonpublic.publication_i.isna().all()
    assert nonpublic.publication_time.isna().all()
    assert nonpublic.confirm_age.isna().all()
    assert not any("reference" in col or "suppression" in col for col in s)
