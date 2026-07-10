"""P2.5 redacted end-to-end pipeline status (ops-auth, read-only).

Composes coarse stage rows for data freshness, YOLO gate evidence, judgment
ACTIVE fingerprint, historical backtest label, forward sample counts, job
executor state, and deploy notes. Never exposes secrets, free-shell actions,
or absolute local paths outside a relative project root view.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.webapp.data_hub import coverage_by_bar, forward_log_health, scan_live_part_files
from src.webapp.model_hub import model_hub_payload
from src.webapp.ops_flags import executor_enabled, ops_status_payload

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Coarse YOLO evidence only — never claims production validation.
YOLO_REPORT_CANDIDATES = (
    PROJECT_ROOT / "analysis" / "p2a_e21_train_report.md",
    PROJECT_ROOT / "analysis" / "p2a_detection_report.md",
)
YOLO_METRICS_CANDIDATES = (
    PROJECT_ROOT / "analysis" / "output" / "p2a_val_metrics.json",
    PROJECT_ROOT / "analysis" / "p2a_val_metrics.json",
)
BACKTEST_LABEL = (
    "historical candidate evidence (pre-holdout / artifact metrics) — not final profitability proof"
)

_ABS_PATH_RE = re.compile(r"(^|[\s\"'])(/Users/|/home/|/private/|/var/folders/|[A-Za-z]:\\)")
_SECRET_KEY_RE = re.compile(
    r"(password|secret|token|api_key|authorization|credential)", re.I
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        # Never leak absolute hosts paths in public pipeline JSON.
        return path.name


def _file_mtime_iso(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _redact_strings(obj: Any) -> Any:
    """Drop absolute-looking paths and secret-like keys from nested JSON."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[str(k)] = "[redacted]"
                continue
            out[str(k)] = _redact_strings(v)
        return out
    if isinstance(obj, list):
        return [_redact_strings(x) for x in obj]
    if isinstance(obj, str):
        if _ABS_PATH_RE.search(obj):
            return Path(obj).name
        return obj
    return obj


def _data_stage() -> dict[str, Any]:
    cov = coverage_by_bar()
    parts = scan_live_part_files(limit=10)
    by_bar = {row["bar"]: row for row in cov.get("by_bar") or []}
    m15 = by_bar.get("15m") or {}
    latest = m15.get("latest_mtime")
    return {
        "id": "data",
        "title": "Market data",
        "status": "ok" if m15.get("series_n", 0) > 0 else "missing",
        "summary": (
            f"15m series={m15.get('series_n', 0)}, "
            f"files={m15.get('file_n', 0)}, "
            f"part_live={parts.get('count', 0)}"
        ),
        "detail": {
            "series_total": cov.get("series_total"),
            "file_total": cov.get("file_total"),
            "latest_mtime_15m": latest,
            "part_files_live": parts.get("count", 0),
            "fetched_dir": cov.get("fetched_dir"),
            "cache_dir": cov.get("cache_dir"),
        },
        "caveat": "Counts are filesystem metadata only; not a fetch trigger.",
    }


def _yolo_stage() -> dict[str, Any]:
    report = next((p for p in YOLO_REPORT_CANDIDATES if p.is_file()), None)
    metrics_path = next((p for p in YOLO_METRICS_CANDIDATES if p.is_file()), None)
    metrics: dict[str, Any] | None = None
    if metrics_path is not None:
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metrics = None
    # Prefer coarse named metrics only.
    coarse: dict[str, Any] = {}
    if isinstance(metrics, dict):
        for key in ("map50", "mAP50", "map50-95", "mAP50-95", "precision", "recall"):
            if key in metrics:
                coarse[key] = metrics[key]
        # Nested common shapes
        for nest in ("metrics", "val", "summary"):
            block = metrics.get(nest)
            if isinstance(block, dict):
                for key in ("map50", "mAP50", "map50-95", "mAP50-95", "precision", "recall"):
                    if key in block and key not in coarse:
                        coarse[key] = block[key]
    status = "ok" if report or coarse else "unknown"
    return {
        "id": "detection_yolo",
        "title": "Detection (YOLO)",
        "status": status,
        "summary": (
            f"report={'yes' if report else 'no'}, "
            f"metric_keys={sorted(coarse.keys()) or ['none']}"
        ),
        "detail": {
            "report_path": _rel(report) if report else None,
            "report_mtime": _file_mtime_iso(report) if report else None,
            "metrics_path": _rel(metrics_path) if metrics_path else None,
            "metrics_coarse": coarse or None,
            "gate_note": (
                "E2.1b observe-only when training; this stage is diagnostic evidence, "
                "not production trading validation."
            ),
        },
        "caveat": "Per-symbol eval history ≠ global wall-clock separation.",
    }


