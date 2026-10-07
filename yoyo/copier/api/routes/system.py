from __future__ import annotations

from fastapi import APIRouter, Depends

from yoyo.copier.api.auth import verify_token
from yoyo.copier.api.deps import get_db
from yoyo.copier.config import app_config, env
from yoyo.copier.exchange import exchange_label, exchange_mode_label
from yoyo.copier.store.sqlite import Database
from yoyo.copier.telegram_channels import list_configured_channels
from yoyo.copier.telegram_listener import telegram_listener_enabled

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/monitor")
def monitor_info(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    tg_list = list_configured_channels()
    pinned_resolved = [
        {
            "name": ch.get("name") or ch.get("username") or "—",
            "id": ch.get("chat_id") or "",
            "username": ch.get("username") or "",
        }
        for ch in tg_list
    ]
    recent, _ = db.list_messages(page=1, per_page=10)

    return {
        "telegram_listener_enabled": telegram_listener_enabled(),
        "telegram_configured": bool(env.telegram_api_id and env.telegram_api_hash),
        "telegram_enabled": app_config.telegram.enabled,
        "safe_mode": True,
        "poll_interval_min_sec": 0,
        "poll_interval_max_sec": 0,
        "channel_gap_min_sec": 0,
        "channel_gap_max_sec": 0,
        "poll_limit": 0,
        "guild_id": "",
        "pinned_channel_names": [c["name"] for c in tg_list],
        "pinned_channels": pinned_resolved,
        "discord_signal_channel_id": "",
        "deepseek_model": app_config.deepseek.model,
        "exchange": exchange_label(db),
        "exchange_mode": exchange_mode_label(db),
        "okx_demo": env.okx_demo,
        "recent_messages": recent,
        "channels_from_cache": True,
        "user_listener_enabled": telegram_listener_enabled(),
    }


@router.post("/refresh-channels")
def refresh_channels(
    db: Database = Depends(get_db),
    _: str = Depends(verify_token),
):
    """Telegram 频道 ID 在 yaml 中配置，无需远程刷新；仅重新加载列表。"""
    del db
    tg_list = list_configured_channels()
    return {
        "ok": True,
        "channels": [
            {
                "name": ch.get("name") or "",
                "id": ch.get("chat_id") or "",
                "username": ch.get("username") or "",
            }
            for ch in tg_list
        ],
    }
