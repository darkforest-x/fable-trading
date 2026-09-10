"""Synthetic causality, parent-child clocks and unchanged research contracts."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_early_warning as ew
from yoyo.evaluation import spike_burst_recall_study as old


def frame(n=100):
    f = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100.,
        atr=1., tr=2., ready=True, history_count=np.arange(n)+400, atr_pct=.01,
        s20=99., e20=99.5, s60=100., e60=100., s120=100., e120=100.,
        ropeHigh=101., pastWidth=1., pastCrosses=3.,
        middle=100.+np.arange(n)*.01, md=1., sb=0., rv=1., expansion=1.),
        index=pd.date_range("2026-07-10T00:00Z", periods=n, freq="h"))
    f.attrs["minutes"] = 60
    return f


def launch(f, i, close=104., volume=600.):
    f.loc[f.index[i], ["open", "high", "low", "close", "volume"]] = [100., close+.2, 99., close, volume]


def test_prefix_invariance_all_columns_and_input_unchanged():
    f = frame()
    for i in (30, 45, 60, 75):
        launch(f, i, close=104+i/20)
    before = f.copy(deep=True)
    full = ew.detect(f)
    for count in (29, 31, 34, 46, 61, 79, 99):
        pd.testing.assert_frame_equal(ew.detect(f.iloc[:count]), full.iloc[:count])
    pd.testing.assert_frame_equal(f, before)


def test_full_condition_edge_not_just_price_breakout():
    f = frame()
    for i in range(30, 34):
        launch(f, i, close=104+i)
    f.loc[f.index[30], "s20"] = 200.
    state = ew.detect(f)
    assert not state.early.iloc[30]
    assert state.early.iloc[31]  # Price breakout true before, but full condition only now turns true.
    assert not state.early.iloc[32]


def test_cooldown_is_since_accepted_event_and_does_not_queue_blocked_edge():
    f = frame()
    launch(f, 30)
    for i in range(35, 44):
        launch(f, i, close=110+i)
    launch(f, 46, close=200.)
    state = ew.detect(f)
    assert np.flatnonzero(state.early).tolist() == [30, 46]
    assert state.cooldown_blocked.iloc[35]
    assert not state.early.iloc[42]  # No queued alert when the 12-bar clock ends.


def test_exact_twelve_spacing_is_allowed_and_holdings_irrelevant():
    f = frame()
    launch(f, 30)
    launch(f, 42, 106.)
    first = ew.detect(f)
    f["replay_trend_side"] = 1
    f["trend_side"] = 1
    second = ew.detect(f)
    assert np.flatnonzero(first.early).tolist() == [30, 42]
    pd.testing.assert_frame_equal(first, second)


def test_confirmation_age_zero_and_once_per_parent():
    f = frame()
    launch(f, 30)
    f.loc[f.index[31:34], ["close", "high", "volume"]] = [104., 104.2, 600.]
    state = ew.detect(f)
    assert state.confirmed.iloc[30] and state.confirm_age.iloc[30] == 0
    assert state.confirmed.sum() == 1
    assert state.parent_i.iloc[30] == 30


def test_confirmation_age_three_uses_frozen_boundary_and_not_rolling_high():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32):
        launch(f, i, 101.1, 100.)
    launch(f, 33, 104., 600.)
    state = ew.detect(f)
    assert not state.confirmed.iloc[30:33].any()
    assert state.confirmed.iloc[33] and state.confirm_age.iloc[33] == 3
    assert state.frozen_parent_high.iloc[33] == 101.
    assert f.close.iloc[33] < state.prog_prior_high.iloc[33]
    assert state.parent_i.iloc[33] == 30


def test_age_four_expiration_cannot_backdate_confirmation():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    for i in (31, 32, 33):
        launch(f, i, 101.1, 100.)
    before = ew.detect(f.iloc[:34])
    launch(f, 34, 104., 600.)
    state = ew.detect(f)
    assert not state.confirmed.any()
    pd.testing.assert_frame_equal(state.iloc[:34], before)


def test_confirmation_does_not_require_efficiency_close_position_or_above_six():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], ["high", "tr", "ropeHigh"]] = [150., 100., 120.]
    state = ew.detect(f)
    assert not state.tag_efficiency.iloc[30]
    assert not state.tag_close_position.iloc[30]
    assert not state.tag_above_six.iloc[30]
    assert state.confirmed.iloc[30]


def test_early_does_not_require_volume_or_md_direction():
    f = frame()
    launch(f, 30)
    f.loc[f.index[30], ["volume", "md"]] = [np.nan, -1.]
    state = ew.detect(f)
    assert state.early.iloc[30] and not state.confirmed.iloc[30]


def test_gap_resets_parent_and_requires_twelve_contiguous_prior_bars():
    f = frame()
    launch(f, 30, 101.1, 100.)
    f.loc[f.index[30], "high"] = 110.
    f = f.drop(f.index[31])
    for i in range(31, 47):
        launch(f, i, 104+i)
    state = ew.detect(f)
    assert not state.confirmed.iloc[31:34].any()
    assert not state.early.iloc[31:43].any()
    assert state.early.iloc[43]
    assert state.prog_volume_base.iloc[43] != state.prog_volume_base.iloc[43]  # No baseline across gap.


def test_breadth_is_same_hour_valid_observations_and_prefix_causal():
    f = frame()
    launch(f, 30)
    f.loc[f.index[31], "ready"] = False
    f.loc[f.index[32], "s20"] = np.nan
    fields = ew.early_fields(f)
    assert not fields.breadth_valid.iloc[0]
    assert fields.breadth_joint.iloc[30]
    assert not fields.breadth_valid.iloc[31:33].any()
    pd.testing.assert_frame_equal(ew.early_fields(f.iloc[:34]), fields.iloc[:34])
    gap = ew.early_fields(f.drop(f.index[20]))
    assert not gap.breadth_valid.iloc[20]


def test_matching_uses_exact_old_label_denominator_without_mutating_old_arms():
    f = frame()
    launch(f, 30)
    launch(f, 33, close=108.)
    labels = old.label_events(f)
    labels["instrument"] = "test"
    ds = []
    ss = []
    for arm in ew.ARMS:
        dummy = pd.DataFrame(dict(trend_side=0, burst=False), index=f.index)
        matches, detections = old.match_signals(labels, [30], f, dummy)
        detections["instrument"], detections["arm"] = "test", arm
        ds.append(detections)
        ss.append(dict(arm=arm, decision_time=f.index[30]+ew.HOUR, match_status=matches.match_status.iloc[0], lag=0, confirm_age=0))
    exposure = pd.DataFrame(dict(decision_time=f.index+ew.HOUR, eligible_bars=1))
    before = labels.copy(deep=True)
    summary = ew.recall_summary(labels, pd.concat(ds), pd.DataFrame(ss), exposure)
    full = summary.loc[summary.period.eq("full")]
    assert full.positive_events.tolist() == [int(labels.label.eq("positive").sum())]*2
    assert old.ARMS == ("v1", "v2")
    pd.testing.assert_frame_equal(labels, before)


def test_controls_use_union_causal_exclusion_and_are_integer_positions():
    f = frame(900)
    union = [350, 352, 500, 503]
    matches = ew.match_controls(f, union, union, "test", 60, ew.START, ew.END)
    for i, controls in matches.items():
        assert isinstance(i, int) and len(controls) <= 3 and len(set(controls)) == len(controls)
        for j in controls:
            assert isinstance(j, int)
            assert all(not (k <= j <= k+12) for k in union)
            assert f.index[i].isocalendar().week == f.index[j].isocalendar().week


def test_forward_outcomes_are_labels_only_and_require_full_future():
    f = frame()
    launch(f, 30)
    f.loc[f.index[31:33], "close"] = 104.
    launch(f, 33, 108.)
    before = ew.detect(f.iloc[:31])
    assert ew.forward_outcome(f, 30) == dict(forward_known=True, forward_success=True)
    assert not ew.forward_outcome(f.iloc[:34], 30)["forward_known"]
    f.loc[f.index[31], "close"] = 102.
    assert ew.forward_outcome(f, 30) == dict(forward_known=True, forward_success=False)
    pd.testing.assert_frame_equal(before, ew.detect(f).iloc[:31])


def test_prepare_checks_commit_before_reading_sources(monkeypatch, tmp_path):
    def reject():
        raise ValueError("Commit exact builder/plan")
    monkeypatch.setattr(ew, "source_pins", reject)
    with pytest.raises(ValueError, match="Commit exact"):
        ew.prepare(tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_evaluate_rejects_second_scoring(monkeypatch, tmp_path):
    monkeypatch.setattr(ew, "source_pins", lambda: {})
    (tmp_path / "evaluation_started.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(ValueError, match="second scoring"):
        ew.evaluate(tmp_path)


def test_synthetic_complete_prepare_evaluate_and_exact_labels(monkeypatch, tmp_path):
    """Exercise orchestration, schemas and parent joins without market data."""
    f = frame(500)
    launch(f, 350)
    f.loc[f.index[351], ["open", "high", "low", "close"]] = [104., 104.5, 103., 104.]
    launch(f, 352, 109.)
    launch(f, 400, 106.)
    labels = old.label_events(f)
    context = dict(instrument="synthetic", asset="SYNTH", symbol="SYNTH/USDT:USDT", venue="okx", minutes=60)
    for key, value in context.items():
        labels[key] = value
    labels["label_id"] = [old.identity("synthetic", "label", int(i)) for i in labels.event_i]
    previous = tmp_path / "prior"
    previous.mkdir()
    old.write_csv(previous / "labels.csv.gz", labels)
    pd.DataFrame(columns=["arm", "period"]).to_csv(previous / "recall_summary.csv", index=False)
    pd.DataFrame(columns=["arm", "period"]).to_csv(previous / "trade_summary.csv", index=False)
    job = dict(context, features_path=str(tmp_path / "unused.pkl"), features_sha256="synthetic", tick=.01)
    coverage = pd.DataFrame([context])
    monkeypatch.setattr(ew, "source_pins", lambda: {})
    monkeypatch.setattr(ew, "authenticated_prior", lambda _: ([job], coverage, {}, labels))
    monkeypatch.setattr(ew, "load_feature", lambda *args: f.copy())
    monkeypatch.setattr(ew, "V2", previous)
    output = tmp_path / "results"
    prepared = ew.prepare(output, previous)
    assert prepared["status"] == "complete" and prepared["segments"] == 1
    assert (output / "labels.csv.gz").read_bytes() == (previous / "labels.csv.gz").read_bytes()
    signals = pd.read_csv(output / "signals.csv.gz")
    parents = pd.read_csv(output / "parent_registry.csv.gz")
    assert len(signals) and signals.parent_event_id.isin(parents.event_id).all()
    assert set(signals.arm) == {"early", "confirmed"}
    assert pd.read_csv(output / "controls.csv.gz").matched_event_id.isin(signals.event_id).all()
    validated = ew.evaluate(output)
    assert validated["status"] == "complete" and validated["primary_tests"] == 2
    trades = pd.read_csv(output / "trade_events.csv.gz")
    assert trades.valid.all() and set(trades.fee_bp) == {20.}
    assert (trades.entry_i == trades.decision_i+1).all()
    with pytest.raises(ValueError, match="overwrite"):
        ew.prepare(output, previous)
    with pytest.raises(ValueError, match="second scoring"):
        ew.evaluate(output)
