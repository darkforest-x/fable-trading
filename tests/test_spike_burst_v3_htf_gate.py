"""Synthetic E aggregation, closed-clock causality and unchanged V3 contracts.

No market files, future labels, real E scoring or fitted thresholds are used.
Gate-state unit tests deliberately stub known/opposed states; aggregation and
341-bar readiness are independently exercised from valid synthetic raw OHLCV.
"""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_early_warning as ew
from yoyo.evaluation import spike_burst_replay as original
from yoyo.evaluation import spike_burst_v3_htf_gate as hg


def frame(n=100, start="2026-01-01T00:00Z"):
    result = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n) + 400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100. + np.arange(n) * .01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range(start, periods=n, freq="h"))
    result.attrs["minutes"] = 60
    return result


def launch(f, i, close=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., close + .2, 99., close, volume]


def price_path(f, prices):
    f["open"] = prices
    f["close"] = prices
    f["high"] = prices + .2
    f["low"] = prices - .2


def stub_gate(monkeypatch, f, opposed):
    """Unit-test event state transitions independently of the measured HTF math."""
    htf = hg.htf_fields(f)
    htf["htf_count"] = 341
    htf["htf_ready"] = True
    htf["htf_unknown"] = False
    htf["htf_md"] = 0.
    htf["htf_sb"] = 0.
    htf["htf_opposed"] = False
    htf.loc[f.index[list(opposed)], "htf_opposed"] = True
    htf.loc[f.index[list(opposed)], "htf_md"] = -1.
    htf["htf_gatepass"] = ~htf.htf_opposed
    monkeypatch.setattr(hg, "htf_fields", lambda source: htf.reindex(source.index).copy())


def test_utc_aligned_complete_ohlcv_is_first_max_min_last_and_sum():
    f = frame(8)
    f.loc[f.index[:4], ["open", "high", "low", "close", "volume"]] = [
        [100., 103., 98., 102., 10.], [102., 106., 101., 103., 20.],
        [103., 104., 97., 100., 30.], [100., 105., 99., 104., 40.]]
    bars = hg.aggregate_4h(f)
    assert bars.index.tolist() == [f.index[0], f.index[4]]
    assert bars.iloc[0][list(hg.OHLCV)].tolist() == [100., 106., 97., 104., 100.]
    assert bars.htf_epoch.tolist() == [0, 0]


def test_partial_head_and_tail_are_discarded_not_relabelled_into_full_buckets():
    f = frame(10, "2026-01-01T01:00Z")  #01..10; only04..07 is complete.
    bars = hg.aggregate_4h(f)
    assert bars.index.tolist() == [pd.Timestamp("2026-01-01T04:00Z")]
    result = hg.htf_fields(f)
    assert result.htf_count.iloc[:6].eq(0).all()
    assert result.htf_close.iloc[:6].isna().all()
    assert result.htf_age_hours.iloc[:6].isna().all()
    assert result.htf_count.iloc[6:].eq(1).all()
    assert result.htf_age_hours.iloc[6:].tolist() == [0., 1., 2., 3.]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_no_complete_bucket_means_unknown_count_zero_and_original_gate_passes(n):
    result = hg.htf_fields(frame(n))
    assert result.htf_unknown.all() and result.htf_gatepass.all()
    assert not result.htf_ready.any() and not result.htf_opposed.any()
    assert result.htf_count.eq(0).all()
    assert result.htf_close.isna().all() and result.htf_md.isna().all()


def test_exact_four_hour_close_is_visible_but_one_hour_before_is_not():
    f = frame(9)
    price_path(f, np.array([100.] * 4 + [110., 111., 112., 113., 120.]))
    result = hg.htf_fields(f)
    assert result.htf_count.iloc[2] == 0  # decision03:00 cannot see00..04.
    assert result.htf_close.iloc[3] == pd.Timestamp("2026-01-01T04:00Z")
    assert result.htf_count.iloc[3] == 1 and result.htf_age_hours.iloc[3] == 0.
    assert result.htf_close.iloc[6] == pd.Timestamp("2026-01-01T04:00Z")
    assert result.htf_age_hours.iloc[6] == 3.
    assert result.htf_close.iloc[7] == pd.Timestamp("2026-01-01T08:00Z")
    assert result.htf_count.iloc[7] == 2 and result.htf_age_hours.iloc[7] == 0.


def test_known_starts_on_341st_complete_bar_not_340_or_incomplete_341():
    f = frame(1368)
    result = hg.htf_fields(f)
    assert result.htf_count.iloc[1359] == 340
    assert result.htf_count.iloc[1362] == 340
    assert not result.htf_ready.iloc[:1363].any()
    assert result.htf_count.iloc[1363] == 341
    assert result.htf_ready.iloc[1363] and not result.htf_unknown.iloc[1363]
    assert result.htf_close.iloc[1363] == f.index[1363] + pd.Timedelta(hours=1)


def test_pre_warmup_opposing_values_remain_unknown_and_do_not_veto():
    f = frame(1360)
    price_path(f, 200. - np.arange(len(f)) * .025)
    row = hg.htf_fields(f).iloc[-1]
    assert row.htf_count == 340 and row.htf_md < row.htf_sb
    assert row.htf_unknown and not row.htf_ready
    assert not row.htf_opposed and row.htf_gatepass


