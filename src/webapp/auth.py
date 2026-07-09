"""P2.5 Phase 0: Bearer token auth for /api/ops/* (no secrets in repo)."""
from __future__ import annotations

from fastapi import HTTPException, Request

from src.webapp.ops_flags import api_token, require_auth_for_ops


def _extract_bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    # Alternate header for simpler static-page fetch
    alt = request.headers.get("x-ops-token") or request.headers.get("X-Ops-Token")
    if alt:
        return alt.strip()
    return None


def verify_ops_request(request: Request) -> None:
    """Raise 401/503 if ops auth is required and token is missing/wrong.

    Never logs the token value.
    """
    if not require_auth_for_ops():
        return
    expected = api_token()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="OPS_AUTH_MODE=token 但未配置 OPS_API_TOKEN；拒绝 ops API（防空门禁）。",
        )
    provided = _extract_bearer(request)
    if not provided or provided != expected:
        raise HTTPException(
            status_code=401,
            detail="需要有效 OPS_API_TOKEN（Authorization: Bearer … 或 X-Ops-Token）。",
        )
