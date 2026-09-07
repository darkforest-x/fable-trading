"""Synthetic V31 runner fail-closed tests; no project market input is read."""
import json
from pathlib import Path

import pytest

from yoyo.evaluation import hourly_impulse_volume_wave_research as r


def frozen(tmp_path):
    path = tmp_path / r.EXPERIMENT
    path.mkdir(parents=True)
    (path / "config.json").write_text(json.dumps(r.frozen_config()))
    return path


def test_frozen_definition():
    c = r.frozen_config()
    assert c["population"] == dict(cases=251, controls=744, matched=248, unmatched=3)
    assert c["parameters"]["preK1_lags_hours"] == [1, 2]
    assert c["parameters"]["minimum_history_at_older_lag"] == 100
    assert c["support"]["minimum_events"] == 80
    assert c["outcomes_read_or_computed"] is False
    assert c["phase_end_exclusive"] == "2025-01-01T00:00:00Z"
    assert r.OHLCV == ["open_time", "open", "high", "low", "close", "volume"]


def test_sources_exclude_labels():
    assert all("label" not in p and "outcome" not in p for p in r.SOURCES)
    assert r.PINE.endswith("lfaZVLub.pine")
    assert "yoyo/evaluation/hourly_impulse_volume_wave_audit.py" in r.SOURCES


@pytest.mark.parametrize("section,key,value", [
    ("parameters", "wave_length", 21),
    ("parameters", "preK1_lags_hours", [0, 1]),
    ("parameters", "minimum_history_at_older_lag", 20),
    ("support", "minimum_events", 79),
])
def test_config_drift_fails_before_io(tmp_path, monkeypatch, section, key, value):
    path = frozen(tmp_path)
    config = r.frozen_config()
    config[section][key] = value
    (path / "config.json").write_text(json.dumps(config))
    monkeypatch.setattr(r, "committed_sources", lambda _: pytest.fail("source check after bad config"))
    with pytest.raises(ValueError, match="config changed"):
        r.run(tmp_path)
    assert not (path / "results").exists()


def test_noncommitted_source_fails_before_materialization(tmp_path, monkeypatch):
    path = frozen(tmp_path)
    monkeypatch.setattr(r, "committed_sources", lambda _: (_ for _ in ()).throw(ValueError("Commit source first")))
    monkeypatch.setattr(r.parent, "verify_inputs", lambda _: pytest.fail("inputs read"))
    with pytest.raises(ValueError, match="Commit source first"):
        r.run(tmp_path)
    assert not (path / "results").exists()


def test_input_failure_preserves_receipt(tmp_path, monkeypatch):
    path = frozen(tmp_path)
    monkeypatch.setattr(r, "committed_sources", lambda _: ("fake-commit", []))
    monkeypatch.setattr(r, "verify_inputs", lambda _: (_ for _ in ()).throw(ValueError("Input drift")))
    with pytest.raises(ValueError, match="Input drift"):
        r.run(tmp_path)
    receipt = json.loads((path / "results/failure.json").read_text())
    assert receipt["status"] == "failed_not_evidence"
    assert not (path / "results/support_frozen.json").exists()


def test_existing_results_never_overwritten(tmp_path, monkeypatch):
    path = frozen(tmp_path)
    (path / "results").mkdir()
    monkeypatch.setattr(r, "committed_sources", lambda _: ("fake", []))
    monkeypatch.setattr(r, "verify_inputs", lambda _: pytest.fail("should not read"))
    with pytest.raises(FileExistsError):
        r.run(tmp_path)
