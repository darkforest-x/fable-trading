from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from yoyo.copier.config import DOCS_DIR, ROOT

RAW_PATH = ROOT / "data" / "chartprime_raw_messages_2026.json"
TRADES_JSON = ROOT / "data" / "chartprime_trades_2026.json"
TRADES_CSV = ROOT / "data" / "chartprime_trades_2026.csv"
REPORT_MD = DOCS_DIR / "CHARTPRIME_CRYPTO_SIGNALS_ANALYSIS.md"
PNL_MD = DOCS_DIR / "CHARTPRIME_PNL_BACKTEST.md"

# 2026-01-01 00:00 Asia/Shanghai == 2025-12-31 16:00 UTC.
START = datetime(2025, 12, 31, 16, 0, tzinfo=timezone.utc)
END = datetime(2026, 6, 9, tzinfo=timezone.utc)

OPEN_RE = re.compile(
    r"#?(?P<symbol>[A-Z0-9]{2,15})\s*\([^)]+\).*?"
    r"(?P<side>LONG|SHORT)\s*:\s*[^,]+,\s*"
    r"ENTRY\s*:\s*(?P<entry>[^,]+),\s*"
    r"EXIT\s*:\s*(?P<tps>.*?),\s*[-–]?\s*SL\s*[:：]\s*(?P<sl>[0-9.]+)",
    re.IGNORECASE,
)

TARGET_WORDS = {
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
}


@dataclass
class Trade:
    id: str
    message_id: str
    time: str
    symbol: str
    side: str
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profits: list[float]
    result: str = "unknown"
    tp_hits: int = 0
    result_updates: list[dict[str, Any]] = field(default_factory=list)
    content: str = ""


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def as_float(text: str) -> float:
    cleaned = text.strip().replace("..", ".")
    return float(cleaned)


def parse_numbers(text: str) -> list[float]:
    normalized = text.replace("..", ".")
    return [as_float(x) for x in re.findall(r"\d+(?:\.\d+)?", normalized)]


def parse_open(row: dict[str, Any]) -> Trade | None:
    content = row["content"].replace("，", ",")
    match = OPEN_RE.search(content)
    if not match:
        return None
    entry_nums = parse_numbers(match.group("entry"))
    tp_nums = parse_numbers(match.group("tps"))
    if len(entry_nums) < 2 or not tp_nums:
        return None
    entry_low = min(entry_nums[0], entry_nums[1])
    entry_high = max(entry_nums[0], entry_nums[1])
    return Trade(
        id=row["id"],
        message_id=row["id"],
        time=row["iso"],
        symbol=match.group("symbol").upper(),
        side=match.group("side").lower(),
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=as_float(match.group("sl")),
        take_profits=tp_nums[:6],
        content=row["content"],
    )


def extract_update_symbol(content: str) -> str | None:
    match = re.search(r"#([A-Z0-9]{2,15})", content)
    return match.group(1).upper() if match else None


def target_hits(content: str) -> int:
    lowered = content.lower()
    hits = 0
    for word, value in TARGET_WORDS.items():
        if re.search(rf"\b{word}\s+target\s+hit", lowered):
            hits = max(hits, value)
    all_match = re.search(r"targets\s+hit\s*:\s*(\d+)", lowered)
    if all_match:
        hits = max(hits, int(all_match.group(1)))
    if "all targets hit" in lowered:
        hits = max(hits, 6)
    return hits


def is_sl_update(content: str) -> bool:
    lowered = content.lower()
    return bool(
        re.search(r"\bsl\s+hit\b", lowered)
        or re.search(r"\bhit\s+sl\b", lowered)
        or re.search(r"stop\s*loss\s*(hit|triggered)", lowered)
    )


