"""Synthetic I/O boundaries for V21: never open a real price/outcome file."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_breadth_research as r


def fixture_source(tmp_path):
    symbol = "ETHUSDT"
    path = tmp_path/r.ARCHIVE/"series"/"synthetic.csv"
    audit_path = tmp_path/r.ARCHIVE/"audits"/(symbol+".json")
    path.parent.mkdir(parents=True)
    times = ["2022-12-28T23:55:00+00:00", "2022-12-29T00:00:00+00:00",
        "2022-12-29T00:05:00+00:00", "2023-01-01T00:00:00+00:00",
        "2025-01-01T00:00:00+00:00"]
    frame = pd.DataFrame({"open_time": times, "open": ["excluded", 100, 100, "excluded", "DO_NOT_PARSE"],
        "high": 102, "low": 99, "close": 101, "volume": 10})
    frame.to_csv(path, index=False)
    audit = {"holdout_ohlcv_rows_materialized": 0, "status": "complete", "symbol": symbol,
        "output_sha256": r.digest(path), "rows": len(frame), "first_time": times[0], "last_time": times[-1]}
    r.write_json(audit_path, audit)
    spec = {"file": "synthetic.csv", "rows": len(frame), "sha256": r.digest(path),
        "audit_sha256": r.digest(audit_path)}
    return symbol, spec, path, audit_path


def test_read_timestamp_first_then_only_explicit_bounded_rows(tmp_path, monkeypatch):
    symbol, spec, path, _ = fixture_source(tmp_path)
    real, calls = pd.read_csv, []
    def spy(p, **kw):
        calls.append((p, kw))
        return real(p, **kw)
    monkeypatch.setattr(r.pd, "read_csv", spy)
    raw, receipt = r.load_external(symbol, spec, "2022-12-29T00:10:00Z", root=tmp_path)
    assert len(raw) == 2
    assert raw.open.astype(float).eq(100).all()
    assert receipt["price_rows_2025_plus_materialized"] == 0
    assert receipt["skipped_before_warmup_rows"] == 1
    assert len(calls) == 2
    assert calls[0][1] == {"usecols": ["open_time"]}
    assert calls[1][1]["usecols"] == r.BAR_COLUMNS
    assert list(calls[1][1]["skiprows"]) == [1]
    assert calls[1][1]["nrows"] == 2
    assert calls[0][0] == calls[1][0] == path


@pytest.mark.parametrize("end", ["2022-12-28", "2022-12-29", "2025-01-02", "2026-05-04"])
def test_bad_phase_refuses_before_read(end, tmp_path, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("Forbidden file access")
    monkeypatch.setattr(r, "digest", forbidden)
    with pytest.raises(ValueError, match="bound"):
        r.load_external("ETHUSDT", {}, end, root=tmp_path)


@pytest.mark.parametrize("field,value", [("holdout_ohlcv_rows_materialized", 1),
    ("status", "partial"), ("symbol", "BTCUSDT"), ("output_sha256", "wrong"),
    ("rows", 999), ("last_time", "2026-05-04T00:00:00Z")])
def test_receipt_mismatch_prevents_price_read(tmp_path, monkeypatch, field, value):
    symbol, spec, _, audit_path = fixture_source(tmp_path)
    audit = json.loads(audit_path.read_text())
    audit[field] = value
    r.write_json(audit_path, audit)
    spec["audit_sha256"] = r.digest(audit_path)
    monkeypatch.setattr(r.pd, "read_csv", lambda *a, **k: pytest.fail("Receipt failed before CSV"))
    with pytest.raises(ValueError, match="receipt"):
        r.load_external(symbol, spec, "2023-01-01", root=tmp_path)


@pytest.mark.parametrize("field", ["audit_sha256", "sha256"])
def test_hash_mismatch_fails_closed(tmp_path, field):
    symbol, spec, _, _ = fixture_source(tmp_path)
    spec[field] = "wrong"
    with pytest.raises(ValueError):
        r.load_external(symbol, spec, "2023-01-01", root=tmp_path)


@pytest.mark.parametrize("mode", ["duplicate", "unsorted", "naive", "numeric", "offgrid", "null"])
def test_timestamp_errors_do_not_read_prices(tmp_path, monkeypatch, mode):
    symbol, spec, path, audit_path = fixture_source(tmp_path)
    frame = pd.read_csv(path)
    if mode == "duplicate": frame.loc[2, "open_time"] = frame.loc[1, "open_time"]
    if mode == "unsorted": frame.loc[[1, 2], "open_time"] = frame.loc[[2, 1], "open_time"].to_numpy()
    if mode == "naive": frame.loc[1, "open_time"] = "2022-12-29 00:00:00"
    if mode == "numeric": frame["open_time"] = range(len(frame))
    if mode == "offgrid": frame.loc[1, "open_time"] = "2022-12-29T00:01:00+00:00"
    if mode == "null": frame.loc[1, "open_time"] = None
    frame.to_csv(path, index=False)
    audit = json.loads(audit_path.read_text())
    spec["sha256"] = audit["output_sha256"] = r.digest(path)
    r.write_json(audit_path, audit)
    spec["audit_sha256"] = r.digest(audit_path)
    real, reads = pd.read_csv, []
    def spy(p, **kw):
        reads.append(kw)
        assert kw == {"usecols": ["open_time"]}
        return real(p, **kw)
    monkeypatch.setattr(r.pd, "read_csv", spy)
    with pytest.raises(ValueError):
        r.load_external(symbol, spec, "2023-01-01", root=tmp_path)
    assert len(reads) == 1


def test_empty_bounded_window_refuses(tmp_path):
    symbol, spec, _, _ = fixture_source(tmp_path)
    monkey = pytest.MonkeyPatch()
    monkey.setattr(r, "WARMUP_START", "2022-12-30")
    try:
        with pytest.raises(ValueError, match="No external prefix"):
            r.load_external(symbol, spec, "2022-12-31", root=tmp_path)
    finally:
        monkey.undo()


def test_fixed_configuration_and_no_second_gate():
    cfg = r.frozen_config()
    base = {"development_folds": r.FOLDS,
        "execution": {"max_hours": 72, "cost_fraction": .002, "stop_first": True}}
    r.verify_config(cfg, base)
    assert list(cfg["external_sources"]) == ["ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    assert cfg["gate"]["rank_length"] == 50
    assert cfg["gate"]["lag_hours_before_entry"] == 1
    assert cfg["gate"]["extra_structure_ma_volume_gate"] is False
    assert cfg["matching_coverage_required"] == .9
    assert cfg["fixed_execution"]["new_intrabar_replays"] == 0
    changed = deepcopy(cfg)
    changed["gate"]["rank_length"] = 40
    with pytest.raises(ValueError, match="Frozen"):
        r.verify_config(changed, base)


@pytest.mark.parametrize("key,value", [("max_hours", 24), ("cost_fraction", 0.), ("stop_first", False)])
def test_execution_changes_refused(key, value):
    base = {"development_folds": r.FOLDS,
        "execution": {"max_hours": 72, "cost_fraction": .002, "stop_first": True}}
    base["execution"][key] = value
    with pytest.raises(ValueError, match="must remain"):
        r.verify_config(r.frozen_config(), base)


@pytest.mark.parametrize("summary", [{"support_pass": False, "support_gates": {}},
    {"support_pass": True, "support_gates": {"minimum_events": False}}])
def test_support_failure_refuses_even_hashing_outcomes(tmp_path, monkeypatch, summary):
    monkeypatch.setattr(r, "digest", lambda *a: pytest.fail("No file hash allowed"))
    with pytest.raises(ValueError, match="support"):
        r.read_outcomes_after_freeze(tmp_path, summary, pd.DataFrame())
    assert list(tmp_path.iterdir()) == []


def test_bad_frozen_population_refuses_outcomes(tmp_path, monkeypatch):
    r.write_json(tmp_path/"context_frozen.json", {"requests": 712, "output_hashes": {}})
    monkeypatch.setattr(r, "read_parent_frame", lambda *a: pytest.fail("No outcome read"))
    with pytest.raises(ValueError, match="713"):
        r.read_outcomes_after_freeze(tmp_path, {"support_pass": True, "support_gates": {"x": True}}, pd.DataFrame(index=range(713)))
    assert not (tmp_path/"outcomes_started.json").exists()


def test_changed_frozen_feature_hash_refuses_outcomes(tmp_path, monkeypatch):
    r.write_json(tmp_path/"context_frozen.json", {"requests": 713, "output_hashes": {"context.csv.gz": "original"}})
    monkeypatch.setattr(r, "digest", lambda *a: "changed")
    monkeypatch.setattr(r, "read_parent_frame", lambda *a: pytest.fail("No outcome read"))
    with pytest.raises(ValueError, match="bytes"):
        r.read_outcomes_after_freeze(tmp_path, {"support_pass": True, "support_gates": {"x": True}}, pd.DataFrame(index=range(713)))
    assert not (tmp_path/"outcomes_started.json").exists()
