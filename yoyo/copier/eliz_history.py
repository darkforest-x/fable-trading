from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from yoyo.copier.ai.deepseek_client import DeepseekClient
from yoyo.copier.config import DOCS_DIR, ROOT

CHANNEL_ID = "1226095782965415936"
RAW_PATH = ROOT / "data" / "eliz_raw_messages_2026.json"
TRADES_PATH = ROOT / "data" / "eliz_trades_2026.json"
REPORT_PATH = DOCS_DIR / "ELIZ_SIGNAL_ANALYSIS.md"
START = "2025-12-12T00:00:00+08:00"
END = "2026-06-12T23:59:59+08:00"


def build_eliz_trades(
    raw_path: Path = RAW_PATH,
    output_path: Path = TRADES_PATH,
    report_path: Path = REPORT_PATH,
) -> list[dict[str, Any]]:
    rows = _load_rows(raw_path)
    messages = [
        {
            "id": str(row.get("id") or "").split("-")[-1],
            "time": str(row.get("iso") or ""),
            "content": _content(str(row.get("text") or "")),
            "raw": str(row.get("text") or ""),
        }
        for row in rows
        if START <= str(row.get("iso") or "") <= END
    ]
    messages = [row for row in messages if row["id"] and row["time"] and row["content"]]
    messages.sort(key=lambda row: row["time"])

    trades: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    seen_opens: set[tuple[Any, ...]] = set()

    for message in messages:
        signal = _parse_open(message)
        if signal:
            key = (
                signal["time"],
                signal["symbol"],
                signal["side"],
                signal["entry_low"],
                signal["entry_high"],
                signal["stop_loss"],
            )
            if key not in seen_opens:
                seen_opens.add(key)
                trades.append(signal)
                active[signal["symbol"]] = signal
            continue

        update = _parse_update(message)
        if not update:
            continue
        trade = active.get(update["symbol"])
        if not trade:
            continue
        _apply_update(trade, message, update)
        if trade["result"] in {"win_tp", "partial_tp", "loss_sl", "breakeven", "closed"}:
            active.pop(trade["symbol"], None)

    replay = replay_parser(messages)
    summary = eliz_summary(trades, replay)
    payload = {
        "channel_id": CHANNEL_ID,
        "channel_name": "Eliz",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": {
            "start": messages[0]["time"] if messages else "",
            "end": messages[-1]["time"] if messages else "",
        },
        "raw_messages": len(messages),
        "summary": summary,
        "parser_replay": replay,
        "trades": trades,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(_report(payload), encoding="utf-8")
    return trades


def load_eliz_trades() -> list[dict[str, Any]]:
    if not TRADES_PATH.exists() and RAW_PATH.exists():
        return build_eliz_trades()
    try:
        payload = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in payload.get("trades") or [] if isinstance(row, dict)]


