"""Synthetic causal features, distinct setup families and execution clocks."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.altseason_engine import (
    ARMS, BAR_COLUMNS, build_features, candidate_events, prepare_simulation,
    simulate_event, simulate_prepared,
)


def hourly(n=1500, start="2026-01-01", unit="ns"):
    rng = np.random.default_rng(307)
    close = 100*np.exp(np.cumsum(rng.normal(0, .002, n)))
    op = np.r_[close[0], close[:-1]]
    index = pd.date_range(start, periods=n, freq="h", tz="UTC").as_unit(unit)
    return pd.DataFrame(dict(open=op, high=np.maximum(op, close)+.2,
        low=np.minimum(op, close)-.2, close=close,
        volume=rng.uniform(50, 100, n), quote_volume=rng.uniform(5_000, 10_000, n)), index=index)


def market(n=8, minutes=60):
    index = pd.date_range("2026-01-01", periods=n, freq=f"{minutes}min", tz="UTC")
    b = pd.DataFrame(dict(open=100., high=101., low=99., close=100.), index=index)
    f = pd.DataFrame(dict(atr=5., md=1., sma20=80., sma60=80.), index=index)
    b.attrs.update(minutes=minutes)
    f.attrs.update(minutes=minutes)
    return b, f


def simulate(b, f, rule="ratchet_sma60", i=0, last_i=None):
    return simulate_event(b, f, i, rule, len(b)-1 if last_i is None else last_i,
                          include_protection=True)


@pytest.mark.parametrize("cut", [71, 344, 1125, 1368])
def test_feature_prefix_and_partial_four_hour_groups_are_causal(cut):
    b = hourly()
    full, prefix = build_features(b), build_features(b.iloc[:cut])
    for minutes in (60, 240):
        f = prefix[minutes]
        pd.testing.assert_frame_equal(f, full[minutes].loc[f.index])
        if not f.empty:
            assert f.index[-1]+pd.Timedelta(minutes=minutes) <= b.index[cut-1]+pd.Timedelta(hours=1)
    if cut >= 344:
        candidates = candidate_events(full[60], 60, last_i=cut-1)
        pd.testing.assert_frame_equal(candidate_events(prefix[60], 60), candidates)


def test_aggregate_utc_grid_and_volume_conservation_with_partial_edges():
    b = hourly(14, start="2026-01-01 01:00")
    f = build_features(b)[240]
    assert len(f) == 2 and f.index[0] == pd.Timestamp("2026-01-01T04Z")
    held = b.iloc[3:7]
    assert f.open.iloc[0] == held.open.iloc[0]
    assert f.close.iloc[0] == held.close.iloc[-1]
    assert f.high.iloc[0] == held.high.max()
    assert f.low.iloc[0] == held.low.min()
    assert f.volume.iloc[0] == pytest.approx(held.volume.sum())
    assert f.quote_volume.iloc[0] == pytest.approx(held.quote_volume.sum())


def test_diagnostic_volume_excludes_current_bar_and_millisecond_clocks_match():
    b = hourly(400)
    f = build_features(b)[60]
    j = 350
    assert f.prior24h_quote_volume.iloc[j] == pytest.approx(b.quote_volume.iloc[j-24:j].sum())
    assert f.prior24h_return.iloc[j] == pytest.approx(b.close.iloc[j-1]/b.close.iloc[j-25]-1)
    shifted = b.copy()
    shifted.index = shifted.index.as_unit("ms")
    pd.testing.assert_frame_equal(build_features(shifted)[60], f)


def test_gap_unconfirmed_and_invalid_price_inputs_are_rejected():
    b = hourly(400)
    with pytest.raises(ValueError, match="continuous"):
        build_features(b.drop(b.index[123]))
    with pytest.raises(ValueError, match="confirmed"):
        build_features(b.assign(confirm=0))
    bad = b.copy()
    bad.loc[bad.index[12], "low"] = 0.
    with pytest.raises(ValueError, match="OHLC"):
        build_features(bad)


def controlled_features(n=400):
    f = build_features(hourly(n))[60]
    f["release_side"] = 0
    for name in ("first12_breakout", "first20_breakout", "dense_recent", "above_all6"):
        f[name] = False
    f.loc[:, ["sma20", "ema20", "sma60", "sma120"]] = [100., 100., 100., 100.]
    f.loc[:, ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    f["relative_volume"] = 1.
    return f


def test_focus_four_arms_and_dense_are_independent_of_positive_md():
    f = controlled_features()
    f.loc[f.index[350], "release_side"] = 1
    f.loc[f.index[355], ["dense_recent", "above_all6", "first12_breakout"]] = True
    f.loc[f.index[355], "md"] = -1.
    c = candidate_events(f, 60)
    assert set(c.loc[c.decision_i.eq(350), "arm"]) == set(list(ARMS)[:4])
    assert c.loc[c.decision_i.eq(355), "arm"].tolist() == ["dense_sma60"]
    assert c.loc[c.decision_i.eq(355), "decision_time"].iloc[0] == f.index[356]


def test_dense_uses_first_prior12_breakout_excluding_current_high():
    b = hourly(400)
    b.loc[:, ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    b.iloc[350, :4] = [100., 103., 100., 102.]
    b.iloc[351, :4] = [102., 105., 101., 104.]
    f = build_features(b)[60]
    assert f.prior12_high.iloc[350] == 101.
    assert f.first12_breakout.iloc[350]
    assert not f.first12_breakout.iloc[351]


def set_trend(f, start=340):
    f.loc[f.index[start:], "sma120"] = 70.
    f.loc[f.index[start:], "sma60"] = 80.+np.arange(len(f)-start)*.01
    f.loc[f.index[start:], ["sma20", "ema20"]] = [95., 96.]
    f.loc[f.index[start:], ["open", "high", "low", "close"]] = [100., 102., 98., 100.]


def test_pullback_is_armed_consumed_and_cannot_repeat_on_continuous_touch():
    f = controlled_features()
    set_trend(f)
    # The first touch recovers at its close; continuing touches cannot rearm.
    f.loc[f.index[350:354], ["low", "high", "close"]] = [95.5, 108., 107.]
    f.loc[f.index[351], ["high", "close"]] = [110., 109.]
    f.loc[f.index[355], ["low", "high", "close"]] = [95.5, 108., 107.]
    c = candidate_events(f, 60)
    assert c.loc[c.arm.eq("pullback_sma60"), "decision_i"].tolist() == [350, 355]
    partial = candidate_events(f, 60, first_i=351)
    assert partial.loc[partial.arm.eq("pullback_sma60"), "decision_i"].tolist() == [355]
    before_retouch = candidate_events(f.iloc[:355], 60)
    pd.testing.assert_frame_equal(before_retouch, c.loc[c.decision_i.lt(355)].reset_index(drop=True))


@pytest.mark.parametrize("cancel", ["timeout", "trend"])
def test_pullback_expiration_and_trend_failure_do_not_recover_old_touch(cancel):
    f = controlled_features()
    set_trend(f)
    f.loc[f.index[350], ["low", "high", "close"]] = [95., 102., 95.5]
    if cancel == "trend":
        f.loc[f.index[352], "sma60"] = 70.
        recovery = 355
    else:
        recovery = 363
    f.loc[f.index[recovery], ["high", "close"]] = [106., 105.]
    c = candidate_events(f, 60)
    assert not c.arm.eq("pullback_sma60").any()


def test_young_rule_does_not_require_or_read_missing_long_moving_averages():
    f = controlled_features(90)
    f.loc[f.index[59], ["close", "high", "relative_volume", "sma20", "prior20_high"]] = [110., 111., 3., 100., 109.]
    f.loc[f.index[59], "first20_breakout"] = True
    f.loc[:, ["sma60", "sma120", "ema60", "ema120"]] = np.nan
    assert not f.ready.any()
    c = candidate_events(f, 60)
    assert c.arm.tolist() == ["young_breakout_sma20"]
    assert c.history_count.tolist() == [60]
    extended = controlled_features()
    extended.loc[extended.index[339], ["close", "high", "relative_volume", "sma20", "prior20_high"]] = [110., 111., 3., 100., 109.]
    extended.loc[extended.index[339], "first20_breakout"] = True
    assert not candidate_events(extended, 60).arm.eq("young_breakout_sma20").any()


def test_prior_ratchet_not_current_ma_and_only_close_breach_exits_next_open():
    b, f = market(6)
    f.sma60 = [99., 102., 100.5, 100., 99., 98.]
    b.iloc[1] = [100., 102., 99.5, 101.]
    b.iloc[2] = [101., 102., 98.5, 100.]
    b.iloc[3] = [100., 101., 97., 98.]
    b.iloc[4] = [97., 100., 95., 99.]
    event = simulate(b, f)
    assert event["trigger_i"] == 3
    assert event["exit_i"] == 4 and event["exit_price"] == 97.
    assert event["trigger_time"] == b.index[4] == event["exit_time"]
    assert event["held_hours_lower"] == event["held_hours_upper"] == 3
    assert all(p["protection"] == 99. for p in event["protection"])


def test_ratchet_cannot_fall_with_ma_and_is_not_an_intrabar_stop():
    b, f = market(6)
    f.sma60 = [95., 101., 99., 97., 96., 95.]
    b.iloc[1] = [100., 104., 96., 103.]
    b.iloc[2] = [103., 104., 96., 102.]
    b.iloc[3] = [102., 104., 95., 100.]
    event = simulate(b, f)
    assert [p["protection"] for p in event["protection"]] == [95., 101., 101., 101.]
    assert event["trigger_i"] == 3 and event["exit_i"] == 4


def test_signal_atr_frozen_next_open_entry_and_fees_use_entry_notional():
    b, f = market(6)
    b.iloc[1:] = [110., 113., 105., 111.]
    f.loc[f.index[1:], "atr"] = .01
    event = simulate(b, f)
    assert event["entry_i"] == 1 and event["entry_price"] == 110.
    assert event["initial_stop"] == 100. and event["initial_risk"] == 10.
    assert event["gross_pnl_per_base"] == 1.
    assert event["total_fee_per_base"] == pytest.approx(.22)
    assert event["net_r"] == pytest.approx(.078)
    assert event["exit_fee_per_base"] == pytest.approx(.11)


def test_initial_stop_precedes_unknown_same_bar_tp_and_extrema_are_not_guessed():
    b, f = market()
    b.iloc[1] = [100., 150., 89., 110.]
    event = simulate(b, f, "fixed3r")
    assert event["exit_price"] == 90. and event["exit_reason"] == "initial_stop"
    assert event["mfe_r"] == 0. and event["mae_r"] == pytest.approx(1.)
    assert not event["exit_time_exact"]
    assert event["held_hours_lower"] == 0. and event["held_hours_upper"] == 1.


def test_observed_tp_gap_open_precedes_later_intrabar_stop():
    b, f = market()
    b.iloc[2] = [140., 150., 89., 101.]
    event = simulate(b, f, "fixed3r")
    assert event["exit_reason"] == "take_profit_gap" and event["exit_price"] == 130.
    assert event["exit_time"] == b.index[2] and event["held_hours_upper"] == 1.
    assert event["mfe_return"] < .5 and event["mae_return"] < .11


def test_stop_gap_fills_worse_open_and_next_open_exit_ignores_later_high():
    b, f = market()
    f.loc[f.index[1], "md"] = 0.
    b.iloc[2] = [85., 190., 80., 105.]
    event = simulate(b, f, "md")
    assert event["exit_reason"] == "initial_stop_gap" and event["exit_price"] == 85.
    assert event["mfe_r"] == pytest.approx(.1)
    assert event["mae_r"] == pytest.approx(1.5)
    b.iloc[2] = [102., 190., 100., 105.]
    event = simulate(b, f, "md")
    assert event["exit_reason"] == "md" and event["exit_price"] == 102.
    assert event["mfe_r"] == pytest.approx(.2)


@pytest.mark.parametrize("minutes", [60, 240])
def test_boundary_mark_censored_and_final_bar_decision_not_discarded(minutes):
    b, f = market(minutes=minutes)
    event = simulate(b, f, last_i=3)
    assert event["censored"] and not event["natural_exit"]
    assert event["exit_reason"] == "boundary_mark" and event["exit_timing"] == "close"
    assert event["exit_time"] == b.index[3]+pd.Timedelta(minutes=minutes)
    assert event["net_bp"] == pytest.approx(-20.)
    assert event["held_hours_lower"] == event["held_hours_upper"] == 3*minutes/60
    unfillable = simulate(b, f, i=3, last_i=3)
    assert not unfillable["valid"] and unfillable["reason"] == "no_entry_bar"
    f2 = controlled_features()
    f2.loc[f2.index[-1], "release_side"] = 1
    assert candidate_events(f2, 60).decision_i.eq(len(f2)-1).sum() == 4


def test_missing_or_impossible_risk_retained_and_future_data_cannot_change_early_exit():
    b, f = market()
    f.loc[f.index[0], "atr"] = np.nan
    assert simulate(b, f)["reason"] == "invalid_initial_risk"
    f.loc[f.index[0], "atr"] = 51.
    assert simulate(b, f)["reason"] == "invalid_initial_risk"
    f.loc[f.index[0], "atr"] = 5.
    f.loc[f.index[1], "md"] = 0.
    before = simulate(b, f, "md", last_i=3)
    b.loc[b.index[4:], :] = -99.
    f.loc[f.index[4:], :] = np.nan
    after = simulate(b, f, "md", last_i=3)
    assert before["exit_time"] == after["exit_time"]
    assert before["net_r"] == after["net_r"]


def test_prepared_snapshot_parity_and_caller_mutations_cannot_change_it():
    b, f = market()
    f.loc[f.index[2], "md"] = 0.
    before = simulate(b, f, "md")
    prepared = prepare_simulation(b, f)
    b.loc[:, :] = -999.
    f.loc[:, :] = np.nan
    after = simulate_prepared(prepared, 0, "md", 7, include_protection=True)
    assert before["exit_time"] == after["exit_time"]
    assert before["net_r"] == after["net_r"]
    with pytest.raises(ValueError):
        prepared.prices[1, 0] = 999.
    with pytest.raises(TypeError):
        prepared.values["md"] = np.zeros(8)


def test_young_breakout_is_first_cross_only_with_prefix_and_boundary_parity():
    b = hourly(100)
    b.loc[:, ["open", "high", "low", "close"]] = [100., 101., 99., 100.]
    b.loc[:, ["volume", "quote_volume"]] = [10., 1000.]
    b.iloc[60, :4] = [100., 104., 99., 103.]
    b.iloc[61, :4] = [103., 106., 102., 105.]
    b.loc[b.index[60:62], "volume"] = 30.
    full = build_features(b)[60]
    c = candidate_events(full, 60)
    young = c.loc[c.arm.eq("young_breakout_sma20")]
    assert young.decision_i.tolist() == [60]
    prefix = build_features(b.iloc[:62])[60]
    pd.testing.assert_frame_equal(candidate_events(prefix, 60), c.loc[c.decision_i.le(61)].reset_index(drop=True))
    assert not candidate_events(full, 60, first_i=61).arm.eq("young_breakout_sma20").any()


def test_young_sma20_execution_needs_no_long_ma_or_md_columns():
    b, f = market()
    f = f.drop(columns=["md", "sma60"])
    event = simulate(b, f, "young_breakout_sma20")
    assert event["valid"] and event["exit_rule"] == "ratchet_sma20"
    assert event["exit_reason"] == "boundary_mark"
