from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/control", tags=["control"])


class ToggleBody(BaseModel):
    enabled: bool


@router.post("/kill-switch")
def kill_switch(
    body: ToggleBody,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    db.set_setting("kill_switch", "true" if body.enabled else "false")
    db.audit("kill_switch", "on" if body.enabled else "off")
    return {"kill_switch": body.enabled}


@router.post("/dry-run")
def dry_run(
    body: ToggleBody,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    db.set_setting("dry_run", "true" if body.enabled else "false")
    db.audit("dry_run", "on" if body.enabled else "off")
    return {"dry_run": body.enabled}
