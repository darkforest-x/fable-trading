"""Ops request gate — auth intentionally disabled (owner request 2026-07).

Historical: P2.5 used Bearer / X-Ops-Token for /api/ops/*. That gate is
fully removed so data-hub / model-hub / experiments load without a token.
"""
from __future__ import annotations

from fastapi import Request


def _extract_bearer(request: Request) -> str | None:
    """Kept for tests / future re-enable; unused while auth is off."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
    alt = request.headers.get("x-ops-token") or request.headers.get("X-Ops-Token")
    if alt:
        return alt.strip()
    return None


def verify_ops_request(request: Request) -> None:
    """No-op: OPS auth fully disabled. Always allows /api/ops/*."""
    return