def test_discarded_partial_head_prices_do_not_seed_recursive_four_hour_math():
    f = frame(1370, "2026-01-01T01:00Z")
    f.loc[f.index[:3], ["open", "high", "low", "close"]] = [1000., 1001., 999., 1000.]
    result = hg.htf_fields(f)
    assert result.htf_ready.iloc[-1]
    assert result.htf_md.iloc[-1] == result.htf_sb.iloc[-1] == 0.


def test_zero_md_sb_after_warmup_is_known_permitted_without_atr_or_volume_gate():
    f = frame(1364)
    f.loc[:, ["open", "high", "low", "close"]] = 100.
    f["volume"] = np.nan
    bars = hg.aggregate_4h(f)
    assert bars.volume.isna().all()
    result = hg.htf_fields(f)
    row = result.iloc[-1]
    assert row.htf_ready and not row.htf_unknown
    assert row.htf_md == row.htf_sb == 0.
    assert row.htf_gatepass and not row.htf_opposed


def test_missing_one_hour_volume_preserves_nan_aggregate_without_losing_price_bar():
    f = frame(1364)
    f.loc[f.index[-2], "volume"] = np.nan
    bars = hg.aggregate_4h(f)
    assert len(bars) == 341 and np.isnan(bars.volume.iloc[-1])
    result = hg.htf_fields(f)
    assert result.htf_count.iloc[-1] == 341 and result.htf_ready.iloc[-1]


def test_recomputed_four_hour_md_matches_original_math_not_hourly_md_average():
    f = frame(1440)
    prices = 100. + np.arange(len(f)) * .025 + np.sin(np.arange(len(f)) / 19.)
    price_path(f, prices)
    f["md"] = 99999.
    f["sb"] = -99999.
    bars = hg.aggregate_4h(f)
    expected = original.features(bars[list(hg.OHLCV)])
    result = hg.htf_fields(f)
    closed = np.arange(3, len(f), 4)
    np.testing.assert_array_equal(result.htf_md.iloc[closed], expected.md)
    np.testing.assert_array_equal(result.htf_sb.iloc[closed], expected.sb)
    assert result.htf_md.iloc[-1] != f.md.iloc[-4:].mean()
    f["md"], f["sb"] = -1e9, 1e9
    pd.testing.assert_frame_equal(hg.htf_fields(f), result)


def test_negative_recovering_md_above_negative_signal_is_allowed():
    f = frame(1440)
    close4 = 200. - np.arange(360) * .15
    close4[-10:] = close4[-11]
    price_path(f, np.repeat(close4, 4))
    row = hg.htf_fields(f).iloc[-1]
    assert row.htf_ready and row.htf_sb < row.htf_md < 0.
    assert not row.htf_opposed and row.htf_gatepass


def test_known_md_below_signal_is_the_only_veto():
    f = frame(1440)
    price_path(f, 200. - np.arange(len(f)) * .025)
    row = hg.htf_fields(f).iloc[-1]
    assert row.htf_ready and row.htf_md < row.htf_sb
    assert row.htf_opposed and not row.htf_gatepass


def test_gap_immediately_discards_recent_known_background_before_new_complete_group():
    f = frame(1380)
    omitted = f.index[1364]  # First hour following the already-known341st4H close.
    f = f.drop(omitted)
    result = hg.htf_fields(f)
    assert result.htf_ready.iloc[1363]
    # Missing00..01, resume01..02: the old00close is only2h old but forbidden.
    resumed = result.iloc[1364]
    assert resumed.htf_unknown and resumed.htf_gatepass
    assert resumed.htf_count == 0 and pd.isna(resumed.htf_close)
    assert np.isnan(resumed.htf_md) and np.isnan(resumed.htf_age_hours)
    first_new_close = omitted + pd.Timedelta(hours=8)
    pos = f.index.get_loc(first_new_close - pd.Timedelta(hours=1))
    assert result.htf_count.iloc[pos] == 1 and result.htf_unknown.iloc[pos]
    assert result.htf_close.iloc[pos] == first_new_close


def test_gap_restarts_recursive_math_and_requires_another_341_complete_bars():
    f = frame(2732)
    price_path(f, 100. + np.arange(len(f)) * .02)
    f = f.drop(f.index[1364])
    result = hg.htf_fields(f)
    assert result.htf_count.iloc[-1] == 341 and result.htf_ready.iloc[-1]
    assert not result.htf_ready.iloc[1364:-1].any()
    standalone = hg.htf_fields(f.iloc[1364:])
    pd.testing.assert_frame_equal(result.iloc[1364:].drop(columns="htf_epoch"),
        standalone.drop(columns="htf_epoch"))


def test_unknown_preserves_all_original_v3_events_and_is_not_labelled_known():
    f = frame()
    for i in (30, 42, 54):
        launch(f, i, 104. + i / 10)
    base, result = ew.detect(f), hg.detect(f)
    assert result.htf_unknown.all() and not result.htf_ready.any()
    assert result.htf_gatepass.all()
    pd.testing.assert_frame_equal(result[base.columns], base)


