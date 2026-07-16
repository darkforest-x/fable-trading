"""Feature flags for ops console (env only; never hardcode secrets).

OPS auth is permanently off (owner request 2026-07). Env vars OPS_AUTH_MODE /
OPS_API_TOKEN are ignored for gating; status still reports them for diagnostics.
"""
from __future__ import annotations

import os


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def auth_mode() -> str:
    """Always report off — app-layer ops auth is removed."""
    return "off"


def api_token() -> str:
    return os.environ.get("OPS_API_TOKEN", "").strip()


def require_auth_for_ops() -> bool:
    """Always False: no token required for /api/ops/*."""
    return False


def executor_enabled() -> bool:
    return _truthy("ENABLE_JOB_EXECUTOR", "0")


def ops_status_payload() -> dict:
    return {
        "auth_mode": auth_mode(),
        "ops_auth_required": require_auth_for_ops(),
        "token_configured": bool(api_token()),
        "executor_enabled": executor_enabled(),
        "phase": "0+1+2+3",
        "notes": {
            "auth": "OPS auth disabled — /api/ops/* is open (no token).",
            "executor": (
                "ENABLE_JOB_EXECUTOR default 0; set 1 only on Mac to allow POST /api/ops/jobs. "
                "VPS must stay 0."
            ),
            "hubs": "GET /api/ops/data-hub and /api/ops/model-hub are read-only (Phase 3).",
        },
    }
