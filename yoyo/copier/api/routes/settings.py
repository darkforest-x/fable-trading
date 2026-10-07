from __future__ import annotations

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.runtime_config import apply_settings_update, get_full_config
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def settings(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return get_full_config(db)


@router.put("")
def update_settings(
    data: dict,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    apply_settings_update(db, data)
    return get_full_config(db)
