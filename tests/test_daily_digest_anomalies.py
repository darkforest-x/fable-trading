"""Digest ↔ pipeline anomaly glue (Todo 9b).

Pure format tests + build_message injection; no Telegram, no holdout.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIGEST_PATH = ROOT / "scripts" / "daily_digest.py"


def _load_digest():
    spec = importlib.util.spec_from_file_location("daily_digest_under_test", DIGEST_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def digest():
    return _load_digest()


def test_format_healthy_empty(digest) -> None:
    text, alert = digest.format_pipeline_anomalies([])
    assert "0 anomalies" in text or "ok" in text.lower()
    assert alert is False
    assert "fingerprint" not in text


def test_format_injected_flags_rank_and_alert(digest) -> None:
    flags = [
        {
            "id": "forward_low_sample",
            "severity": "info",
            "stage": "forward",
            "message": "Decision trades 7/100 below 25%.",
        },
        {
            "id": "executor_enabled",
            "severity": "crit",
            "stage": "jobs",
            "message": "ENABLE_JOB_EXECUTOR is on.",
        },
        {
            "id": "data_stale",
            "severity": "warn",
            "stage": "data",
            "message": "15m data mtime age high.",
        },
    ]
    text, alert = digest.format_pipeline_anomalies(flags, limit=2)
    assert alert is True  # warn+crit present
    assert "executor_enabled" in text  # crit ranks first
    assert "data_stale" in text
    # limit=2 drops the info flag from the body
    assert "forward_low_sample" not in text
    assert "3 flag" in text


def test_format_info_only_no_header_alert(digest) -> None:
    flags = [
        {
            "id": "yolo_evidence_missing",
            "severity": "info",
            "stage": "detection_yolo",
            "message": "YOLO report not found.",
        }
    ]
    text, alert = digest.format_pipeline_anomalies(flags)
    assert alert is False
    assert "yolo_evidence_missing" in text


def test_build_message_injected_healthy(digest, monkeypatch) -> None:
    monkeypatch.setattr(digest, "data_freshness", lambda: ("数据更新：ok", False))
    monkeypatch.setattr(digest, "forward_board", lambda: "主线：stub")
    monkeypatch.setattr(digest, "system_health", lambda: "系统：stub")
    msg, alert = digest.build_message(anomalies=[])
    assert alert is False
    assert "管道健康：ok" in msg
    assert "‼️" not in msg


def test_build_message_injected_crit(digest, monkeypatch) -> None:
    monkeypatch.setattr(digest, "data_freshness", lambda: ("数据更新：ok", False))
    monkeypatch.setattr(digest, "forward_board", lambda: "主线：stub")
    monkeypatch.setattr(digest, "system_health", lambda: "系统：stub")
    flags = [
        {
            "id": "active_missing",
            "severity": "crit",
            "stage": "judgment",
            "message": "ACTIVE missing.",
        }
    ]
    msg, alert = digest.build_message(anomalies=flags)
    assert alert is True
    assert "active_missing" in msg
    assert "‼️" in msg


def test_main_dry_run_skips_telegram(digest, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        digest,
        "load_pipeline_anomalies",
        lambda: [
            {
                "id": "fingerprint_mismatch",
                "severity": "warn",
                "stage": "judgment",
                "message": "fp mismatch",
            }
        ],
    )
    monkeypatch.setattr(digest, "data_freshness", lambda: ("数据更新：ok", False))
    monkeypatch.setattr(digest, "forward_board", lambda: "主线：stub")
    monkeypatch.setattr(digest, "system_health", lambda: "系统：stub")
    sent = []

    def _boom(*_a, **_k):
        sent.append(True)
        raise AssertionError("Telegram send must not run in --dry-run")

    monkeypatch.setattr(digest, "send", _boom)
    rc = digest.main(["--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert not sent
    assert "telegram_send: SKIPPED" in out
    assert "anomaly_count: 1" in out
    assert "fingerprint_mismatch" in out
    assert "anomaly_ids: fingerprint_mismatch" in out
