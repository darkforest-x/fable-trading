from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from yoyo.copier.config import DOCS_DIR, ROOT

CHANNEL_ID = "1131521990814089276"
RAW_PATH = ROOT / "data" / "arthur_raw_messages_2026.json"
TRADES_PATH = ROOT / "data" / "arthur_trades_2026.json"
REPORT_PATH = DOCS_DIR / "ARTHUR_SIGNAL_ANALYSIS.md"

PRICE_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?:k)?"


def build_arthur_trades(
    raw_path: Path = RAW_PATH,
    output_path: Path = TRADES_PATH,
    report_path: Path = REPORT_PATH,
) -> list[dict[str, Any]]:
    rows = _load_rows(raw_path)
    messages = [
        {
            "id": str(row.get("id") or ""),
            "time": str(row.get("iso") or ""),
            "content": _content(str(row.get("content") or row.get("text") or "")),
            "raw": str(row.get("content") or row.get("text") or ""),
        }
        for row in rows
    ]
    messages = [row for row in messages if row["id"] and row["time"] and row["content"]]
    messages.sort(key=lambda row: row["time"])

    trades: list[dict[str, Any]] = []
    active: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending_last_symbol: str | None = None

    category_counts: Counter[str] = Counter()
    for message in messages:
        category_counts[_category(message["content"])] += 1

        signal = _parse_structured_open(message)
        if signal:
            trades.append(signal)
            if signal["valid_levels"]:
                active[signal["symbol"]].append(signal)
                pending_last_symbol = signal["symbol"]
            continue

        manual = _parse_manual_open(message)
        if manual:
            trades.append(manual)
            active[manual["symbol"]].append(manual)
            pending_last_symbol = manual["symbol"]
            continue

        update = _parse_update(message, pending_last_symbol)
        if not update:
            continue
        matched = _match_active(active, update)
        if not matched:
            continue
        _apply_update(matched, message, update)
        if update["kind"] in {"closed", "breakeven", "invalidated"}:
            active[matched["symbol"]] = [row for row in active[matched["symbol"]] if row["id"] != matched["id"]]

    summary = arthur_summary(trades, messages, category_counts)
    payload = {
        "channel_id": CHANNEL_ID,
        "channel_name": "Arthur行情分析",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": {
            "start": messages[0]["time"] if messages else "",
            "end": messages[-1]["time"] if messages else "",
        },
        "raw_rows": len(rows),
        "raw_messages": len(messages),
        "summary": summary,
        "trades": trades,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_report(payload), encoding="utf-8")
    return trades


def load_arthur_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists() and RAW_PATH.exists():
        return build_arthur_trades()
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in payload.get("trades") or [] if isinstance(row, dict)]


