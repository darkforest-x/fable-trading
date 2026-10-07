from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.discord_channels import register_web_channel
from yoyo.copier.ingest import ingest_message
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database

router = APIRouter(prefix="/messages", tags=["messages"])

DISCORD_EPOCH_MS = 1420070400000
MAX_SIGNAL_AGE_MIN = 10.0  # override with the `max_signal_age_min` setting


def discord_message_age_min(external_id: str, now_ms: float | None = None) -> float | None:
    """Age of a Discord message from its snowflake id (the last number in external_id).

    After a network outage, sleep or tab reload, Discord replays everything missed
    and the extension submits it as new. Without this, an hours-old entry signal
    would open a position at today's price.
    """
    found = re.findall(r"\d{15,25}", external_id or "")
    if not found:
        return None
    sent_ms = (int(found[-1]) >> 22) + DISCORD_EPOCH_MS
    return ((now_ms if now_ms is not None else time.time() * 1000) - sent_ms) / 60000


def max_signal_age_min(db: Database) -> float:
    try:
        return float(db.get_setting("max_signal_age_min") or MAX_SIGNAL_AGE_MIN)
    except (TypeError, ValueError):
        return MAX_SIGNAL_AGE_MIN


class IngestBody(BaseModel):
    content: str
    author: str = "discord-web"
    channel_id: str = "discord-web"
    channel_name: str = "Discord Web"
    guild_id: str = ""
    external_id: str
    source: str = "discord-web"
    attachments: list[dict] = Field(default_factory=list)


@router.post("/ingest")
def ingest(body: IngestBody, db: Database = Depends(get_db)):
    if db.message_exists(body.external_id):
        return {"ok": True, "duplicate": True}
    registered = False
    source = body.source
    age = None
    if source.startswith("discord-web"):
        registered = register_web_channel(db, body.channel_id, body.guild_id, body.channel_name)
        age = discord_message_age_min(body.external_id)
        if age is not None and age > max_signal_age_min(db) and not source.endswith("_recovery"):
            # "_recovery" sources are stored, parsed and notified, never executed.
            source = "discord-web-late_recovery"
    message_id = ingest_message(
        db,
        ActionRouter(db),
        external_id=body.external_id,
        channel_id=body.channel_id,
        author=body.author,
        content=body.content,
        attachments=body.attachments,
        source=source,
    )
    return {"ok": True, "duplicate": False, "message_id": message_id, "channel_registered": registered,
            "late": source == "discord-web-late_recovery", "age_min": None if age is None else round(age, 1)}


@router.get("")
@router.get("/")
def list_messages(
    page: int = 1,
    per_page: int = 50,
    status: str | None = None,
    intent: str | None = None,
    channel_id: str | None = None,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    rows, total = db.list_messages(
        page=page, per_page=per_page, status=status, intent=intent, channel_id=channel_id
    )
    return {"items": rows, "total": total, "page": page, "per_page": per_page}


@router.get("/{message_id}")
def message_detail(
    message_id: int,
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    return db.get_message(message_id) or {}