def eliz_summary(
    trades: list[dict[str, Any]] | None = None,
    replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = list(trades if trades is not None else load_eliz_trades())
    counts = Counter(str(row.get("result") or "unknown") for row in rows)
    managed = counts["win_tp"] + counts["partial_tp"] + counts["breakeven"] + counts["loss_sl"] + counts["closed"]
    non_loss = counts["win_tp"] + counts["partial_tp"] + counts["breakeven"] + counts["closed"]
    strict = counts["win_tp"] + counts["loss_sl"]
    months: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        months[str(row.get("time") or "")[:7]][str(row.get("result") or "unknown")] += 1
    rr_values = [float(row["realized_rr"]) for row in rows if row.get("realized_rr") is not None]
    backtest = _backtest(rows)
    return {
        "trades": len(rows),
        "win_tp": counts["win_tp"],
        "partial_tp": counts["partial_tp"],
        "breakeven": counts["breakeven"],
        "closed": counts["closed"],
        "loss_sl": counts["loss_sl"],
        "unknown": counts["unknown"],
        "managed": managed,
        "non_loss_rate": non_loss / managed * 100 if managed else 0.0,
        "strict_win_rate": counts["win_tp"] / strict * 100 if strict else 0.0,
        "long_count": sum(1 for row in rows if row.get("side") == "long"),
        "short_count": sum(1 for row in rows if row.get("side") == "short"),
        "limit_count": sum(1 for row in rows if row.get("order_type") == "limit"),
        "spot_count": sum(1 for row in rows if row.get("order_type") == "spot"),
        "filled": sum(1 for row in rows if row.get("filled")),
        "moved_be": sum(1 for row in rows if row.get("moved_be")),
        "risk_updates": sum(len(row.get("risk_updates") or []) for row in rows),
        "symbols": dict(Counter(str(row.get("symbol") or "-") for row in rows).most_common()),
        "monthly": {month: dict(counter) for month, counter in sorted(months.items())},
        "rr_count": len(rr_values),
        "rr_sum": sum(rr_values),
        "rr_avg": sum(rr_values) / len(rr_values) if rr_values else 0.0,
        "backtest": backtest,
        "parser_replay": replay or {},
    }


def replay_parser(messages: list[dict[str, Any]]) -> dict[str, Any]:
    async def run() -> dict[str, Any]:
        client = DeepseekClient()
        client.client = None
        counts: Counter[str] = Counter()
        actionable: Counter[str] = Counter()
        missed_open: list[dict[str, str]] = []
        context = ""
        for message in messages:
            expected_open = _looks_like_open(message["content"])
            result = await client.analyze(message["content"], author="Eliz", context=context)
            counts[result.intent] += 1
            if result.should_act:
                actionable[result.intent] += 1
            if expected_open and result.intent != "open":
                missed_open.append(
                    {
                        "time": message["time"],
                        "content": " ".join(message["content"].split())[:180],
                        "intent": result.intent,
                        "reason": result.reject_reason or "",
                    }
                )
            context = f"{message['content']}\n{context}"[:2400]
        return {
            "messages": len(messages),
            "intent_counts": dict(counts),
            "actionable_counts": dict(actionable),
            "expected_opens": sum(1 for message in messages if _looks_like_open(message["content"])),
            "missed_opens": missed_open[:20],
            "missed_open_count": len(missed_open),
        }

    return asyncio.run(run())


def _load_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def _clean(text: str) -> str:
    text = "".join(ch for ch in str(text or "") if unicodedata.category(ch) != "Cf")
    text = text.replace("：", ":")
    return re.sub(r"[ \t]+", " ", text)


def _content(text: str) -> str:
    text = _clean(text)
    if "-------------------------------" in text:
        text = text.split("-------------------------------", 1)[0]
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line in {"Eliz", "APP", "—", "[", "]", "添加反应", "转发", "更多"}:
            continue
        if line.startswith("@Eliz"):
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}", line):
            continue
        if re.match(r"20\d\d[年/]\d{1,2}", line):
            continue
        if line.startswith(":"):
            continue
        lines.append(line)
    return "\n".join(lines)


PRICE_RE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"


def _parse_open(message: dict[str, Any]) -> dict[str, Any] | None:
    text = _clean(message["content"])
    match = _open_match(text)
    if not match:
        return None
    symbol, side, entry_low, entry_high, stop_loss, order_type = match
    return {
        "id": message["id"],
        "time": message["time"],
        "symbol": symbol,
        "side": side,
        "order_type": order_type,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "take_profits": _take_profits(text),
        "result": "unknown",
        "result_time": None,
        "realized_rr": None,
        "filled": False,
        "moved_be": False,
        "risk_updates": [],
        "updates": [],
        "content": text,
    }


def _open_match(text: str) -> tuple[str, str, float, float, float, str] | None:
    patterns = [
        (
            rf"\b(?:limit\s+|spot\s+)?(long|short)\s+#?([A-Z][A-Z0-9]{{1,15}})\b\s+"
            rf"({PRICE_RE})(?:(?:\s*[-–—]\s*|\s+)({PRICE_RE}))?\s*(?:stop|sl)\s*({PRICE_RE})\b",
            "direction_first",
        ),
        (
            rf"\b#?([A-Z][A-Z0-9]{{1,15}})\b\s+(spot\s+|limit\s+)?(?:(long|short)\s+)?"
            rf"({PRICE_RE})(?:\s+({PRICE_RE}))?\s*(?:stop|sl)\s*({PRICE_RE})\b",
            "symbol_first",
        ),
    ]
    for pattern, kind in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        if kind == "direction_first":
            side = m.group(1).lower()
            symbol = m.group(2).upper()
            first = _price(m.group(3))
            second = _price(m.group(4)) if m.group(4) else first
            stop_loss = _price(m.group(5))
            order_type = "limit" if re.search(r"\blimit\b", text, re.I) else "market"
        else:
            symbol = m.group(1).upper()
            side = m.group(3).lower() if m.group(3) else ""
            first = _price(m.group(4))
            second = _price(m.group(5)) if m.group(5) else first
            stop_loss = _price(m.group(6))
            order_type = "spot" if (m.group(2) or "").strip().lower() == "spot" else "limit" if (m.group(2) or "").strip().lower() == "limit" else "market"
        if symbol in {"LIMIT", "SPOT", "LONG", "SHORT", "STOP"}:
            continue
        if symbol == "BTC" and max(first, second) < 1000 <= stop_loss:
            first *= 1000
            second *= 1000
        low, high = min(first, second), max(first, second)
        if not side:
            mid = (low + high) / 2
            if stop_loss < mid:
                side = "long"
            elif stop_loss > mid:
                side = "short"
        if side:
            return symbol, side, low, high, stop_loss, order_type
    return None