def attach_updates(trades: list[Trade], rows: list[dict[str, Any]]) -> None:
    by_symbol: dict[str, list[Trade]] = defaultdict(list)
    trade_idx = 0
    trades_sorted = sorted(trades, key=lambda t: t.time)
    for row in sorted(rows, key=lambda r: r["iso"]):
        row_time = row["iso"]
        while trade_idx < len(trades_sorted) and trades_sorted[trade_idx].time <= row_time:
            by_symbol[trades_sorted[trade_idx].symbol].append(trades_sorted[trade_idx])
            trade_idx += 1

        content = row["content"]
        symbol = extract_update_symbol(content)
        if not symbol or symbol not in by_symbol:
            continue
        candidates = by_symbol[symbol]
        if not candidates:
            continue
        trade = candidates[-1]
        if trade.message_id == row["id"]:
            continue

        hit = target_hits(content)
        sl = is_sl_update(content)
        if not hit and not sl:
            continue
        update = {
            "message_id": row["id"],
            "time": row["iso"],
            "content": content,
        }
        if hit:
            update["tp_hits"] = hit
            trade.tp_hits = max(trade.tp_hits, min(hit, len(trade.take_profits)))
        if sl:
            update["sl_hit"] = True
            if trade.tp_hits == 0:
                trade.result = "loss_sl"
        trade.result_updates.append(update)

    for trade in trades:
        if trade.result == "loss_sl":
            continue
        if trade.tp_hits > 0:
            trade.result = "win_tp"


def pct_move(trade: Trade, exit_price: float) -> float:
    entry = (trade.entry_low + trade.entry_high) / 2
    if trade.side == "long":
        return (exit_price - entry) / entry
    return (entry - exit_price) / entry


def net_pnl_for_margin(trade: Trade, margin: float = 100.0, leverage: float = 5.0) -> float:
    notional = margin * leverage
    fee = notional * 0.001
    if trade.result == "loss_sl":
        gross = notional * pct_move(trade, trade.stop_loss)
        return gross - fee
    if trade.tp_hits <= 0:
        return 0.0
    part = notional / max(len(trade.take_profits), 1)
    gross = sum(part * pct_move(trade, tp) for tp in trade.take_profits[: trade.tp_hits])
    return gross - fee


def compound_result(
    trades: list[Trade],
    initial_equity: float = 500.0,
    leverage: float = 5.0,
) -> dict[str, float | int | bool]:
    equity = initial_equity
    peak = initial_equity
    max_drawdown = 0.0
    settled_count = 0
    wiped_out = False
    for trade in sorted(trades, key=lambda item: item.time):
        if trade.result == "unknown":
            continue
        settled_count += 1
        return_rate = round(net_pnl_for_margin(trade, margin=100.0, leverage=leverage), 4) / 100.0
        equity = max(0.0, equity * (1 + return_rate))
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
        if equity <= 0:
            wiped_out = True
            break
    return {
        "initial_equity": initial_equity,
        "final_equity": equity,
        "net_pnl": equity - initial_equity,
        "return_pct": (equity / initial_equity - 1) * 100 if initial_equity else 0.0,
        "max_drawdown_pct": max_drawdown,
        "settled_trades": settled_count,
        "wiped_out": wiped_out,
    }


def write_csv(trades: list[Trade]) -> None:
    fields = [
        "time",
        "symbol",
        "side",
        "entry_low",
        "entry_high",
        "stop_loss",
        "take_profits",
        "result",
        "tp_hits",
        "net_pnl_100u_5x",
        "message_id",
    ]
    with TRADES_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            writer.writerow(
                {
                    "time": trade.time,
                    "symbol": trade.symbol,
                    "side": trade.side,
                    "entry_low": trade.entry_low,
                    "entry_high": trade.entry_high,
                    "stop_loss": trade.stop_loss,
                    "take_profits": "/".join(f"{x:g}" for x in trade.take_profits),
                    "result": trade.result,
                    "tp_hits": trade.tp_hits,
                    "net_pnl_100u_5x": round(net_pnl_for_margin(trade), 4),
                    "message_id": trade.message_id,
                }
            )


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(x) for x in row) + " |")
    return "\n".join(out)


