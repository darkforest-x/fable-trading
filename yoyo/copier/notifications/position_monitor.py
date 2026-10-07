from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from yoyo.copier.exchange import (
    TradingClient,
    configured_routed_exchange_names,
    create_exchange_client,
    exchange_label,
    normalize_exchange,
    selected_exchange,
)
from yoyo.copier.notifications.telegram import send_telegram_notification
from yoyo.copier.store.sqlite import Database

logger = logging.getLogger(__name__)

SNAPSHOT_KEY_PREFIX = "exchange_position_monitor_snapshot"
DEFAULT_POLL_SECONDS = 60


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_key(position: dict[str, Any]) -> str:
    inst_id = str(position.get("instId") or position.get("inst_id") or "-")
    pos_side = str(position.get("posSide") or position.get("pos_side") or "net")
    return f"{inst_id}:{pos_side}"


def position_snapshot(positions: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    snapshot: dict[str, dict[str, str]] = {}
    for position in positions:
        key = _position_key(position)
        snapshot[key] = {
            "inst_id": str(position.get("instId") or position.get("inst_id") or "-"),
            "pos_side": str(position.get("posSide") or position.get("pos_side") or "net"),
            "pos": str(position.get("pos") or "0"),
            "notional_usdt": str(position.get("notionalUsd") or position.get("notional") or "0"),
            "avg_px": str(position.get("avgPx") or position.get("avg_px") or "-"),
            "upl": str(position.get("upl") or "0"),
        }
    return snapshot


def _snapshot_key(db: Database, exchange_name: str | None = None) -> str:
    selected = normalize_exchange(exchange_name) if exchange_name else selected_exchange(db)
    return f"{SNAPSHOT_KEY_PREFIX}_{selected}"


def _load_snapshot(db: Database, exchange_name: str | None = None) -> dict[str, dict[str, str]] | None:
    raw = db.get_setting(_snapshot_key(db, exchange_name))
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {
                str(k): dict(v)
                for k, v in parsed.items()
                if isinstance(v, dict)
            }
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


def _save_snapshot(
    db: Database,
    snapshot: dict[str, dict[str, str]],
    exchange_name: str | None = None,
) -> None:
    db.set_setting(_snapshot_key(db, exchange_name), json.dumps(snapshot, ensure_ascii=False, sort_keys=True))


def _position_lines(position: dict[str, str], *, include_upl: bool = True) -> list[str]:
    lines = [
        f"品种: {position.get('inst_id', '-')}",
        f"方向: {position.get('pos_side', '-')}",
        f"实际仓位: {position.get('notional_usdt', '-')} USDT",
        f"均价: {position.get('avg_px', '-')}",
    ]
    if include_upl:
        lines.append(f"未实现盈亏: {position.get('upl', '0')} USDT")
    return lines


async def check_positions_once(
    db: Database,
    client: TradingClient | None = None,
    exchange_name: str | None = None,
) -> dict[str, int]:
    selected = normalize_exchange(exchange_name) if exchange_name else selected_exchange(db)
    exchange = exchange_label(exchange=selected)
    trade_client = client or create_exchange_client(exchange=selected)
    previous = _load_snapshot(db, selected)
    positions = trade_client.get_positions()
    positions_error = str(getattr(trade_client, "last_positions_error", "") or "")
    if positions_error:
        db.audit(
            "exchange_position_monitor_skip",
            f"{exchange}: positions read failed; snapshot kept",
            payload={"exchange": selected, "error": positions_error[:500]},
        )
        return {
            "seeded": 0,
            "opened": 0,
            "closed": 0,
            "changed": 0,
            "orphan_triggers": 0,
            "skipped": 1,
        }
    orphan_triggers = _cleanup_orphan_exit_triggers(db, trade_client, positions, exchange)
    current = position_snapshot(positions)
    if previous is None:
        _save_snapshot(db, current, selected)
        db.audit("exchange_position_monitor_seed", f"{exchange} positions={len(current)}")
        return {"seeded": 1, "opened": 0, "closed": 0, "changed": 0, "orphan_triggers": orphan_triggers}

    opened = sorted(set(current) - set(previous))
    closed = sorted(set(previous) - set(current))
    changed: list[str] = []
    for key in sorted(set(current) & set(previous)):
        old_pos = _as_float(previous[key].get("pos"))
        new_pos = _as_float(current[key].get("pos"))
        if abs(old_pos - new_pos) > 1e-12:
            changed.append(key)

    for key in opened:
        await send_telegram_notification(
            db,
            f"{exchange} 持仓已出现",
            [
                *_position_lines(current[key]),
                "说明: 订单可能已经成交或仓位被手动打开。",
            ],
        )

    for key in closed:
        await send_telegram_notification(
            db,
            f"{exchange} 持仓已消失",
            [
                *_position_lines(previous[key], include_upl=False),
                "最终盈亏: 待成交账本确认",
                f"说明: 持仓消失前的浮盈不是最终利润；可能已 TP / SL / 手动平仓，请到 {exchange} 核对最终成交。",
            ],
        )

    for key in changed:
        title, lines = _changed_position_notice(exchange, previous[key], current[key])
        await send_telegram_notification(
            db,
            title,
            lines,
        )

    if opened or closed or changed:
        _save_snapshot(db, current, selected)
        db.audit(
            "exchange_position_monitor_change",
            f"{exchange} opened={len(opened)} closed={len(closed)} changed={len(changed)}",
            payload={"opened": opened, "closed": closed, "changed": changed},
        )

    return {
        "seeded": 0,
        "opened": len(opened),
        "closed": len(closed),
        "changed": len(changed),
        "orphan_triggers": orphan_triggers,
    }


def _cleanup_orphan_exit_triggers(
    db: Database,
    client: TradingClient,
    positions: list[dict[str, Any]],
    exchange: str,
) -> int:
    cancel_trigger = getattr(client, "cancel_trigger_order", None)
    if not callable(cancel_trigger):
        return 0

    try:
        open_orders = client.get_open_orders()
        triggers = client.get_open_triggers()
    except Exception as e:
        db.audit("orphan_trigger_cleanup_error", f"{exchange}: read failed: {str(e)[:480]}")
        return 0

    active_insts = {
        str(row.get("instId") or row.get("inst_id") or "")
        for row in [*positions, *open_orders]
        if str(row.get("instId") or row.get("inst_id") or "")
    }
    canceled = 0
    for trigger in triggers:
        inst_id = str(trigger.get("instId") or trigger.get("inst_id") or "")
        order_id = str(trigger.get("ordId") or trigger.get("order_id") or "")
        if not inst_id or not order_id or inst_id in active_insts:
            continue
        response = cancel_trigger(order_id)
        if response.get("ok"):
            canceled += 1
            db.audit(
                "orphan_trigger_cancelled",
                f"{exchange} {inst_id} trigger={order_id}",
                payload={"exchange": exchange, "inst_id": inst_id, "order_id": order_id},
            )
        else:
            db.audit(
                "orphan_trigger_cancel_failed",
                f"{exchange} {inst_id} trigger={order_id}: {response.get('error') or response.get('response')}",
                payload={"exchange": exchange, "inst_id": inst_id, "order_id": order_id, "response": response},
            )
    return canceled


def _changed_position_notice(
    exchange: str,
    previous: dict[str, str],
    current: dict[str, str],
) -> tuple[str, list[str]]:
    old_pos = abs(_as_float(previous.get("pos")))
    new_pos = abs(_as_float(current.get("pos")))
    old_notional = _as_float(previous.get("notional_usdt"))
    new_notional = _as_float(current.get("notional_usdt"))
    reduced = new_pos < old_pos
    increased = new_pos > old_pos
    if reduced:
        title = f"{exchange} 部分止盈/减仓"
        action_line = (
            f"减少仓位: {_fmt_num(old_pos - new_pos)} 张"
            if old_pos
            else "减少仓位: -"
        )
        note = "说明: 仓位变小，通常是 TP 命中、手动减仓或部分平仓。"
    elif increased:
        title = f"{exchange} 持仓加仓"
        action_line = (
            f"增加仓位: {_fmt_num(new_pos - old_pos)} 张"
            if new_pos
            else "增加仓位: -"
        )
        note = "说明: 仓位变大，可能是加仓或挂单继续成交。"
    else:
        title = f"{exchange} 持仓变化"
        action_line = "仓位张数未变"
        note = "说明: 仓位参数发生变化。"
    notional_delta = new_notional - old_notional
    return title, [
        f"品种: {current.get('inst_id', '-')}",
        f"方向: {current.get('pos_side', '-')}",
        action_line,
        f"原实际仓位: {previous.get('notional_usdt', '-')} USDT",
        f"新实际仓位: {current.get('notional_usdt', '-')} USDT",
        f"名义变化: {_signed_usdt(notional_delta)}",
        f"均价: {current.get('avg_px', '-')}",
        f"当前浮盈: {current.get('upl', '0')} USDT",
        note,
    ]


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _signed_usdt(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.4f} USDT"


def position_monitor_enabled(db: Database | None = None) -> bool:
    if db is None:
        return False
    return bool(configured_routed_exchange_names(db))


def start_position_monitor(
    db: Database,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> None:
    exchange_names = configured_routed_exchange_names(db)
    if not exchange_names:
        logger.info("持仓监控未启动：已路由交易所 API key 未配置")
        return

    clients = {name: create_exchange_client(exchange=name) for name in exchange_names}
    logger.info(
        "持仓监控已启动：%s，间隔 %ss",
        ", ".join(exchange_label(exchange=name) for name in exchange_names),
        poll_seconds,
    )
    while True:
        for name, client in list(clients.items()):
            exchange = exchange_label(exchange=name)
            try:
                asyncio.run(check_positions_once(db, client, name))
            except Exception as e:
                logger.warning("%s 持仓监控失败: %s", exchange, e)
                db.audit("exchange_position_monitor_error", f"{exchange}: {str(e)[:480]}")
        time.sleep(max(15, int(poll_seconds)))
