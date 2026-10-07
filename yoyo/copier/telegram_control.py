from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from yoyo.copier.chartprime_history import (
    CHANNEL_ID,
    chartprime_compound_summary,
    chartprime_monthly_summary,
    chartprime_summary,
    load_chartprime_trades,
)
from yoyo.copier.exchange import (
    configured_routed_exchange_names,
    create_exchange_client,
    exchange_configured,
    exchange_label,
    get_channel_exchange_map,
    get_channel_leverage_map,
    routed_exchange_names,
)
from yoyo.copier.feiyang_history import CHANNEL_ID as FEIYANG_CHANNEL_ID
from yoyo.copier.feiyang_history import feiyang_summary, load_feiyang_trades
from yoyo.copier.runtime_config import get_full_config
from yoyo.copier.store.sqlite import Database
from yoyo.copier.woods_history import CHANNEL_ID as WOODS_CHANNEL_ID
from yoyo.copier.woods_history import load_woods_trades, woods_summary

HISTORY_PAGE_SIZE = 12
EXCHANGE_SNAPSHOT_TTL_SECONDS = 8.0
EXCHANGE_QUERY_TIMEOUT_SECONDS = 4.5
_EXCHANGE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg-exchange")
_EXCHANGE_SNAPSHOT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_EXCHANGE_FAILURE_CACHE: dict[str, float] = {}
_EXCHANGE_LAST_ERROR: dict[str, str] = {}
PANEL_EXCHANGE_SNAPSHOT_KEY_PREFIX = "telegram_panel_exchange_snapshot"
ROUTE_ORDER = [
    "1226095564073205780",
    "1131521990814089276",
    "1356581750914027590",
    "988830102957736027",
]
CHANNEL_NAMES = {
    "988830102957736027": "ChartPrime",
    "1226095564073205780": "Woods",
    "1356581750914027590": "比特币飞扬",
    "1131521990814089276": "Arthur行情分析",
}
BLOGGERS = {
    "chartprime": {"channel_id": "988830102957736027", "name": "ChartPrime", "history": "chartprime"},
    "woods": {"channel_id": "1226095564073205780", "name": "Woods", "history": "woods"},
    "feiyang": {"channel_id": "1356581750914027590", "name": "比特币飞扬", "history": "feiyang"},
    "mia": {"channel_id": "1131521990814089276", "name": "Arthur行情分析", "history": "mia"},
}


def control_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "总览状态", "callback_data": "ctl:status"},
                {"text": "我的订单", "callback_data": "ctl:my_orders"},
                {"text": "📸 持仓截图", "callback_data": "ctl:orders_card_snapshot"},
            ],
            [
                {"text": "ChartPrime", "callback_data": "ctl:blogger:chartprime"},
                {"text": "Woods", "callback_data": "ctl:blogger:woods"},
            ],
            [
                {"text": "比特币飞扬", "callback_data": "ctl:blogger:feiyang"},
                {"text": "Arthur行情", "callback_data": "ctl:blogger:mia"},
            ],
            [
                {"text": "全部统计", "callback_data": "ctl:stats"},
                {"text": "最近信号", "callback_data": "ctl:signals"},
            ],
            [
                {"text": "Ping", "callback_data": "ctl:ping"},
                {"text": "刷新", "callback_data": "ctl:panel"},
            ],
        ]
    }


def blogger_keyboard(blogger: str) -> dict[str, Any]:
    item = BLOGGERS.get(blogger) or BLOGGERS["chartprime"]
    rows = [
        [
            {"text": f"{item['name']} 统计", "callback_data": f"ctl:blogger:{blogger}"},
            {"text": "最近消息", "callback_data": f"ctl:blogger:{blogger}:recent"},
        ]
    ]
    if item.get("history"):
        rows.append([{"text": "开单历史", "callback_data": f"ctl:history:{item['history']}:1"}])
    if blogger == "chartprime":
        rows.append(
            [
                {"text": "频道分析", "callback_data": "ctl:channel_report"},
                {"text": "亏损样本", "callback_data": "ctl:blogger_losses"},
            ]
        )
    rows.extend(
        [
            [
                {"text": "我的订单", "callback_data": "ctl:my_orders"},
                {"text": "全部统计", "callback_data": "ctl:stats"},
            ],
            [
                {"text": "返回总览", "callback_data": "ctl:panel"},
                {"text": "刷新", "callback_data": f"ctl:blogger:{blogger}"},
            ],
        ]
    )
    return {"inline_keyboard": rows}


def history_keyboard(page: int, pages: int, blogger: str = "chartprime") -> dict[str, Any]:
    prev_page = max(1, page - 1)
    next_page = min(pages, page + 1)
    return {
        "inline_keyboard": [
            *blogger_keyboard(blogger)["inline_keyboard"],
            [
                {"text": "上一页", "callback_data": f"ctl:history:{blogger}:{prev_page}"},
                {"text": f"{page}/{pages}", "callback_data": "ctl:noop"},
                {"text": "下一页", "callback_data": f"ctl:history:{blogger}:{next_page}"},
            ],
        ]
    }


def secondary_keyboard() -> dict[str, Any]:
    return control_keyboard()


def _route_labels(db: Database | None = None, *, include_amounts: bool = False) -> list[str]:
    fallback = [
        "Woods → Gate 12x",
        "Arthur行情分析 → Gate Arthur 5x",
        "比特币飞扬 → Gate 飞扬 15x",
        "ChartPrime → OKX 20x",
    ]
    if db is not None:
        exchange_map = get_channel_exchange_map(db)
        leverage_map = get_channel_leverage_map(db)
        snapshots = _exchange_snapshot_map(list(dict.fromkeys(exchange_map.values()))) if include_amounts else {}
        ordered = [cid for cid in ROUTE_ORDER if cid in exchange_map]
        ordered.extend(cid for cid in exchange_map if cid not in ordered)
        routes = []
        for channel_id in ordered:
            exchange = exchange_map[channel_id]
            route = (
                f"{CHANNEL_NAMES.get(channel_id, channel_id)} → "
                f"{exchange_label(exchange=exchange)} {leverage_map.get(channel_id, 1)}x"
            )
            if include_amounts:
                route = f"{route} · {_balance_amounts(snapshots.get(exchange))}"
            routes.append(route)
        if routes:
            return routes
    return fallback


