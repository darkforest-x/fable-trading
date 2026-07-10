"""P2.5 Phase 0+1: auth flags + experiment registry + agenda (no network)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.webapp import agenda_payloads, experiment_registry, ops_flags
from src.webapp.auth import verify_ops_request


def _req(headers: dict | None = None):
    return SimpleNamespace(headers=headers or {})


def test_ops_status_payload_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPS_AUTH_MODE", raising=False)
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    monkeypatch.delenv("OPS_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("ENABLE_JOB_EXECUTOR", raising=False)
    body = ops_flags.ops_status_payload()
    assert body["auth_mode"] == "off"
    assert body["ops_auth_required"] is False
    assert body["executor_enabled"] is False
    assert body["token_configured"] is False


def test_auth_off_allows_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "off")
    monkeypatch.delenv("OPS_REQUIRE_AUTH", raising=False)
    verify_ops_request(_req())  # no raise


def test_auth_token_rejects_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "test-secret-token")
    with pytest.raises(HTTPException) as ei:
        verify_ops_request(_req())
    assert ei.value.status_code == 401


def test_auth_token_accepts_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "test-secret-token")
    verify_ops_request(_req({"Authorization": "Bearer test-secret-token"}))


def test_auth_token_accepts_x_ops_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.setenv("OPS_API_TOKEN", "test-secret-token")
    verify_ops_request(_req({"X-Ops-Token": "test-secret-token"}))


def test_auth_token_mode_without_secret_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_AUTH_MODE", "token")
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as ei:
        verify_ops_request(_req({"Authorization": "Bearer anything"}))
    assert ei.value.status_code == 503


def test_list_experiments_reads_output_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "analysis" / "output"
    out.mkdir(parents=True)
    sample = [
        {"config": "tp5_sl2", "val_auc": 0.56, "perm_p": 0.001, "top_net_maker_006": 0.002},
        {"config": "tp4_sl2", "val_auc": 0.57, "perm_p": 0.01, "top_net_maker_006": 0.001},
    ]
    (out / "swap_replication.json").write_text(json.dumps(sample), encoding="utf-8")
    monkeypatch.setattr(experiment_registry, "OUTPUT_DIR", out)
    monkeypatch.setattr(experiment_registry, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(experiment_registry, "PROJECT_ROOT", tmp_path)

    payload = experiment_registry.list_experiments(sort="val_auc", order="desc")
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["id"] == "swap_replication"
    assert item["metrics"]["val_auc"] == 0.57
    assert item["metrics"]["top_net_maker"] == 0.001


def test_list_experiments_dict_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "analysis" / "output"
    out.mkdir(parents=True)
    (out / "data_audit_summary.json").write_text(
        json.dumps({"series_total": 10, "blacklist_candidate_n": 2}),
        encoding="utf-8",
    )
    monkeypatch.setattr(experiment_registry, "OUTPUT_DIR", out)
    monkeypatch.setattr(experiment_registry, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(experiment_registry, "PROJECT_ROOT", tmp_path)
    payload = experiment_registry.list_experiments()
    assert payload["count"] == 1
    assert payload["items"][0]["kind"] == "audit"


def test_list_experiments_formats_structured_config_for_display(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "analysis" / "output"
    out.mkdir(parents=True)
    (out / "short_replication.json").write_text(
        json.dumps({"config": {"name": "swap_short_tp5_sl2", "tp": 5.0, "sl": 2.0}}),
        encoding="utf-8",
    )
    (out / "p3_backtest.json").write_text(
        json.dumps(
            {
                "config": {
                    "max_concurrent": 10,
                    "base_cost_round_trip": 0.003,
                    "score_scope": "pre_holdout_only",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(experiment_registry, "OUTPUT_DIR", out)
    monkeypatch.setattr(experiment_registry, "ANALYSIS_DIR", tmp_path / "analysis")
    monkeypatch.setattr(experiment_registry, "PROJECT_ROOT", tmp_path)

    payload = experiment_registry.list_experiments()

    configs = {item["id"]: item["config"] for item in payload["items"]}
    assert configs["short_replication"] == "swap_short_tp5_sl2"
    assert configs["p3_backtest"] == "base_cost_round_trip=0.003, max_concurrent=10 (+1)"


def test_experiment_detail_blocks_path_traversal() -> None:
    assert experiment_registry.experiment_detail("../secret") is None
    assert experiment_registry.experiment_detail("a/b") is None


def test_agenda_payload_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agenda_payloads, "AGENDA_PATH", tmp_path / "missing.md")
    payload = agenda_payloads.agenda_payload()
    assert payload["exists"] is False


def test_agenda_payload_reads_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "RESEARCH_AGENDA.md"
    p.write_text("# agenda\n\n- item\n", encoding="utf-8")
    monkeypatch.setattr(agenda_payloads, "AGENDA_PATH", p)
    payload = agenda_payloads.agenda_payload()
    assert payload["exists"] is True
    assert "agenda" in payload["markdown"]
