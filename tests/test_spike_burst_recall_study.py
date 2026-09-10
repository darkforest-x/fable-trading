"""Synthetic-only checks for independent labels, clocks and frozen evaluation."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_recall_study as study


def frame(n=80, start="2026-07-10T00:00Z"):
    return pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=100., quote_volume=10000.,
        atr=1., ready=True, history_count=np.arange(n), atr_pct=.01, rv=1., expansion=1.),
        index=pd.date_range(start, periods=n, freq="h"))


def anchor(f, i, close=102.):
    f.loc[f.index[i], ["open", "high", "low", "close"]] = [100., close + .1, 99., close]


def state(f, signals=(), active=()):
    s = pd.DataFrame(dict(burst=False, trend_side=0, route="", quiet_bars=12, pending_side=0), index=f.index)
    for i in signals:
        s.loc[s.index[i], ["burst", "route"]] = [True, "price_first"]
    for i in active:
        s.loc[s.index[i], "trend_side"] = 1
    return s


def positive_frame():
    f = frame()
    anchor(f, 12)
    f.loc[f.index[13]:, ["open", "high", "low", "close"]] = [103., 103.1, 102.5, 103.]
    anchor(f, 15, 106.)
    return f


def test_labels_use_future_closes_not_wicks_and_frozen_atr():
    f = positive_frame()
    labels = study.label_events(f)
    row = labels.loc[labels.event_i.eq(12)].iloc[0]
    assert row.label == "positive" and row.target_lag == 3
    assert row.decision_time == f.index[12] + study.HOUR
    f.loc[f.index[15], "close"] = 105.9
    f.loc[f.index[15], "high"] = 1000.
    f.loc[f.index[13]:, "atr"] = .00001
    row = study.label_events(f).iloc[0]
    assert row.label == "negative"  # No close hit 102 + 4*1.
    assert row.future_peak_return > 8 and row.large_peak


def test_adverse_before_target_is_negative_even_when_later_winner():
    f = positive_frame()
    f.loc[f.index[13], "close"] = 100.
    row = study.label_events(f).iloc[0]
    assert row.label == "negative" and row.adverse_lag == 1 and row.target_lag == 3


def test_target_before_adverse_is_positive():
    f = positive_frame()
    f.loc[f.index[16], "close"] = 99.
    row = study.label_events(f).iloc[0]
    assert row.label == "positive" and row.target_lag < row.adverse_lag


def test_refractory_precedes_labels_and_is_fixed_not_rolling():
    f = frame(100)
    anchor(f, 12)
    f.loc[f.index[13], "close"] = 99.
    anchor(f, 20, 106.)  # Would be positive, but blocked by earliest negative anchor.
    anchor(f, 23, 110.)
    anchor(f, 36, 111.)
    rows = study.label_events(f)
    assert rows.event_i.tolist()[:2] == [12, 36]
    assert rows.iloc[0].label == "negative"


def test_first_cross_uses_each_bars_own_prior_twelve_range():
    f = frame(80)
    for i in range(12, 45):
        anchor(f, i, 102. + i)
    assert study.label_events(f).event_i.tolist() == [12]


def test_refractory_carries_across_reporting_start():
    f = frame(80)
    anchor(f, 12)
    anchor(f, 20, 104.)
    rows = study.label_events(f, start=f.index[15], end=f.index[-1])
    assert rows.event_i.tolist() == [12]
    assert not rows.iloc[0].in_study


def test_incomplete_future_remains_unknown_even_if_target_already_hit():
    f = positive_frame().iloc[:20]
    row = study.label_events(f).iloc[0]
    assert row.label == "unknown" and row.unknown_reason == "insufficient_future_24"
    assert pd.isna(row.target_lag) and pd.isna(row.future_peak_return)


def test_future_gap_is_unknown_and_past_gap_does_not_create_anchor():
    f = positive_frame().drop(positive_frame().index[25])
    assert study.label_events(f).iloc[0].unknown_reason == "future_gap"
    g = positive_frame().drop(positive_frame().index[5])
    assert 11 not in study.label_events(g).event_i.tolist()


def test_unknown_nan_and_not_ready_do_not_become_success():
    f = positive_frame()
    f.loc[f.index[22], "close"] = np.nan
    assert study.label_events(f).iloc[0].label == "unknown"
    f["ready"] = False
    assert study.label_events(f).empty


@pytest.mark.parametrize("bad", ["naive", "duplicate", "unaligned"])
def test_invalid_clock_fails(bad):
    f = positive_frame()
    if bad == "naive":
        f.index = f.index.tz_localize(None)
    elif bad == "duplicate":
        f.index = pd.DatetimeIndex([f.index[0]] + list(f.index[:-1]))
    else:
        f.index = f.index + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError):
        study.label_events(f)


def test_only_actual_confirmation_counts_and_repeated_signals_are_duplicates():
    f = positive_frame()
    labels = study.label_events(f)
    matches, detections = study.match_signals(labels, [11, 14, 15, 19], f, state(f))
    assert matches.match_status.tolist() == ["unmatched", "matched", "duplicate", "unmatched"]
    d = detections.loc[detections.event_i.eq(12)].iloc[0]
    assert d.first_signal_lag == 2
    assert not d.hit_0 and not d.hit_1 and d.hit_2 and d.hit_6


def test_ongoing_tracking_is_separate_not_an_imaginary_arrow():
    f = positive_frame()
    _, detection = study.match_signals(study.label_events(f), [], f, state(f, active=[11, 12]))
    row = detection.iloc[0]
    assert row.already_tracking
    assert not row.hit_0 and not row.hit_6 and pd.isna(row.first_signal_lag)


def test_signal_on_new_entry_is_not_already_tracking():
    f = positive_frame()
    _, detection = study.match_signals(study.label_events(f), [12], f, state(f, [12], [11, 12]))
    assert not detection.iloc[0].already_tracking and detection.iloc[0].hit_0


def test_tail_unknown_separate_from_false_alert():
    f = positive_frame()
    matches, _ = study.match_signals(study.label_events(f), [70], f, state(f))
    assert matches.iloc[0].match_status == "unknown"


def test_random_exclusion_is_past_only_and_source_fields_causal():
    f = frame(500, "2026-07-01T00:00Z")
    f["history_count"] = 0
    f.loc[f.index[[405, 407, 410]], "history_count"] = 400
    a = study.match_controls(f, [410], [410], "okx:synthetic", 60)
    b = study.match_controls(f, [410, 420], [410], "okx:synthetic", 60)
    assert set(a[410]) == {405, 407} == set(b[410])
    c = study.match_controls(f, [400, 410], [410], "okx:synthetic", 60)
    assert c[410] == []


def test_precision_and_recall_denominators_differ_and_tracking_not_credit():
    f = positive_frame()
    labels = study.label_events(f).assign(instrument="x")
    detections = []
    signals = []
    for arm in study.ARMS:
        m, d = study.match_signals(labels, [13, 14, 20, 70], f, state(f))
        d["arm"], d["instrument"] = arm, "x"
        detections.append(d)
        m["arm"] = arm
        m["decision_time"] = [f.index[i] + study.HOUR for i in m.decision_i]
        signals.append(m)
    exposure = pd.DataFrame(dict(decision_time=f.index + study.HOUR, eligible_bars=1))
    summary = study.recall_summary(labels, pd.concat(detections), pd.concat(signals), exposure)
    row = summary.loc[summary.arm.eq("v1") & summary.period.eq("full")].iloc[0]
    assert row.positive_events == 1 and row.recall_1 == 1
    assert row.signals == 4 and row.unique_matched == 1 and row.duplicates == 1
    assert row.unmatched == 1 and row.unknown_signals == 1
    assert row.precision_all == .25 and row.precision_adjudicated == pytest.approx(1/3)
    assert row.false_alerts_per_100_asset_days == 30


def test_zero_events_no_fabricated_recall_or_precision():
    f = frame()
    labels = study.label_events(f).assign(instrument="x")
    detections, signals = [], []
    for arm in study.ARMS:
        m, d = study.match_signals(labels, [], f, state(f))
        detections.append(d.assign(instrument="x", arm=arm))
        signals.append(m.assign(arm=arm, decision_time=pd.Series(dtype="datetime64[ns, UTC]")))
    labels["decision_time"] = pd.to_datetime(labels.decision_time, utc=True)
    summary = study.recall_summary(labels, pd.concat(detections), pd.concat(signals), pd.DataFrame(
        dict(decision_time=f.index + study.HOUR, eligible_bars=1)))
    row = summary.iloc[0]
    assert row.positive_events == 0 and pd.isna(row.recall_1) and pd.isna(row.precision_all)
    assert not row.target_80_met


def test_confirmation_and_case_windows_are_explicit():
    assert study.SNAPSHOT_OPENS[0].tz_convert("Asia/Shanghai").hour == 22
    assert (study.SNAPSHOT_OPENS[0] + study.HOUR).tz_convert("Asia/Shanghai").hour == 23
    assert (study.SNAPSHOT_OPENS[1] + study.HOUR).tz_convert("Asia/Shanghai").day == 20
    assert study.NIGHT[0].tz_convert("Asia/Shanghai").hour == 18
    assert study.NIGHT[1].tz_convert("Asia/Shanghai").hour == 6


def test_prepare_freezes_globally_before_simulation_and_evaluate_tamper_fails(tmp_path, monkeypatch):
    f = positive_frame()
    f["history_count"] = 400
    f.attrs.update(minutes=60, period_seconds=3600)
    source = tmp_path / "synthetic.pkl.gz"
    f.to_pickle(source)
    job = dict(instrument="okx:SYN-USDT-SWAP:segment0", asset="SYN", symbol="SYN-USDT-SWAP", venue="okx",
        minutes=60, tick=".01", features_path=str(source), features_sha256=study.sha(source))
    coverage = pd.DataFrame([dict(venue="okx", symbol=job["symbol"], minutes=60)])
    monkeypatch.setattr(study, "source_pins", lambda: {"synthetic": "fixed"})
    monkeypatch.setattr(study, "load_prior", lambda prior: ([job], coverage, {str(source): study.sha(source)}))
    monkeypatch.setattr(study.v1, "replay", lambda frame, tick: state(frame, [13], range(13, 20)))
    fake = SimpleNamespace(progressive_fields=lambda frame: pd.DataFrame(index=frame.index),
        replay=lambda frame, tick: state(frame, [12], range(12, 20)))
    monkeypatch.setitem(sys.modules, "yoyo.evaluation.spike_burst_progressive", fake)
    import yoyo.evaluation
    monkeypatch.setattr(yoyo.evaluation, "spike_burst_progressive", fake, raising=False)
    monkeypatch.setattr(study, "simulate_trade", lambda *a, **kw: pytest.fail("prepare must never score"))
    folder = tmp_path / "results"
    receipt = study.prepare(folder, tmp_path)
    assert receipt["status"] == "complete"
    assert (folder / "matching.json").exists() and not (folder / "trade_events.csv.gz").exists()
    times = pd.read_csv(folder / "event_timing_changes.csv.gz")
    assert times.loc[times.event_i.eq(12), "earlier_v2"].iloc[0]
    # Any modification after preparation fails before the execution function.
    with (folder / "signals.csv.gz").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(ValueError, match="Changed authenticated"):
        study.evaluate(folder)


def test_second_preparation_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "source_pins", lambda: {})
    fake = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "yoyo.evaluation.spike_burst_progressive", fake)
    (tmp_path / "existing.json").write_text("{}")
    with pytest.raises(ValueError, match="overwrite"):
        study.prepare(tmp_path)


def _synthetic_prepared(tmp_path, monkeypatch):
    f = positive_frame()
    f["history_count"] = 400
    f.attrs.update(minutes=60, period_seconds=3600)
    source = tmp_path / "synthetic.pkl.gz"
    f.to_pickle(source)
    job = dict(instrument="okx:SYN:segment0", asset="SYN", symbol="SYN", venue="okx", minutes=60,
        tick=".01", features_path=str(source), features_sha256=study.sha(source))
    coverage = pd.DataFrame([dict(venue="okx", symbol="SYN", minutes=60)])
    monkeypatch.setattr(study, "source_pins", lambda: {"synthetic": "fixed"})
    monkeypatch.setattr(study, "load_prior", lambda prior: ([job], coverage, {str(source): study.sha(source)}))
    monkeypatch.setattr(study.v1, "replay", lambda frame, tick: state(frame, [13], range(13, 20)))
    fake = SimpleNamespace(progressive_fields=lambda frame: pd.DataFrame(index=frame.index),
        replay=lambda frame, tick: state(frame, [12], range(12, 20)))
    monkeypatch.setitem(sys.modules, "yoyo.evaluation.spike_burst_progressive", fake)
    import yoyo.evaluation
    monkeypatch.setattr(yoyo.evaluation, "spike_burst_progressive", fake, raising=False)
    folder = tmp_path / "results"
    study.prepare(folder, tmp_path)
    return folder, source


def test_synthetic_prepared_evaluate_uses_frozen_simulator_and_no_accounts(tmp_path, monkeypatch):
    folder, _ = _synthetic_prepared(tmp_path, monkeypatch)
    original = study.simulate_trade
    calls = []

    def spy(*args, **kwargs):
        assert (folder / "prepared_manifest.json").exists()
        calls.append(args[1])
        return original(*args, **kwargs)

    monkeypatch.setattr(study, "simulate_trade", spy)
    receipt = study.evaluate(folder)
    assert receipt["status"] == "complete" and receipt["primary_tests"] == 2
    assert sorted(calls) == [12, 13]
    assert not (folder / "accounts").exists()
    summary = pd.read_csv(folder / "trade_summary.csv")
    assert summary.loc[summary.period.eq("full"), "candidates"].tolist() == [1, 1]
    assert summary.loc[summary.period.eq("full"), "matched"].tolist() == [0, 0]
    assert summary.loc[summary.period.eq("full"), "holm_p"].isna().all()
    with pytest.raises(ValueError, match="second scoring"):
        study.evaluate(folder)


def test_source_change_during_evaluation_cannot_produce_complete_receipt(tmp_path, monkeypatch):
    folder, source = _synthetic_prepared(tmp_path, monkeypatch)
    original = study.simulate_trade

    def tamper(*args, **kwargs):
        value = original(*args, **kwargs)
        with source.open("ab") as handle:
            handle.write(b"changed while scoring")
        return value

    monkeypatch.setattr(study, "simulate_trade", tamper)
    with pytest.raises(ValueError, match="Changed authenticated"):
        study.evaluate(folder)
    assert not (folder / "validation_manifest.json").exists()


def test_prior_matching_and_features_must_both_be_manifest_authenticated(tmp_path, monkeypatch):
    source = tmp_path / "source.pkl.gz"
    positive_frame().to_pickle(source)
    job = dict(instrument="okx:SYN:segment0", asset="SYN", symbol="SYN", venue="okx", minutes=60,
        tick=".01", features_path=str(source), features_sha256=study.sha(source))
    pins = {"yoyo/evaluation/" + name: study.sha(study.ROOT / "yoyo/evaluation" / name)
            for name in ("spike_burst_replay.py", "spike_burst_execution.py")}
    matching = tmp_path / "matching.json"
    study.write_json(matching, dict(source_hashes=pins, jobs=[job]))
    coverage = tmp_path / "coverage.csv"
    pd.DataFrame([dict(venue="okx", symbol="SYN", minutes=60)]).to_csv(coverage, index=False)
    study.write_json(tmp_path / "dataset_manifest.json", dict(status="complete", source_hashes=pins,
        artifacts=[study.artifact(matching), study.artifact(coverage)], feature_sources=[study.artifact(source)]))
    monkeypatch.setattr(study, "PRIOR_MANIFEST_SHA", study.sha(tmp_path / "dataset_manifest.json"))
    monkeypatch.setattr(study, "PRIOR_MATCHING_SHA", study.sha(matching))
    assert len(study.load_prior(tmp_path)[0]) == 1
    with source.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="Changed authenticated"):
        study.load_prior(tmp_path)


def test_prepared_source_pins_must_match_evaluation(tmp_path, monkeypatch):
    folder, _ = _synthetic_prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(study, "source_pins", lambda: {"synthetic": "changed"})
    with pytest.raises(ValueError, match="Prepared plan/source mismatch"):
        study.evaluate(folder)


def test_actual_progressive_interface_on_synthetic_features(tmp_path, monkeypatch):
    from yoyo.evaluation import spike_burst_progressive
    f = frame(500, "2026-07-01T00:00Z")
    i = np.arange(len(f))
    f["close"] = 100. + np.sin(i / 7)
    f["open"], f["high"], f["low"] = f.close - .1, f.close + .5, f.close - .5
    base = study.v1.features(f[["open", "high", "low", "close", "volume", "quote_volume"]])
    base["history_count"], base["atr_pct"] = i, base.atr / base.close
    old = study.v1.replay(base, .01)
    base["replay_burst"] = old.burst
    enhanced = pd.concat([base, spike_burst_progressive.progressive_fields(base)], axis=1)
    actual = spike_burst_progressive.replay(enhanced, .01)
    assert actual.index.equals(base.index)
    source = tmp_path / "synthetic.pkl.gz"
    base.to_pickle(source)
    job = dict(instrument="okx:SYN:segment0", asset="SYN", symbol="SYN", venue="okx", minutes=60,
        tick=".01", features_path=str(source), features_sha256=study.sha(source))
    coverage = pd.DataFrame([dict(venue="okx", symbol="SYN", minutes=60)])
    monkeypatch.setattr(study, "source_pins", lambda: {"synthetic": "fixed"})
    monkeypatch.setattr(study, "load_prior", lambda prior: ([job], coverage, {str(source): study.sha(source)}))
    result = study.prepare(tmp_path / "results", tmp_path)
    assert result["status"] == "complete" and result["segments"] == 1