def panel_text(db: Database | None = None) -> str:
    max_positions = get_full_config(db)["risk"]["max_open_positions"] if db is not None else 3
    route_lines = "\n".join(_route_card_lines(db))
    return "\n".join(
        [
            "<b>Discord Multi-Exchange Copier</b>",
            "<code>四位博主 · 实时跟单</code>",
            "",
            route_lines,
            "",
            f"仓位 <code>按博主配置</code>    限制 <code>最多 {max_positions} 持仓</code>",
            "<i>选择下方按钮操作。</i>",
        ]
    )


def _route_card_lines(db: Database | None = None) -> list[str]:
    if db is None:
        return [escape(route) for route in _route_labels(None)]

    exchange_map = get_channel_exchange_map(db)
    leverage_map = get_channel_leverage_map(db)
    snapshots = _exchange_snapshot_map(list(dict.fromkeys(exchange_map.values())))
    ordered = [cid for cid in ROUTE_ORDER if cid in exchange_map]
    ordered.extend(cid for cid in exchange_map if cid not in ordered)

    lines: list[str] = []
    for channel_id in ordered:
        exchange = exchange_map[channel_id]
        snapshot = snapshots.get(exchange)
        if snapshot:
            _save_panel_exchange_snapshot(db, exchange, snapshot)
        else:
            snapshot = _load_panel_exchange_snapshot(db, exchange)
        balance, profit = _balance_pair(snapshot)
        dot = _profit_dot(profit)
        name = escape(CHANNEL_NAMES.get(channel_id, channel_id))
        route = escape(f"{exchange_label(exchange=exchange)} {leverage_map.get(channel_id, 1)}x")
        lines.append(f"{dot} <b>{name}</b>  <code>{route}</code>")
        lines.append(f"   余额 <code>{balance}</code>   收益 <code>{profit}</code>")
    return lines or [escape(route) for route in _route_labels(None)]


