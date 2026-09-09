"""Focused offline audit integrity checks; no provider or strategy is called."""
import hashlib
import json

import pytest

from yoyo.evaluation.ashare_audit import AShareAuditError, main, semantic_content, verify_receipts


def source_fixture(tmp_path):
    source = tmp_path / "universe_raw.csv"
    raw = b"code,tradeStatus,code_name\nsh.600000,1,example\n"
    request = {"provider": "baostock", "day": "2020-01-02"}
    receipt = {"sha256": hashlib.sha256(raw).hexdigest(), "rows": 1, "request": request,
               "request_key": hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    source.write_bytes(raw)
    source.with_suffix(".csv.json").write_text(json.dumps(receipt))
    return source


@pytest.mark.parametrize("fault", ["bytes", "request", "rows", "missing_receipt"])
def test_source_corruption_fails_closed(tmp_path, fault):
    source = source_fixture(tmp_path)
    assert len(verify_receipts(tmp_path)) == 1
    receipt_path = source.with_suffix(".csv.json")
    if fault == "bytes":
        source.write_bytes(source.read_bytes().replace(b"600000", b"600001"))
    elif fault == "missing_receipt":
        receipt_path.unlink()
    else:
        receipt = json.loads(receipt_path.read_text())
        if fault == "request": receipt["request"]["day"] = "2025-01-02"
        if fault == "rows": receipt["rows"] = 2
        receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(AShareAuditError):
        verify_receipts(tmp_path)


def test_semantic_comparison_ignores_location_but_keeps_data_evidence():
    original = {"created_at": "old", "builder_commit": "old", "path": "old/path",
                "stocks": [{"daily_path": "old.csv", "rows": 2674, "daily_sha256": "abc"}],
                "exclusions_sha256": "def"}
    copied = {**original, "created_at": "new", "builder_commit": "new", "path": "new/path",
              "stocks": [{"daily_path": "new.csv", "rows": 2674, "daily_sha256": "abc"}]}
    assert semantic_content(original) == semantic_content(copied)
    copied["stocks"][0]["rows"] = 2673
    assert semantic_content(original) != semantic_content(copied)


def test_cli_never_overwrites_original_or_writes_into_frozen_data(tmp_path):
    existing = tmp_path / "original.json"
    existing.write_text("original evidence")
    with pytest.raises(AShareAuditError, match="must not be overwritten"):
        main(["--data", str(tmp_path / "data"), "--output", str(existing)])
    assert existing.read_text() == "original evidence"
    with pytest.raises(AShareAuditError, match="outside the frozen"):
        main(["--data", str(tmp_path / "data"), "--output", str(tmp_path / "data/new.json")])