def test_vetoed_edge_is_consumed_without_spending_accepted_cooldown(monkeypatch):
    f = frame()
    launch(f, 30)
    launch(f, 32, 108.)
    stub_gate(monkeypatch, f, [30])
    result = hg.detect(f)
    assert result.htf_blocked.iloc[30] and not result.early.iloc[30]
    assert not result.cooldown_blocked.iloc[30]
    assert result.early.iloc[32]  # Rejected30 did not create a twelve-bar clock.
    assert result.parent_i.iloc[32] == 32


def test_recovery_does_not_queue_a_previously_consumed_raw_edge(monkeypatch):
    f = frame()
    for i in range(30, 34):
        launch(f, i, 110. + i)
    launch(f, 36, 150.)
    stub_gate(monkeypatch, f, [30])
    result = hg.detect(f)
    assert result.htf_blocked.iloc[30]
    assert result.htf_gatepass.iloc[31] and not result.early.iloc[31]
    assert not result.candidate_edge.iloc[31]
    assert result.early.iloc[36]


def test_rejected_edge_does_not_shift_existing_accepted_cooldown(monkeypatch):
    f = frame()
    launch(f, 30)
    launch(f, 42, 106.)
    launch(f, 45, 110.)
    stub_gate(monkeypatch, f, [42])
    result = hg.detect(f)
    assert result.early.iloc[30] and result.htf_blocked.iloc[42]
    assert result.early.iloc[45]


def test_cooldown_and_htf_veto_reasons_are_separate(monkeypatch):
    f = frame()
    launch(f, 30)
    launch(f, 35, 106.)
    launch(f, 42, 110.)
    stub_gate(monkeypatch, f, [35])
    result = hg.detect(f)
    assert result.cooldown_blocked.iloc[35]
    assert not result.htf_blocked.iloc[35]
    assert np.flatnonzero(result.early).tolist() == [30, 42]


def test_child_is_not_rechecked_against_new_opposing_htf_and_cannot_reopen_parent(monkeypatch):
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32):
        launch(f, i, 101.1, 100.)
    launch(f, 33, 104.)
    stub_gate(monkeypatch, f, [33])
    result = hg.detect(f)
    assert result.early.iloc[30] and not result.confirmed.iloc[30]
    assert result.htf_opposed.iloc[33] and result.confirmed.iloc[33]
    assert result.confirm_age.iloc[33] == 3 and result.parent_i.iloc[33] == 30
    assert result.frozen_parent_high.iloc[33] == 101.
    assert result.confirmed.sum() == result.early.sum() == 1


def test_rejected_parent_cannot_receive_orphan_child(monkeypatch):
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    launch(f, 33, 104.)
    stub_gate(monkeypatch, f, [30])
    result = hg.detect(f)
    assert result.htf_blocked.iloc[30]
    assert not result.early.any() and not result.confirmed.any()
    assert np.isnan(result.parent_i.iloc[33])


def test_parent_expires_after_age_three_and_gap_also_clears_it():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32, 33):
        launch(f, i, 101.1, 100.)
    launch(f, 34, 104.)
    result = hg.detect(f)
    assert result.early.iloc[30] and not result.confirmed.any()
    assert np.isnan(result.parent_i.iloc[34])
    gap = f.drop(f.index[31])
    gap_result = hg.detect(gap)
    assert np.isnan(gap_result.parent_i.iloc[31])
    assert not gap_result.confirmed.any()


def test_prefix_and_unclosed_future_bar_mutation_cannot_change_previous_background():
    f = frame(1400)
    price_path(f, 100. + np.arange(len(f)) * .01 + np.sin(np.arange(len(f)) / 9.))
    before = f.copy(deep=True)
    full = hg.detect(f)
    for size in (1, 3, 4, 7, 8, 100, 1359, 1360, 1363, 1364, 1399):
        pd.testing.assert_frame_equal(hg.detect(f.iloc[:size]), full.iloc[:size])
    mutated = f.copy()
    mutated.loc[mutated.index[1363:], ["open", "high", "low", "close", "volume"]] = [500., 501., 499., 500., 1e9]
    pd.testing.assert_frame_equal(hg.detect(mutated).iloc[:1363], full.iloc[:1363])
    pd.testing.assert_frame_equal(f, before)


@pytest.mark.parametrize("column,value", [("close", np.nan), ("high", 1.), ("low", -1.), ("volume", np.inf)])
def test_malformed_source_fails_explicitly_without_fabricating_a_complete_bucket(column, value):
    f = frame(8)
    f.loc[f.index[2], column] = value
    with pytest.raises(ValueError):
        hg.aggregate_4h(f)


def test_non_hourly_or_duplicate_source_times_are_rejected():
    f = frame(8)
    with pytest.raises(ValueError, match="hourly candles"):
        hg.aggregate_4h(f.set_axis(f.index + pd.Timedelta(minutes=30)))
    with pytest.raises(ValueError, match="hourly candles"):
        hg.aggregate_4h(pd.concat([f.iloc[:1], f]))
