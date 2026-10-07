from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from yoyo.copier.config import ROOT, app_config

logger = logging.getLogger(__name__)

DEFAULT_PATH = ROOT / "config" / "telegram_channels.yaml"


def load_telegram_channels_config(path: Path | None = None) -> dict[str, Any]:
    path = path or Path(app_config.telegram.channels_config_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return {"channels": []}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"channels": []}


def save_telegram_channels_yaml(channels: list[dict[str, str]], path: Path | None = None) -> None:
    path = path or Path(app_config.telegram.channels_config_path)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "channels": [
            {
                "name": ch.get("name", ""),
                "username": ch.get("username", ""),
                "chat_id": ch.get("chat_id", ""),
            }
            for ch in channels
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(payload, f, allow_unicode=True, default_flow_style=False)


def list_configured_channels(path: Path | None = None) -> list[dict[str, str]]:
    cfg = load_telegram_channels_config(path)
    out: list[dict[str, str]] = []
    for item in cfg.get("channels") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or "").strip()
        username = str(item.get("username") or "").strip().lstrip("@")
        chat_id = str(item.get("chat_id") or "").strip()
        if not name and username:
            name = username
        if not name and chat_id:
            name = chat_id
        if username or chat_id:
            out.append({"name": name, "username": username, "chat_id": chat_id})
    return out


async def resolve_telegram_entities(client: Any, path: Path | None = None) -> tuple[list[Any], dict[str, str]]:
    """
    解析配置中的频道/群为 Telethon entity。
    返回 (entities, name_map: channel_id_str -> display_name)
    """
    configured = list_configured_channels(path)
    entities: list[Any] = []
    name_map: dict[str, str] = {}
    for ch in configured:
        entity = None
        if ch.get("chat_id"):
            try:
                entity = await client.get_entity(int(ch["chat_id"]))
            except (ValueError, TypeError):
                entity = await client.get_entity(ch["chat_id"])
        elif ch.get("username"):
            entity = await client.get_entity(ch["username"])
        if not entity:
            logger.warning("无法解析 Telegram: %s", ch)
            continue
        from telethon import utils

        cid = str(utils.get_peer_id(entity))
        entities.append(entity)
        name_map[cid] = ch["name"]
        if ch.get("chat_id") and str(ch["chat_id"]) != cid:
            name_map[str(ch["chat_id"])] = ch["name"]
        logger.info("Telegram 已加入监听: %s (%s)", ch["name"], cid)
    return entities, name_map
