"""P2.5 Phase 3: read-only data hub + model hub (no network)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.webapp import data_hub, model_hub, ops_flags
from src.webapp.auth import verify_ops_request
from src.webapp.server import ops_data_hub, ops_model_hub


def _req(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


def _write_csv(path: Path, name: str, body: str = "open_time,open,high,low,close,volume\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    p = path / name
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# data hub
# ---------------------------------------------------------------------------


def test_coverage_by_bar_counts_files(tmp_path: Path) -> None:
    fetched = tmp_path / "kline_fetched"
    cache = tmp_path / "kline_cache"
    _write_csv(fetched, "okx_BTC_USDT_SWAP_15m_1000.csv")
    _write_csv(fetched, "okx_ETH_USDT_SWAP_15m_2000.csv")
    _write_csv(fetched, "okx_SOL_USDT_SWAP_5m_500.csv")
    _write_csv(cache, "gate_BTC_USDT_15m_800.csv")

    cov = data_hub.coverage_by_bar(fetched_dir=fetched, cache_dir=cache)
    by = {row["bar"]: row for row in cov["by_bar"]}
    assert by["15m"]["series_n"] == 3  # okx BTC SWAP, okx ETH SWAP, gate BTC
    assert by["15m"]["raw_fetched_csv"] == 2
    assert by["15m"]["raw_cache_csv"] == 1
    assert by["5m"]["series_n"] == 1
    assert by["5m"]["named_rows_sum"] == 500
    assert cov["series_total"] == 4


def test_load_audit_summary_embeds_json(tmp_path: Path) -> None:
    p = tmp_path / "data_audit_summary.json"
    payload = {
        "series_total": 10,
        "flagged": 2,
        "blacklist_candidate_n": 1,
        "by_bar": {"15m": 5},
        "worst_gaps": [{"symbol": f"S{i}"} for i in range(30)],
    }
    p.write_text(json.dumps(payload), encoding="utf-8")
    out = data_hub.load_audit_summary(p)
    assert out["exists"] is True
    assert out["summary"]["series_total"] == 10
    assert len(out["summary"]["worst_gaps"]) == 20
    assert out["summary"]["worst_gaps_truncated"] is True
    assert out["summary"]["worst_gaps_total"] == 30


def test_load_audit_summary_missing(tmp_path: Path) -> None:
    out = data_hub.load_audit_summary(tmp_path / "nope.json")
    assert out["exists"] is False
    assert out["summary"] is None


def test_forward_log_health_from_path(tmp_path: Path) -> None:
    log = tmp_path / "forward_log.csv"
    log.write_text(
        "source,symbol,signal_time,detected_at,status,score,threshold,model_path,"
        "dataset_sha256,signal_i,entry_time,entry_price,maker_filled,outcome,label,"
        "exit_offset,exit_time,realized_ret,atr_pct,dense_run_len\n"
        "okx,BTC_USDT_SWAP,2026-07-08,2026-07-09T01:00:00+00:00,closed,0.4,0.3,m.txt,"
        "abc,1,2026-07-08,1.0,True,tp,1,2,2026-07-08,0.01,0.01,3\n"
        "okx,ETH_USDT_SWAP,2026-07-08,2026-07-09T02:00:00+00:00,open,0.5,0.3,m.txt,"
        "abc,2,,,False,,,,,\n",
        encoding="utf-8",
    )
    health = data_hub.forward_log_health(log)
    assert health["exists"] is True
    assert health["total_rows"] == 2
    assert health["closed_rows"] == 1
    assert health["open_rows"] == 1
    assert health["decision_trades"] == 1
    assert health["decision_target"] == 100
    assert health["decision_remaining"] == 99
    assert health["latest_detected_at"] is not None


def test_forward_log_health_missing(tmp_path: Path) -> None:
    health = data_hub.forward_log_health(tmp_path / "missing.csv")
    assert health["exists"] is False
    assert health["total_rows"] == 0
    assert health["decision_remaining"] == 100


def test_data_hub_payload_composed(tmp_path: Path) -> None:
    fetched = tmp_path / "fetched"
    _write_csv(fetched, "okx_BTC_USDT_SWAP_15m_100.csv")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"series_total": 1, "flagged": 0}), encoding="utf-8")
    flog = tmp_path / "fwd.csv"
    flog.write_text(
        "source,symbol,signal_time,detected_at,status,score,threshold,model_path,"
        "dataset_sha256,signal_i,entry_time,entry_price,maker_filled,outcome,label,"
        "exit_offset,exit_time,realized_ret,atr_pct,dense_run_len\n",
        encoding="utf-8",
    )
    body = data_hub.data_hub_payload(
        fetched_dir=fetched,
        cache_dir=tmp_path / "empty_cache",
        audit_path=audit,
        forward_log_path=flog,
    )
    assert body["read_only"] is True
    assert body["coverage"]["by_bar"]
    assert body["audit"]["summary"]["series_total"] == 1
    assert body["forward"]["exists"] is True


# ---------------------------------------------------------------------------
# model hub
# ---------------------------------------------------------------------------


def test_list_frozen_models_pair_and_meta(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    dataset = tmp_path / "data" / "ds.csv"
    dataset.parent.mkdir(parents=True)
    dataset.write_text("a,b\n1,2\n", encoding="utf-8")
    sha = hashlib.sha256(dataset.read_bytes()).hexdigest()

    meta = {
        "config": "tp5_sl2_swap",
        "created_at": "2026-07-09T00:00:00+00:00",
        "threshold_val_q90": 0.37,
        "dataset_path": "data/ds.csv",
        "dataset_sha256": sha,
        "feature_columns": ["f1", "f2", "f3"],
        "best_iteration": 10,
    }
    (models / "frozen_tp5_sl2_swap_20260709.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )
    (models / "frozen_tp5_sl2_swap_20260709.txt").write_text("tree\n", encoding="utf-8")
    # orphan json without txt
    (models / "frozen_orphan_20260101.json").write_text(
        json.dumps({"config": "orphan", "threshold_val_q90": 0.1}),
        encoding="utf-8",
    )
    # ACTIVE pointer
    active_path = models / "ACTIVE"
    active_path.write_text("models/frozen_tp5_sl2_swap_20260709.txt\n", encoding="utf-8")

    body = model_hub.model_hub_payload(
        models_dir=models,
        project_root=tmp_path,
        active_path=active_path,
        verify_fingerprint=True,
    )
    assert body["read_only"] is True
    assert body["promote_available"] is False
    assert body["count"] == 2
    assert body["paired_count"] == 1
    assert body["active"]["exists"] is True
    assert body["active"]["artifact_id"] == "frozen_tp5_sl2_swap_20260709"

    by_id = {it["artifact_id"]: it for it in body["items"]}
    main = by_id["frozen_tp5_sl2_swap_20260709"]
    assert main["pair_status"] == "paired"
    assert main["is_active"] is True
    assert main["threshold_val_q90"] == 0.37
    assert main["dataset_sha256"] == sha
    assert main["n_features"] == 3
    assert main["fingerprint"]["fingerprint_status"] == "ok"
    assert main["fingerprint"]["match"] is True

    orphan = by_id["frozen_orphan_20260101"]
    assert orphan["pair_status"] == "missing_txt"
    assert orphan["is_active"] is False


def test_fingerprint_unverifiable_when_dataset_missing(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    meta = {
        "config": "x",
        "dataset_path": "data/missing.csv",
        "dataset_sha256": "deadbeef",
        "threshold_val_q90": 0.2,
        "feature_columns": ["a"],
    }
    (models / "frozen_x_1.json").write_text(json.dumps(meta), encoding="utf-8")
    (models / "frozen_x_1.txt").write_text("m", encoding="utf-8")
    items = model_hub.list_frozen_models(
        models, project_root=tmp_path, verify_fingerprint=True, active={}
    )
    assert items[0]["fingerprint"]["fingerprint_status"] == "unverifiable"


def test_fingerprint_mismatch(tmp_path: Path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    dataset = tmp_path / "data" / "ds.csv"
    dataset.parent.mkdir()
    dataset.write_text("x\n1\n", encoding="utf-8")
    meta = {
        "config": "x",
        "dataset_path": "data/ds.csv",
        "dataset_sha256": "0" * 64,
        "threshold_val_q90": 0.2,
        "feature_columns": ["a"],
    }
    (models / "frozen_x_1.json").write_text(json.dumps(meta), encoding="utf-8")
    (models / "frozen_x_1.txt").write_text("m", encoding="utf-8")
    items = model_hub.list_frozen_models(
        models, project_root=tmp_path, verify_fingerprint=True, active={}
    )
    assert items[0]["fingerprint"]["fingerprint_status"] == "mismatch"
    assert items[0]["fingerprint"]["match"] is False


def test_read_active_pointer_missing(tmp_path: Path) -> None:
    out = model_hub.read_active_pointer(tmp_path / "ACTIVE")
    assert out["exists"] is False
    assert out["artifact_id"] is None


def test_model_hub_empty_dir(tmp_path: Path) -> None:
    body = model_hub.model_hub_payload(
        models_dir=tmp_path / "empty",
        project_root=tmp_path,
        active_path=tmp_path / "ACTIVE",
    )
    assert body["count"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# API routes + auth + flags
# ---------------------------------------------------------------------------


def test_ops_status_phase_includes_3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPS_AUTH_MODE", raising=False)
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    body = ops_flags.ops_status_payload()
    assert "3" in body["phase"]
    assert body["executor_enabled"] is False


def test_data_hub_route_requires_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "phase3-secret")
    with pytest.raises(HTTPException) as ei:
        ops_data_hub(_req())
    assert ei.value.status_code == 401


def test_model_hub_route_accepts_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "phase3-secret")
    models = tmp_path / "models"
    models.mkdir()
    (models / "frozen_demo_1.json").write_text(
        json.dumps({"config": "demo", "threshold_val_q90": 0.5, "feature_columns": []}),
        encoding="utf-8",
    )
    (models / "frozen_demo_1.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(model_hub, "DEFAULT_MODELS_DIR", models)
    monkeypatch.setattr(model_hub, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(model_hub, "DEFAULT_ACTIVE_POINTER", models / "ACTIVE")

    # Route calls model_hub_payload() with defaults — patch module-level defaults used inside.
    out = ops_model_hub(_req({"X-Ops-Token": "phase3-secret"}))
    assert out["read_only"] is True
    assert out["count"] >= 1


def test_data_hub_route_auth_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "off")
    monkeypatch.delenv("OPS_REQUIRE_AUTH", raising=False)
    # Isolate from real large data dirs where possible
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(data_hub, "FETCHED_DIR", empty)
    monkeypatch.setattr(data_hub, "CACHE_DIR", empty)
    monkeypatch.setattr(data_hub, "AUDIT_SUMMARY_PATH", tmp_path / "no_audit.json")
    monkeypatch.setattr(data_hub, "FORWARD_LOG_PATH", tmp_path / "no_fwd.csv")
    # forward_log_health uses FORWARD_LOG_PATH constant from forward_types when log_path is None;
    # also patch list_series path via empty dirs on data_hub module constants used by coverage.
    body = ops_data_hub(_req())
    assert body["read_only"] is True
    assert "coverage" in body
    assert "audit" in body
    assert "forward" in body


def test_executor_still_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    assert ops_flags.executor_enabled() is False


def test_verify_ops_request_still_gates() -> None:
    # smoke: auth helper still importable for hubs
    assert callable(verify_ops_request)