def render_reports(trades: list[Trade], rows: list[dict[str, Any]]) -> None:
    settled = [t for t in trades if t.result != "unknown"]
    wins = [t for t in trades if t.result == "win_tp"]
    losses = [t for t in trades if t.result == "loss_sl"]
    unknown = [t for t in trades if t.result == "unknown"]
    tp_counter = Counter(t.tp_hits for t in trades)
    symbol_counter = Counter(t.symbol for t in trades)
    side_counter = Counter(t.side for t in trades)
    months: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        months[trade.time[:7]].append(trade)

    monthly_rows = []
    for month in sorted(months):
        items = months[month]
        mw = sum(1 for t in items if t.result == "win_tp")
        ml = sum(1 for t in items if t.result == "loss_sl")
        mu = sum(1 for t in items if t.result == "unknown")
        settled_count = mw + ml
        win_rate = f"{mw / settled_count * 100:.1f}%" if settled_count else "-"
        avg_tp = sum(t.tp_hits for t in items) / len(items)
        monthly_rows.append([month, len(items), mw, ml, mu, win_rate, f"{avg_tp:.2f}"])

    recent_rows = [
        [t.time.replace("T", " ")[:19], t.symbol, t.side, t.result, t.tp_hits]
        for t in trades[-20:]
    ]
    loss_rows = [
        [t.time.replace("T", " ")[:19], t.symbol, t.side, f"{t.entry_low:g}-{t.entry_high:g}", f"{t.stop_loss:g}"]
        for t in losses
    ]
    symbol_rows = []
    for symbol, count in symbol_counter.most_common(15):
        items = [t for t in trades if t.symbol == symbol]
        sw = sum(1 for t in items if t.result == "win_tp")
        sl = sum(1 for t in items if t.result == "loss_sl")
        settled_count = sw + sl
        symbol_rows.append(
            [
                symbol,
                count,
                sw,
                sl,
                f"{sw / settled_count * 100:.1f}%" if settled_count else "-",
                f"{sum(t.tp_hits for t in items) / len(items):.2f}",
            ]
        )

    report = f"""# ChartPrime crypto-signals 频道统计

频道：`https://discord.com/channels/748196867879469219/988830102957736027`

统计时间：2026-01-01 到 2026-06-08，时区 Asia/Shanghai。

数据来源：重新通过已登录 Discord Web 页面滚动抓取，原始消息 `{len(rows)}` 条，覆盖 {rows[0]['iso']} 到 {rows[-1]['iso']}。本报告只统计标准 `LONG/SHORT + ENTRY + EXIT + SL` 开仓格式。

## 核心结论

{md_table(["指标", "数值"], [
    ["可归并开仓", len(trades)],
    ["至少命中 1 个 TP", len(wins)],
    ["明确 SL 亏损", len(losses)],
    ["未完成 / 未知", len(unknown)],
    ["已结算样本", len(settled)],
    ["已结算胜率", f"{len(wins) / len(settled) * 100:.1f}%" if settled else "-"],
    ["全样本首 TP 命中率", f"{len(wins) / len(trades) * 100:.1f}%" if trades else "-"],
    ["平均命中 TP 数", f"{sum(t.tp_hits for t in trades) / len(trades):.2f}" if trades else "-"],
    ["最高命中 TP 数", max(tp_counter) if tp_counter else 0],
    ["多单 / 空单", f"{side_counter.get('long', 0)} / {side_counter.get('short', 0)}"],
])}

## 月度表现

{md_table(["月份", "开仓", "胜", "亏", "未知", "胜率", "平均 TP"], monthly_rows)}

## TP 命中分布

{md_table(["命中 TP 数", "笔数"], [[k, tp_counter[k]] for k in sorted(tp_counter)])}

## 高频币种

{md_table(["币种", "开仓", "胜", "亏", "胜率", "平均 TP"], symbol_rows)}

## 最近 20 笔开单

{md_table(["时间", "币种", "方向", "结果", "TP"], recent_rows)}

## 明确亏损样本

{md_table(["时间", "币种", "方向", "进场", "SL"], loss_rows)}

## 输出文件

- 原始消息：`data/chartprime_raw_messages_2026.json`
- 逐笔明细 JSON：`data/chartprime_trades_2026.json`
- 逐笔明细 CSV：`data/chartprime_trades_2026.csv`
"""
    REPORT_MD.write_text(report, encoding="utf-8")

    compound = compound_result(trades)
    pnl_rows = [
        ["初始权益", f"{compound['initial_equity']:.2f} USDT"],
        ["开仓笔数", len(trades)],
        ["参与复利的已结算笔数", compound["settled_trades"]],
        ["期末权益", f"{compound['final_equity']:.2f} USDT"],
        ["净收益", f"{compound['net_pnl']:.2f} USDT"],
        ["累计收益率", f"{compound['return_pct']:.2f}%"],
        ["最大回撤", f"{compound['max_drawdown_pct']:.2f}%"],
        ["是否归零", "是" if compound["wiped_out"] else "否"],
    ]
    PNL_MD.write_text(
        f"""# ChartPrime crypto-signals 理论收益测算

频道：`https://discord.com/channels/748196867879469219/988830102957736027`

区间：2026-01-01 到 2026-06-08，时区 Asia/Shanghai。

本报告由重新抓取的逐笔明细生成，不是 OKX 实盘账单。

## 测算假设

| 参数 | 取值 |
| --- | ---: |
| 初始权益 | 500 USDT |
| 每单保证金 | 下单时的全部账户权益 |
| 杠杆 | 5x |
| 每单名义仓位 | 下单时账户权益 × 5 |
| 复利方式 | 每笔收益或亏损滚入下一笔 |
| 止盈方式 | 6 个 TP 等分，每个 TP 平 1/6 |
| 未命中 TP 的剩余仓位 | 按入场价退出，收益记 0 |
| 明确 SL | 全仓按 SL 退出 |
| 手续费 | 开仓 0.05% + 平仓 0.05% |

该结果是按信号时间顺序逐笔结算的理论复利模型，假设上一笔完全结束后才投入下一笔；未计滑点、资金费率、同时持仓和交易所仓位限制。

## 结论

{md_table(["指标", "数值"], pnl_rows)}
""",
        encoding="utf-8",
    )


