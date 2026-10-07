from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yoyo.copier.config import ROOT
from yoyo.copier.exchange import (
    configured_routed_exchange_names,
    create_exchange_client,
    exchange_label,
    exchange_mode_label,
    get_channel_exchange_map,
    get_channel_leverage_map,
)
from yoyo.copier.notifications.telegram import CHANNEL_NAMES, send_telegram_notification
from yoyo.copier.runtime_config import get_full_config
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 300
STARTUP_NOTIFY_KEY = "health_startup_notified_at"
EXCHANGE_STATE_KEY_PREFIX = "health_exchange_state"
PUBLIC_URL_KEY = "health_public_url"
PUBLIC_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)
TUNNEL_URL_PATH = ROOT / "data" / "cloudflared-tunnel.url"
TUNNEL_LOG_PATHS = (
    ROOT / "logs" / "cloudflared-tunnel.err.log",
    ROOT / "logs" / "cloudflared-tunnel.log",
)


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def latest_tunnel_url(paths: tuple[Path, ...] = TUNNEL_LOG_PATHS) -> str:
    candidates: list[tuple[float, str]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            matches = PUBLIC_URL_PATTERN.findall(path.read_text(encoding="utf-8", errors="ignore"))
            if matches:
                candidates.append((path.stat().st_mtime, matches[-1]))
        except OSError:
            logger.exception("读取 Cloudflare Tunnel 日志失败: %s", path)
    if not candidates:
        return ""
    return max(candidates, key=lambda item: item[0])[1]


def sync_public_url(db: Database, *, notify_change: bool) -> str:
    url = latest_tunnel_url()
    if not url:
        return ""
    previous = db.get_setting(PUBLIC_URL_KEY) or ""
    TUNNEL_URL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not TUNNEL_URL_PATH.exists() or TUNNEL_URL_PATH.read_text().strip() != url:
            TUNNEL_URL_PATH.write_text(f"{url}\n", encoding="utf-8")
    except OSError:
        logger.exception("写入 Cloudflare Tunnel 地址失败")
    db.set_setting(PUBLIC_URL_KEY, url)
    if notify_change and previous and previous != url:
        asyncio.run(
            send_telegram_notification(
                db,
                "手机访问地址已更新",
                [
                    f"新地址: {url}",
                    "原因: Cloudflare 临时隧道重连后地址会变化。",
                ],
            )
        )
    return url


def exchange_status(db: Database, exchange_name: str) -> dict[str, Any]:
    client = create_exchange_client(exchange=exchange_name)
    result = client.health_check()
    if not result.get("ok"):
        return {
            "ok": False,
            "exchange": exchange_name,
            "label": exchange_label(exchange=exchange_name),
            "mode": exchange_mode_label(exchange=exchange_name),
            "error": str(result.get("error") or "unknown error")[:300],
        }
    try:
        positions = client.get_positions()
    except Exception as e:
        return {
            "ok": False,
            "exchange": exchange_name,
            "label": exchange_label(exchange=exchange_name),
            "mode": exchange_mode_label(exchange=exchange_name),
            "error": str(e)[:300],
        }
    balance = result.get("balance") or {}
    return {
        "ok": True,
        "exchange": exchange_name,
        "label": exchange_label(exchange=exchange_name),
        "mode": exchange_mode_label(exchange=exchange_name),
        "total_usdt": _as_float(balance.get("total_usdt")),
        "available_usdt": _as_float(balance.get("available_usdt")),
        "unrealized_pnl": _as_float(balance.get("unrealized_pnl")),
        "positions": len(positions),
    }


def _route_lines(db: Database) -> list[str]:
    exchange_map = get_channel_exchange_map(db)
    leverage_map = get_channel_leverage_map(db)
    lines: list[str] = []
    for channel_id, exchange_name in exchange_map.items():
        channel_name = CHANNEL_NAMES.get(channel_id, channel_id)
        leverage = leverage_map.get(channel_id, 1)
        lines.append(
            f"路由: {channel_name} -> {exchange_label(exchange=exchange_name)} {leverage}x"
        )
    return lines or ["路由: 未配置频道"]


def _startup_lines(db: Database, statuses: list[dict[str, Any]], public_url: str) -> list[str]:
    risk = get_full_config(db)["risk"]
    lines = [
        f"运行模式: {'模拟' if risk['dry_run'] else '实盘'}",
        f"紧急停止: {'已开启' if risk['kill_switch'] else '关闭'}",
        f"仓位规则: 全仓保证金 {float(risk['position_pct']) * 100:.0f}%",
        *_route_lines(db),
    ]
    for status in statuses:
        if status["ok"]:
            lines.append(
                f"{status['label']}: 正常 | 余额 {status['total_usdt']:.2f} USDT | "
                f"持仓 {status['positions']}"
            )
        else:
            lines.append(f"{status['label']}: 异常 | {status['error']}")
    lines.append(f"手机面板: {public_url or '等待 Cloudflare 地址'}")
    return lines


def _startup_notification_due(db: Database, cooldown_seconds: int = 300) -> bool:
    raw = db.get_setting(STARTUP_NOTIFY_KEY)
    try:
        return not raw or _now_ts() - int(raw) >= cooldown_seconds
    except ValueError:
        return True


def _save_exchange_state(db: Database, status: dict[str, Any]) -> None:
    key = f"{EXCHANGE_STATE_KEY_PREFIX}_{status['exchange']}"
    payload: dict[str, Any] = {"ok": bool(status["ok"])}
    if status["ok"]:
        payload.update(
            {
                "balance": {
                    "total_usdt": status.get("total_usdt"),
                    "available_usdt": status.get("available_usdt"),
                    "unrealized_pnl": status.get("unrealized_pnl"),
                },
                "positions": status.get("positions"),
            }
        )
    db.set_setting(key, json.dumps(payload, sort_keys=True))


def _load_exchange_ok(db: Database, exchange_name: str) -> bool | None:
    raw = db.get_setting(f"{EXCHANGE_STATE_KEY_PREFIX}_{exchange_name}")
    if not raw:
        return None
    try:
        return bool(json.loads(raw).get("ok"))
    except (json.JSONDecodeError, AttributeError):
        return None


async def notify_exchange_transition(
    db: Database,
    status: dict[str, Any],
    previous_ok: bool | None,
) -> None:
    if previous_ok is None or previous_ok == status["ok"]:
        return
    if status["ok"]:
        await send_telegram_notification(
            db,
            f"{status['label']} 已恢复",
            [
                f"环境: {status['mode']}",
                f"余额: {status['total_usdt']:.2f} USDT",
                f"持仓: {status['positions']}",
            ],
        )
    else:
        await send_telegram_notification(
            db,
            f"{status['label']} 连接异常",
            [
                f"环境: {status['mode']}",
                f"错误: {status['error']}",
                "系统将继续自动重试；恢复后会再次通知。",
            ],
        )


def check_health_once(db: Database, *, send_startup: bool = False) -> dict[str, Any]:
    public_url = sync_public_url(db, notify_change=not send_startup)
    statuses = [exchange_status(db, name) for name in configured_routed_exchange_names(db)]
    if send_startup and _startup_notification_due(db):
        asyncio.run(send_telegram_notification(db, "跟单系统已启动", _startup_lines(db, statuses, public_url)))
        db.set_setting(STARTUP_NOTIFY_KEY, str(_now_ts()))
    for status in statuses:
        previous_ok = _load_exchange_ok(db, status["exchange"])
        asyncio.run(notify_exchange_transition(db, status, previous_ok))
        _save_exchange_state(db, status)
    db.audit(
        "health_check",
        ", ".join(
            f"{status['label']}={'ok' if status['ok'] else 'error'}" for status in statuses
        ),
    )
    return {"public_url": public_url, "exchanges": statuses}


def start_health_monitor(db: Database, poll_seconds: int = DEFAULT_POLL_SECONDS) -> None:
    time.sleep(3)
    try:
        check_health_once(db, send_startup=True)
    except Exception:
        logger.exception("启动自检失败")
        db.audit("health_check_error", "startup")
    while True:
        time.sleep(max(60, int(poll_seconds)))
        try:
            check_health_once(db)
        except Exception as e:
            logger.exception("定时健康检查失败")
            db.audit("health_check_error", str(e)[:480])
