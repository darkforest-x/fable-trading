"""Causality, gap-reset, matching and diagnostic-boundary dataset tests."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import altseason_dataset as ds


def bars(index):
    n = len(index)
    frame = pd.DataFrame({"open": np.full(n, 100.0), "high": np.full(n, 102.0), "low": np.full(n, 98.0),
                          "close": np.full(n, 101.0), "volume": np.full(n, 10.0), "quote_volume": np.full(n, 1000.0)}, index=index)
    frame["ts"] = index.asi8 // 1000000
    return frame


def test_hour_gap_splits_without_filling_and_conflicting_price_rejected():
    index = pd.date_range("2026-07-01", periods=6, freq="h", tz="UTC")
    frame = bars(index).drop(index[3])
    segments, coverage = ds.split_hourly(frame)
    assert [len(x) for x in segments] == [3, 2]
    assert coverage["gap_count"] == 1 and coverage["missing_hours_between_rows"] == 1
    changed = frame.iloc[:1].copy()
    changed["close"] = 100
    with pytest.raises(ValueError, match="Conflicting"):
        ds.split_hourly(pd.concat([frame, changed]))


def test_duplicate_identical_is_counted_and_unconfirmed_fails():
    index = pd.date_range("2026-07-01", periods=3, freq="h", tz="UTC")
    frame = bars(index)
    _, coverage = ds.split_hourly(pd.concat([frame, frame.iloc[:1]]))
    assert coverage["duplicate_rows_removed"] == 1
    frame["confirmed"] = [1, 0, 1]
    with pytest.raises(ValueError, match="Unconfirmed"):
        ds.split_hourly(frame)


def test_higher_timeframe_is_available_at_close_not_at_open():
    one = pd.DataFrame(index=pd.date_range("2026-08-01", periods=8, freq="h", tz="UTC"))
    four = pd.DataFrame({"md": [1.0, 3.0], "sb": [2.0, 2.0], "ready": [True, True]}, index=pd.date_range("2026-08-01", periods=2, freq="4h", tz="UTC"))
    joined = ds.higher_permission(one, four)
    assert joined.iloc[:3].isna().all()
    assert joined.iloc[3:7].eq(0).all()
    assert joined.iloc[7] == 1
    four["ready"] = False
    assert ds.higher_permission(one, four).isna().all()


def test_calendar_requires_complete_utc_windows_and_separates_labels(tmp_path):
    index = pd.date_range("2026-07-10", periods=11 * 24, freq="h", tz="UTC")
    f = bars(index)
    f["sma60"], f["md"], f["ready"] = 99, 1, True
    f.loc[index[10], "high"] = 200
    rows, contexts = ds.calendar_artifacts(f, dict(venue="test", symbol="A", asset="A", instrument="t"), tmp_path / "60", tmp_path / "240")
    days = [x for x in rows if x["kind"] == "day"]
    weeks = [x for x in rows if x["kind"] == "week"]
    assert len(days) == 11 and len(weeks) == 1
    assert weeks[0]["window_start"] == pd.Timestamp("2026-07-13T00Z")
    assert days[0]["peak_return"] == 1 and days[0]["peak_time"] == index[10]
    assert days[0]["close_return"] == pytest.approx(.01)
    assert contexts[0]["time"] == pd.Timestamp("2026-07-11T00Z")
    assert contexts[0]["quote_volume_24h"] == 24000
    shortened, _ = ds.calendar_artifacts(f.iloc[1:], {}, tmp_path / "60", tmp_path / "240")
    assert not any(x["kind"] == "day" and x["window_start"] == index[0] for x in shortened)


def test_matching_excludes_candidates_and_shares_across_exits():
    index = pd.date_range("2026-07-01", periods=1200, freq="h", tz="UTC")
    f = pd.DataFrame({"atr_pct": np.ones(len(index)), "history_count": np.arange(len(index)) + 1}, index=index)
    candidates = pd.DataFrame({"decision_i": [600, 600, 670], "arm": ["focus_md", "focus_sma60", "dense_sma60"]})
    matching = ds.match_controls(f, candidates, candidates, "gate:ABC:segment0", 60)
    again = ds.match_controls(f, candidates, candidates.assign(net_return=[999, -999, 0]), "gate:ABC:segment0", 60)
    assert matching == again and len(matching) == 2
    for i, chosen in matching.items():
        assert len(chosen) == 3 and len(set(chosen)) == 3
        assert all(abs(j - 600) > 12 and abs(j - 670) > 12 for j in chosen)
        assert all(f.history_count.iloc[j] >= 340 for j in chosen)
        assert all(ds._week(pd.DatetimeIndex([index[j] + pd.Timedelta(hours=1)]))[0] == ds._week(pd.DatetimeIndex([index[i] + pd.Timedelta(hours=1)]))[0] for j in chosen)


def test_process_gap_segments_do_not_create_new_young_listing(tmp_path, monkeypatch):
    first = pd.date_range("2026-07-15", periods=360, freq="h", tz="UTC")
    second = pd.date_range(first[-1] + pd.Timedelta(hours=2), periods=100, freq="h", tz="UTC")
    source = tmp_path / "bars.csv.gz"
    bars(first.append(second)).to_csv(source, index=False)
    # This fixture isolates source-origin eligibility; actual engine build still
    # validates/warms every history independently. Synthetic candidates require
    # the dataset to reject the otherwise eligible second-segment young event.
    original = ds.engine.candidate_events

    def forced(features, minutes, *args, **kwargs):
        found = original(features, minutes, *args, **kwargs)
        if minutes == 60 and len(features) > 65:
            i = 65
            row = ds._feature_context(features, i, minutes)
            row.update(arm="young_breakout_sma20", exit_rule="ratchet_sma20")
            return pd.concat([found, pd.DataFrame([row])], ignore_index=True) if len(found) else pd.DataFrame([row])
        return found

    monkeypatch.setattr(ds.engine, "candidate_events", forced)
    result = ds.process_market(source, dict(venue="gate", symbol="ABC_USDT", base_symbol="ABC", eligible=True), tmp_path / "out")
    assert not result["errors"]
    assert [x["young_source_allowed"] for x in result["segments"]] == [True, False]
    events = pd.read_csv(result["events_path"])
    assert len(events) == 1 and events.instrument.iloc[0].endswith("segment0")
    assert result["segments"][1]["timeframes"]["60"]["young_evaluable_bars"] == 0


def test_bad_source_creates_explicit_error_artifact(tmp_path):
    source = tmp_path / "bad.csv"
    pd.DataFrame({"ts": [1]}).to_csv(source, index=False)
    result = ds.process_market(source, dict(venue="gate", symbol="BAD_USDT", base_symbol="BAD", eligible=True), tmp_path / "bad_out")
    assert result["errors"] and result["candidates"] == 0
    assert pd.read_csv(result["events_path"]).empty


def test_missing_source_is_an_explicit_market_error(tmp_path):
    result = ds.process_market(tmp_path / "absent.csv", dict(venue="gate", symbol="BAD_USDT", base_symbol="BAD", eligible=True), tmp_path / "absent_out")
    assert result["errors"][0]["message"] == "Normalized input file is missing"