def main() -> None:
    rows = json.loads(RAW_PATH.read_text(encoding="utf-8"))
    rows = sorted(rows, key=lambda r: r["iso"])
    rows_2026 = [r for r in rows if START <= parse_time(r["iso"]) < END]
    trades = [trade for row in rows_2026 if (trade := parse_open(row))]
    attach_updates(trades, rows_2026)
    payload = {
        "channel_id": "988830102957736027",
        "source": str(RAW_PATH.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_messages": len(rows),
        "messages_2026": len(rows_2026),
        "trades": [asdict(t) | {"net_pnl_100u_5x": round(net_pnl_for_margin(t), 4)} for t in trades],
    }
    TRADES_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(trades)
    render_reports(trades, rows_2026)
    settled = [t for t in trades if t.result != "unknown"]
    wins = [t for t in trades if t.result == "win_tp"]
    losses = [t for t in trades if t.result == "loss_sl"]
    print(
        json.dumps(
            {
                "raw_messages": len(rows),
                "messages_2026": len(rows_2026),
                "trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "unknown": len(trades) - len(settled),
                "win_rate": round(len(wins) / len(settled) * 100, 2) if settled else None,
                "outputs": [str(TRADES_JSON), str(TRADES_CSV), str(REPORT_MD), str(PNL_MD)],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
