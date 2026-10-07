from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yoyo.copier.config import DOCS_DIR, ROOT

CHANNEL_ID = "1226095564073205780"
RAW_PATH = ROOT / "data" / "woods_raw_messages_2026.json"
TRADES_PATH = ROOT / "data" / "woods_trades_2026.json"
REPORT_PATH = DOCS_DIR / "WOODS_SIGNAL_ANALYSIS.md"
CUTOFF_UTC = "2025-12-15T16:00:00"

PRICE_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?|\.\d+)(?:k)?"
SYMBOL_STOPWORDS = {
    "WOODS",
    "RE",
    "RELONG",
    "RELONGING",
    "RESHORT",
    "RISKY",
    "AGGRESSIVE",
    "SCALP",
    "LIMIT",
    "LONG",
    "SHORT",
    "SPOT",
    "TP",
    "SL",
    "STOP",
    "TRADES",
}


def build_woods_trades(
    raw_path: Path = RAW_PATH,
    output_path: Path = TRADES_PATH,
    report_path: Path = REPORT_PATH,
    *,
    cutoff_utc: str = CUTOFF_UTC,
) -> list[dict[str, Any]]:
    messages = _load_messages(raw_path, cutoff_utc=cutoff_utc)
    messages.sort(key=lambda row: row["time"])

    trades: list[dict[str, Any]] = []
    active: dict[str, list[dict[str, Any]]] = defaultdict(list)
    category_counts: Counter[str] = Counter()
    skipped_duplicates = 0

    for message in messages:
        content = message["content"]
        category_counts[_category(content)] += 1

        signal = _parse_open(message)
        if signal:
            duplicate = _find_recent_duplicate(trades, signal)
            if duplicate:
                skipped_duplicates += 1
                duplicate.setdefault("duplicate_message_ids", []).append(signal["id"])
                continue
            trades.append(signal)
            if signal.get("valid_levels"):
                active[signal["symbol"]].append(signal)
            continue

        update = _parse_update(message)
        if not update:
            continue
        trade = _latest_active(active, update["symbol"], allow_filled_only=update["kind"] != "cancel")
        if not trade:
            continue
        _apply_update(trade, update, message)
        if trade.get("result") in {"win_profit", "loss_sl", "breakeven", "canceled"}:
            active[trade["symbol"]] = [row for row in active[trade["symbol"]] if row["id"] != trade["id"]]

    summary = woods_summary(trades, messages, category_counts, skipped_duplicates)
    payload = {
        "channel_id": CHANNEL_ID,
        "channel_name": "Woods",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "range": {
            "start": messages[0]["time"] if messages else "",
            "end": messages[-1]["time"] if messages else "",
            "cutoff_utc": cutoff_utc,
        },
        "raw_messages": len(messages),
        "skipped_duplicates": skipped_duplicates,
        "summary": summary,
        "trades": trades,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_report(payload), encoding="utf-8")
    return trades


def load_woods_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists() and RAW_PATH.exists():
        return build_woods_trades()
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in payload.get("trades") or [] if isinstance(row, dict)]


def woods_summary(
    trades: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    category_counts: Counter[str] | None = None,
    skipped_duplicates: int = 0,
) -> dict[str, Any]:
    if trades is None and messages is None and category_counts is None and TRADES_PATH.exists():
        try:
            payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
            summary = payload.get("summary")
            if isinstance(summary, dict):
                return dict(summary)
        except (OSError, json.JSONDecodeError):
            pass
    rows = list(trades if trades is not None else load_woods_trades())
    counts = Counter(str(row.get("result") or "unknown") for row in rows)
    months: dict[str, Counter[str]] = defaultdict(Counter)
    symbols = Counter(str(row.get("symbol") or "-") for row in rows)
    rr_rows = [row for row in rows if row.get("realized_rr") is not None]
    for row in rows:
        months[str(row.get("time") or "")[:7]][str(row.get("result") or "unknown")] += 1
    settled = counts["win_profit"] + counts["loss_sl"]
    non_loss = counts["win_profit"] + counts["breakeven"]
    return {
        "messages": len(messages or []),
        "category_counts": dict(category_counts or Counter()),
        "trades": len(rows),
        "valid_trades": sum(1 for row in rows if row.get("valid_levels")),
        "invalid_levels": counts["invalid_levels"],
        "skipped_duplicates": skipped_duplicates,
        "filled": sum(1 for row in rows if row.get("filled")),
        "moved_stop": sum(1 for row in rows if row.get("moved_stop_count")),
        "partial_close": sum(1 for row in rows if row.get("partial_close_count")),
        "win_profit": counts["win_profit"],
        "loss_sl": counts["loss_sl"],
        "breakeven": counts["breakeven"],
        "canceled": counts["canceled"],
        "unknown": counts["unknown"],
        "settled": settled,
        "win_rate": counts["win_profit"] / settled * 100 if settled else 0,
        "non_loss_rate": non_loss / (non_loss + counts["loss_sl"]) * 100 if non_loss + counts["loss_sl"] else 0,
        "long_count": sum(1 for row in rows if row.get("side") == "long"),
        "short_count": sum(1 for row in rows if row.get("side") == "short"),
        "symbols": dict(symbols.most_common()),
        "monthly": {month: dict(counter) for month, counter in sorted(months.items())},
        "rr_count": len(rr_rows),
        "rr_sum": sum(float(row["realized_rr"]) for row in rr_rows),
        "rr_avg": sum(float(row["realized_rr"]) for row in rr_rows) / len(rr_rows) if rr_rows else None,
    }


