from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from yoyo.copier.config import ROOT

CHANNEL_ID = "1487843144950349824"
RAW_PATH = ROOT / "data" / "mia_raw_messages_2026.json"
TRADES_PATH = ROOT / "data" / "mia_trades_2026.json"


def build_mia_trades(
    raw_path: Path = RAW_PATH,
    output_path: Path = TRADES_PATH,
) -> list[dict[str, Any]]:
    try:
        rows = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    messages = [
        {
            "id": str(row.get("id") or ""),
            "time": str(row.get("iso") or ""),
            "content": _content(str(row.get("text") or "")),
            "raw": str(row.get("text") or ""),
        }
        for row in rows
    ]
    messages = [row for row in messages if row["id"] and row["time"] and row["content"]]
    messages.sort(key=lambda item: item["time"])

    trades: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    seen_opens: set[tuple[str, str, str, float, float, float, float | None]] = set()

    for message in messages:
        open_signal = _parse_open(message["content"], message["time"], message["id"])
        if open_signal and _is_primary_signal(message["content"]):
            key = _trade_key(open_signal)
            if key not in seen_opens:
                seen_opens.add(key)
                trades.append(open_signal)
                active.append(open_signal)
            continue

        update = _classify_update(message["content"])
        if not update:
            continue

        quoted = _parse_open(message["content"], message["time"], message["id"])
        trade = _match_trade(active, update, quoted)
        if not trade:
            continue
        _apply_update(trade, message, update)
        if trade["result"] in {"win_tp", "loss_sl", "breakeven", "canceled"}:
            active = [row for row in active if row["id"] != trade["id"]]

    payload = {
        "channel_id": CHANNEL_ID,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_messages": len(messages),
        "range": {
            "start": messages[0]["time"] if messages else "",
            "end": messages[-1]["time"] if messages else "",
        },
        "summary": mia_summary(trades),
        "trades": trades,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return trades


def load_mia_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists() and RAW_PATH.exists():
        return build_mia_trades()
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in payload.get("trades") or [] if isinstance(row, dict)]


