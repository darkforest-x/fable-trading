from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class MessageRecord:
    id: int | None
    discord_message_id: str
    channel_id: str
    author: str
    content: str
    attachments_json: str
    created_at: str
    status: str  # pending, analyzed, skipped, executed, error


@dataclass
class SignalRecord:
    id: int | None
    message_id: int
    intent: str
    should_act: bool
    confidence: float
    ai_json: str
    reject_reason: str | None
    status: str


@dataclass
class AuditRecord:
    id: int | None
    message_id: int | None
    event: str
    detail: str
    payload_json: str
    created_at: str


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
