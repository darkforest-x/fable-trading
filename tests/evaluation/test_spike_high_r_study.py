"""Identity and event coverage checks for the High-R research runner."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_high_r_study import completed, digest, tag
from yoyo.evaluation.spike_high_r_report import block_test, periods
from yoyo.evaluation import spike_high_r_study as study


def test_receipt_rejects_modified_outputs(tmp_path):
    p = tmp_path / "trades.csv"
    p.write_text("original")
    r = dict(status="complete", key=tmp_path.name, run_identity="test", files={p.name: digest(p)})
    (tmp_path / "completion.json").write_text(json.dumps(r))
    assert completed(tmp_path, "test") == r
    p.write_text("changed")
    with pytest.raises(ValueError, match="hash changed"):
        completed(tmp_path, "test")


def test_receipt_rejects_foreign_identity(tmp_path):
    (tmp_path / "completion.json").write_text(json.dumps(dict(status="complete", key=tmp_path.name,
        run_identity="old", files={})))
    with pytest.raises(ValueError, match="identity"):
        completed(tmp_path, "new")


def test_tag_uses_signal_identity_not_execution_row_number():
    d = pd.DataFrame(dict(stream_key=["x", "x"], signal_i=[14, 14], side=[1, -1]))
    assert tag(d, "high_r_v1").event_key.tolist() == ["x:14:1", "x:14:-1"]
    with pytest.raises(ValueError, match="duplicate"):
        tag(pd.concat([d, d]), "high_r_v1")


def test_time_split_excludes_unresolved_and_crossing_from_earlier():
    d = pd.DataFrame({"entry_time": pd.to_datetime(["2025-09-09", "2025-09-09", "2025-09-10", "2025-09-09"], utc=True),
                      "exit_time": pd.to_datetime(["2025-09-09", "2025-09-10", "2025-09-11", None], utc=True)})
    p = dict(periods(d))
    assert p["earlier"].index.tolist() == [0]
    assert p["later"].index.tolist() == [2]
    assert p["crossing"].index.tolist() == [1, 3]


def test_month_signflip_known_exact_null():
    assert block_test([1, 1, 1])["p"] == .125
    assert block_test([0, 0, 0])["p"] == 1.
    assert block_test([-1, -1, -1])["p"] == 1.


def test_statistics_receipt_is_pinned_not_self_authenticating(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "STATS", tmp_path)
    monkeypatch.setattr(study, "EXP", tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"statistics_receipt_sha256": "0"*64, "expected_streams": 3531}))
    (tmp_path / "statistics_receipt.json").write_text(json.dumps({"source_receipts": [], "files": {}}))
    with pytest.raises(ValueError, match="identity changed"):
        study.verified_sources()
