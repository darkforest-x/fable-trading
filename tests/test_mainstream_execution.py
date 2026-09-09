"""Synthetic clock, matching and fill-contract checks; no research price IO."""

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.mainstream_execution import evaluate_book


def market(n=1100, minutes=60):
    index = pd.date_range("2025-01-01", periods=n, freq=f"{minutes}min", tz="UTC")
    bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100.), index=index)
    features = pd.DataFrame(dict(atr=1., md=1., release_side=0,
        relative_volume=np.arange(n, dtype=float), near_zero_bars=12,
        momentum10=np.arange(n, dtype=float) / 10), index=index)
    return bars, features


def run(bars, features, candidates, *, arms=None, minutes=60, first_i=0):
    return evaluate_book(bars, features, candidates, first_i=first_i,
        last_i=len(bars)-1, symbol="SYNTHETIC", minutes=minutes,
        fold="synthetic", seed=1234, arms=arms)


def test_matching_uses_actual_close_month_and_shared_union_without_strategy_gates():
    bars, features = market(1700)
    decision = bars.index.get_loc(pd.Timestamp("2025-01-31T23:00:00Z"))
    other_decision = decision + 3
    features.loc[features.index[[decision+1, decision+2]], "release_side"] = 1
    candidates = [dict(arm="baseline", anchor_i=decision-10, decision_i=decision),
                  dict(arm="filter", anchor_i=decision-10, decision_i=decision),
                  dict(arm="later", anchor_i=decision, decision_i=other_decision)]
    book = run(bars, features, candidates)
    events, controls = book["events"], book["controls"]
    assert events.month.eq("2025-02").all()
    assert events.control_count.eq(3).all()
    assert controls.month.eq("2025-02").all()
    assert controls.matched_month.eq(controls.month).all()
    assert controls.matched_volbin.eq(controls.volbin).all()
    assert controls.control_signal_i.is_unique
    assert not set(controls.control_signal_i) & {decision, other_decision, decision+1, decision+2}
    baseline, filtered = events.iloc[0], events.iloc[1]
    assert baseline.control_indexes == filtered.control_indexes
    assert baseline.control_mean_net_bp == filtered.control_mean_net_bp
    # No signal-quality or ready flags may select a more favorable control pool.
    changed = features.copy()
    changed["relative_volume"] = 0.
    changed["momentum10"] = -100.
    changed["ready"] = False
    repeated = run(bars, changed, candidates)
    pd.testing.assert_frame_equal(controls, repeated["controls"])
    ratios = features.atr / bars.close
    expected = np.ceil(ratios.rolling(240).rank(pct=True) * 5).clip(1, 5)
    for row in controls.itertuples():
        assert row.volbin == expected.iloc[row.control_signal_i]


def test_same_confirmation_keeps_distinct_anchor_ids_but_only_one_fill():
    bars, features = market(320)
    bars.iloc[261:] = [110., 111., 109., 110.]
    features.iloc[260, features.columns.get_loc("atr")] = 2.
    features.iloc[265:, features.columns.get_loc("md")] = 0.
    book = run(bars, features,
        [dict(arm="wait", anchor_i=250, decision_i=260),
         dict(arm="wait", anchor_i=255, decision_i=260)])
    events = book["events"]
    assert events.event_id.is_unique
    assert events.portfolio_selected.tolist() == [True, False]
    assert events.entry_i.eq(261).all() and events.entry_price.eq(110).all()
    assert events.signal_atr.eq(2).all() and events.initial_stop.eq(106).all()
    assert events.exit_i.eq(266).all() and events.exit_reason.eq("md").all()
    assert events.decision_time.eq(bars.index[261]).all()
    assert events.wait_bars.tolist() == [10, 5]
    assert events.relative_volume.eq(260).all()
    assert events.anchor_relative_volume.tolist() == [250, 255]
    assert events.wait_price_change_bp.tolist() == pytest.approx([1000., 1000.])
    assert book["diagnostics"][0]["selected_events"] == 1
    assert book["diagnostics"][0]["skipped_overlap"] == 1