def arthur_summary(
    trades: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
    category_counts: Counter[str] | None = None,
) -> dict[str, Any]:
    rows = list(trades if trades is not None else load_arthur_trades())
    counts = Counter(str(row.get("result") or "unknown") for row in rows)
    months: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        months[str(row.get("time") or "")[:7]][str(row.get("result") or "unknown")] += 1
    rr_rows = [row for row in rows if row.get("realized_rr") is not None]
    valid_structured = [row for row in rows if row.get("source") == "structured" and row.get("valid_levels")]
    invalid_structured = [row for row in rows if row.get("source") == "structured" and not row.get("valid_levels")]
    manual = [row for row in rows if row.get("source") == "manual_missing_levels"]
    categories = dict(category_counts or Counter())
    return {
        "messages": len(messages or []),
        "trading_related": sum(categories.get(key, 0) for key in ("structured_open", "manual_open", "update", "plan_watch")),
        "category_counts": categories,
        "trades": len(rows),
        "valid_structured": len(valid_structured),
        "invalid_structured": len(invalid_structured),
        "manual_missing_levels": len(manual),
        "filled": sum(1 for row in rows if row.get("filled")),
        "partial_close": sum(1 for row in rows if row.get("partial_close")),
        "moved_stop": sum(1 for row in rows if row.get("moved_stop")),
        "closed": counts["closed"],
        "breakeven": counts["breakeven"],
        "partial_profit_be": counts["partial_profit_be"],
        "invalid_levels": counts["invalid_levels"],
        "unknown": counts["unknown"],
        "long_count": sum(1 for row in rows if row.get("side") == "long"),
        "short_count": sum(1 for row in rows if row.get("side") == "short"),
        "symbols": dict(Counter(str(row.get("symbol") or "-") for row in rows)),
        "monthly": {month: dict(counter) for month, counter in sorted(months.items())},
        "rr_count": len(rr_rows),
        "rr_sum": sum(float(row["realized_rr"]) for row in rr_rows),
        "actionability": {
            "auto_trade_safe": len(valid_structured),
            "requires_ai_or_manual_review": len(invalid_structured) + len(manual),
            "watch_only_messages": categories.get("plan_watch", 0),
        },
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def _content(text: str) -> str:
    text = text.replace("：", ":")
    if "-------------------------------" in text:
        text = text.split("-------------------------------", 1)[0]
    text = re.sub(r"\(已编辑\).*", "", text, flags=re.S)
    return re.sub(r"[ \t]+", " ", text).strip()


def _category(text: str) -> str:
    lower = text.lower()
    if _structured_match(text):
        return "structured_open"
    if _manual_open_match(text):
        return "manual_open"
    if re.search(r"\b(filled|closed|stops? to entry|moved|tp|breakeven|break even|invalidated|cancel)\b", lower):
        return "update"
    if re.search(r"\b(looking|watching|gameplan|will look|if we|setup|plan|bias|target|shorts?|longs?|bid|bids)\b", lower):
        return "plan_watch"
    return "noise"


def _parse_structured_open(message: dict[str, Any]) -> dict[str, Any] | None:
    match = _structured_match(message["content"])
    if not match:
        return None
    symbol, side, risk_pct, entry, tp, sl, filled = match
    valid = _valid_levels(side, entry, tp, sl)
    return {
        "id": message["id"],
        "time": message["time"],
        "symbol": symbol,
        "side": side,
        "source": "structured",
        "order_type": "market" if filled else "limit",
        "risk_pct": risk_pct,
        "entry_low": entry,
        "entry_high": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "position_filled_at_signal": filled,
        "filled": filled,
        "valid_levels": valid,
        "reject_reason": "" if valid else "direction/TP/SL levels conflict",
        "result": "unknown" if valid else "invalid_levels",
        "result_time": None,
        "realized_rr": None,
        "partial_close": False,
        "moved_stop": None,
        "updates": [],
        "content": message["content"],
    }


def _structured_match(text: str) -> tuple[str, str, float, float, float, float, bool] | None:
    pattern = (
        rf"\b([A-Z]{{2,12}})\s+(?:(?:Positional|Swing|Intraday)\s+)?(Long|Short)(?:\s+\w+)?\s+Risk\s*:\s*(\d+(?:\.\d+)?)%\s+"
        rf"Entry\s*:\s*({PRICE_RE})\s+TP\s*:\s*({PRICE_RE})\s+SL\s*:\s*({PRICE_RE})"
        rf".*?Position\s+Filled\s*:\s*(Yes|No)"
    )
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    return (
        m.group(1).upper(),
        m.group(2).lower(),
        float(m.group(3)),
        _price(m.group(4)),
        _price(m.group(5)),
        _price(m.group(6)),
        m.group(7).lower() == "yes",
    )


def _parse_manual_open(message: dict[str, Any]) -> dict[str, Any] | None:
    match = _manual_open_match(message["content"])
    if not match:
        return None
    symbol, side, risk_pct, maybe_stop = match
    return {
        "id": message["id"],
        "time": message["time"],
        "symbol": symbol,
        "side": side,
        "source": "manual_missing_levels",
        "order_type": "market",
        "risk_pct": risk_pct,
        "entry_low": None,
        "entry_high": None,
        "stop_loss": maybe_stop,
        "take_profit": None,
        "filled": True,
        "valid_levels": False,
        "reject_reason": "missing entry or complete stop/TP levels",
        "result": "unknown",
        "result_time": None,
        "realized_rr": None,
        "partial_close": False,
        "moved_stop": None,
        "updates": [],
        "content": message["content"],
    }


def _manual_open_match(text: str) -> tuple[str, str, float | None, float | None] | None:
    m = re.search(
        rf"\b(Longed|Shorted)\s+([A-Z]{{2,12}})(?:\s+(\d+(?:\.\d+)?)%\s+Risk)?(?:\s+({PRICE_RE})\s+SL)?",
        text,
        re.I,
    )
    if not m:
        return None
    return (
        m.group(2).upper(),
        "long" if m.group(1).lower() == "longed" else "short",
        float(m.group(3)) if m.group(3) else None,
        _price(m.group(4)) if m.group(4) else None,
    )


def _parse_update(message: dict[str, Any], fallback_symbol: str | None) -> dict[str, Any] | None:
    text = message["content"]
    lower = text.lower()
    symbol = _symbol(text) or fallback_symbol
    if not symbol:
        return None
    if "closed half" in lower or "closed hald" in lower:
        if "stops to entry" in lower or "stops moved to entry" in lower:
            return {"kind": "filled_partial_close_move_stop" if "filled" in lower else "partial_close_move_stop", "symbol": symbol, "text": text}
        if "filled" in lower:
            return {"kind": "filled_partial_close", "symbol": symbol, "text": text}
        return {"kind": "partial_close", "symbol": symbol, "text": text}
    if re.fullmatch(r"filled\s*[^\w]*", lower.strip()) or "bid filled" in lower:
        return {"kind": "filled", "symbol": symbol, "text": text}
    if "stops to entry" in lower or "stops moved to entry" in lower:
        return {"kind": "move_stop_entry", "symbol": symbol, "text": text}
    if "long closed at breakeven" in lower or "closed at breakeven" in lower:
        return {"kind": "breakeven", "symbol": symbol, "text": text}
    if "closed" in lower:
        return {"kind": "closed", "symbol": symbol, "text": text}
    if "invalidated" in lower:
        return {"kind": "invalidated", "symbol": symbol, "text": text}
    move = re.search(r"moved my stops? to (?:the low at\s*)?({PRICE_RE})", lower, re.I)
    if move:
        return {"kind": "move_stop_price", "symbol": symbol, "price": _price(move.group(1)), "text": text}
    return None


def _match_active(active: dict[str, list[dict[str, Any]]], update: dict[str, Any]) -> dict[str, Any] | None:
    rows = active.get(str(update.get("symbol") or "").upper()) or []
    if not rows:
        return None
    return rows[-1]


def _apply_update(trade: dict[str, Any], message: dict[str, Any], update: dict[str, Any]) -> None:
    trade["updates"].append({"time": message["time"], **update})
    kind = update["kind"]
    if kind == "filled":
        trade["filled"] = True
    elif kind == "filled_partial_close":
        trade["filled"] = True
        trade["partial_close"] = True
        if trade.get("moved_stop") == "entry":
            trade["result"] = "partial_profit_be"
    elif kind == "partial_close_move_stop":
        trade["partial_close"] = True
        trade["moved_stop"] = "entry"
        trade["result"] = "partial_profit_be"
    elif kind == "filled_partial_close_move_stop":
        trade["filled"] = True
        trade["partial_close"] = True
        trade["moved_stop"] = "entry"
        trade["result"] = "partial_profit_be"
    elif kind == "partial_close":
        trade["partial_close"] = True
        trade["result"] = "partial_profit_be" if trade.get("moved_stop") == "entry" else trade.get("result", "unknown")
    elif kind == "move_stop_entry":
        trade["moved_stop"] = "entry"
        if trade.get("partial_close"):
            trade["result"] = "partial_profit_be"
    elif kind == "move_stop_price":
        trade["moved_stop"] = update.get("price")
    elif kind == "breakeven":
        trade["result"] = "partial_profit_be" if trade.get("partial_close") else "breakeven"
        trade["result_time"] = message["time"]
    elif kind == "closed":
        trade["result"] = "closed"
        trade["result_time"] = message["time"]
    elif kind == "invalidated":
        trade["result"] = "invalidated"
        trade["result_time"] = message["time"]


def _symbol(text: str) -> str | None:
    skip = {"LFG", "TP", "SL", "BE", "NYO", "FVG", "NPOC", "HTF", "LTF"}
    known = ("BTC", "ETH", "CRV", "XPL", "AVAX", "SOL", "HYPE", "ADA", "SILVER")
    known_match = re.search(r"\b(" + "|".join(known) + r")\b", text, re.I)
    if known_match:
        return known_match.group(1).upper()
    for m in re.finditer(r"\b([A-Z]{2,12})\b", text):
        symbol = m.group(1).upper()
        if symbol not in skip:
            return symbol
    return None


def _valid_levels(side: str, entry: float, tp: float, sl: float) -> bool:
    if side == "long":
        return tp > entry > sl
    return tp < entry < sl


def _price(value: str) -> float:
    value = value.replace(",", "").lower()
    if value.endswith("k"):
        return float(value[:-1]) * 1000
    return float(value)


def _report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    rows = payload["trades"]
    monthly = []
    for month, counts in s["monthly"].items():
        total = sum(counts.values())
        monthly.append(
            f"- {month}: {total} 条开仓/执行样本 | "
            f"部分盈利/保本 {counts.get('partial_profit_be', 0) + counts.get('breakeven', 0)} | "
            f"关闭 {counts.get('closed', 0)} | 无结果 {counts.get('unknown', 0)} | 无效 {counts.get('invalid_levels', 0)}"
        )
    trade_lines = []
    for row in rows:
        entry = "-"
        if row.get("entry_low") is not None:
            entry = str(row.get("entry_low"))
        trade_lines.append(
            f"- {row['time'][:16]} `{row['symbol']}` {row['side'].upper()} | "
            f"{row['source']} | entry {entry} | SL {row.get('stop_loss') or '-'} | "
            f"TP {row.get('take_profit') or '-'} | {row['result']}"
        )
    category = s["category_counts"]
    return f"""# Arthur 行情分析频道历史开单分析

频道：`https://discord.com/channels/1004707886657699901/{CHANNEL_ID}`

抓取范围：`{payload['range']['start']}` -> `{payload['range']['end']}`  
原始抓取：{payload.get('raw_rows', payload['raw_messages'])} 条  
有效文本消息：{payload['raw_messages']} 条

## 结论

- Arthur 不是标准信号流，更多是交易计划、盘中观察、跟单账户更新。
- 真正可安全自动执行的结构化开仓只有 {s['valid_structured']} 条。
- 需要 AI/人工复核的开仓类消息 {s['actionability']['requires_ai_or_manual_review']} 条，主要原因是缺少入场/止损/止盈，或方向与 TP/SL 互相矛盾。
- 计划/观察类消息 {s['actionability']['watch_only_messages']} 条，只应转发或记录，不应直接下单。
- 当前样本太少，不能像 ChartPrime/Eliz 那样给出可靠胜率和收益曲线。

## 消息分类

- 结构化开仓：{category.get('structured_open', 0)} 条
- 手动开仓但缺关键价位：{category.get('manual_open', 0)} 条
- 成交/平仓/移动止损更新：{category.get('update', 0)} 条
- 计划/观察/如果触发：{category.get('plan_watch', 0)} 条
- 其他/噪音：{category.get('noise', 0)} 条

## 可归并交易

- 总开仓/执行样本：{s['trades']} 条
- 可自动跟单：{s['valid_structured']} 条
- 无效结构化：{s['invalid_structured']} 条
- 缺价位手动开仓：{s['manual_missing_levels']} 条
- 已成交更新：{s['filled']} 次
- 部分平仓：{s['partial_close']} 次
- 移动止损：{s['moved_stop']} 次
- 多/空：{s['long_count']} / {s['short_count']}

## 明细

{chr(10).join(trade_lines) if trade_lines else "- 暂无"}

## 月度

{chr(10).join(monthly) if monthly else "- 暂无"}

## 自动执行建议

- 可执行：`BTC Long Risk: 2% Entry: 60780 TP: 66400 SL: 58500 Position Filled: No`
- 可执行：`BTC Positional Long Risk: 3% Entry: 63680 TP: 97k SL: 56900 Position Filled: Yes`
- 拒绝/需复核：`CRV Long Risk: 2% Entry: 0.2276 TP: 0.2072 SL: 0.28`，因为 Long 的 TP 低于入场且 SL 高于入场。
- 只记录不下单：`Looking for...`、`Watching...`、`will long if...`、`gameplan...`。
- 缺价位时只转发/提醒：`Longed ETH 1% Risk`、`Longed CRV 1% 0.2220 SL`。

## 文件

- 原始消息：`data/arthur_raw_messages_2026.json`
- 归并交易：`data/arthur_trades_2026.json`
"""


if __name__ == "__main__":
    build_arthur_trades()
