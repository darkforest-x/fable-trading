"""A report must never concatenate unvalidated foreign streams into a complete run."""
import hashlib
import json

import pytest
import pandas as pd

from yoyo.evaluation.spike_v112_support_report import validated_tables, validate_control_keys


def test_report_rejects_extra_stream_before_loading(tmp_path):
    identity = {"inputs": {"BTCUSDT": "input-sha"}}
    (tmp_path / "identity.json").write_text(json.dumps(identity))
    (tmp_path / "streams" / "BTCUSDT").mkdir(parents=True)
    (tmp_path / "streams" / "stale_extra").mkdir()
    manifest = {"symbols": 1, "run_identity": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    with pytest.raises(ValueError, match="stream inventory mismatch"):
        validated_tables(tmp_path, manifest)


def test_report_rejects_manifest_identity_mismatch(tmp_path):
    (tmp_path / "identity.json").write_text(json.dumps({"inputs": {}}))
    with pytest.raises(ValueError, match="identity hash"):
        validated_tables(tmp_path, {"run_identity": "foreign"})


def test_report_rejects_missing_control_on_treatment_only_trade():
    trades = pd.DataFrame({"arm": ["box_any", "box_support"], "trade_key": ["old", "freed"]})
    with pytest.raises(ValueError, match="coverage mismatch"):
        validate_control_keys(trades, trades.iloc[:1])
    validate_control_keys(trades, trades.copy())
