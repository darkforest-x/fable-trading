from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from yoyo.copier.config import ROOT, env

logger = logging.getLogger(__name__)

PINNED_FILE = ROOT / "config" / "pinned_channels.yaml"
CACHE_FILE = ROOT / "data" / "pinned_channels_cache.json"
API_BASE = "https://discord.com/api/v9"

# 内存 + 磁盘缓存，避免管理台刷新反复打 Discord guild/channels
CACHE_TTL_SEC = 3600

_mem: tuple[float, list[str], dict[str, str]] | None = None


def load_pinned_config(path: Path | None = None, db: Any = None) -> dict[str, Any]:
    if db is not None:
        from yoyo.copier.runtime_config import load_pinned_from_db

        cached = load_pinned_from_db(db)
        if cached:
            return cached
    path = path or PINNED_FILE
    if not path.exists():
        return {"guild_id": "", "channels": []}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _headers() -> dict[str, str]:
    return {
        "Authorization": env.discord_user_token,
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }


def _load_disk_cache() -> tuple[list[str], dict[str, str]] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        ids = [str(x) for x in data.get("ids", [])]
        mapping = {str(k): str(v) for k, v in (data.get("mapping") or {}).items()}
        if ids and mapping:
            return ids, mapping
    except Exception as e:
        logger.warning("读取频道缓存失败: %s", e)
    return None


def _save_disk_cache(ids: list[str], mapping: dict[str, str]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(
            {
                "ids": ids,
                "mapping": mapping,
                "cached_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _from_cache() -> tuple[list[str], dict[str, str]] | None:
    global _mem
    if _mem and (time.time() - _mem[0]) < CACHE_TTL_SEC:
        return _mem[1], _mem[2]
    disk = _load_disk_cache()
    if disk:
        _mem = (time.time(), disk[0], disk[1])
        return disk
    return None


def _store_cache(ids: list[str], mapping: dict[str, str]) -> None:
    global _mem
    _mem = (time.time(), ids, mapping)
    _save_disk_cache(ids, mapping)


def resolve_pinned_channel_ids(
    path: Path | None = None,
    *,
    allow_network: bool = True,
    db: Any = None,
) -> tuple[list[str], dict[str, str]]:
    """
    按频道名称解析 ID。默认使用缓存，不重复请求 Discord。
    allow_network=False：仅读缓存（管理台 API 必须用此模式）。
    allow_network=True：缓存过期或 force 时请求 Discord；429 时回退缓存。
    """
    cached = _from_cache()
    if not allow_network:
        if cached:
            return cached
        logger.warning("无频道缓存且禁止联网解析，请重启主进程或执行 sync_pinned")
        return [], {}

    if cached and _mem and (time.time() - _mem[0]) < CACHE_TTL_SEC:
        return cached

    cfg = load_pinned_config(path, db=db)
    guild_id = str(cfg.get("guild_id", ""))
    names = [str(n).strip().lstrip("#") for n in cfg.get("channels", []) if n]
    if not guild_id or not names or not env.discord_user_token:
        return cached or ([], {})

    url = f"{API_BASE}/guilds/{guild_id}/channels"
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, headers=_headers())
        if r.status_code == 429:
            retry = int(r.headers.get("Retry-After", "120"))
            logger.warning(
                "Discord 429 限速，%ss 内使用本地缓存（请勿频繁刷新管理台）",
                retry,
            )
            if cached:
                return cached
            return [], {}
        if r.status_code != 200:
            logger.error("无法拉取服务器频道列表: %s", r.status_code)
            if cached:
                return cached
            return [], {}
        all_ch = r.json()
    except Exception as e:
        logger.exception("拉取频道列表失败: %s", e)
        if cached:
            return cached
        return [], {}

    by_name: dict[str, str] = {}
    for c in all_ch:
        if c.get("type") not in (0, 5):
            continue
        name = (c.get("name") or "").strip()
        by_name[name] = str(c["id"])

    resolved: list[str] = []
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for want in names:
        cid = by_name.get(want)
        if not cid:
            for gname, gid in by_name.items():
                if want in gname or gname.endswith(want) or want.replace("｜", "|") in gname:
                    cid = gid
                    break
        if cid:
            if cid not in resolved:
                resolved.append(cid)
            mapping[want] = cid
        else:
            missing.append(want)

    if missing:
        logger.warning("未找到置顶频道（请检查名称）: %s", missing)
    if resolved:
        logger.info("已解析置顶频道 %s 个（已写入缓存）", len(resolved))
        _store_cache(resolved, mapping)
    return resolved, mapping
