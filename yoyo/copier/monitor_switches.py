"""运行时监控与功能开关（存 SQLite，管理台可改，轮询循环会读取）。"""

from __future__ import annotations

import json
import logging
from typing import Any

from yoyo.copier.config import ROOT, app_config
from yoyo.copier.telegram_channels import list_configured_channels
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)

SETTING_KEY = "monitor_switches"

# 功能开关说明（管理台展示用）
FEATURE_META: dict[str, dict[str, str]] = {
    "telegram_listen": {
        "label": "Telegram 社群监听",
        "desc": "Telethon 实时接收配置的 TG 频道/群消息",
    },
    "user_poll": {
        "label": "Discord 轮询（已弃用）",
        "desc": "旧版 Discord REST 轮询，默认关闭",
    },
    "bot_listener": {
        "label": "Discord Bot（已弃用）",
        "desc": "旧版 Discord Bot 监听，默认关闭",
    },
    "ai_parse": {
        "label": "DeepSeek 意图解析",
        "desc": "关闭后仅入库消息，不调用 AI",
    },
    "risk_check": {
        "label": "风控校验",
        "desc": "白名单、仓位、Kill Switch 等规则",
    },
    "okx_execute": {
        "label": "交易所下单执行",
        "desc": "关闭后不开单（与 dry_run 模拟可同时存在）",
    },
    "telegram_notify": {
        "label": "Telegram 通知",
        "desc": "开单、风控拒绝、执行失败等关键事件推送到 TG 群",
    },
    "telegram_forward_raw": {
        "label": "Telegram 原文转发",
        "desc": "将监听频道原始消息全文转发到 TG；关闭后只推送交易结果与异常",
    },
    "ingest_messages": {
        "label": "消息入库",
        "desc": "关闭后忽略一切新消息（总闸）",
    },
}

DEFAULT_FEATURES: dict[str, bool] = {
    "telegram_listen": True,
    "user_poll": False,
    "bot_listener": False,
    "ai_parse": True,
    "risk_check": True,
    "okx_execute": True,
    "telegram_notify": True,
    "telegram_forward_raw": False,
    "ingest_messages": True,
}


def _default_channels(db: Database | None = None) -> dict[str, bool]:
    del db
    ids: list[str] = []
    for ch in list_configured_channels():
        if ch.get("chat_id"):
            ids.append(str(ch["chat_id"]))
    return {cid: True for cid in ids}


def merge_channel_ids(db: Database, channel_ids: list[str]) -> None:
    raw = _load_raw(db)
    ch_map = dict(raw.get("channels") or {})
    if not ch_map:
        ch_map = _default_channels(db)
    for cid in channel_ids:
        ch_map.setdefault(str(cid), True)
    feat = {**DEFAULT_FEATURES, **(raw.get("features") or {})}
    db.set_setting(SETTING_KEY, json.dumps({"features": feat, "channels": ch_map}))


def _load_raw(db: Database) -> dict[str, Any]:
    raw = db.get_setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def get_switches(db: Database) -> dict[str, Any]:
    raw = _load_raw(db)
    features = {**DEFAULT_FEATURES, **(raw.get("features") or {})}
    channels = raw.get("channels") or {}
    if not channels:
        channels = _default_channels(db)
    else:
        for cid in _default_channels(db):
            channels.setdefault(cid, True)
    names = _channel_names(db)
    channel_list = []
    for cid, enabled in sorted(channels.items(), key=lambda x: names.get(x[0], x[0])):
        channel_list.append(
            {
                "channel_id": cid,
                "channel_name": names.get(cid, cid[:12] + "…"),
                "enabled": bool(enabled),
            }
        )
    return {
        "features": features,
        "feature_meta": FEATURE_META,
        "channels": channel_list,
    }


def _channel_names(db: Database | None = None) -> dict[str, str]:
    del db
    out: dict[str, str] = {}
    for ch in list_configured_channels():
        cid = ch.get("chat_id") or ""
        if cid:
            out[cid] = ch.get("name") or cid
    return out


def save_switches(
    db: Database,
    features: dict[str, bool] | None = None,
    channels: dict[str, bool] | None = None,
) -> dict[str, Any]:
    current = get_switches(db)
    feat = dict(current["features"])
    if features:
        feat.update(features)
    ch_map = {c["channel_id"]: c["enabled"] for c in current["channels"]}
    if channels:
        ch_map.update({str(k): bool(v) for k, v in channels.items()})
    payload = {"features": feat, "channels": ch_map}
    db.set_setting(SETTING_KEY, json.dumps(payload, ensure_ascii=False))
    db.audit("monitor_switches", json.dumps(payload)[:500])
    logger.info("监控开关已更新")
    return get_switches(db)


def feature_enabled(db: Database, key: str, default: bool = True) -> bool:
    raw = _load_raw(db)
    features = raw.get("features") or {}
    if key not in features:
        return DEFAULT_FEATURES.get(key, default)
    return bool(features[key])


def channel_enabled(db: Database, channel_id: str) -> bool:
    raw = _load_raw(db)
    channels = raw.get("channels")
    if not channels:
        return True
    return bool(channels.get(str(channel_id), False))


def enabled_channel_ids(db: Database, candidates: list[str]) -> list[str]:
    if not feature_enabled(db, "telegram_listen", True):
        return []
    return [c for c in candidates if channel_enabled(db, c)]
