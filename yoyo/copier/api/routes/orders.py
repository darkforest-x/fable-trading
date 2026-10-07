from __future__ import annotations

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db, get_okx
from yoyo.copier.okx_cache import get_positions_cached
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("")
@router.get("/")
def orders(
    limit: int = 100,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return {"items": db.list_orders(limit=limit)}


@router.get("/positions")
def positions(
    _: str = Depends(verify_token),
):
    return {"items": get_positions_cached(get_okx(), force=True)}


@router.post("/sync")
def sync_positions(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    positions = get_positions_cached(get_okx(), force=True)
    db.audit("position_sync", f"positions={len(positions)}")
    return {"ok": True, "items": positions}
