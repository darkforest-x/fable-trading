"""Synthetic-only lineage, causal matching, clocks and global freeze checks."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_dataset as ds


def feature_frame(minutes=60, n=1800, start="2026-06-15"):
    index = pd.date_range(start, periods=n, freq=pd.Timedelta(minutes=minutes), tz="UTC")
    frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=10.,
        quote_volume=1000., ready=np.arange(n) >= 340, release_side=0,
        near_zero_bars=12., history_count=np.arange(n) + 1, atr_pct=.02,
        prior24h_quote_volume=1000. * (1440 // minutes)), index=index)
    frame.index.name = "open_time"
    return frame


def make_prior(tmp_path, exclude=""):
    old = tmp_path / "old"
    folder = old / "results/markets/okx/SYN-USDT-SWAP"
    folder.mkdir(parents=True)
    market = dict(venue="okx", symbol="SYN-USDT-SWAP", raw={"tickSz": "0.01"})
    catalog = old / "data/catalog/okx.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(dict(markets=[market])))
    feature_paths = []
    for minutes in ds.MINUTES:
        f = feature_frame(minutes)
        f.loc[f.index[700 if minutes == 60 else 350], "release_side"] = 1
        path = folder / ("segment0_%d_features.pkl.gz" % minutes)
        f.to_pickle(path, compression={"method": "gzip", "mtime": 0})
        feature_paths.append(path)
    source = old / "source.csv"
    source.write_text("synthetic_source_only\n")
    coverage = dict(venue="okx", symbol="SYN-USDT-SWAP", asset="SYN",
        identity=dict(source_path=str(source), source_sha256=ds.sha256(source), market_hash=ds.market_hash(market)),
        artifacts=[ds.artifact(p) for p in feature_paths], errors=[], exclude_reason=exclude,
        segments=[dict(segment=0, instrument="okx:SYN-USDT-SWAP:segment0", young_source_allowed=False)])
    coverage_path = folder / "coverage.json"
    coverage_path.write_text(json.dumps(coverage))
    aggregate = old / "results/coverage.csv"
    pd.DataFrame([coverage]).to_csv(aggregate, index=False)
    stubs = []
    for name in ("events.csv.gz", "controls.csv.gz"):
        path = old / "results" / name
        pd.DataFrame({"event_id": []}).to_csv(path, index=False)
        stubs.append(path)
    manifest = dict(artifacts=[ds.artifact(p) for p in [aggregate] + stubs])
    (old / "RESULTS_MANIFEST.json").write_text(json.dumps(manifest))
    return old, coverage, feature_paths, catalog


@pytest.mark.parametrize("market,expected", [
    ({"venue": "okx", "raw": {"tickSz": "0.0001"}}, "0.0001"),
    ({"venue": "binance", "raw": {"filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.05"}]}}, "0.05"),
    ({"venue": "gate", "raw": {"order_price_round": "0.001"}}, "0.001"),
])
def test_native_ticks(market, expected):
    assert ds.market_tick(market)[0] == expected


@pytest.mark.parametrize("value", [None, "", "0", "-1", "NaN", "Infinity", "1e-9999", True])
def test_invalid_native_ticks_do_not_get_price_derived_fallback(value):
    with pytest.raises(ValueError):
        ds.market_tick({"venue": "okx", "raw": {"tickSz": value}})


def test_duplicate_binance_filter_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        ds.market_tick({"venue": "binance", "raw": {"filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "1"}] * 2}})


def test_coverage_and_both_timeframes_are_verified(tmp_path):
    old, _, paths, _ = make_prior(tmp_path)
    verified = ds.verify_inputs(old)
    assert [s["minutes"] for s in verified["segments"]] == [60, 240]
    assert {s["source_features_path"] for s in verified["segments"]} == {str(p) for p in paths}
    assert verified["coverages"][0]["tick"] == "0.01"


def test_4h_pickle_bytes_checked_before_any_deserialization(tmp_path, monkeypatch):
    old, _, paths, _ = make_prior(tmp_path)
    paths[1].write_bytes(b"tampered pickle")
    monkeypatch.setattr(pd, "read_pickle", lambda *a, **k: pytest.fail("Unverified pickle read"))
    with pytest.raises(ValueError, match="SHA mismatch"):
        ds.verify_inputs(old)


def test_catalog_tick_mutation_rejected_even_when_structure_valid(tmp_path):
    old, _, _, catalog = make_prior(tmp_path)
    data = json.loads(catalog.read_text())
    data["markets"][0]["raw"]["tickSz"] = "1"
    catalog.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="market hash mismatch"):
        ds.verify_inputs(old)


def test_missing_4h_not_silently_dropped(tmp_path):
    old, coverage, _, _ = make_prior(tmp_path)
    coverage["artifacts"] = coverage["artifacts"][:1]
    (old / "results/markets/okx/SYN-USDT-SWAP/coverage.json").write_text(json.dumps(coverage))
    aggregate = old / "results/coverage.csv"
    pd.DataFrame([coverage]).to_csv(aggregate, index=False)
    manifest = json.loads((old / "RESULTS_MANIFEST.json").read_text())
    manifest["artifacts"][0] = ds.artifact(aggregate)
    (old / "RESULTS_MANIFEST.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="absent from frozen aggregate"):
        ds.verify_inputs(old)


@pytest.mark.parametrize("minutes", [60, 240])
def test_matching_same_week_bucket_known_past_exclusion_deterministic(minutes):
    frame = feature_frame(minutes)
    frame = frame.loc[frame.index + pd.Timedelta(minutes=minutes) <= ds.END]
    signal = 700 if minutes == 60 else 350
    all_indices = [signal, signal + 18]
    matches = ds.match_controls(frame, all_indices, [signal], "synthetic", minutes)
    assert matches == ds.match_controls(frame.assign(net_bp=1e12), all_indices, [signal], "synthetic", minutes)
    assert len(matches[signal]) == 3
    closes = frame.index + pd.Timedelta(minutes=minutes)
    for j in matches[signal]:
        assert frame.history_count.iloc[j] >= 340
        assert j + 1 < len(frame)
        assert all(not k <= j <= k + 12 for k in all_indices)
        assert ds._week(closes[[j]])[0] == ds._week(closes[[signal]])[0]


def test_control_before_future_signal_is_eligible():
    frame = feature_frame(n=730)
    # Make only bar699 eligible. Signal700 has not happened at699 close.
    frame["history_count"] = 1
    frame.loc[frame.index[699], "history_count"] = 340
    matches = ds.match_controls(frame, [700], [700], "synthetic", 60)
    assert matches[700] == [699]
    # A past signal698 is already known and now correctly excludes699.
    assert ds.match_controls(frame, [698, 700], [700], "synthetic", 60)[700] == []


def test_matching_future_tail_rejected():
    with pytest.raises(ValueError, match="beyond cutoff"):
        ds.match_controls(feature_frame(240), [350], [350], "x", 240)


@pytest.mark.parametrize("minutes", [60, 240])
def test_feature_clock_gap_and_cutoff(minutes, tmp_path):
    frame = feature_frame(minutes)
    path = tmp_path / "f.pkl.gz"
    frame.to_pickle(path)
    loaded = ds.load_feature(path, ds.sha256(path), minutes)
    assert loaded.index[-1] + pd.Timedelta(minutes=minutes) <= ds.END
    assert loaded.index[0] == frame.index[0]
    frame.drop(frame.index[400]).to_pickle(path)
    with pytest.raises(ValueError, match="continuous"):
        ds.load_feature(path, ds.sha256(path), minutes)


def test_no_score_without_frozen_global_matching(tmp_path):
    with pytest.raises(FileNotFoundError):
        ds.score_jobs([], tmp_path)


def test_build_freezes_all_markets_before_simulation_and_preserves_clocks(tmp_path, monkeypatch):
    old, _, _, _ = make_prior(tmp_path)
    output = tmp_path / "new_results"
    monkeypatch.setattr(ds, "_verify_committed", lambda: {"synthetic_fixture": "fixed"})
    replay = ds.replay_engine.replay
    def fake_burst(frame, tick):
        result = replay(frame, tick)
        i = 700 if frame.attrs["minutes"] == 60 else 350
        result.iloc[i, result.columns.get_loc("burst")] = True
        result.iloc[i, result.columns.get_loc("route")] = "price_first"
        return result
    monkeypatch.setattr(ds.replay_engine, "replay", fake_burst)
    execution = ds.simulate_trade
    calls = []
    def watched_execution(frame, i, tick, end):
        frozen = json.loads((output / "matching.json").read_text())
        assert len(frozen["jobs"]) == 2
        assert frozen["candidate_rows"] == 4
        assert (output / "candidate_schedule.csv.gz").is_file()
        assert (output / "control_schedule.csv.gz").is_file()
        assert all(Path(job["features_path"]).is_file() for job in frozen["jobs"])
        calls.append((frame.attrs["minutes"], i))
        return execution(frame, i, tick, end)
    monkeypatch.setattr(ds, "simulate_trade", watched_execution)
    manifest = ds.build_dataset(old, output)
    assert manifest["status"] == "complete" and manifest["prepared_jobs"] == 2
    assert manifest["event_count"] == 4
    assert manifest["control_reuse"]["maximum_control_reuse"] == 2
    assert len(calls) == len(set(calls))  # Same decision across arms uses one execution.
    events = pd.read_csv(output / "events.csv.gz")
    controls = pd.read_csv(output / "controls.csv.gz")
    assert set(events.arm) == set(ds.ARMS)
    assert events.tick.eq(.01).all()
    assert events.exit_rule.eq("burst_trail").all()
    for row in events.itertuples():
        frame = pd.read_pickle(row.features_path)
        decision = frame.index[row.decision_i] + pd.Timedelta(minutes=row.minutes)
        assert pd.Timestamp(row.decision_time) == decision
        assert pd.Timestamp(row.entry_time) == decision
    assert controls.matched_event_id.isin(events.event_id).all()
    for item in manifest["artifacts"]:
        assert ds.sha256(item["path"]) == item["sha256"]
    with pytest.raises(ValueError, match="overwrite"):
        ds.build_dataset(old, output)


def test_excluded_universe_stays_in_coverage_and_writes_empty_schema(tmp_path, monkeypatch):
    old, _, _, _ = make_prior(tmp_path, "background_btc_eth")
    monkeypatch.setattr(ds, "_verify_committed", lambda: {})
    monkeypatch.setattr(ds, "simulate_trade", lambda *a, **k: pytest.fail("Excluded outcome simulated"))
    result = ds.build_dataset(old, tmp_path / "output")
    assert result["event_count"] == result["prepared_jobs"] == 0
    assert result["coverage_status_counts"] == {"excluded": 2}
    assert set(ds.EVENT_COLUMNS).issubset(pd.read_csv(tmp_path / "output/events.csv.gz").columns)


def test_output_cannot_overwrite_prior(tmp_path):
    old, _, _, _ = make_prior(tmp_path)
    with pytest.raises(ValueError, match="previous experiment"):
        ds.build_dataset(old, old / "new")
