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
    """Coarse deploy role without leaking host paths or secrets.

    Detect known VPS install root (/opt/fable-trading) only as a boolean role
    flag — never put absolute paths into the public JSON.
    """
    root = PROJECT_ROOT.resolve()
    on_vps_tree = root.as_posix() == "/opt/fable-trading"
    ex = executor_enabled()
    if on_vps_tree:
        status = "vps_executor_off" if not ex else "vps_executor_on"
        summary = (
            "VPS ops console; executor must stay off; auth via env file."
            if not ex
            else "VPS ops console with executor ON (unexpected on public host)."
        )
    else:
        status = "local_ops"
        summary = "Loopback/local ops console; VPS deploy is a separate verified step."
    return {
        "id": "deploy",
        "title": "Deployment",
        "status": status,
        "summary": summary,
        "detail": {
            "role": "vps" if on_vps_tree else "local",
            "bind_expectation": "VPS public with executor=0" if on_vps_tree else "127.0.0.1 or Mac-local",
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


# Coarse thresholds for read-only anomaly flags (not trading parameters).
DATA_STALE_HOURS = 36.0
YOLO_EVIDENCE_STALE_DAYS = 14.0
FORWARD_LOW_SAMPLE_FRACTION = 0.25  # vs decision_target


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_since(ts: str | None) -> float | None:
    dt = _parse_iso(ts)
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 3600.0)


def collect_anomalies(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive read-only health flags from stage metadata (no I/O, no writes).

    Each flag: id, severity (info|warn|crit), stage, message.
    Deterministic from the provided stages list only.
    """
    by_id = {s.get("id"): s for s in stages}
    flags: list[dict[str, Any]] = []

    data = by_id.get("data") or {}
    ddet = data.get("detail") or {}
    if data.get("status") == "missing" or not ddet.get("series_total"):
        flags.append(
            {
                "id": "data_missing",
                "severity": "crit",
                "stage": "data",
                "message": "No market-data series detected on disk metadata scan.",
            }
        )
    else:
        age_h = _hours_since(ddet.get("latest_mtime_15m"))
        if age_h is None:
            flags.append(
                {
                    "id": "data_mtime_unknown",
                    "severity": "warn",
                    "stage": "data",
                    "message": "15m latest_mtime missing; cannot judge data freshness.",
                }
            )
        elif age_h > DATA_STALE_HOURS:
            flags.append(
                {
                    "id": "data_stale",
                    "severity": "warn",
                    "stage": "data",
                    "message": (
                        f"15m data mtime age ~{age_h:.1f}h exceeds "
                        f"{DATA_STALE_HOURS:.0f}h freshness threshold."
                    ),
                }
            )

    yolo = by_id.get("detection_yolo") or {}
    ydet = yolo.get("detail") or {}
    if yolo.get("status") in {"unknown", "missing"} or (
        not ydet.get("report_path") and not ydet.get("metrics_coarse")
    ):
        flags.append(
            {
                "id": "yolo_evidence_missing",
                "severity": "info",
                "stage": "detection_yolo",
                "message": "YOLO diagnostic report/metrics not found (not a live gate).",
            }
        )
    else:
        y_age_h = _hours_since(ydet.get("report_mtime"))
        if y_age_h is not None and y_age_h > YOLO_EVIDENCE_STALE_DAYS * 24:
            flags.append(
                {
                    "id": "yolo_evidence_stale",
                    "severity": "info",
                    "stage": "detection_yolo",
                    "message": (
                        f"YOLO report mtime age ~{y_age_h / 24:.1f}d exceeds "
                        f"{YOLO_EVIDENCE_STALE_DAYS:.0f}d diagnostic window."
                    ),
                }
            )

    judg = by_id.get("judgment") or {}
    jdet = judg.get("detail") or {}
    fp = jdet.get("fingerprint_status")
    if judg.get("status") == "missing" or not (jdet.get("active") or {}).get("exists"):
        flags.append(
            {
                "id": "active_missing",
                "severity": "crit",
                "stage": "judgment",
                "message": "ACTIVE judgment pointer missing.",
            }
        )
    elif fp == "mismatch":
        flags.append(
            {
                "id": "fingerprint_mismatch",
                "severity": "warn",
                "stage": "judgment",
                "message": "ACTIVE dataset fingerprint mismatch vs frozen metadata.",
            }
        )
    elif fp in {"error", "unverifiable"}:
        flags.append(
            {
                "id": "fingerprint_unverifiable",
                "severity": "warn",
                "stage": "judgment",
                "message": f"ACTIVE fingerprint status={fp}.",
            }
        )

    fwd = by_id.get("forward") or {}
    fdet = fwd.get("detail") or {}
    if fwd.get("status") == "missing" or not fdet.get("total_rows"):
        flags.append(
            {
                "id": "forward_log_missing",
                "severity": "warn",
                "stage": "forward",
                "message": "Forward paper log missing or empty.",
            }
        )
    else:
        decision = int(fdet.get("decision_trades") or 0)
        target = int(fdet.get("decision_target") or 100) or 100
        if decision < max(1, int(target * FORWARD_LOW_SAMPLE_FRACTION)):
            flags.append(
                {
                    "id": "forward_low_sample",
                    "severity": "info",
                    "stage": "forward",
                    "message": (
                        f"Decision trades {decision}/{target} below "
                        f"{FORWARD_LOW_SAMPLE_FRACTION:.0%} of target "
                        "(not promotion evidence)."
                    ),
                }
            )

    jobs = by_id.get("jobs") or {}
    if (jobs.get("detail") or {}).get("executor_enabled") or jobs.get("status") == "executor_on":
        flags.append(
            {
                "id": "executor_enabled",
                "severity": "crit",
                "stage": "jobs",
                "message": "ENABLE_JOB_EXECUTOR is on; unexpected on public VPS.",
            }
        )

    deploy = by_id.get("deploy") or {}
    if deploy.get("status") == "vps_executor_on":
        flags.append(
            {
                "id": "vps_executor_on",
                "severity": "crit",
                "stage": "deploy",
                "message": "Deploy role reports VPS with executor ON.",
            }
        )

    return flags


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
    anomalies = collect_anomalies(stages)
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
        "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "notes": {
            "purpose": "Coarse redacted pipeline view for operators",
            "not_proof": "Historical backtest and short forward are not future-return guarantees",
            "holdout": "Judgment holdout remains sealed on this surface",
            "anomalies": "Read-only health flags from stage metadata; not alerts/actions",
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
