"""Which Discord server channels the copier monitors, and where their trades go.

The extension only sees channels open in a Chrome tab and reports their id and
tab title. Two modes:

* **Watch list** (owner, 2026-10-07: "换成这些" + 模拟实盘): the owner names the
  channels. A reported channel whose title matches a listed name is switched on
  and routed to the list's route (``paper``); any other channel is ignored --
  an open general-chat tab must not feed DeepSeek and Telegram.
* **No list**: a new server channel is registered monitor-only (stored, parsed,
  notified, not traded), because an unrouted channel would otherwise fall back
  to the default exchange (OKX).

Either way a channel the owner switched off stays off, and direct messages never
get here (the extension sends no guild id for them).
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

from yoyo.copier.exchange import CHANNEL_EXCHANGE_MAP_KEY, get_channel_exchange_map
from yoyo.copier.monitor_switches import SETTING_KEY as SWITCHES_KEY
from yoyo.copier.store.sqlite import Database

NAMES_KEY = "discord_channel_names"
BLOCKED_KEY = "blocked_trade_channels"
WATCHLIST_KEY = "discord_watchlist"
ROUTE_BACKUP_KEY = "exchange_channel_map_before_watchlist"
SNOWFLAKE = re.compile(r"\d{15,25}")
# Discord tab titles: "(3) Discord | #channel | Server" or "#channel | Server - Discord".
TITLE_NOISE = re.compile(r"^\(\d+\)\s*|\s*[|-]\s*Discord\s*$|^Discord\s*\|\s*", re.IGNORECASE)


def _json(db: Database, key: str, default: Any) -> Any:
    try:
        value = json.loads(db.get_setting(key) or "null")
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _save(db: Database, key: str, value: Any) -> None:
    db.set_setting(key, json.dumps(value, ensure_ascii=False, sort_keys=True))


def normalize(name: str) -> str:
    """Letters and digits only (CJK included): '#🏹｜michele行情分析' -> 'michele行情分析'."""
    return re.sub(r"[\W_]+", "", str(name or "").lower())


def channel_names(db: Database) -> dict[str, str]:
    return {str(k): str(v) for k, v in _json(db, NAMES_KEY, {}).items() if v}


def clean_title(title: str) -> str:
    text = str(title or "").strip()
    for _ in range(3):
        text = TITLE_NOISE.sub("", text).strip()
    return text[:80]


def watchlist(db: Database) -> dict[str, Any]:
    return _json(db, WATCHLIST_KEY, {})


def watched_name(db: Database, title: str, guild_id: str) -> Optional[str]:
    """The listed name this tab title belongs to, if the guild and a title segment match."""
    wl = watchlist(db)
    if not wl.get("names") or str(wl.get("guild_id") or guild_id) != str(guild_id):
        return None
    listed = {normalize(n): n for n in wl["names"]}
    for segment in clean_title(title).split("|"):
        hit = listed.get(normalize(segment))
        if hit:
            return hit
    return None


def _switches(db: Database) -> dict[str, Any]:
    switches = _json(db, SWITCHES_KEY, {})
    if not isinstance(switches.get("channels"), dict):
        switches["channels"] = {}
    return switches


def _set_blocked(db: Database, channel_id: str, blocked: bool) -> None:
    current = _json(db, BLOCKED_KEY, [])
    ids = {str(k) for k, v in current.items() if v} if isinstance(current, dict) else {str(c) for c in current}
    ids = ids | {channel_id} if blocked else ids - {channel_id}
    _save(db, BLOCKED_KEY, sorted(ids))


def trade_blocked_channels(db: Database) -> set[str]:
    blocked = _json(db, BLOCKED_KEY, [])
    if isinstance(blocked, dict):
        return {str(k) for k, v in blocked.items() if v}
    return {str(c) for c in blocked}


def _route(db: Database, channel_id: str, route: str) -> None:
    routes = get_channel_exchange_map(db)
    routes[channel_id] = route
    _save(db, CHANNEL_EXCHANGE_MAP_KEY, routes)


def register_web_channel(db: Database, channel_id: str, guild_id: str, title: str = "") -> bool:
    """Return True when the channel was newly switched on."""
    channel_id, guild_id = str(channel_id or ""), str(guild_id or "")
    if not (SNOWFLAKE.fullmatch(channel_id) and SNOWFLAKE.fullmatch(guild_id)):
        return False
    names = channel_names(db)
    wl = watchlist(db)
    hit = watched_name(db, title, guild_id) if wl.get("names") else None
    display = hit or clean_title(title)
    if display and names.get(channel_id) != display:
        names[channel_id] = display
        _save(db, NAMES_KEY, names)

    switches = _switches(db)
    channels = switches["channels"]
    if channel_id in channels:
        return False
    if wl.get("names"):
        if not hit:
            return False  # not on the owner's list: remember the name, do not monitor
        channels[channel_id] = True
        _save(db, SWITCHES_KEY, switches)
        _route(db, channel_id, str(wl.get("route") or "paper"))
        _set_blocked(db, channel_id, False)
        db.audit("channel_auto_registered", hit, payload={"channel_id": channel_id, "guild_id": guild_id,
                                                          "name": hit, "route": wl.get("route") or "paper"})
        return True
    if not channels:
        # No allow-list yet means every channel is already monitored; creating one
        # here would silently switch all the others off. Only keep trading safe.
        if channel_id not in get_channel_exchange_map(db):
            _set_blocked(db, channel_id, True)
        return False
    channels[channel_id] = True
    _save(db, SWITCHES_KEY, switches)
    routed = channel_id in get_channel_exchange_map(db)
    if not routed:
        _set_blocked(db, channel_id, True)
    db.audit("channel_auto_registered", display or channel_id,
             payload={"channel_id": channel_id, "guild_id": guild_id, "name": display,
                      "monitor": True, "trading": "routed" if routed else "blocked"})
    return True


def apply_watchlist(db: Database, guild_id: str, names: Iterable[str], known: dict[str, str],
                    route: str = "paper") -> dict[str, Any]:
    """Replace the monitored set with ``names``; ids already known are switched on now.

    ``known`` maps channel id -> listed name for channels whose id is on record.
    Every other channel in the switch map is switched off; its route is kept so the
    backup can restore it. The routes in force before the first list are saved once.
    """
    names = [str(n) for n in names]
    listed = {normalize(n) for n in names}
    unknown = [n for n in known.values() if normalize(n) not in listed]
    if unknown:
        raise ValueError(f"known ids name channels missing from the list: {unknown}")
    if db.get_setting(ROUTE_BACKUP_KEY) is None:
        _save(db, ROUTE_BACKUP_KEY, get_channel_exchange_map(db))
    _save(db, WATCHLIST_KEY, {"guild_id": str(guild_id), "names": names, "route": route})
    switches = _switches(db)
    for cid in list(switches["channels"]):
        switches["channels"][cid] = cid in known
    for cid in known:
        switches["channels"][cid] = True
    _save(db, SWITCHES_KEY, switches)
    current_names = channel_names(db)
    for cid, name in known.items():
        _route(db, cid, route)
        _set_blocked(db, cid, False)
        current_names[cid] = name
    _save(db, NAMES_KEY, current_names)
    result = {"guild_id": str(guild_id), "names": names, "route": route, "enabled_now": sorted(known),
              "switched_off": sorted(c for c, on in switches["channels"].items() if not on)}
    db.audit("watchlist_applied", f"{len(names)} names, route={route}", payload=result)
    return result