def mia_summary(trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = list(trades if trades is not None else load_mia_trades())
    counts = Counter(str(row.get("result") or "unknown") for row in rows)
    settled = counts["win_tp"] + counts["loss_sl"]
    months: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        month = str(row.get("time") or "")[:7]
        if month:
            months[month][str(row.get("result") or "unknown")] += 1
    return {
        "trades": len(rows),
        "win_tp": counts["win_tp"],
        "partial_tp": counts["partial_tp"],
        "loss_sl": counts["loss_sl"],
        "breakeven": counts["breakeven"],
        "canceled": counts["canceled"],
        "unknown": counts["unknown"],
        "settled": settled,
        "win_rate": counts["win_tp"] / settled * 100 if settled else 0.0,
        "at_least_partial_tp": counts["win_tp"] + counts["partial_tp"],
        "long_count": sum(1 for row in rows if row.get("side") == "long"),
        "short_count": sum(1 for row in rows if row.get("side") == "short"),
        "limit_count": sum(1 for row in rows if row.get("order_type") == "limit"),
        "market_count": sum(1 for row in rows if row.get("order_type") == "market"),
        "moved_stop": sum(1 for row in rows if row.get("moved_stop") is not None),
        "partial_close": sum(1 for row in rows if row.get("partial_close")),
        "symbols": dict(Counter(str(row.get("symbol") or "-") for row in rows)),
        "monthly": {month: dict(counter) for month, counter in sorted(months.items())},
    }


def _content(text: str) -> str:
    drop = {
        "比特币米娅",
        "APP",
        "—",
        "[",
        "]",
        "添加反应",
        "转发",
        "更多",
        ":poop:",
        ":question:",
        ":Spot:",
        "点击反应",
    }
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in drop:
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}", line):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        if re.match(r"2026年\d{1,2}月\d{1,2}日星期.", line):
            continue
        if re.match(r"2026/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _is_primary_signal(text: str) -> bool:
    idx = text.find("米娅")
    if idx < 0:
        return False
    prefix = text[:idx]
    if "置顶消息通知" in prefix or "回复 比特币米娅" in prefix or "查看被回复" in text:
        return False
    update_words = ("触发", "止盈", "止损", "保本", "成本", "取消", "恭喜", "获利", "浮盈")
    return not any(word in prefix for word in update_words)


def _parse_open(text: str, timestamp: str, message_id: str) -> dict[str, Any] | None:
    if "短线合约交易策略" not in text or "进场点位" not in text or "止损点位" not in text:
        return None
    symbol_match = re.search(r"米娅\s*([A-Z0-9]+)\s*短线合约交易策略", text, re.I)
    side_match = re.search(r"(做多|做空)(?:（限价）|\\(限价\\))?", text)
    limit = bool(re.search(r"(做多|做空)(?:（限价）|\\(限价\\))", text))
    entry_match = re.search(
        r"进场点位[:：]\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(?:-|–|—)\s*([0-9]+(?:\.[0-9]+)?))?",
        text,
    )
    stop_match = re.search(r"止损点位[:：]\s*([0-9]+(?:\.[0-9]+)?)", text)
    target_match = re.search(r"止盈点位[:：]\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not symbol_match or not side_match or not entry_match or not stop_match:
        return None
    low = float(entry_match.group(1))
    high = float(entry_match.group(2) or low)
    symbol = symbol_match.group(1).upper()
    side = "long" if side_match.group(1) == "做多" else "short"
    target = float(target_match.group(1)) if target_match else None
    invalid_target = False
    mid = (low + high) / 2
    if target is not None:
        invalid_target = (side == "long" and target <= mid) or (side == "short" and target >= mid)
    return {
        "id": message_id,
        "time": timestamp,
        "symbol": symbol,
        "side": side,
        "order_type": "limit" if limit else "market",
        "entry_low": min(low, high),
        "entry_high": max(low, high),
        "stop_loss": float(stop_match.group(1)),
        "take_profit": target,
        "invalid_target": invalid_target,
        "result": "unknown",
        "result_time": None,
        "result_note": "",
        "exit_price": None,
        "partial_close": False,
        "partial_pct": None,
        "moved_stop": None,
        "updates": [],
    }


def _classify_update(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    symbol = _symbol(text)
    price = _price_after(text, ("现价", "BTC现价", "ZEC现价"))
    move = _price_after(text, ("止损位移至", "止损位重设为", "修改至"))
    partial_pct = _percent(text)
    if re.search(r"取消(?:限价|挂单|限价单)|限价(?:单|策略)?取消", text):
        return {"kind": "canceled", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    if re.search(r"触发止损|止损出局", text):
        return {"kind": "loss_sl", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    if re.search(r"保本出局|成本价出局|空仓出局|没有什么盈亏|没有盈亏", text):
        return {"kind": "breakeven", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    if re.search(r"短线稳健.*(?:止盈|出局)|止盈\s*\d+%|中长线止盈\s*\d+%|浮盈", text):
        return {"kind": "partial_tp", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    if re.search(r"全部仓位止盈出局|剩余仓位.*止盈出局|再次恭喜.*止盈出局|获利\d+点.*止盈出局|止盈出局吧|止盈出局", text):
        return {"kind": "win_tp", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    if move is not None or "成本保护" in text:
        return {"kind": "move_sl", "symbol": symbol, "price": price, "move": move, "partial_pct": partial_pct}
    return None


def _match_trade(
    active: list[dict[str, Any]],
    update: dict[str, Any],
    quoted: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if quoted:
        quoted_key = _trade_key(quoted)
        for trade in reversed(active):
            if _trade_key(trade) == quoted_key:
                return trade
    symbol = str(update.get("symbol") or "")
    candidates = [
        trade
        for trade in active
        if not symbol or trade.get("symbol") == symbol
    ]
    price = update.get("price")
    if price is not None and update.get("kind") in {"win_tp", "partial_tp"}:
        directional = [
            trade
            for trade in candidates
            if _is_profitable_price(trade, float(price))
        ]
        if directional:
            candidates = directional
    if price is not None and update.get("kind") == "loss_sl":
        loss_side = [
            trade
            for trade in candidates
            if not _is_profitable_price(trade, float(price))
        ]
        if loss_side:
            candidates = loss_side
    return candidates[-1] if candidates else None


def _apply_update(trade: dict[str, Any], message: dict[str, str], update: dict[str, Any]) -> None:
    kind = update["kind"]
    if update.get("move") is not None:
        trade["moved_stop"] = update["move"]
    if kind in {"partial_tp", "move_sl"}:
        trade["partial_close"] = trade["partial_close"] or kind == "partial_tp" or update.get("partial_pct") is not None
        if update.get("partial_pct") is not None:
            trade["partial_pct"] = update["partial_pct"]
        if trade["result"] == "unknown" and kind == "partial_tp":
            trade["result"] = "partial_tp"
    elif kind in {"win_tp", "loss_sl", "breakeven", "canceled"}:
        trade["result"] = kind
        trade["result_time"] = message["time"]
        trade["exit_price"] = update.get("price")
    trade["result_note"] = _preview(message["content"])
    trade["updates"].append(
        {
            "message_id": message["id"],
            "time": message["time"],
            "kind": kind,
            "price": update.get("price"),
            "move": update.get("move"),
            "partial_pct": update.get("partial_pct"),
            "content": _preview(message["content"], 240),
        }
    )


def _trade_key(row: dict[str, Any]) -> tuple[str, str, str, float, float, float, float | None]:
    return (
        str(row.get("time") or "")[:10],
        str(row.get("symbol") or ""),
        str(row.get("side") or ""),
        float(row.get("entry_low") or 0),
        float(row.get("entry_high") or 0),
        float(row.get("stop_loss") or 0),
        float(row["take_profit"]) if row.get("take_profit") is not None else None,
    )


def _is_profitable_price(trade: dict[str, Any], price: float) -> bool:
    entry = (float(trade.get("entry_low") or 0) + float(trade.get("entry_high") or 0)) / 2
    if entry <= 0:
        return False
    if trade.get("side") == "long":
        return price >= entry
    return price <= entry


def _symbol(text: str) -> str:
    m = re.search(r"米娅\s*([A-Z0-9]+)\s*短线合约交易策略", text, re.I)
    if m:
        return m.group(1).upper()
    m = re.search(r"\b(BTC|ZEC|ETH|SOL|DOGE|BNB|XRP|ADA|LINK)\b", text, re.I)
    return m.group(1).upper() if m else ""


def _price_after(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        m = re.search(rf"{re.escape(label)}\s*([0-9]+(?:\.[0-9]+)?)", text)
        if m:
            return float(m.group(1))
    return None


def _percent(text: str) -> float | None:
    values = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", text)]
    return max(values) if values else None


def _preview(text: str, limit: int = 120) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."
