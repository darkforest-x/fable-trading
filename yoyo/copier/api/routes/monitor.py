from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.monitor_switches import get_switches, save_switches
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/monitor", tags=["monitor"])


class FeatureUpdate(BaseModel):
    features: dict[str, bool] | None = None
    channels: dict[str, bool] | None = None


class ChannelToggle(BaseModel):
    channel_id: str
    enabled: bool


@router.get("/switches")
def list_switches(db: Database = Depends(get_db), _: str = Depends(verify_token)):
    return get_switches(db)


@router.put("/switches")
def update_switches(
    body: FeatureUpdate,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return save_switches(db, features=body.features, channels=body.channels)


@router.post("/channels/toggle")
def toggle_channel(
    body: ChannelToggle,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return save_switches(db, channels={body.channel_id: body.enabled})