def _judgment_stage() -> dict[str, Any]:
    hub = model_hub_payload(verify_fingerprint=True)
    active = hub.get("active") or {}
    items = hub.get("items") or []
    active_id = active.get("artifact_id")
    match = next((it for it in items if it.get("artifact_id") == active_id), None)
    fp = (match or {}).get("fingerprint") or {}
    return {
        "id": "judgment",
        "title": "Judgment model",
        "status": "ok" if active.get("exists") else "missing",
        "summary": (
            f"ACTIVE={active_id or 'none'}, "
            f"fingerprint={fp.get('fingerprint_status', 'n/a')}, "
            f"frozen_pairs={hub.get('paired_count', 0)}"
        ),
        "detail": {
            "active": {
                "exists": active.get("exists"),
                "artifact_id": active_id,
                "path": active.get("path"),
            },
            "fingerprint_status": fp.get("fingerprint_status"),
            "model_count": hub.get("count"),
            "paired_count": hub.get("paired_count"),
            "promote_available": hub.get("promote_available"),
        },
        "caveat": "Champion frozen; no promote action on this surface.",
    }


def _backtest_stage() -> dict[str, Any]:
    return {
        "id": "backtest",
        "title": "Backtest evidence",
        "status": "label_only",
        "summary": BACKTEST_LABEL,
        "detail": {
            "evidence_label": BACKTEST_LABEL,
            "holdout_policy": "judgment holdout sealed; not scored here",
        },
        "caveat": "Do not treat dashboard PF tiles as a new parameter search result.",
    }


def _forward_stage() -> dict[str, Any]:
    health = forward_log_health()
    n_closed = health.get("closed_rows", 0)
    n_open = health.get("open_rows", 0)
    n_total = health.get("total_rows", 0)
    decision = health.get("decision_trades", 0)
    target = health.get("decision_target", 100)
    return {
        "id": "forward",
        "title": "Forward paper book",
        "status": "ok" if health.get("exists") else "missing",
        "summary": (
            f"exists={health.get('exists')}, total={n_total}, "
            f"open={n_open}, closed={n_closed}, decision={decision}/{target}"
        ),
        "detail": {
            "log_path": health.get("path"),
            "mtime": health.get("mtime"),
            "total_rows": n_total,
            "open_rows": n_open,
            "closed_rows": n_closed,
            "decision_trades": decision,
            "decision_target": target,
            "sample_caveat": (
                "Forward sample size may be small; short-window PnL is not promotion evidence."
            ),
        },
        "caveat": "Append-only paper tracking; ACTIVE champion unchanged.",
    }


def _jobs_stage() -> dict[str, Any]:
    ex = executor_enabled()
    return {
        "id": "jobs",
        "title": "Scheduled / ops jobs",
        "status": "executor_off" if not ex else "executor_on",
        "summary": f"ENABLE_JOB_EXECUTOR={'1' if ex else '0'}",
        "detail": {
            "executor_enabled": ex,
            "write_actions": [],
            "note": "VPS must keep executor off; whitelist POST only when explicitly enabled on Mac.",
        },
        "caveat": "This endpoint never starts jobs.",
    }


def _deploy_stage() -> dict[str, Any]:
    return {
        "id": "deploy",
        "title": "Deployment",
        "status": "local_ops",
        "summary": "Loopback/local ops console; VPS deploy is a separate verified step.",
        "detail": {
            "bind_expectation": "127.0.0.1 or VPS with executor=0",
            "label_studio": "public review workflow separate from trading executor",
            "env_flags_visible": {
                "OPS_AUTH_MODE": os.environ.get("OPS_AUTH_MODE", "off"),
                "ENABLE_JOB_EXECUTOR": os.environ.get("ENABLE_JOB_EXECUTOR", "0"),
                # Never echo OPS_API_TOKEN.
                "OPS_API_TOKEN_configured": bool(os.environ.get("OPS_API_TOKEN", "").strip()),
            },
        },
        "caveat": "Credentials live only in env / untracked access notes.",
    }


def pipeline_status_payload() -> dict[str, Any]:
    """Build redacted multi-stage pipeline snapshot."""
    stages = [
        _data_stage(),
        _yolo_stage(),
        _judgment_stage(),
        _backtest_stage(),
        _forward_stage(),
        _jobs_stage(),
        _deploy_stage(),
    ]
    ops = ops_status_payload()
    raw = {
        "generated_at": _iso_now(),
        "read_only": True,
        "write_actions": [],
        "executor_enabled": executor_enabled(),
        "ops_auth_required": ops.get("ops_auth_required"),
        "auth_mode": ops.get("auth_mode"),
        "token_configured": ops.get("token_configured"),
        "stages": stages,
        "notes": {
            "purpose": "Coarse redacted pipeline view for operators",
            "not_proof": "Historical backtest and short forward are not future-return guarantees",
            "holdout": "Judgment holdout remains sealed on this surface",
        },
    }
    return _redact_strings(raw)


def assert_pipeline_payload_safe(payload: dict[str, Any]) -> list[str]:
    """Return list of safety violations (empty == safe). Used by tests."""
    violations: list[str] = []
    blob = json.dumps(payload, ensure_ascii=False)
    if _ABS_PATH_RE.search(blob):
        violations.append("absolute_path_leak")
    if "OPS_API_TOKEN" in blob and "[redacted]" not in blob:
        # token value itself should never appear; key name in notes is ok only if not a secret value
        pass
    if payload.get("read_only") is not True:
        violations.append("read_only_false")
    if payload.get("write_actions"):
        violations.append("write_actions_nonempty")
    for key in ("password", "secret", "api_key"):
        if re.search(rf'"{key}"\s*:\s*"[^"]+"', blob, re.I):
            violations.append(f"secret_field:{key}")
    return violations