@pytest.mark.parametrize("minutes, md_end", [(60, 1000), (240, 500)])
def test_more_than_thirty_days_runs_to_md_next_open_and_boundary_stays_censored(minutes, md_end):
    bars, features = market(md_end+20, minutes)
    candidates = [dict(arm="baseline", anchor_i=250, decision_i=250)]
    features.iloc[md_end:, features.columns.get_loc("md")] = 0.
    book = run(bars, features, candidates, minutes=minutes)
    event = book["events"].iloc[0]
    assert event.valid and event.exit_reason == "md"
    assert event.exit_i == md_end+1 and event.exit_at_open
    assert event.hold_seconds > 30*86400
    assert not event.censored
    assert event.net_return == pytest.approx(-0.002)
    assert book["curves"]["baseline"].index[-1] == bars.index[-1] + pd.Timedelta(minutes=minutes)
    assert book["curves"]["baseline"].iloc[-1] == pytest.approx(.998)
    assert book["diagnostics"][0]["max_hold_days"] == 36500
    boundary_features = features.assign(md=1.)
    boundary = run(bars, boundary_features, candidates, minutes=minutes)["events"].iloc[0]
    assert boundary.exit_reason == "boundary_mark"
    assert boundary.censored and not boundary.natural_exit
    assert boundary.hold_seconds > 30*86400


def test_initial_stop_has_priority_and_three_r_is_an_explicit_separate_arm():
    bars, features = market(320)
    candidates = [dict(arm="baseline", anchor_i=250, decision_i=250),
                  dict(arm="baseline_3r", anchor_i=250, decision_i=250)]
    bars.iloc[251] = [100., 107., 97., 100.]
    stopped = run(bars, features, candidates)["events"]
    assert stopped.exit_reason.eq("initial_stop").all()
    assert stopped.exit_price.eq(98).all()
    assert stopped.net_bp.tolist() == pytest.approx([-220., -220.])
    bars.iloc[251] = [100., 107., 99., 100.]
    features.iloc[255:, features.columns.get_loc("md")] = 0.
    events = run(bars, features, candidates)["events"].set_index("arm")
    assert events.loc["baseline", "exit_rule"] == "md"
    assert events.loc["baseline", "exit_i"] == 256
    assert events.loc["baseline_3r", "exit_rule"] == "fixed3r"
    assert events.loc["baseline_3r", "exit_price"] == 106
    assert events.loc["baseline_3r", "net_bp"] == pytest.approx(580.)
    assert events.loc["baseline", "control_indexes"] == events.loc["baseline_3r", "control_indexes"]


def test_gap_invalid_candidate_is_retained_and_never_filled():
    bars, features = market(320)
    missing = bars.index[263]
    bars, features = bars.drop(missing), features.drop(missing)
    book = run(bars, features, [dict(arm="baseline", anchor_i=260, decision_i=260)])
    event = book["events"].iloc[0]
    assert not event.valid and event.invalid_reason == "gap_during_holding"
    assert not event.portfolio_selected and pd.isna(event.excess_bp)
    assert book["diagnostics"][0]["invalid_candidate_count"] == 1
    assert book["diagnostics"][0]["invalid_events"] == 1
    assert book["curves"]["baseline"].eq(1).all()


def test_unavailable_entry_is_retained_without_reading_a_later_price():
    bars, features = market(320)
    book = run(bars, features, [dict(arm="baseline", anchor_i=300, decision_i=319)])
    event = book["events"].iloc[0]
    assert not event.valid and event.invalid_reason == "no_entry_bar"
    assert pd.isna(event.wait_price_change_bp)
    assert event.requested_control_count == 0
    assert book["diagnostics"][0]["invalid_candidate_count"] == 1


def test_empty_arms_keep_full_cash_curves_and_zero_diagnostics():
    bars, features = market(280)
    book = run(bars, features, [], arms=["baseline", "baseline_3r", "wait"])
    assert book["events"].empty and book["controls"].empty
    assert list(book["curves"]) == ["baseline", "baseline_3r", "wait"]
    for curve in book["curves"].values():
        assert curve.eq(1).all() and len(curve) == len(bars)
    for diagnostic in book["diagnostics"]:
        assert diagnostic["candidate_count"] == 0
        assert diagnostic["selected_events"] == 0
        assert diagnostic["max_drawdown"] == 0
    assert run(bars, features, [])["curves"] == {}


def test_nonpositive_md_at_actual_confirmation_is_rejected():
    bars, features = market(320)
    features.iloc[260, features.columns.get_loc("md")] = 0.
    with pytest.raises(ValueError, match="actual decision"):
        run(bars, features, [dict(arm="wait", anchor_i=250, decision_i=260)])


@pytest.mark.parametrize("unit", ["ms", "us"])
def test_index_resolution_preserves_actual_close_matching_and_equity(unit):
    bars, features = market(320)
    candidates = [dict(arm="baseline", anchor_i=250, decision_i=250)]
    expected = run(bars, features, candidates)
    bars.index = bars.index.as_unit(unit)
    features.index = features.index.as_unit(unit)
    actual = run(bars, features, candidates)
    assert actual["events"].control_indexes.tolist() == expected["events"].control_indexes.tolist()
    pd.testing.assert_series_equal(actual["curves"]["baseline"], expected["curves"]["baseline"])
