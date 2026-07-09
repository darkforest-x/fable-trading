"""Feature flags for P2.5 ops console (env only; never hardcode secrets)."""
from __future__ import annotations

import os


def _truthy(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def auth_mode() -> str:
    """off | token | nginx (nginx = document that reverse proxy auth is external)."""
    mode = os.environ.get("OPS_AUTH_MODE", "off").strip().lower()
    if mode not in {"off", "token", "nginx"}:
        return "off"
    return mode


def api_token() -> str:
    return os.environ.get("OPS_API_TOKEN", "").strip()


def require_auth_for_ops() -> bool:
    """When token mode and token is set, ops API requires Bearer.

    OPS_REQUIRE_AUTH=1 forces ops auth even if token empty (then all requests 503/401).
    nginx mode assumes edge auth; app-layer ops still open unless REQUIRE_AUTH=1 + token.
    """
    mode = auth_mode()
    if mode == "token":
        return True
    if _truthy("OPS_REQUIRE_AUTH", "0"):
        return True
    return False


def executor_enabled() -> bool:
    return _truthy("ENABLE_JOB_EXECUTOR", "0")


def ops_status_payload() -> dict:
    token = api_token()
    mode = auth_mode()
    return {
        "auth_mode": mode,
        "ops_auth_required": require_auth_for_ops(),
        "token_configured": bool(token),
        "executor_enabled": executor_enabled(),
        "phase": "0+1+2",
        "notes": {
            "auth": "Set OPS_AUTH_MODE=token and OPS_API_TOKEN=<secret> to protect /api/ops/*",
            "executor": (
                "ENABLE_JOB_EXECUTOR default 0; set 1 only on Mac to allow POST /api/ops/jobs. "
                "VPS must stay 0."
            ),
        },
    }
