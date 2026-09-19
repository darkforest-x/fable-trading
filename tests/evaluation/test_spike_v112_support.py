"""Focused V11.2 guards: strict support, original box consumption, and safe resume."""
import hashlib
import json

import numpy as np

from yoyo.evaluation import spike_v112_support_study as support
from yoyo.evaluation.spike_v10_4 import box_joints


def test_support_gate_is_strict_and_rejects_nonfinite_values():
    assert support.support_gate(100.0001, 100.0)
    assert not support.support_gate(100.0, 100.0)
    assert not support.support_gate(np.nan, 100.0)
    assert not support.support_gate(100.0, np.inf)


def test_support_gate_uses_only_the_same_bar_prefix():
    close = np.array([99.0, 101.0, 102.0])
    rope = np.array([100.0, 100.0, 100.0])
    before = support.support_mask(close, rope)
    close[2], rope[2] = -999.0, np.nan
    assert support.support_mask(close, rope)[:2].tolist() == before[:2].tolist()


def test_rejected_first_break_consumes_box_and_cannot_retry_later():
    open_box = np.zeros(12, bool); open_box[2:10] = True
    entry = np.where(open_box, 2, -1)
    breaks = np.zeros(12, bool); breaks[[4, 7]] = True
    original = box_joints(open_box, entry, breaks)
    passed = np.zeros(12, bool); passed[7] = True
    assert np.flatnonzero(original).tolist() == [4]
    assert not (original & passed).any()


def test_independent_serial_replay_can_free_a_previously_blocked_candidate(monkeypatch):
    calls = []

    def attempt(_prepared, i):
        calls.append(i)
        return "closed", {"signal_i": i, "exit_i": 10 if i == 1 else i + 1}

    monkeypatch.setattr(support.inc, "attempt", attempt)
    prepared = type("Prepared", (), {"frame": range(20)})()
    baseline, _ = support.serial(prepared, np.array([1, 5]), "stream", "box_any")
    treatment, _ = support.serial(prepared, np.array([5]), "stream", "box_support")
    assert [row["signal_i"] for row in baseline] == [1]
    assert [row["signal_i"] for row in treatment] == [5]
    assert treatment[0]["trade_key"] == "stream:box_any:5"
    assert calls == [1, 5]


def test_resume_rejects_stale_receipt_or_corrupt_output(tmp_path):
    final = tmp_path / "BTC"; final.mkdir()
    data = final / "trades.csv.gz"; data.write_bytes(b"receipt-test")
    digest = hashlib.sha256(data.read_bytes()).hexdigest()
    files = {f"{name}.csv.gz": digest for name in support.OUTPUT_TABLES}
    for name in support.OUTPUT_TABLES:
        (final / f"{name}.csv.gz").write_bytes(b"receipt-test")
    (final / "completion.json").write_text(json.dumps({"status": "complete", "run_identity": "old", "input_sha256": "input", "files": files}))
    try:
        support._validate_completion(final, "new", "input")
    except ValueError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("stale completion was accepted")
    (final / "completion.json").write_text(json.dumps({"status": "complete", "run_identity": "new", "input_sha256": "input", "files": files}))
    (final / "controls.csv.gz").write_bytes(b"corrupt")
    try:
        support._validate_completion(final, "new", "input")
    except ValueError as exc:
        assert "drift" in str(exc)
    else:
        raise AssertionError("corrupt completion was accepted")


def test_identity_includes_exchange_metadata_and_frozen_config_is_checked():
    config = json.loads(support.CONFIG.read_text())
    support._validate_config(config)
    identity, _, _ = support._identity({}, config)
    assert identity["exchange_info"]["path"] == str(support.study.EXCHANGE_INFO)
    changed = dict(config); changed["roundtrip_cost"] = 0.001
    try:
        support._validate_config(changed)
    except ValueError as exc:
        assert "roundtrip_cost" in str(exc)
    else:
        raise AssertionError("changed frozen cost was accepted")


def test_smoke_or_uncommitted_run_cannot_be_a_complete_manifest():
    receipt = [{"symbol": "A", "failures": []}]
    assert not support._manifest_complete(explicit_symbols=True, allow_uncommitted=False, receipts=receipt,
                                          expected=1, failures={})
    assert not support._manifest_complete(explicit_symbols=False, allow_uncommitted=True, receipts=receipt,
                                          expected=1, failures={})
    assert support._manifest_complete(explicit_symbols=False, allow_uncommitted=False, receipts=receipt,
                                      expected=1, failures={})
