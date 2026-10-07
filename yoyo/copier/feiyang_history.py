from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from yoyo.copier.config import ROOT

CHANNEL_ID = "1356581750914027590"
RAW_PATH = ROOT / "data" / "feiyang_raw_messages_2026.json"
TRADES_PATH = ROOT / "data" / "feiyang_trades_2026.json"


def build_feiyang_trades(raw_path: Path = RAW_PATH, output_path: Path = TRADES_PATH) -> list[dict[str, Any]]:
    try:
        rows = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    messages = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        text = str(row.get("text") or "")
        timestamp = _timestamp(text)
        content = _content(text)
        key = (timestamp, _normalize(content))
        if not timestamp or not content or key in seen:
            continue
        seen.add(key)
        messages.append({"time": timestamp, "content": content})
    messages.sort(key=lambda item: item["time"])

    trades: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    latest_symbol = ""
    for message in messages:
        content = message["content"]
        signal = _parse_signal(content, message["time"])
        if signal:
            symbol = signal["symbol"]
            previous = active.get(symbol)
            if previous and _hours_between(previous["time"], signal["time"]) <= 6:
                previous["result"] = "superseded"
                previous["result_time"] = signal["time"]
                previous["result_note"] = "后续策略修改/替代"
            trades.append(signal)
            active[symbol] = signal
            latest_symbol = symbol
            continue

        symbol = _symbol(content) or latest_symbol
        trade = active.get(symbol)
        if not trade:
            continue
        outcome = _outcome(content)
        if outcome:
            trade["result"] = outcome
            trade["result_time"] = message["time"]
            trade["result_note"] = _preview(content)
            if outcome in {"win_tp", "loss_sl", "breakeven", "canceled"}:
                active.pop(symbol, None)
        if re.search(r"止盈\s*50%|平一半|止盈一半", content):
            trade["partial_close"] = True
        move = re.search(r"(?:成本价|保本价|成本)\s*([0-9]+(?:\.[0-9]+)?)", content)
        if move:
            trade["moved_stop"] = float(move.group(1))

    payload = {
        "channel_id": CHANNEL_ID,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "raw_messages": len(messages),
        "trades": trades,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return trades


def load_feiyang_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists() and RAW_PATH.exists():
        return build_feiyang_trades()
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in payload.get("trades") or [] if isinstance(row, dict)]


def feiyang_summary() -> dict[str, Any]:
    trades = load_feiyang_trades()
    counts = {
        key: sum(1 for row in trades if row.get("result") == key)
        for key in ("win_tp", "loss_sl", "breakeven", "canceled", "superseded", "unknown")
    }
    settled = counts["win_tp"] + counts["loss_sl"]
    return {
        "trades": len(trades),
        **counts,
        "settled": settled,
        "win_rate": counts["win_tp"] / settled * 100 if settled else 0,
        "partial_close": sum(1 for row in trades if row.get("partial_close")),
        "moved_stop": sum(1 for row in trades if row.get("moved_stop") is not None),
        "long_count": sum(1 for row in trades if row.get("side") == "long"),
        "short_count": sum(1 for row in trades if row.get("side") == "short"),
    }


def _timestamp(text: str) -> str:
    match = re.search(r"2026年(\d{1,2})月(\d{1,2})日星期.\s+(\d{1,2}):(\d{2})", text)
    if match:
        return f"2026-{int(match.group(1)):02d}-{int(match.group(2)):02d}T{int(match.group(3)):02d}:{match.group(4)}:00"
    match = re.search(r"2026/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", text)
    if match:
        return f"2026-{int(match.group(1)):02d}-{int(match.group(2)):02d}T{int(match.group(3)):02d}:{match.group(4)}:00"
    return ""


def _content(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [
        line
        for line in lines
        if line not in {"比特币飞扬", "APP", "—", "[", "]", "添加反应", "转发", "更多"}
        and not re.fullmatch(r"\d{1,2}:\d{2}", line)
        and not line.startswith("2026年")
        and not re.match(r"2026/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}", line)
        and not re.fullmatch(r"\d+", line)
        and not line.startswith(":")
    ]
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).replace("，", ",").replace("：", ":")


def _parse_signal(text: str, timestamp: str) -> dict[str, Any] | None:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line.startswith("飞扬合约策略"):
        return None
    symbol_match = re.search(r"具体产品[:：]\s*([A-Z0-9]+)", text, re.I)
    side_match = re.search(r"进行方向[:：]\s*(做多|做空)", text)
    entry_match = re.search(
        r"进场点位[:：]\s*([0-9]+(?:\.[0-9]+)?)(?:\s*(?:-|–|—)\s*([0-9]+(?:\.[0-9]+)?))?",
        text,
    )
    stop_match = re.search(r"止损点位[:：]\s*([0-9]+(?:\.[0-9]+)?)", text)
    target_match = re.search(r"止盈点位[:：]\s*([0-9]+(?:\.[0-9]+)?)", text)
    if not symbol_match or not side_match or not entry_match or not stop_match:
        return None
    low = _price(entry_match.group(1))
    high = _price(entry_match.group(2)) if entry_match.group(2) else low
    return {
        "time": timestamp,
        "symbol": symbol_match.group(1).upper(),
        "side": "long" if side_match.group(1) == "做多" else "short",
        "entry_low": min(low, high),
        "entry_high": max(low, high),
        "stop_loss": float(stop_match.group(1)),
        "take_profit": float(target_match.group(1)) if target_match else None,
        "result": "unknown",
        "result_time": None,
        "result_note": "",
        "partial_close": False,
        "moved_stop": None,
    }


def _symbol(text: str) -> str:
    match = re.search(r"\b(BTC|ETH|BNB|ZEC|DOGE|SOL|XRP|ADA|LINK)\b", text, re.I)
    return match.group(1).upper() if match else ""


def _outcome(text: str) -> str:
    if re.search(r"取消|撤销|撤掉", text):
        return "canceled"
    if re.search(r"触发止损", text):
        return "loss_sl"
    if re.search(r"触发成本价|成本价.*出局|没有什么盈亏|没有盈亏", text):
        return "breakeven"
    if re.search(r"全部止盈|止盈出局|出局吧.*盈利|获利.*出局", text):
        return "win_tp"
    return ""


def _hours_between(left: str, right: str) -> float:
    return (datetime.fromisoformat(right) - datetime.fromisoformat(left)).total_seconds() / 3600


def _preview(text: str, limit: int = 120) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 3] + "..."


def _price(value: str) -> float:
    if value.startswith("0") and "." not in value and len(value) > 1:
        return float(f"0.{value[1:]}")
    return float(value)
