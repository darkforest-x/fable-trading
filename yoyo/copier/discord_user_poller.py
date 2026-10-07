"""
用户 Token + REST 轮询监听（实验性，违反 Discord ToS）。

safe_mode 下使用保守频率、抖动、429 退避，降低封号概率（无法 100% 保证）。
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

from yoyo.copier.config import ROOT, app_config, env
from yoyo.copier.discord_user_listener import user_listener_enabled
from yoyo.copier.monitor_switches import enabled_channel_ids, feature_enabled
from yoyo.copier.pinned_channels import resolve_pinned_channel_ids
from yoyo.copier.router.action_router import ActionRouter
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v9"
# 浏览器风格 UA，避免明显 bot 特征
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _poll_settings(db: Database) -> dict[str, float | int]:
    from yoyo.copier.runtime_config import get_user_listener

    ul = get_user_listener(db)
    round_min = float(ul["poll_interval_min_sec"])
    round_max = float(ul["poll_interval_max_sec"])
    gap_min = float(ul["channel_gap_min_sec"])
    gap_max = float(ul["channel_gap_max_sec"])
    if ul.get("safe_mode"):
        round_min = max(round_min, 30.0)
        round_max = max(round_max, round_min)
        if round_max > 300.0:
            round_max = 300.0
    return {
        "round_min": round_min,
        "round_max": round_max,
        "gap_min": gap_min,
        "gap_max": max(gap_max, gap_min),
        "limit": max(1, min(int(ul["poll_limit"]), 10)),
        "max_channels": max(1, int(ul["max_channels"])),
    }


def _random_delay(db: Database, lo: float, hi: float, label: str) -> float:
    delay = random.uniform(lo, hi)
    logger.info("风控等待 %s: %.0f 秒", label, delay)
    time.sleep(delay)
    return delay


def _sleep_between_rounds(db: Database) -> None:
    s = _poll_settings(db)
    _random_delay(db, float(s["round_min"]), float(s["round_max"]), "下轮轮询")


def _sleep_between_channels(db: Database) -> None:
    s = _poll_settings(db)
    _random_delay(db, float(s["gap_min"]), float(s["gap_max"]), "频道间隔")


def _headers() -> dict[str, str]:
    return {
        "Authorization": env.discord_user_token,
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def _fetch_messages(
    client: httpx.Client, channel_id: str, limit: int
) -> tuple[list[dict[str, Any]], int | None]:
    """Returns (messages, retry_after_seconds on 429)."""
    url = f"{API_BASE}/channels/{channel_id}/messages"
    r = client.get(url, headers=_headers(), params={"limit": limit})
    if r.status_code == 401:
        logger.error("用户 Token 无效或已过期，请更新 .env")
        return [], None
    if r.status_code == 429:
        retry = int(r.headers.get("Retry-After", "60"))
        logger.warning("Discord 限速 429，退避 %ss", retry)
        return [], retry
    r.raise_for_status()
    data = r.json()
    return (data if isinstance(data, list) else []), None


def _message_to_payload(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": m.get("id"),
        "channel_id": m.get("channel_id"),
        "content": m.get("content", ""),
        "author": m.get("author", {}),
        "attachments": m.get("attachments", []),
    }


def _process_sync(db: Database, router: ActionRouter, payload: dict[str, Any]) -> None:
    from yoyo.copier.discord_user_listener import _process_sync as inner

    inner(db, router, payload)


def start_user_poller(db: Database) -> None:
    """Blocking poll loop — run in daemon thread."""
    if not user_listener_enabled():
        return

    settings = _poll_settings(db)
    router = ActionRouter(db)
    ul = app_config.user_listener

    if ul.use_pinned_list:
        pinned_path = ROOT / ul.pinned_config_path
        channels, name_map = resolve_pinned_channel_ids(
            pinned_path, allow_network=True, db=db
        )
        if not channels:
            channels, name_map = resolve_pinned_channel_ids(
                pinned_path, allow_network=False, db=db
            )
            if channels:
                logger.warning("Discord 限速，使用本地频道 ID 缓存继续轮询")
        db.audit("pinned_channels", str(name_map))
        if not channels:
            logger.error(
                "置顶频道列表为空（可能 Discord 429）。"
                "等待几分钟后重启，或: PYTHONPATH=. python -m src.tools.sync_pinned"
            )
            return
    else:
        channels = list(ul.channel_ids)

    channels = channels[: int(settings["max_channels"])]
    if len(channels) > int(settings["max_channels"]):
        logger.warning("safe_mode 仅监听前 %s 个置顶频道", len(channels))

    seen: set[str] = set()
    bootstrapped = False
    logger.info(
        "【随机轮询】频道 %s 个 | 每轮间隔 %s~%ss | 频道间隔 %s~%ss | limit=%s",
        len(channels),
        int(settings["round_min"]),
        int(settings["round_max"]),
        int(settings["gap_min"]),
        int(settings["gap_max"]),
        settings["limit"],
    )
    db.audit("user_poller_start", ",".join(channels))

    with httpx.Client(timeout=30) as client:
        while True:
            try:
                if not feature_enabled(db, "user_poll"):
                    logger.debug("用户轮询已关闭（开关），等待 60s")
                    time.sleep(60)
                    continue
                if not feature_enabled(db, "ingest_messages"):
                    time.sleep(60)
                    continue

                active = enabled_channel_ids(db, channels)
                if not active:
                    logger.warning("无已启用的监控频道，等待 60s（请在管理台「控制中心」开启）")
                    time.sleep(60)
                    continue

                round_channels = list(active)
                random.shuffle(round_channels)

                if not bootstrapped:
                    for i, ch_id in enumerate(round_channels):
                        messages, retry = _fetch_messages(
                            client, ch_id, int(settings["limit"])
                        )
                        if retry:
                            time.sleep(retry)
                            continue
                        for m in messages:
                            if m.get("id"):
                                seen.add(str(m["id"]))
                        if i < len(round_channels) - 1:
                            _sleep_between_channels(db)
                    bootstrapped = True
                    logger.info("轮询已就绪，仅处理新消息")
                    _sleep_between_rounds(db)
                    continue

                max_retry = 0
                for i, ch_id in enumerate(round_channels):
                    messages, retry = _fetch_messages(
                        client, ch_id, int(settings["limit"])
                    )
                    if retry:
                        max_retry = max(max_retry, retry)
                        continue
                    for m in reversed(messages):
                        mid = str(m.get("id", ""))
                        if not mid or mid in seen:
                            continue
                        author = m.get("author") or {}
                        if author.get("bot"):
                            continue
                        seen.add(mid)
                        if len(seen) > 5000:
                            seen.clear()
                        logger.info(
                            "轮询收到消息 ch=%s author=%s",
                            ch_id,
                            author.get("username"),
                        )
                        _process_sync(db, router, _message_to_payload(m))
                    if i < len(round_channels) - 1:
                        _sleep_between_channels(db)

                if max_retry:
                    time.sleep(max_retry)
                else:
                    _sleep_between_rounds(db)
            except httpx.HTTPStatusError as e:
                logger.error("Discord API 错误: %s", e.response.status_code)
                _sleep_between_rounds(db)
            except Exception as e:
                logger.exception("轮询异常: %s", e)
                _sleep_between_rounds(db)