def _parse_update(message: dict[str, Any]) -> dict[str, Any] | None:
    text = _clean(message["content"])
    symbol_match = re.search(r"\b([A-Z0-9]{2,15})\s*(?:[|｜]\s*trades)?\s*:", text, re.I)
    if not symbol_match:
        return None
    symbol = symbol_match.group(1).upper()
    lower = text.lower()
    rr = _realized_rr(text)
    risk = _risk_pct(text)
    if "limit order filled" in lower or "限价订单已成交" in text:
        return {"symbol": symbol, "kind": "filled", "rr": rr, "risk": risk, "text": text}
    if re.search(r"\btp\d*\s+hit\b|tp1|目标位", lower):
        return {"symbol": symbol, "kind": "partial_tp", "rr": rr, "risk": risk, "text": text}
    if "stops moved to be" in lower or "stops moved" in lower:
        return {"symbol": symbol, "kind": "move_be", "rr": rr, "risk": risk, "text": text}
    if "stopped be" in lower or "closed be" in lower:
        return {"symbol": symbol, "kind": "breakeven", "rr": rr, "risk": risk, "text": text}
    if "stopped out" in lower:
        return {"symbol": symbol, "kind": "loss_sl", "rr": rr, "risk": risk, "text": text}
    if "closed slight loss" in lower or "closed in loss" in lower:
        return {"symbol": symbol, "kind": "loss_sl" if (rr is None or rr < 0) else "closed", "rr": rr, "risk": risk, "text": text}
    if "closed in profit" in lower or "closed in small profit" in lower or "closed in profits" in lower:
        close_pct = _close_pct(text)
        return {
            "symbol": symbol,
            "kind": "win_tp" if close_pct >= 99 else "partial_tp",
            "rr": rr,
            "risk": risk,
            "text": text,
        }
    if risk is not None:
        return {"symbol": symbol, "kind": "risk_update", "rr": rr, "risk": risk, "text": text}
    return None


def _apply_update(trade: dict[str, Any], message: dict[str, Any], update: dict[str, Any]) -> None:
    trade["updates"].append({"time": message["time"], **update})
    if update.get("rr") is not None:
        trade["realized_rr"] = update["rr"]
    if update.get("risk") is not None:
        trade["risk_updates"].append({"time": message["time"], "risk_pct": update["risk"]})
    kind = update["kind"]
    if kind == "filled":
        trade["filled"] = True
    elif kind == "move_be":
        trade["moved_be"] = True
    elif kind in {"partial_tp", "win_tp", "loss_sl", "breakeven"}:
        trade["result"] = kind
        trade["result_time"] = message["time"]
        if kind == "breakeven":
            trade["moved_be"] = True
    elif kind == "risk_update":
        pass


def _looks_like_open(text: str) -> bool:
    return _open_match(_clean(text)) is not None


def _take_profits(text: str) -> list[float]:
    m = re.search(rf"\btp\s+((?:{PRICE_RE}\s*){{1,8}})", text, re.I)
    if not m:
        return []
    return [_price(value) for value in re.findall(PRICE_RE, m.group(1))]


def _realized_rr(text: str) -> float | None:
    m = re.search(r"Realized\s+R/R\s*%?\s*:\s*(-?\d+(?:\.\d+)?)", text, re.I)
    return float(m.group(1)) if m else None


def _risk_pct(text: str) -> float | None:
    m = re.search(r"Risk\s+updated\s+to\s+(\d+(?:\.\d+)?)\s*%", text, re.I)
    return float(m.group(1)) if m else None


def _close_pct(text: str) -> float:
    m = re.search(r"\((\d+(?:\.\d+)?)\s*%\)", text)
    return float(m.group(1)) if m else 100.0


def _price(value: str) -> float:
    return float(value.replace(",", ""))


