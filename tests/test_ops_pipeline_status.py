"""P2.5 redacted pipeline status: structure + redaction + auth gate."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.webapp import pipeline_status
from src.webapp.auth import verify_ops_request
from src.webapp.server import ops_pipeline


def _req(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


def test_pipeline_payload_structure_and_safety(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    monkeypatch.setenv("OPS_AUTH_MODE", "off")
    payload = pipeline_status.pipeline_status_payload()
    assert payload["read_only"] is True
    assert payload["write_actions"] == []
    assert payload["executor_enabled"] is False
    ids = [s["id"] for s in payload["stages"]]
    assert ids == [
        "data",
        "detection_yolo",
        "judgment",
        "backtest",
        "forward",
        "jobs",
        "deploy",
    ]
    assert "not final profitability proof" in payload["stages"][3]["summary"]
    jobs = next(s for s in payload["stages"] if s["id"] == "jobs")
    assert jobs["detail"]["executor_enabled"] is False
    assert jobs["detail"]["write_actions"] == []
    violations = pipeline_status.assert_pipeline_payload_safe(payload)
    assert violations == []


def test_redact_strings_strips_absolute_and_secrets() -> None:
    dirty = {
        "ok": "models/ACTIVE",
        "leak": "/Users/zhangzc/secret/path/file.txt",
        "password": "should-not-appear",
        "nested": [{"token": "abc", "rel": "data/forward_log.csv"}],
    }
    clean = pipeline_status._redact_strings(dirty)
    assert clean["leak"] == "file.txt"
    assert clean["password"] == "[redacted]"
    assert clean["nested"][0]["token"] == "[redacted]"
    assert clean["nested"][0]["rel"] == "data/forward_log.csv"
    assert clean["ok"] == "models/ACTIVE"


def test_ops_pipeline_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "pipe-test-token")
    with pytest.raises(HTTPException) as ei:
        verify_ops_request(_req())
    assert ei.value.status_code in (401, 503)
    # Authenticated path returns payload via route helper.
    out = ops_pipeline(_req({"Authorization": "Bearer pipe-test-token"}))
    assert out["read_only"] is True
    assert "stages" in out


def test_deploy_stage_role_local_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    stage = pipeline_status._deploy_stage()
    # CI/dev trees are never /opt/fable-trading.
    assert stage["detail"]["role"] == "local"
    assert stage["status"] == "local_ops"
    assert "/opt/" not in str(stage)
    assert "/Users/" not in str(stage)


def test_deploy_stage_role_vps_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(pipeline_status, "PROJECT_ROOT", Path("/opt/fable-trading"))
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    stage = pipeline_status._deploy_stage()
    assert stage["detail"]["role"] == "vps"
    assert stage["status"] == "vps_executor_off"
    # Boolean role only — no absolute path leak in public fields.
    blob = str(stage)
    assert "/opt/fable-trading" not in blob