def _balance_amounts(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "余额 - · 收益 -"
    balance = snapshot.get("balance") or {}
    return (
        f"余额 {_usdt_short(balance.get('total_usdt'))} · "
        f"收益 {_signed_usdt_short(balance.get('unrealized_pnl'))}"
    )


def _balance_pair(snapshot: dict[str, Any] | None) -> tuple[str, str]:
    if not snapshot:
        return "-", "-"
    balance = snapshot.get("balance") or {}
    return _usdt_short(balance.get("total_usdt")), _signed_usdt_short(balance.get("unrealized_pnl"))


def _profit_dot(value: str) -> str:
    if value == "-":
        return "⚪"
    if value.startswith("+") and value != "+0.00U":
        return "🟢"
    return "⚪"


def _save_panel_exchange_snapshot(db: Database, exchange: str, snapshot: dict[str, Any]) -> None:
    balance = snapshot.get("balance") or {}
    payload = {
        "balance": {
            "total_usdt": balance.get("total_usdt"),
            "unrealized_pnl": balance.get("unrealized_pnl"),
        }
    }
    try:
        db.set_setting(
            f"{PANEL_EXCHANGE_SNAPSHOT_KEY_PREFIX}_{exchange}",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    except Exception:
        pass


def _load_panel_exchange_snapshot(db: Database, exchange: str) -> dict[str, Any] | None:
    for key in (
        f"{PANEL_EXCHANGE_SNAPSHOT_KEY_PREFIX}_{exchange}",
        f"health_exchange_state_{exchange}",
    ):
        raw = db.get_setting(key)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("balance"), dict):
            return {"balance": payload["balance"]}
    return None


def _usdt_short(value: Any) -> str:
    try:
        return f"{float(value):.2f}U"
    except (TypeError, ValueError):
        return "-"


def _signed_usdt_short(value: Any) -> str:
    try:
        return f"{float(value):+.2f}U"
    except (TypeError, ValueError):
        return "-"


def _line(label: str, value: Any) -> str:
    return f"{escape(label)}: <code>{escape(str(value))}</code>"


def _fmt_float(value: Any, digits: int = 4) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _short_time(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        text = str(value or "-")
        return text.replace("T", " ").replace("Z", "")[:19]


def _preview(text: Any, limit: int = 86) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _fmt_num(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return "-"


def _short_trade_time(value: Any) -> str:
    text = str(value or "").replace("T", " ")
    return text[5:16] if len(text) >= 16 else text


def render_ping() -> str:
    return "<b>Pong</b>\n<code>Bot 在线 | 按钮可用</code>"


def render_status(db: Database) -> str:
    local_orders = db.list_orders(limit=1000)
    successful = [
        row
        for row in local_orders
        if str(row.get("status") or "") in {"live", "open", "filled", "partially_filled", "closed", "canceled"}
    ]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    today_successful = sum(1 for row in successful if _local_order_date(row.get("created_at")) == today)
    lines = [
        "<b>当前状态</b>",
        "<code>Real-time exchange snapshot</code>",
        "",
        *[f"路由: <code>{escape(route)}</code>" for route in _route_labels(db)],
        "",
    ]
    total_positions = 0
    total_pending = 0
    snapshots = _exchange_snapshot_map(routed_exchange_names(db))
    for exchange in routed_exchange_names(db):
        label = exchange_label(exchange=exchange)
        if not exchange_configured(exchange=exchange):
            lines.append(f"⚪ <b>{escape(label)}</b> · API 未配置，跟单暂停")
            continue
        snapshot = snapshots.get(exchange)
        if not snapshot:
            reason = _EXCHANGE_LAST_ERROR.get(exchange, "实时查询失败")
            lines.append(f"🔴 <b>{escape(label)}</b> · {escape(_preview(reason, 72))}")
            continue
        balance = snapshot.get("balance") or {}
        positions = snapshot.get("positions") or []
        pending = snapshot.get("open_orders") or []
        total_positions += len(positions)
        total_pending += len(pending)
        lines.append(
            f"🟢 <b>{escape(label)}</b> · 余额 {_fmt_float(balance.get('total_usdt'), 2)} USDT · "
            f"持仓 {len(positions)} · 挂单 {len(pending)}"
        )
    lines.extend(
        [
            "",
            _line("当前持仓", total_positions),
            _line("等待成交", total_pending),
            _line("今日成功下单记录", today_successful),
            _line("累计成功下单记录", len(successful)),
        ]
    )
    return "\n".join(lines)


def _local_order_date(value: Any):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed.astimezone(ZoneInfo("Asia/Shanghai")).date()
    except (ValueError, TypeError):
        return None


def _trade_result_label(result: Any) -> str:
    if result in {"win_tp", "win_profit"}:
        return "胜"
    if result == "loss_sl":
        return "亏"
    if result == "breakeven":
        return "保本"
    if result == "canceled":
        return "取消"
    if result == "superseded":
        return "修改"
    return "未知"


def render_blogger_signals(
    page: int = 1,
    page_size: int = HISTORY_PAGE_SIZE,
    blogger: str = "chartprime",
) -> str:
    is_feiyang = blogger == "feiyang"
    is_woods = blogger == "woods"
    trades = (
        load_feiyang_trades()
        if is_feiyang
        else load_woods_trades()
        if is_woods
        else load_chartprime_trades()
    )
    name = "比特币飞扬" if is_feiyang else "Woods" if is_woods else "ChartPrime"
    if not trades:
        return "<b>博主历史开单</b>\n暂无逐笔明细，请先重新抓取 Discord 历史。"
    pages = max(1, (len(trades) + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    rows = trades[start : start + page_size]
    lines = [
        "<b>开单历史</b>",
        f"<code>{name} | {page}/{pages} | {len(trades)} trades</code>",
        "",
    ]
    for idx, row in enumerate(rows, start=start + 1):
        time_text = _short_trade_time(row.get("time"))
        entry = f"{_fmt_num(row.get('entry_low'))}-{_fmt_num(row.get('entry_high'))}"
        stop = _fmt_num(row.get("stop_loss"))
        lines.append(
            f"<code>{idx:02d}</code> {escape(str(row.get('symbol') or '-'))} "
            f"{escape(str(row.get('side') or '-').upper())} | "
            f"{_trade_result_label(row.get('result'))}"
            f"{' | TP' + str(int(row.get('tp_hits') or 0)) if not is_feiyang and not is_woods else ''}\n"
            f"<code>{escape(time_text)} | entry {escape(entry)} | SL {escape(stop)}</code>"
        )
    return "\n".join(lines)


def render_db_blogger_history(
    db: Database,
    blogger: str,
    *,
    page: int = 1,
    page_size: int = HISTORY_PAGE_SIZE,
) -> tuple[str, int]:
    item = BLOGGERS.get(blogger) or BLOGGERS["woods"]
    channel_id = str(item["channel_id"])
    rows = _db_blogger_open_rows(db, channel_id)
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    page = max(1, min(page, pages))
    start = (page - 1) * page_size
    current = rows[start : start + page_size]
    lines = [
        "<b>开单历史</b>",
        f"<code>{escape(str(item['name']))} | {page}/{pages} | {len(rows)} trades</code>",
        "",
    ]
    if not current:
        lines.append("暂无开仓记录。")
        return "\n".join(lines), pages
    order_by_message = _latest_order_by_message(db, channel_id)
    for idx, row in enumerate(current, start=start + 1):
        data = _normalized_signal_data(_safe_json_dict(row.get("ai_json")))
        symbol = str(data.get("symbol") or "-").upper()
        side = str(data.get("side") or "-").upper()
        entry = _entry_range(data)
        stop = _fmt_num(data.get("stop_loss"))
        order = order_by_message.get(int(row.get("message_id") or 0))
        order_status = str(order.get("status") or "未跟单") if order else "未跟单"
        lines.append(
            f"<code>{idx:02d}</code> {escape(symbol)} {escape(side)} | {escape(order_status)}\n"
            f"<code>{escape(_short_time(row.get('message_created_at')))} | "
            f"entry {escape(entry)} | SL {escape(stop)} | #{row.get('message_id')}</code>"
        )
    return "\n".join(lines), pages


def _db_blogger_open_rows(db: Database, channel_id: str) -> list[dict[str, Any]]:
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT s.*, m.created_at AS message_created_at, m.content
            FROM signals s
            JOIN messages m ON m.id=s.message_id
            JOIN (
                SELECT message_id, MAX(id) AS latest_signal_id
                FROM signals
                GROUP BY message_id
            ) latest ON latest.latest_signal_id=s.id
            WHERE m.channel_id=? AND s.intent='open'
            ORDER BY m.created_at DESC, m.id DESC""",
            (str(channel_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def _latest_order_by_message(db: Database, channel_id: str) -> dict[int, dict[str, Any]]:
    orders = [
        row
        for row in db.list_orders(limit=10000)
        if str(row.get("channel_id") or "") == str(channel_id)
    ]
    result: dict[int, dict[str, Any]] = {}
    for order in sorted(orders, key=lambda item: int(item.get("id") or 0)):
        try:
            message_id = int(order.get("message_id") or 0)
        except (TypeError, ValueError):
            continue
        result[message_id] = order
    return result


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _normalized_signal_data(data: dict[str, Any]) -> dict[str, Any]:
    result = dict(data)
    symbol = str(result.get("symbol") or "").upper()
    try:
        low = float(result["entry_low"])
        high = float(result["entry_high"])
        stop = float(result["stop_loss"])
    except (KeyError, TypeError, ValueError):
        return result
    if symbol == "BTC" and 10 <= min(low, high, stop) and max(low, high, stop) < 1000:
        result["entry_low"] = low * 1000
        result["entry_high"] = high * 1000
        result["stop_loss"] = stop * 1000
    elif symbol == "DOGE" and 0.5 <= min(low, high, stop) and max(low, high, stop) < 1:
        result["entry_low"] = low / 10
        result["entry_high"] = high / 10
        result["stop_loss"] = stop / 10
    return result


def _entry_range(data: dict[str, Any]) -> str:
    low = data.get("entry_low")
    high = data.get("entry_high")
    if low is not None and high is not None:
        if _fmt_num(low) == _fmt_num(high):
            return _fmt_num(low)
        return f"{_fmt_num(low)}-{_fmt_num(high)}"
    if low is not None:
        return _fmt_num(low)
    if high is not None:
        return _fmt_num(high)
    return "-"


def render_blogger_losses() -> str:
    losses = [t for t in load_chartprime_trades() if t.get("result") == "loss_sl"]
    lines = [
        "<b>博主明确亏损样本</b>",
        f"<code>ChartPrime | {len(losses)} losses</code>",
        "",
    ]
    if not losses:
        lines.append("暂无亏损样本。")
        return "\n".join(lines)
    for idx, row in enumerate(losses, start=1):
        time_text = _short_trade_time(row.get("time"))
        entry = f"{_fmt_num(row.get('entry_low'))}-{_fmt_num(row.get('entry_high'))}"
        lines.append(
            f"<code>{idx:02d}</code> {escape(str(row.get('symbol') or '-'))} "
            f"{escape(str(row.get('side') or '-').upper())} | "
            f"<code>{escape(time_text)} | entry {escape(entry)} | SL {_fmt_num(row.get('stop_loss'))}</code>"
        )
    return "\n".join(lines)


def _side_label(value: Any) -> str:
    return "多" if value in {"buy", "long"} else "空" if value in {"sell", "short"} else str(value or "-")


def _live_order_snapshot(
    db: Database,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    positions: list[dict[str, Any]] = []
    open_orders: list[dict[str, Any]] = []
    triggers: list[dict[str, Any]] = []
    errors: list[str] = []
    exchanges = configured_routed_exchange_names(db)
    snapshots = _exchange_snapshot_map(exchanges)
    for exchange in exchanges:
        label = exchange_label(exchange=exchange)
        snapshot = snapshots.get(exchange)
        if not snapshot:
            reason = _EXCHANGE_LAST_ERROR.get(exchange)
            errors.append(f"{label}: {_preview(reason, 48)}" if reason else label)
            continue
        positions.extend({**row, "_exchange": label} for row in snapshot.get("positions", []))
        open_orders.extend({**row, "_exchange": label} for row in snapshot.get("open_orders", []))
        triggers.extend({**row, "_exchange": label} for row in snapshot.get("triggers", []))
    return positions, open_orders, triggers, errors


def _signed_usdt(value: Any) -> str:
    try:
        amount = float(value)
        return f"{amount:+.2f} USDT"
    except (TypeError, ValueError):
        return "- USDT"


def _trigger_prices(position: dict[str, Any], triggers: list[dict[str, Any]]) -> tuple[str, str]:
    inst_id = str(position.get("instId") or "")
    mark = float(position.get("markPx") or position.get("markPx") or 0)
    side = str(position.get("posSide") or "")
    prices: list[float] = []
    for row in triggers:
        if str(row.get("instId") or "") != inst_id:
            continue
        for key in ("triggerPx", "slTriggerPx", "tpTriggerPx"):
            try:
                value = float(row.get(key) or 0)
                if value > 0:
                    prices.append(value)
            except (TypeError, ValueError):
                continue
    if not prices:
        return "-", "-"
    if side == "long":
        stop = [p for p in prices if not mark or p < mark]
        take = [p for p in prices if mark and p > mark]
    else:
        stop = [p for p in prices if mark and p > mark]
        take = [p for p in prices if not mark or p < mark]
    return (_fmt_num(stop[0]) if stop else "-", _fmt_num(take[0]) if take else "-")


def _position_line(position: dict[str, Any], triggers: list[dict[str, Any]]) -> str:
    inst_id = position.get("instId") or position.get("inst_id") or "-"
    side = position.get("posSide") or position.get("side")
    exchange = position.get("_exchange") or "-"
    notional = _fmt_num(position.get("notionalUsd") or position.get("notional"))
    avg = _fmt_num(position.get("avgPx"))
    mark = _fmt_num(position.get("markPx"))
    liq = _fmt_num(position.get("liqPx"))
    stop, take = _trigger_prices(position, triggers)
    return (
        f"🟣 {escape(str(inst_id))} · {escape(_side_label(side))} · {escape(str(exchange))}\n"
        f"<code>实际仓位 {escape(notional)} USDT · 浮盈 {_signed_usdt(position.get('upl'))}</code>\n"
        f"<code>均价 {escape(avg)} · 标记 {escape(mark)} · 止损 {escape(stop)}</code>\n"
        f"<code>强平 {escape(liq)} · 止盈 {escape(take)}</code>"
    )


def _pending_order_line(order: dict[str, Any]) -> str:
    inst_id = order.get("instId") or order.get("inst_id") or "-"
    side = order.get("side")
    exchange = order.get("_exchange") or "-"
    amount = _fmt_num(order.get("notionalUsd"))
    price = _fmt_num(order.get("px") or order.get("price"))
    return (
        f"🟡 {escape(str(inst_id))} · {escape(_side_label(side))} · {escape(str(exchange))}\n"
        f"<code>委托金额 {escape(amount)} USDT · 委托价 {escape(price)}</code>"
    )


def _query_exchange_snapshot(exchange: str) -> dict[str, Any]:
    client = create_exchange_client(exchange=exchange)
    health = client.health_check()
    if not health.get("ok"):
        raise RuntimeError(str(health.get("error") or "exchange health check failed"))
    return {
        "balance": health.get("balance") or client.get_balance_summary(),
        "positions": client.get_positions(),
        "open_orders": client.get_open_orders(),
        "triggers": client.get_open_triggers(),
    }


def _exchange_snapshot_map(exchanges: list[str]) -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    result: dict[str, dict[str, Any]] = {}
    futures = {}
    for exchange in exchanges:
        cached = _EXCHANGE_SNAPSHOT_CACHE.get(exchange)
        if cached and now - cached[0] <= EXCHANGE_SNAPSHOT_TTL_SECONDS:
            result[exchange] = cached[1]
            continue
        failed_at = _EXCHANGE_FAILURE_CACHE.get(exchange)
        if failed_at and now - failed_at <= EXCHANGE_SNAPSHOT_TTL_SECONDS:
            continue
        futures[_EXCHANGE_EXECUTOR.submit(_query_exchange_snapshot, exchange)] = exchange
    try:
        completed = as_completed(futures, timeout=EXCHANGE_QUERY_TIMEOUT_SECONDS)
        for future in completed:
            exchange = futures[future]
            try:
                snapshot = future.result()
            except Exception as exc:
                _EXCHANGE_LAST_ERROR[exchange] = str(exc)[:300]
                _EXCHANGE_FAILURE_CACHE[exchange] = time.monotonic()
                continue
            _EXCHANGE_SNAPSHOT_CACHE[exchange] = (time.monotonic(), snapshot)
            _EXCHANGE_FAILURE_CACHE.pop(exchange, None)
            _EXCHANGE_LAST_ERROR.pop(exchange, None)
            result[exchange] = snapshot
    except FutureTimeoutError:
        pass
    for future, exchange in futures.items():
        if future.done() or exchange in result:
            continue
        try:
            snapshot = future.result(timeout=0)
        except FutureTimeoutError:
            _EXCHANGE_LAST_ERROR[exchange] = "实时查询超时"
            _EXCHANGE_FAILURE_CACHE[exchange] = time.monotonic()
            continue
        except Exception as exc:
            _EXCHANGE_LAST_ERROR[exchange] = str(exc)[:300]
            _EXCHANGE_FAILURE_CACHE[exchange] = time.monotonic()
            continue
        _EXCHANGE_SNAPSHOT_CACHE[exchange] = (time.monotonic(), snapshot)
        _EXCHANGE_FAILURE_CACHE.pop(exchange, None)
        _EXCHANGE_LAST_ERROR.pop(exchange, None)
        result[exchange] = snapshot
    return result


def _saved_order_notional(order: dict[str, Any]) -> str:
    try:
        payload = json.loads(str(order.get("response_json") or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return "-"
    if not isinstance(payload, dict):
        return "-"
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else payload
    return _fmt_num(plan.get("notional_usdt"))


def _usdt_or_dash(value: str) -> str:
    return "-" if value == "-" else f"{value} USDT"


def _history_group_name(order: dict[str, Any]) -> str:
    channel_id = str(order.get("channel_id") or "")
    if channel_id in CHANNEL_NAMES:
        return CHANNEL_NAMES[channel_id]
    author = str(order.get("author") or "").strip()
    return author or "其他"


def _display_order_status(
    order: dict[str, Any],
    *,
    active_order_ids: set[str],
    active_inst_ids: set[str],
) -> str:
    status = str(order.get("status") or "-")
    if status in {"live", "open", "filled", "partially_filled"}:
        order_id = str(order.get("okx_ord_id") or "")
        inst_id = str(order.get("inst_id") or "")
        if order_id not in active_order_ids and inst_id not in active_inst_ids:
            return "交易所无持仓/挂单"
    return status


def _history_status_icon(status: str) -> str:
    if status == "error":
        return "🔴"
    if status == "交易所无持仓/挂单":
        return "🟤"
    if status in {"canceled", "stale", "closed"}:
        return "⚪"
    return "🟡"


def render_my_orders(db: Database, limit: int = 8, *, refresh: bool = True) -> str:
    orders = db.list_orders(limit=limit)
    positions, open_orders, triggers, errors = (
        _live_order_snapshot(db) if refresh else ([], [], [], [])
    )
    live_ids = {str(row.get("ordId") or row.get("id") or "") for row in open_orders}
    position_inst_ids = {str(row.get("instId") or "") for row in positions}
    open_order_inst_ids = {str(row.get("instId") or "") for row in open_orders}
    active_inst_ids = position_inst_ids | open_order_inst_ids
    lines = ["<b>我的订单</b>", "<code>Gate + OKX 实时状态</code>"]

    lines.extend(["", f"<b>🟣 当前持仓 · {len(positions)}</b>"])
    if positions:
        lines.extend(_position_line(row, triggers) for row in positions)
    else:
        lines.append("暂无已成交持仓")

    lines.extend(["", f"<b>🟡 等待成交 · {len(open_orders)}</b>"])
    if open_orders:
        lines.extend(_pending_order_line(row) for row in open_orders)
    else:
        lines.append("暂无交易所挂单")

    history_rows = [
        row
        for row in orders
        if str(row.get("okx_ord_id") or "") not in live_ids
        and not (
            str(row.get("status") or "") in {"live", "open", "filled", "partially_filled"}
            and str(row.get("inst_id") or "") in position_inst_ids
        )
    ][:limit]
    lines.extend(["", f"<b>⚪ 历史记录 · 按博主 · 最近 {len(history_rows)}</b>"])
    if not history_rows:
        lines.append("暂无历史记录")
    current_group = ""
    for order in history_rows:
        group = _history_group_name(order)
        if group != current_group:
            current_group = group
            lines.append(f"<b>{escape(group)}</b>")
        status = _display_order_status(
            order,
            active_order_ids=live_ids,
            active_inst_ids=active_inst_ids,
        )
        icon = _history_status_icon(status)
        lines.append(
            f"{icon} #{order.get('id')} · {escape(str(order.get('inst_id') or '-'))} · "
            f"{escape(_side_label(order.get('side')))} · {escape(status)}\n"
            f"<code>名义仓位 {escape(_usdt_or_dash(_saved_order_notional(order)))} · "
            f"价格 {escape(str(order.get('px') or '-'))} · "
            f"{escape(_short_time(order.get('created_at')))}</code>"
        )
    if errors:
        lines.extend(["", f"🔴 实时查询失败: {escape(', '.join(errors))}"])
    return "\n".join(lines)


def render_orders(db: Database, limit: int = 8) -> str:
    return render_my_orders(db, limit, refresh=False)


def _follow_outcome(content: Any) -> str | None:
    text = str(content or "").lower()
    if "stopped out" in text or "明确止损" in text:
        return "loss"
    if (
        "stopped be" in text
        or "closed be" in text
        or ("breakeven" in text and ("closed" in text or "stopped" in text))
        or ("盈亏平衡" in text and "平仓" in text)
    ):
        return "breakeven"
    if "closed in profit" in text or "closed in small profit" in text or "target hit" in text or "tp" in text:
        return "win"
    return None


def _channel_follow_stats(
    db: Database,
    channel_id: str,
    positions: list[dict[str, Any]],
    exchange: str,
) -> dict[str, Any]:
    orders = [row for row in db.list_orders(limit=10000) if str(row.get("channel_id") or "") == channel_id]
    messages, _ = db.list_messages(channel_id=channel_id, per_page=10000)
    open_signals = sum(1 for row in messages if str(row.get("intent") or "") == "open")
    success_statuses = {"live", "open", "filled", "partially_filled", "closed"}
    successful = [row for row in orders if str(row.get("status") or "") in success_statuses]
    failed = [row for row in orders if str(row.get("status") or "") == "error"]
    canceled = [row for row in orders if str(row.get("status") or "") == "canceled"]
    followed_symbols: dict[str, str] = {}
    for row in sorted(successful, key=lambda item: str(item.get("created_at") or "")):
        symbol = str(row.get("inst_id") or "").upper().split("-", 1)[0]
        followed_symbols.setdefault(symbol, str(row.get("created_at") or ""))
    outcomes = [
        outcome
        for row in messages
        if any(
            symbol
            and re.search(rf"\b{re.escape(symbol)}\b", str(row.get("content") or ""), re.I)
            and str(row.get("created_at") or "") >= opened_at
            for symbol, opened_at in followed_symbols.items()
        )
        and (outcome := _follow_outcome(row.get("content"))) is not None
    ]
    wins = outcomes.count("win")
    losses = outcomes.count("loss")
    breakeven = outcomes.count("breakeven")
    settled = wins + losses
    dates = sorted(
        {
            str(row.get("created_at") or "")[:10]
            for row in successful
            if str(row.get("created_at") or "")[:10]
        }
    )
    symbols = {str(row.get("inst_id") or "") for row in successful}
    upl = sum(
        float(row.get("upl") or 0)
        for row in positions
        if str(row.get("instId") or row.get("inst_id") or "") in symbols
        and str(row.get("_exchange") or "") == exchange
    )
    return {
        "open_signals": open_signals,
        "orders": len(orders),
        "successful": len(successful),
        "failed": len(failed),
        "canceled": len(canceled),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": wins / settled * 100 if settled else None,
        "active_days": len(dates),
        "frequency": len(successful) / len(dates) if dates else 0,
        "upl": upl,
    }


def _backtest_summary(channel_id: str) -> str:
    if channel_id == CHANNEL_ID:
        summary = chartprime_summary()
        compound = chartprime_compound_summary()
        return (
            f"信号回测 · {summary['trades']} 单 · 胜率 {summary['win_rate']:.1f}% · "
            f"500U 复利净收益 {_signed_usdt(compound['net_pnl'])}"
        )
    if channel_id == FEIYANG_CHANNEL_ID:
        summary = feiyang_summary()
        return (
            f"信号回测 · {summary['trades']} 单 · 明确盈亏胜率 "
            f"{summary['win_rate']:.1f}% · 利润模型待补"
        )
    if channel_id == WOODS_CHANNEL_ID:
        summary = woods_summary()
        return (
            f"近半年统计 · {summary['trades']} 单 · 明确盈亏胜率 "
            f"{summary['win_rate']:.1f}% · 非亏损率 {summary['non_loss_rate']:.1f}%"
        )
    return "信号回测 · 尚未抓取完整历史，暂无可靠回测"


def _blogger_key_from_history(value: str) -> str:
    if value in BLOGGERS:
        return value
    return "chartprime"


def render_blogger_panel(db: Database, blogger: str, *, refresh: bool = True) -> str:
    item = BLOGGERS.get(blogger) or BLOGGERS["chartprime"]
    channel_id = str(item["channel_id"])
    positions = _live_order_snapshot(db)[0] if refresh else []
    exchange_map = get_channel_exchange_map(db)
    leverage_map = get_channel_leverage_map(db)
    exchange = exchange_label(exchange=exchange_map.get(channel_id, "okx"))
    leverage = leverage_map.get(channel_id, get_full_config(db)["okx"]["default_leverage"])
    stats = _channel_follow_stats(db, channel_id, positions, exchange)
    rate = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "暂无已结算样本"
    recent, _ = db.list_messages(channel_id=channel_id, per_page=3)
    lines = [
        f"<b>{escape(str(item['name']))}</b>",
        f"<code>{escape(channel_id)} | {escape(exchange)} {leverage}x</code>",
        "",
        f"开仓信号 <code>{stats['open_signals']}</code> · 成功跟单 <code>{stats['successful']}</code>",
        f"失败 <code>{stats['failed']}</code> · 取消 <code>{stats['canceled']}</code>",
        f"结果 · 胜 <code>{stats['wins']}</code> / 亏 <code>{stats['losses']}</code> / 保本 <code>{stats['breakeven']}</code>",
        f"胜率 <code>{rate}</code> · 当前浮盈 <code>{_signed_usdt(stats['upl'])}</code>",
        f"频率 <code>{stats['frequency']:.2f} 单/活跃日</code>",
        f"<i>{escape(_backtest_summary(channel_id))}</i>",
        "",
        "<b>最近消息</b>",
    ]
    if not recent:
        lines.append("暂无消息。")
    for row in recent:
        intent = str(row.get("intent") or "-")
        lines.append(
            f"<code>#{row.get('id')} · {escape(intent)} · {escape(_short_time(row.get('created_at')))}</code>\n"
            f"{escape(_preview(row.get('content'), 92))}"
        )
    return "\n".join(lines)


def render_blogger_recent(db: Database, blogger: str, limit: int = 8) -> str:
    item = BLOGGERS.get(blogger) or BLOGGERS["chartprime"]
    channel_id = str(item["channel_id"])
    rows, _ = db.list_messages(channel_id=channel_id, per_page=limit)
    lines = [
        f"<b>{escape(str(item['name']))} 最近消息</b>",
        f"<code>{escape(channel_id)}</code>",
        "",
    ]
    if not rows:
        lines.append("暂无消息。")
        return "\n".join(lines)
    for row in rows:
        intent = str(row.get("intent") or "-")
        should_act = "执行" if row.get("should_act") else "记录"
        lines.append(
            f"<code>#{row.get('id')} · {escape(intent)} · {should_act} · {escape(_short_time(row.get('created_at')))}</code>\n"
            f"{escape(_preview(row.get('content'), 110))}"
        )
    return "\n".join(lines)


def render_stats(db: Database, *, refresh: bool = True) -> str:
    positions = _live_order_snapshot(db)[0] if refresh else []
    exchange_map = get_channel_exchange_map(db)
    lines = [
        "<b>我的跟单数据</b>",
        "<code>按博主归属 · 实盘记录与信号回测分开</code>",
    ]
    for channel_id, name in CHANNEL_NAMES.items():
        exchange = exchange_label(exchange=exchange_map.get(channel_id, "okx"))
        stats = _channel_follow_stats(db, channel_id, positions, exchange)
        rate = f"{stats['win_rate']:.1f}%" if stats["win_rate"] is not None else "暂无已结算样本"
        lines.append("")
        lines.extend(
            [
                f"<b>{escape(name)} → {escape(exchange)}</b>",
                f"开仓信号 <code>{stats['open_signals']}</code> · "
                f"成功跟单 <code>{stats['successful']}</code> · "
                f"失败 <code>{stats['failed']}</code> · 取消 <code>{stats['canceled']}</code>",
                f"跟单结果 · 胜 <code>{stats['wins']}</code> / 亏 <code>{stats['losses']}</code> / "
                f"保本 <code>{stats['breakeven']}</code> · 胜率 <code>{rate}</code>",
                f"当前浮盈 <code>{_signed_usdt(stats['upl'])}</code> · "
                f"频率 <code>{stats['frequency']:.2f} 单/活跃日</code>",
                "已实现利润 <code>待逐笔成交账本采集</code>",
                f"<i>{escape(_backtest_summary(channel_id))}</i>",
            ]
        )
    lines.extend(
        [
            "",
            "说明: 跟单胜率仅统计与你成功跟单后出现的明确盈利/止损结果；"
            "回测是博主历史信号表现，不等同于你的实盘利润。",
        ]
    )
    return "\n".join(lines)


def render_channel_report() -> str:
    summary = chartprime_summary()
    compound = chartprime_compound_summary()
    fly = feiyang_summary()
    monthly = chartprime_monthly_summary()
    monthly_lines = [
        f"{item['month'][5:]}月 {item['win_rate']:.1f}% ({item['wins']}/{item['trades']})"
        for item in monthly
        if item["month"].startswith("2026-")
    ]
    lines = [
        "<b>ChartPrime 频道分析</b>",
        f"<code>{CHANNEL_ID} | 2026-01-01 -> 2026-06-08</code>",
        "",
        "<b>Signal</b>",
        _line("可归并开仓", summary["trades"]),
        _line("至少命中1个TP", summary["wins"]),
        _line("明确SL亏损", summary["losses"]),
        _line("未完成/未知", summary["unknown"]),
        _line("已结算胜率", f"{summary['win_rate']:.1f}%"),
        _line("全样本首TP命中率", f"{summary['first_tp_rate']:.1f}%"),
        _line("平均命中TP数", f"{summary['avg_tp']:.2f}"),
        _line("多单/空单", f"{summary['long_count']} / {summary['short_count']}"),
        "",
        "<b>500U 全仓 5x 复利回测</b>",
        _line("初始权益", f"{compound['initial_equity']:.2f} USDT"),
        _line("期末权益", f"{compound['final_equity']:.2f} USDT"),
        _line("净收益", f"{compound['net_pnl']:.2f} USDT"),
        _line("累计收益率", f"{compound['return_pct']:.2f}%"),
        _line("最大回撤", f"{compound['max_drawdown_pct']:.2f}%"),
        "每笔使用当时全部权益作保证金，名义仓位为权益的 5 倍，盈亏滚入下一笔。",
        "说明: 理论上按信号顺序逐笔结算，未计同时持仓、滑点和资金费率。",
        "",
        "<b>Monthly</b>",
        " | ".join(monthly_lines[:3]) if monthly_lines else "-",
        " | ".join(monthly_lines[3:]) if len(monthly_lines) > 3 else "",
        "",
        "<b>比特币飞扬频道分析</b>",
        f"<code>{FEIYANG_CHANNEL_ID} | 2026-05-02 -> 2026-06-09</code>",
        _line("归并策略", fly["trades"]),
        _line("明确盈利", fly["win_tp"]),
        _line("明确止损", fly["loss_sl"]),
        _line("保本退出", fly["breakeven"]),
        _line("取消/修改", f"{fly['canceled']} / {fly['superseded']}"),
        _line("未结算", fly["unknown"]),
        _line("明确盈亏胜率", f"{fly['win_rate']:.1f}% ({fly['win_tp']}/{fly['settled']})"),
        _line("部分止盈", fly["partial_close"]),
        _line("移动止损", fly["moved_stop"]),
        _line("多单/空单", f"{fly['long_count']} / {fly['short_count']}"),
        "说明: 胜率只计算有明确盈利或明确止损结果的策略，保本、取消、修改和未知不计入。",
    ]
    return "\n".join(lines)


def render_signals(db: Database, limit: int = 5) -> str:
    signals = db.recent_open_signals(limit=limit)
    lines = ["<b>最近开仓信号</b>"]
    if not signals:
        lines.append("暂无开仓信号。")
        return "\n".join(lines)
    for sig in signals:
        ai_data: dict[str, Any] = {}
        raw_ai = sig.get("ai_json")
        if raw_ai:
            try:
                ai_data = json.loads(raw_ai) if isinstance(raw_ai, str) else dict(raw_ai)
            except (json.JSONDecodeError, TypeError, ValueError):
                ai_data = {}
        symbol = ai_data.get("symbol") or "-"
        side = ai_data.get("side") or "-"
        lines.append(
            f"#{sig.get('message_id')} {escape(str(symbol).upper())} {escape(str(side).upper())} "
            f"conf={_fmt_float(sig.get('confidence'), 2)} "
            f"{escape(str(sig.get('status') or '-'))} "
            f"{_short_time(sig.get('created_at'))}\n"
            f"{escape(_preview(sig.get('content')))}"
        )
    return "\n".join(lines)


def render_callback(data: str, db: Database) -> str:
    if data.startswith("ctl:blogger:"):
        parts = data.split(":")
        blogger = parts[2] if len(parts) >= 3 else "chartprime"
        if len(parts) >= 4 and parts[3] == "recent":
            return render_blogger_recent(db, blogger)
        return render_blogger_panel(db, blogger)
    if data == "ctl:ping":
        return render_ping()
    if data == "ctl:status":
        return render_status(db)
    if data == "ctl:orders":
        return render_blogger_signals(1)
    if data == "ctl:blogger_signals":
        return render_blogger_signals(1)
    if data.startswith("ctl:blogger_signals:"):
        try:
            page = int(data.rsplit(":", 1)[1])
        except ValueError:
            page = 1
        return render_blogger_signals(page)
    if data == "ctl:blogger_losses":
        return render_blogger_losses()
    if data == "ctl:my_orders":
        return render_my_orders(db)
    if data == "ctl:stats":
        return render_stats(db)
    if data == "ctl:channel_report":
        return render_channel_report()
    if data == "ctl:signals":
        return render_signals(db)
    return panel_text(db)


def render_callback_response(data: str, db: Database) -> dict[str, Any]:
    if data.startswith("ctl:blogger:"):
        parts = data.split(":")
        blogger = parts[2] if len(parts) >= 3 else "chartprime"
        return {"text": render_callback(data, db), "reply_markup": blogger_keyboard(blogger)}
    if data.startswith("ctl:history:"):
        parts = data.split(":")
        blogger = parts[2] if len(parts) >= 4 else "chartprime"
        try:
            page = int(parts[-1])
        except ValueError:
            page = 1
        if blogger == "mia":
            text, pages = render_db_blogger_history(db, blogger, page=page)
            page = max(1, min(page, pages))
            return {
                "text": text,
                "reply_markup": history_keyboard(page, pages, _blogger_key_from_history(blogger)),
            }
        trades = (
            load_feiyang_trades()
            if blogger == "feiyang"
            else load_woods_trades()
            if blogger == "woods"
            else load_chartprime_trades()
        )
        total = len(trades)
        pages = max(1, (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)
        page = max(1, min(page, pages))
        return {
            "text": render_blogger_signals(page, blogger=blogger),
            "reply_markup": history_keyboard(page, pages, _blogger_key_from_history(blogger)),
        }
    if data in {"ctl:panel", "ctl:noop"}:
        return {"text": panel_text(db), "reply_markup": control_keyboard()}
    if data in {"ctl:channel_report", "ctl:blogger_losses", "ctl:my_orders", "ctl:stats", "ctl:signals", "ctl:status", "ctl:ping"}:
        return {"text": render_callback(data, db), "reply_markup": secondary_keyboard()}
    return {"text": render_callback(data, db), "reply_markup": control_keyboard()}