def _load_messages(path: Path, *, cutoff_utc: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("messages") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        message_id = str(row.get("id") or "")
        time = str(row.get("time") or row.get("iso") or "")
        if not message_id or not time or time < cutoff_utc or message_id in seen:
            continue
        content = _content(str(row.get("content") or row.get("text") or ""))
        if not content:
            continue
        seen.add(message_id)
        out.append({"id": message_id, "time": time, "author": row.get("author") or "Woods", "content": content})
    return out


def _content(text: str) -> str:
    text = "".join(ch for ch in str(text or "") if unicodedata.category(ch) != "Cf")
    text = text.replace("：", ":")
    if "-------------------------------" in text:
        text = text.split("-------------------------------", 1)[0]
    text = re.sub(r"\(已编辑\).*", "", text, flags=re.S)
    text = re.sub(r"^@Woods粉\s*", "", text, flags=re.I)
    text = re.sub(r"^@Woods\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _category(text: str) -> str:
    if _parse_open({"id": "", "time": "", "content": text}):
        return "open"
    if _parse_update({"id": "", "time": "", "content": text}):
        return "update"
    lower = text.lower()
    if re.search(r"\b(watch|looking|gameplan|bias|liquidity|sweep|target|resistance|support)\b", lower):
        return "commentary"
    return "noise"


def _parse_open(message: dict[str, Any]) -> dict[str, Any] | None:
    text = message["content"]
    lower = text.lower()
    if re.search(r":\s*(?:limit order|stops? moved|tp\d?|closed|stopped|risk updated)", lower):
        return None
    if not re.search(r"\b(?:stop|sl)\b", lower):
        return None

    stop_match = re.search(
        rf"\b(?:stop|sl)\s*:?\s*(?:(?:\d+(?:\.\d+)?)%\s*)?(?:\(({PRICE_RE})\)|({PRICE_RE}))",
        text,
        re.I,
    )
    if not stop_match:
        return None
    stop_loss = _price(stop_match.group(1) or stop_match.group(2))

    before_stop = text[: stop_match.start()]
    prices = [_price(match.group(0)) for match in re.finditer(PRICE_RE, before_stop, re.I)]
    if not prices:
        return None
    entry_values = prices[-2:] if len(prices) >= 2 and _near_pair(prices[-2], prices[-1]) else [prices[-1]]

    symbol = _symbol(before_stop)
    if not symbol:
        return None
    side = _side(before_stop)
    entry_values, stop_loss = _normalize_levels(symbol, entry_values, stop_loss)
    entry_low = min(entry_values)
    entry_high = max(entry_values)
    avg_entry = (entry_low + entry_high) / 2
    if side is None:
        side = "long" if stop_loss < avg_entry else "short" if stop_loss > avg_entry else None
    if side is None:
        return None
    valid = (side == "long" and stop_loss < avg_entry) or (side == "short" and stop_loss > avg_entry)
    take_profit = _take_profit(text, symbol, stop_loss)
    return {
        "id": message["id"],
        "time": message["time"],
        "symbol": symbol,
        "side": side,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "source": "woods_shorthand",
        "order_type": "limit" if "limit" in lower else "market_or_unspecified",
        "valid_levels": valid,
        "result": "unknown" if valid else "invalid_levels",
        "result_time": None,
        "realized_rr": None,
        "filled": False,
        "moved_stop_count": 0,
        "partial_close_count": 0,
        "updates": [],
        "content": text,
    }


def _parse_update(message: dict[str, Any]) -> dict[str, Any] | None:
    text = message["content"]
    lower = text.lower()
    symbol = _update_symbol(text)
    if not symbol:
        return None
    kind = ""
    if "limit order cancelled" in lower or "order cancelled" in lower:
        kind = "cancel"
    elif "limit order filled" in lower or "order filled" in lower:
        kind = "filled"
    elif "stops moved" in lower or "stop moved" in lower:
        kind = "move_stop"
    elif re.search(r"\btp\s*\d|\btp\d\b", lower):
        kind = "partial_close"
    elif "closed in profit" in lower or "closed in small profit" in lower:
        kind = "win_profit"
    elif "stopped be" in lower or "breakeven" in lower or "break even" in lower:
        kind = "breakeven"
    elif "stopped out" in lower:
        kind = "loss_sl"
    else:
        return None
    rr = None
    rr_match = re.search(r"realized\s+r/r\s*:\s*(-?\d+(?:\.\d+)?)", lower, re.I)
    if rr_match:
        rr = float(rr_match.group(1))
    return {"kind": kind, "symbol": symbol, "realized_rr": rr, "content": text, "time": message["time"], "id": message["id"]}


def _apply_update(trade: dict[str, Any], update: dict[str, Any], message: dict[str, Any]) -> None:
    trade.setdefault("updates", []).append(
        {
            "id": message["id"],
            "time": message["time"],
            "kind": update["kind"],
            "realized_rr": update.get("realized_rr"),
            "content": message["content"],
        }
    )
    kind = update["kind"]
    if kind == "filled":
        trade["filled"] = True
        return
    if kind == "move_stop":
        trade["moved_stop_count"] = int(trade.get("moved_stop_count") or 0) + 1
        return
    if kind == "partial_close":
        trade["partial_close_count"] = int(trade.get("partial_close_count") or 0) + 1
        return
    if kind == "cancel":
        trade["result"] = "canceled"
    elif kind in {"win_profit", "loss_sl", "breakeven"}:
        trade["result"] = kind
    trade["result_time"] = message["time"]
    if update.get("realized_rr") is not None:
        trade["realized_rr"] = update["realized_rr"]


def _symbol(text: str) -> str:
    tokens = re.findall(r"\b[A-Z][A-Z0-9]{1,15}\b", text, re.I)
    for token in tokens:
        upper = token.upper()
        if upper not in SYMBOL_STOPWORDS and not upper.isdigit():
            if upper == "FART" and re.search(r"\bfart\s+coin\b", text, re.I):
                return "FARTCOIN"
            return upper
    return ""


def _update_symbol(text: str) -> str:
    match = re.search(r"\b([A-Z][A-Z0-9]{1,15})\s*(?:[|｜]\s*trades?)?\s*:", text, re.I)
    if not match:
        return _symbol(text)
    token = match.group(1).upper()
    return "" if token in SYMBOL_STOPWORDS else token


def _side(text: str) -> str | None:
    lower = text.lower()
    if re.search(r"\bshort(?:ing)?\b", lower):
        return "short"
    if re.search(r"\b(?:long|relong|relonging|longing|knife\s+catch)\b", lower):
        return "long"
    return None


def _price(value: str) -> float:
    raw = str(value or "").lower().replace(",", "")
    multiplier = 1000 if raw.endswith("k") else 1
    raw = raw[:-1] if raw.endswith("k") else raw
    if raw.startswith("."):
        raw = f"0{raw}"
    return float(raw) * multiplier


def _normalize_levels(symbol: str, entries: list[float], stop_loss: float) -> tuple[list[float], float]:
    values = [*entries, stop_loss]
    if symbol == "BTC":
        if max(values) < 1000 and min(values) >= 10:
            return [round(v * 1000, 10) for v in entries], round(stop_loss * 1000, 10)
        if stop_loss >= 1000 and any(v < 1000 for v in entries):
            return [round(v * 1000, 10) if v < 1000 else v for v in entries], stop_loss
        if stop_loss < 1000 and any(v >= 1000 for v in entries):
            return entries, round(stop_loss * 1000, 10)
    if symbol == "DOGE" and 0.5 <= min(values) and max(values) < 1:
        return [round(v / 10, 10) for v in entries], round(stop_loss / 10, 10)
    return entries, stop_loss


def _take_profit(text: str, symbol: str, stop_loss: float) -> float | None:
    match = re.search(rf"\btp(?:\s*\d+)?\s*:?\s*({PRICE_RE})", text, re.I)
    if not match:
        return None
    values, _ = _normalize_levels(symbol, [_price(match.group(1))], stop_loss)
    return values[0] if values else None


def _near_pair(a: float, b: float) -> bool:
    high = max(abs(a), abs(b), 1)
    return abs(a - b) / high <= 0.12


def _latest_active(
    active: dict[str, list[dict[str, Any]]],
    symbol: str,
    *,
    allow_filled_only: bool,
) -> dict[str, Any] | None:
    candidates = [row for row in active.get(symbol, []) if row.get("result") == "unknown"]
    if allow_filled_only:
        filled = [row for row in candidates if row.get("filled")]
        if filled:
            return filled[-1]
    return candidates[-1] if candidates else None


def _find_recent_duplicate(trades: list[dict[str, Any]], signal: dict[str, Any]) -> dict[str, Any] | None:
    for row in reversed(trades[-20:]):
        if row.get("symbol") != signal.get("symbol") or row.get("side") != signal.get("side"):
            continue
        if row.get("result") not in {"unknown", "canceled"}:
            continue
        if abs(float(row.get("entry_low") or 0) - float(signal.get("entry_low") or 0)) > 1e-8:
            continue
        if abs(float(row.get("entry_high") or 0) - float(signal.get("entry_high") or 0)) > 1e-8:
            continue
        if abs(float(row.get("stop_loss") or 0) - float(signal.get("stop_loss") or 0)) > 1e-8:
            continue
        if _hours_between(str(row.get("time") or ""), str(signal.get("time") or "")) <= 12:
            return row
    return None


def _hours_between(start: str, end: str) -> float:
    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 9999
    return abs((b - a).total_seconds()) / 3600


def _report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    monthly = summary.get("monthly") or {}
    month_lines = []
    for month, counts in monthly.items():
        wins = counts.get("win_profit", 0)
        losses = counts.get("loss_sl", 0)
        settled = wins + losses
        rate = wins / settled * 100 if settled else 0
        month_lines.append(f"- {month}: 胜率 {rate:.1f}% ({wins}/{settled})，总开仓 {sum(counts.values())}")
    symbols = ", ".join(f"{sym} {n}" for sym, n in list((summary.get("symbols") or {}).items())[:12])
    return "\n".join(
        [
            "# Woods 近半年交易统计",
            "",
            f"- 频道: `{CHANNEL_ID}`",
            f"- 范围: `{payload['range']['start']}` -> `{payload['range']['end']}`",
            f"- 原始消息: {payload['raw_messages']}",
            f"- 可归并开仓: {summary['trades']}",
            f"- 有效开仓: {summary['valid_trades']}",
            f"- 跳过重复开仓: {summary['skipped_duplicates']}",
            f"- 明确盈利: {summary['win_profit']}",
            f"- 明确止损: {summary['loss_sl']}",
            f"- 保本退出: {summary['breakeven']}",
            f"- 取消挂单: {summary['canceled']}",
            f"- 未结算/未知: {summary['unknown']}",
            f"- 明确盈亏胜率: {summary['win_rate']:.1f}% ({summary['win_profit']}/{summary['settled']})",
            f"- 非亏损率: {summary['non_loss_rate']:.1f}%",
            f"- 多/空: {summary['long_count']} / {summary['short_count']}",
            f"- 触发成交: {summary['filled']}",
            f"- 移动止损: {summary['moved_stop']}",
            f"- 部分止盈: {summary['partial_close']}",
            f"- Realized R/R: {summary['rr_count']} 条，均值 {summary['rr_avg']:.2f}" if summary.get("rr_avg") is not None else "- Realized R/R: 无",
            f"- 主要币种: {symbols}",
            "",
            "## Monthly",
            *month_lines,
            "",
            "说明: 统计基于 Discord 页面可见历史消息解析；胜率只计算明确盈利/明确止损，保本、取消、未知不计入胜率分母。",
        ]
    )
