from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.monitor_switches import get_switches, save_switches
from yoyo.copier.store.sqlite import Database
from yoyo.copier.discord_channels import watchlist, channel_names

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


@router.get("/focus")
def focus(db: Database = Depends(get_db), _: str = Depends(verify_token)):
    """Evidence about the selected channel; receipt time is not message time."""
    import json
    import time
    from yoyo.copier.monitor_switches import channel_enabled
    from yoyo.copier.exchange import selected_exchange
    selected = watchlist(db)
    ids = selected.get("channel_ids", [])
    if len(ids) != 1:
        return {"configured": False, "reason": "尚未设置唯一频道"}
    cid = ids[0]
    with db._connect() as conn:
        counts = {r[0]: r[1] for r in conn.execute("SELECT status,COUNT(*) FROM messages WHERE channel_id=? GROUP BY status", (cid,))}
        intents = {r[0]: r[1] for r in conn.execute("SELECT s.intent,COUNT(DISTINCT m.id) FROM messages m JOIN signals s ON s.message_id=m.id WHERE m.channel_id=? GROUP BY s.intent", (cid,))}
        latest = conn.execute("SELECT created_at,discord_message_id FROM messages WHERE channel_id=? ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        reasons = [dict(r) for r in conn.execute("SELECT a.event,a.detail,COUNT(DISTINCT a.message_id) AS n FROM audit_logs a JOIN messages m ON m.id=a.message_id WHERE m.channel_id=? AND a.event IN ('risk_rejected','execution_skipped','recovery_signal_skipped','execution_error','message_revision') GROUP BY a.event,a.detail ORDER BY n DESC LIMIT 12", (cid,))]
        old_channels = conn.execute("SELECT COUNT(DISTINCT channel_id) FROM messages WHERE channel_id!=?", (cid,)).fetchone()[0]
    try:
        heartbeat = json.loads(db.get_setting("discord_heartbeat:" + cid) or "null")
    except ValueError:
        heartbeat = None
    now = int(time.time() * 1000)
    return {"configured": True, "channel_id": cid, "name": channel_names(db).get(cid, cid),
            "url": f"https://discord.com/channels/{selected['guild_id']}/{cid}",
            "enabled": channel_enabled(db, cid), "route": selected_exchange(db, cid),
            "dry_run": db.get_setting("dry_run") == "true", "kill_switch": db.get_setting("kill_switch") == "true",
            "counts": counts, "intents": intents, "total": sum(counts.values()), "last_received": dict(latest) if latest else None,
            "heartbeat": heartbeat, "collector_online": bool(heartbeat and now - heartbeat["received_ms"] < 120000),
            "reasons": reasons, "archived_channels": old_channels,
            "author_performance": {"win_rate": None, "sum_r": None,
               "reason": "原始消息未形成逐笔核验的完整成交、分批退出与初始风险记录，不能把信号目标或本地模拟结果算作博主实际业绩。"}}
