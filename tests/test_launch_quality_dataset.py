"""Synthetic provenance, frozen matching, temporal isolation and reuse tests."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import altseason_dataset as old_ds
from yoyo.evaluation import launch_quality_dataset as ds


def features(n=1800, start="2026-05-01"):
    index = pd.date_range(start, periods=n, freq="h", tz="UTC")
    bars = pd.DataFrame(dict(open=100., high=101., low=99., close=100.,
                             volume=10., quote_volume=1000.), index=index)
    f = ds.engine.build_features(bars)[60]
    f["atr"], f["atr_pct"], f["md"], f["higher_permission"] = 5., .05, 1., 0.
    f["release_side"] = 0
    f.loc[:, ["sma20", "ema20", "sma60", "sma120"]] = 100.
    for name in ("first12_breakout", "first20_breakout", "dense_recent", "above_all6"):
        f[name] = False
    return f


def make_old(tmp_path, exclude=""):
    old = tmp_path / "old"
    market_dir = old / "results/markets/okx/SYN-USDT-SWAP"
    market_dir.mkdir(parents=True)
    f = features()
    f.loc[f.index[360], "release_side"] = 1
    f.loc[f.index[700], "release_side"] = 1
    path = market_dir / "segment0_60_features.pkl.gz"
    f.to_pickle(path, compression={"method": "gzip", "mtime": 0})
    source = old / "source.csv"
    pd.DataFrame({"ts": f.index.asi8 // 1000000, "close": f.close.to_numpy()}).to_csv(source, index=False)
    identity = dict(source_path=str(source), source_sha256=ds.sha256(source))
    cov = dict(venue="okx", symbol="SYN-USDT-SWAP", asset="SYN", identity=identity,
               artifacts=[ds.artifact(path)], errors=[], exclude_reason=exclude,
               segments=[dict(segment=0, instrument="okx:SYN-USDT-SWAP:segment0", young_source_allowed=False)])
    (market_dir / "coverage.json").write_text(json.dumps(cov))
    coverage_path = old / "results/coverage.csv"
    pd.DataFrame([cov]).to_csv(coverage_path, index=False)
    i = int(f.index.get_loc(pd.Timestamp("2026-07-10T12:00Z")))
    row = dict(event_id="known1", venue="okx", symbol="SYN-USDT-SWAP", asset="SYN",
               instrument=cov["segments"][0]["instrument"], features_path=str(path), minutes=60,
               arm="focus_md", exit_rule="md", period="first31", decision_i=i,
               decision_time=f.index[i]+pd.Timedelta(hours=1), valid=True,
               net_bp=123.45678912345678, arbitrary_original_field="retained", signal_quote_volume=1000.)
    ev = pd.DataFrame([] if exclude else [row], columns=row)
    control = dict(row, event_id="control1", matched_event_id="known1", control_number=0)
    ct = pd.DataFrame([] if exclude else [control], columns=control)
    ev_path, ct_path = old / "results/events.csv.gz", old / "results/controls.csv.gz"
    ev.to_csv(ev_path, index=False)
    ct.to_csv(ct_path, index=False)
    manifest = dict(artifacts=[ds.artifact(p) for p in (coverage_path, ev_path, ct_path)],
                    evaluation=dict(source_hashes={"yoyo/evaluation/altseason_engine.py": ds.sha256(ds.engine.__file__)}))
    (old / "RESULTS_MANIFEST.json").write_text(json.dumps(manifest))
    return old, f, cov, path


def test_matching_same_week_bucket_exclusion_and_no_original_constant_mutation():
    f = features(1200)
    candidates = pd.DataFrame(dict(decision_i=[360, 360, 410, 700],
                                  arm=["focus_md", "focus_3r", "dense_sma60", "pullback_sma60"]))
    selected = candidates.iloc[:1]
    start, end = old_ds.EVAL_START, old_ds.EVAL_END
    m = ds.match_controls(f, candidates, selected, "x")
    assert m == ds.match_controls(f, candidates.assign(net_bp=1e12), selected, "x")
    assert len(m[360]) == 3 and len(set(m[360])) == 3
    for j in m[360]:
        assert all(abs(j-k)>12 for k in [360, 410, 700])
        assert f.history_count.iloc[j] >= 340
        assert ds._week(f.index[[j]]+pd.Timedelta(hours=1))[0] == ds._week(f.index[[360]]+pd.Timedelta(hours=1))[0]
    assert (old_ds.EVAL_START, old_ds.EVAL_END) == (start, end)


def test_matching_rejects_future_feature_tail():
    f = features()
    cand = pd.DataFrame(dict(decision_i=[360]))
    with pytest.raises(ValueError, match="beyond"):
        ds.match_controls(f, cand, cand, "x")


def test_coverage_source_bytes_and_authenticated_metadata(tmp_path):
    old, _, cov, _ = make_old(tmp_path)
    verified = ds.load_verified_coverage(old)
    assert len(verified["coverages"]) == len(verified["feature_index"]) == 1
    Path(cov["identity"]["source_path"]).write_text("changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        ds.load_verified_coverage(old)


def test_coverage_cannot_replace_source_hash_along_with_file(tmp_path):
    old, _, cov, _ = make_old(tmp_path)
    p = old / "results/markets/okx/SYN-USDT-SWAP/coverage.json"
    cov["exclude_reason"] = "background_btc_eth"
    p.write_text(json.dumps(cov))
    with pytest.raises(ValueError, match="exclusion"):
        ds.load_verified_coverage(old)


def test_feature_hash_verified_before_pickle_deserialization(tmp_path, monkeypatch):
    old, _, _, path = make_old(tmp_path)
    path.write_bytes(b"invalid and untrusted pickle")
    def prohibited(*args, **kwargs):
        raise AssertionError("Unverified pickle was read")
    monkeypatch.setattr(pd, "read_pickle", prohibited)
    with pytest.raises(ValueError, match="SHA mismatch"):
        ds.load_verified_coverage(old)


def test_known_fields_are_retained_and_all_files_are_sha_checked(tmp_path):
    old, _, _, _ = make_old(tmp_path)
    verified = ds.load_verified_coverage(old)
    events, controls = ds.read_known(verified)
    original = pd.read_csv(old / "results/events.csv.gz", float_precision="round_trip")
    pd.testing.assert_frame_equal(events.loc[:, original.columns].reset_index(drop=True), original)
    assert events.research_period.eq("known").all()
    assert controls.matched_event_id.tolist() == ["known1"]
    (old / "results/events.csv.gz").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA mismatch"):
        ds.read_known(verified)


def test_earlier_simulation_cannot_exit_on_known_period_prices(tmp_path):
    _, f, cov, path = make_old(tmp_path)
    i = int(f.index.get_loc(pd.Timestamp("2026-07-09T20:00Z")))
    f.loc[f.index >= ds.EARLIER_END, ["open", "high", "low", "close"]] = [2., 3., 1., 2.]
    f.to_pickle(path)
    job = dict(venue="okx", symbol="SYN-USDT-SWAP", asset="SYN",
               instrument=cov["segments"][0]["instrument"], features_path=str(path),
               features_sha256=ds.sha256(path), last_i=i+3,
               last_open_time="2026-07-09T23:00:00+00:00", decisions=[i], matching={str(i): []})
    ev, ct = ds.simulate_earlier_jobs([job])
    assert len(ev) == 1 and ct.empty
    assert ev.censored.iloc[0] and not ev.natural_exit.iloc[0]
    assert ev.exit_price.iloc[0] == 100.
    assert ev.exit_time.iloc[0] == ds.EARLIER_END
    assert ev.exit_i.iloc[0] == i+3
    assert ev.net_bp.iloc[0] == pytest.approx(-20.)


def test_excluded_market_remains_in_coverage_but_creates_no_events(tmp_path):
    old, _, _, _ = make_old(tmp_path, exclude="stable_asset")
    verified = ds.load_verified_coverage(old)
    out = tmp_path / "out"
    out.mkdir()
    jobs, rows, matching = ds.freeze_earlier_matching(verified, out)
    assert not jobs and rows[0]["exclude_reason"] == "stable_asset"
    assert rows[0]["earlier_evaluable_bars"] > 0
    assert rows[0]["earlier_candidates"] == 0
    assert json.loads(matching.read_text())["returns_accessed"] is False


def test_full_build_freezes_all_matching_first_and_writes_verifiable_outputs(tmp_path, monkeypatch):
    old, _, _, _ = make_old(tmp_path)
    out = tmp_path / "new_results"
    original = ds.engine.simulate_prepared
    calls = []
    def check_matching(*args, **kwargs):
        frozen = json.loads((out / "earlier_matching.json").read_text())
        assert frozen["outcomes_simulated"] is False
        assert len(frozen["jobs"]) == 1
        assert frozen["jobs"][0]["last_open_time"] == "2026-07-09T23:00:00+00:00"
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(ds.engine, "simulate_prepared", check_matching)
    result = ds.build_dataset(old, out)
    assert calls and result["earlier_events"] == 2 and result["known_events"] == 1
    for row in result["artifacts"]:
        assert ds.sha256(row["path"]) == row["sha256"]
        assert Path(row["path"]).stat().st_size == row["size_bytes"]
    coverage = pd.read_csv(out / "coverage.csv")
    assert coverage.known_candidates.tolist() == [1]
    assert coverage.earlier_candidates.tolist() == [2]
    controls = pd.read_csv(out / "earlier_controls.csv.gz")
    events = pd.read_csv(out / "earlier_events.csv.gz")
    assert set(controls.matched_event_id).issubset(set(events.event_id))
    assert all(events.arm.eq("focus_md"))
    assert all(events.features_path.eq(str(old / "results/markets/okx/SYN-USDT-SWAP/segment0_60_features.pkl.gz")))


def test_old_experiment_is_read_only_even_with_mistyped_output(tmp_path):
    old = tmp_path / "old"
    with pytest.raises(ValueError, match="overwrite"):
        ds.build_dataset(old, old / "results")
    assert not old.exists()