def _backtest(trades: list[dict[str, Any]], initial: float = 500.0, leverage: float = 5.0) -> dict[str, Any]:
    equity = initial
    peak = initial
    max_dd = 0.0
    counted = 0
    for trade in sorted(trades, key=lambda row: row["time"]):
        rr = trade.get("realized_rr")
        if rr is None:
            if trade.get("result") == "loss_sl":
                rr = -1.0
            elif trade.get("result") == "breakeven":
                rr = 0.0
            else:
                continue
        risk_pct = 1.0
        if trade.get("risk_updates"):
            risk_pct = float(trade["risk_updates"][-1]["risk_pct"])
        # Eliz reports R/R on the signal risk. Use the visible risk percentage instead of
        # full-margin price math, because many entries are spot/CMP and not fixed TP ladders.
        equity *= 1 + (float(rr) * risk_pct / 100)
        counted += 1
        peak = max(peak, equity)
        if peak:
            max_dd = max(max_dd, (peak - equity) / peak * 100)
    return {
        "initial_equity": initial,
        "final_equity": equity,
        "net_pnl": equity - initial,
        "return_pct": (equity / initial - 1) * 100 if initial else 0.0,
        "max_drawdown_pct": max_dd,
        "counted_trades": counted,
        "rule": "按消息 Realized R/R × 最近 risk% 回测；默认 risk=1%。",
    }


def _report(payload: dict[str, Any]) -> str:
    s = payload["summary"]
    monthly = []
    for month, counts in s["monthly"].items():
        total = sum(counts.values())
        good = counts.get("win_tp", 0) + counts.get("partial_tp", 0) + counts.get("breakeven", 0) + counts.get("closed", 0)
        monthly.append(
            f"- {month}: {total} 单 | 成功/保本 {good} | SL {counts.get('loss_sl', 0)} | 未知 {counts.get('unknown', 0)}"
        )
    replay = s["parser_replay"]
    missed = replay.get("missed_open_count", 0)
    expected = replay.get("expected_opens", 0)
    bt = s["backtest"]
    return f"""# Eliz 频道近半年交易统计与回放

频道：`https://discord.com/channels/1004707886657699901/{CHANNEL_ID}`

抓取范围：`{payload['range']['start']}` -> `{payload['range']['end']}`  
原始消息：{payload['raw_messages']} 条  
可归并开单：{s['trades']} 单

## 结果

- 完整盈利/全平盈利：{s['win_tp']} 单
- 部分止盈：{s['partial_tp']} 单
- 保本/BE：{s['breakeven']} 单
- 其他平仓：{s['closed']} 单
- 明确止损亏损：{s['loss_sl']} 单
- 未完成/未知：{s['unknown']} 单
- 已管理样本非亏损率：{s['non_loss_rate']:.1f}%
- 严格口径胜率：{s['strict_win_rate']:.1f}%
- 多单/空单：{s['long_count']} / {s['short_count']}
- 限价/现货：{s['limit_count']} / {s['spot_count']}
- 限价成交更新：{s['filled']} 次
- 移动 BE：{s['moved_be']} 次
- 风险比例更新：{s['risk_updates']} 次

## R/R 与回测

- 有明确 Realized R/R 的交易：{s['rr_count']} 单
- Realized R/R 合计：{s['rr_sum']:.2f}R
- 平均 Realized R/R：{s['rr_avg']:.2f}R
- 初始权益：{bt['initial_equity']:.2f} USDT
- 回测最终权益：{bt['final_equity']:.2f} USDT
- 回测净收益：{bt['net_pnl']:.2f} USDT
- 回测收益率：{bt['return_pct']:.2f}%
- 最大回撤：{bt['max_drawdown_pct']:.2f}%
- 计入回测交易：{bt['counted_trades']} 单

口径：{bt['rule']} 该频道经常发送 `Risk updated to x%`，所以回测按信号风险比例计算，不按全仓价格波动硬算。

## 自动解析回放

- 回放消息：{replay.get('messages', 0)} 条
- 历史可识别开仓样本：{expected} 条
- 开仓漏识别：{missed} 条
- 漏识别率：{(missed / expected * 100 if expected else 0):.1f}%
- Intent 分布：`{replay.get('intent_counts', {})}`
- 可执行动作分布：`{replay.get('actionable_counts', {})}`

## 月度

{chr(10).join(monthly)}

## 常见话术

- 开仓：`Xpl limit 0.0782 0.075 stop 0.067`
- 方向在前：`Long useless 0.1 0.093 stop 0.088`
- 做空：`Limit short link 9.58 9.75 stop 10.13`
- 限价成交：`Limit order filled`
- 移动止损：`Stops moved to BE`
- 部分止盈：`TP1 hit & stops moved to BE`
- 平仓：`Closed in profits (...) • Realized R/R`
- 止损：`Stopped out • Realized R/R: -1.00`
- 风险调整：`Risk updated to 2%`

## 文件

- 原始消息：`data/eliz_raw_messages_2026.json`
- 归并交易：`data/eliz_trades_2026.json`
"""
