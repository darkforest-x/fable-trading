"""Build a compact, static chart/data bundle for the frozen inverse 1.5x account.

The account ledger is captured from the native VectorBT order callback used by
``cloud_close_inverse_examples``. This module never changes its paths, sizing,
or entry/exit decisions; it only records the already frozen fills and source
market bars for browsing.
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_inverse_examples as examples
from yoyo.evaluation.cloud_close_reward_paths import _ema_points_12h
from yoyo.monitor.cloud_signal_plans import stop_pct_for
from yoyo.monitor.monday_fib import week_window


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "yoyo/monitor/static/strategy-booklet/cloud-v17-history"
HOUR_MS = 3_600_000
STEP = 12 * HOUR_MS
BEFORE, AFTER = 64, 16
INK, GRAY = "#223343", "#5e6c79"
UP, DOWN = "#00877c", "#cf555a"
FAST, SLOW, RAIL = "#397cb2", "#ba801f", "#70578c"


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _json_gzip(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=9) as stream:
            stream.write(payload)


def _num(value: Any) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


def _utc(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").isoformat().replace("+00:00", "Z")


def _bjt(ms: int, fmt: str = "%m/%d") -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime(fmt)


def _safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", symbol)


def _xml(value: Any) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _missing_ranges(expected: np.ndarray, available: set[int]) -> list[dict[str, int]]:
    missing = [int(t) for t in expected if int(t) not in available]
    if not missing:
        return []
    ranges: list[dict[str, int]] = []
    start = previous = missing[0]
    count = 1
    for stamp in missing[1:]:
        if stamp == previous + STEP:
            previous, count = stamp, count + 1
            continue
        ranges.append({"startMs": start, "endMs": previous, "count": count})
        start = previous = stamp
        count = 1
    ranges.append({"startMs": start, "endMs": previous, "count": count})
    return ranges


def _format_reason(reason: str) -> str:
    return {
        "stop": "止损", "stop_gap_open": "跳空止损", "ema_flip": "云层翻转平仓",
        "ema_flip_open": "开盘云层翻转平仓", "take_profit": "止盈", "take_profit_open": "开盘止盈",
        "data_gap_markout": "数据缺口结算", "series_tail_close": "序列尾结算",
        "stale_12h_markout": "陈旧12H结算", "delisting_settlement": "退市结算",
        "reclaim_failed_at_close": "收盘失守回收线",
    }.get(reason, reason)


def _native_fills(fills: pd.DataFrame, data: dict, funded: pd.DataFrame) -> pd.DataFrame:
    """Use the same receipt enrichment as the frozen four-example native callback."""
    if fills.empty:
        raise ValueError("Native execution emitted no actual order rows")
    part = fills[fills.underlying_event_id.isin(funded.index)].copy()
    selected = data["selected"].set_index(["event_id", "slot"])
    selected_rows = selected.loc[list(zip(part.underlying_event_id, part.col))]
    part["action"] = np.where(part.stage.eq(0), "entry",
                              np.where(part.stage.eq(2), "trim", selected_rows.exit_reason.to_numpy()))
    part["fill_price"] = part.underlying_entry_price * (1 + part.underlying_side * (part.price - 1) / 1.5)
    part["symbol"] = part.underlying_event_id.map(funded.symbol)
    part["side"] = part.underlying_event_id.map(funded.side)
    # The native engine resolves each intrabar order to an H1 interval, not a tick timestamp.
    part["phase"] = np.where(part.stage.eq(3), "intrabar", "open")
    part = part.rename(columns={"underlying_event_id": "event_id", "hour": "hour_ms", "col": "child_slot"})
    keep = ["event_id", "symbol", "side", "hour_ms", "phase", "action", "child_slot", "fill_price",
            "net_units", "fees", "underlying_cash"]
    return part[keep].sort_values(["event_id", "hour_ms", "child_slot", "action"], kind="stable").reset_index(drop=True)


def _fill_rows(group: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for r in group.itertuples(index=False):
        rows.append({
            "hourMs": int(r.hour_ms), "phase": str(r.phase), "action": str(r.action),
            "childSlot": int(r.child_slot), "fillPrice": _num(r.fill_price),
            "netUnits": _num(r.net_units), "fees": _num(r.fees), "cashPnl": _num(r.underlying_cash),
        })
    return rows


def _draw_chart(record: dict[str, Any], frame: pd.DataFrame, ema: pd.DataFrame,
                fills: pd.DataFrame, path: pd.Series, monday: pd.DataFrame,
                dest: Path) -> dict[str, Any]:
    """Write one standalone SVG, leaving each missing 12H candle as empty time."""
    start, end = int(record["windowStartMs"]), int(record["windowEndMs"])
    expected = np.arange(start, end + STEP, STEP, dtype=np.int64)
    ix = pd.Index(expected)
    visible = frame.reindex(ix)
    visible_ema = ema.reindex(ix)
    have = set(int(t) for t in frame.index)
    gaps = _missing_ranges(expected, have)

    width, height = 1280, 560
    left, right, top, bottom = 72., 1156., 118., 492.
    n = len(expected)
    x = np.linspace(left, right, n) if n > 1 else np.array([left])
    values = []
    for col in ("low", "high"):
        values.extend([float(v) for v in visible[col].dropna().to_numpy()])
    for col in ("ema20", "ema50"):
        values.extend([float(v) for v in visible_ema[col].dropna().to_numpy()])
    values.extend(float(r.fill_price) for r in fills.itertuples() if pd.notna(r.fill_price))
    rail = float(path.stop_price)
    if not values or min(values) <= 0:
        raise ValueError(f"No positive chart prices for {record['id']}")
    lo, hi = min(values + [rail]), max(values + [rail])
    use_log = hi / lo > 8
    if use_log:
        low_t, high_t = math.log(lo), math.log(hi)
        pad = max((high_t - low_t) * .12, .04)
        low_t, high_t = low_t - pad, high_t + pad
        y = lambda value: bottom - (math.log(float(value)) - low_t) / (high_t - low_t) * (bottom - top)
    else:
        pad = max((hi - lo) * .13, hi * .002)
        low_t, high_t = lo - pad, hi + pad
        y = lambda value: bottom - (float(value) - low_t) / (high_t - low_t) * (bottom - top)

    is_long = int(record["side"]) == 1
    color = lambda i: UP if float(visible.close.iat[i]) >= float(visible.open.iat[i]) else DOWN
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="34" y="43" font-size="25" font-weight="650" fill="{INK}">{_xml(record["symbol"])} / USDT</text>',
        f'<text x="34" y="72" font-size="13" fill="{GRAY}">OKX 永续 · 12H · {"多单" if is_long else "空单"} · {pd.Timestamp(int(record["entryMs"]), unit="ms", tz="UTC").year} 年 · event {record["id"]}</text>',
        f'<text x="1244" y="43" text-anchor="end" font-size="22" font-weight="600" fill="{UP if record["netPnl"] >= 0 else DOWN}">{record["netPnl"]:+,.2f} U</text>',
        f'<text x="1244" y="70" text-anchor="end" font-size="12" fill="{GRAY}">本笔资金净盈亏 · 1.5×</text>',
    ]

    # Missing timestamps keep their normal x position; shaded bands make source gaps visible.
    for g in gaps:
        first = (g["startMs"] - start) // STEP
        last = (g["endMs"] - start) // STEP
        gx0 = x[first] - 4
        gx1 = x[last] + 4
        parts.append(f'<rect x="{gx0:.1f}" y="{top}" width="{max(gx1-gx0,5):.1f}" height="{bottom-top}" fill="#f4f5f6"/>')
        if gx1 - gx0 > 32:
            parts.append(f'<text x="{(gx0+gx1)/2:.1f}" y="{top+18}" text-anchor="middle" font-size="9" fill="#77828b">缺K×{g["count"]}</text>')

    # Horizontal price grid and right-side labels.
    for frac in np.linspace(0, 1, 6):
        yy = top + frac * (bottom - top)
        value = math.exp(high_t - frac * (high_t - low_t)) if use_log else high_t - frac * (high_t - low_t)
        parts.append(f'<path d="M {left} {yy:.1f} H {right}" stroke="#e9edef" stroke-width="1"/>')
        parts.append(f'<text x="{right+12}" y="{yy+4:.1f}" font-size="11" fill="{GRAY}">{value:.5g}</text>')

    # Historical frozen stop/1.618 rail. It is drawn only across the original signal week.
    entry_key = int(record["entryMs"]) - STEP
    week = week_window(entry_key)
    rx1 = (int(week["end"]) - start) / STEP
    rx2 = (int(week["expires"]) - start) / STEP
    if rx2 >= 0 and rx1 <= n - 1:
        xa = float(np.interp(max(0., rx1), np.arange(n), x))
        xb = float(np.interp(min(n - 1., rx2), np.arange(n), x))
        parts.append(f'<path d="M {xa:.1f} {y(rail):.1f} H {xb:.1f}" stroke="{RAIL}" stroke-width="1.5" stroke-dasharray="7 5"/>')
        rail_label = "当周 1.618 外轨 / 止损" if int(record["mondayBarCount"]) == 24 else "冻结止损价（周一K线有缺）"
        parts.append(f'<text x="{min(xb, right-4):.1f}" y="{y(rail)-6:.1f}" text-anchor="end" font-size="10" fill="{RAIL}">{rail_label} {rail:.5g}</text>')

    # EMA lines break at missing data and across a reset. No forward-filled prices are drawn.
    for field, stroke in (("ema20", FAST), ("ema50", SLOW)):
        line = []
        for i, value in enumerate(visible_ema[field].to_numpy()):
            if pd.notna(value) and pd.notna(visible.close.iat[i]):
                line.append(f"{x[i]:.1f},{y(value):.1f}")
            elif len(line) > 1:
                parts.append(f'<polyline points="{" ".join(line)}" fill="none" stroke="{stroke}" stroke-width="1.4"/>')
                line = []
            else:
                line = []
        if len(line) > 1:
            parts.append(f'<polyline points="{" ".join(line)}" fill="none" stroke="{stroke}" stroke-width="1.4"/>')

    # One wick and one real body per available 12H candle.
    bar_width = min(7.0, max(1.8, (right - left) / max(n, 1) * .62))
    for i in range(n):
        if pd.isna(visible.open.iat[i]):
            continue
        c = color(i)
        xx = x[i]
        yo, yc = y(visible.open.iat[i]), y(visible.close.iat[i])
        yh, yl = y(visible.high.iat[i]), y(visible.low.iat[i])
        parts.append(f'<path d="M {xx:.1f} {yh:.1f} V {yl:.1f}" stroke="{c}" stroke-width="1.15"/>')
        body_top, body_bottom = min(yo, yc), max(yo, yc)
        if body_bottom - body_top < 1.25:
            parts.append(f'<path d="M {xx-bar_width/2:.1f} {yo:.1f} H {xx+bar_width/2:.1f}" stroke="{c}" stroke-width="1.7"/>')
        else:
            parts.append(f'<rect x="{xx-bar_width/2:.1f}" y="{body_top:.1f}" width="{bar_width:.1f}" height="{body_bottom-body_top:.1f}" fill="{c}" stroke="{c}" stroke-width=".7"/>')

    # Collapse only identical same-hour/action receipts for display; every raw order remains in event JSON.
    plotted = fills[fills.action.ne("trim")].groupby(["hour_ms", "action"], sort=True).fill_price
    low_prices, high_prices = plotted.min(), plotted.max()
    if not np.allclose(low_prices.to_numpy(), high_prices.to_numpy(), rtol=1e-9, atol=1e-12):
        raise ValueError(f"Native fills disagree within a chart marker group for {record['id']}")
    visible_fills = plotted.first().reset_index().sort_values("hour_ms", kind="stable")
    label_rows = []
    for num, r in enumerate(visible_fills.itertuples(index=False)):
        stamp = int(r.hour_ms) // STEP * STEP
        if stamp < start or stamp > end:
            continue
        bar_idx = int((stamp - start) // STEP)
        xx, yy = float(x[bar_idx]), y(r.fill_price)
        action = str(r.action)
        if action == "entry":
            label = "开多" if is_long else "开空"
            c = "#185a8b"
        elif action.startswith("take_profit"):
            label = "翻倍止盈" if np.isclose(r.fill_price, 2 * float(path.entry_price)) and is_long else "2R 止盈"
            c = "#986311"
        elif action.startswith("stop"):
            label, c = "止损", "#a23742"
        elif action.startswith("ema_flip"):
            label, c = "余仓平仓", INK
        else:
            label, c = _format_reason(action), INK
        dx = 10 if xx < (left + right) / 2 else -10
        dy = -20 if (num % 2 == 0) else 24
        anchor = "start" if dx > 0 else "end"
        parts.append(f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4.1" fill="{c}" stroke="#fff" stroke-width="1.4"/>')
        label_rows.append((xx, yy, dx, dy, anchor, c, label, float(r.fill_price)))
    for xx, yy, dx, dy, anchor, c, label, price in label_rows:
        lx, ly = xx + dx, yy + dy
        parts.append(f'<path d="M {xx:.1f} {yy:.1f} L {lx:.1f} {ly:.1f}" stroke="{c}" stroke-width=".8"/>')
        parts.append(f'<text x="{lx:.1f}" y="{ly-3:.1f}" text-anchor="{anchor}" font-size="10" fill="{c}" stroke="#fff" stroke-width="3" paint-order="stroke">{_xml(label)} {price:.5g}</text>')

    # Requested window positions include missing slots. X labels are BJT, matching the existing 12H booklet.
    tick_idx = np.unique(np.linspace(0, n - 1, 7, dtype=int))
    for i in tick_idx:
        parts.append(f'<path d="M {x[i]:.1f} {bottom} v 5" stroke="#aab4bb"/>')
        parts.append(f'<text x="{x[i]:.1f}" y="{bottom+23}" text-anchor="middle" font-size="11" fill="{GRAY}">{_bjt(int(expected[i]))}</text>')
    parts.append(f'<text x="{left}" y="{height-18}" font-size="10" fill="{GRAY}">UTC+8 日期 · 可见K线 {len(have)}/{n} · 缺失K线 {n-len(have)}{(" · 对数轴" if use_log else "")}</text>')
    parts.append(f'<path d="M {left} {bottom} H {right}" stroke="#aab4bb" stroke-width=".8"/>')
    parts.append('</svg>')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(parts) + "\n")
    return {"expectedBars": int(n), "availableBars": int(len(have)), "missingBars": int(n - len(have)),
            "missingRanges": gaps, "logScale": bool(use_log)}


def build() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    funded = pd.read_csv(examples.BASE / "funded_inverse_1.5x.csv.gz", index_col="event_id",
                         float_precision="round_trip")
    tape = pd.read_csv(examples.BASE / "selected_paths.csv.gz", dtype={"symbol": str},
                       float_precision="round_trip")
    if len(funded) != 1252 or not funded.index.is_unique:
        raise ValueError(f"Expected 1252 unique funded rows, got {len(funded)}")
    primary = tape[tape.tp_r.eq(0)].copy()
    if primary.event_id.duplicated().any() or set(primary.event_id) != set(funded.index):
        raise ValueError("The tp_r=0 source paths do not bijectively match funded rows")
    primary = primary.set_index("event_id").loc[funded.index]

    # Build the exact corrected native inputs and use the original callback receipt path.
    _, quotes, sources = examples.original.source.inputs()
    data, _ = examples.original.old.parent.assemble(
        tape, examples.original.old.changed_limits(tape, quotes), "OKX", quotes)
    market = examples.original.old.exposure.underlying(data, quotes)
    multiples = pd.Series(1.5, index=market[-1].event_id)
    receipts: list[pd.DataFrame] = []

    def fill_sink(fills: pd.DataFrame) -> None:
        receipts.append(_native_fills(fills, data, funded))

    hourly = pd.read_csv(examples.BASE / "hourly_inverse_1.5x.csv.gz", index_col=0,
                         parse_dates=True, float_precision="round_trip")

    def verify(_, replay_data, replay, curve, multipliers, slip) -> None:
        if slip != 0 or set(replay.index) != set(funded.index) or not replay.index.is_unique:
            raise ValueError("Native inverse account roster or cost scenario differs from the frozen funded input")
        if not curve.index.equals(hourly.index):
            raise ValueError("Native hourly receipt calendar differs from the frozen account")
        np.testing.assert_allclose(curve[hourly.columns], hourly, rtol=1e-9, atol=1e-5)
        np.testing.assert_allclose(replay.cash_pnl.reindex(funded.index), funded.cash_pnl, rtol=1e-9, atol=1e-5)

    result = examples.original.exposure.simulate(
        data, market, quotes, multiples, 0., OUT, "history", snapshot=verify, fill_sink=fill_sink)
    if result.get("unresolved") or len(receipts) != 1:
        raise ValueError("Native fill receipt was unresolved or not emitted exactly once")
    fills = receipts[0]
    native_cash = fills.groupby("event_id").underlying_cash.sum().reindex(funded.index)
    np.testing.assert_allclose(native_cash, funded.cash_pnl, rtol=1e-9, atol=1e-6)
    if native_cash.isna().any() or set(fills.event_id) != set(funded.index):
        raise ValueError("All 1252 funded rows must have native order receipts")

    # Load and cache real 12H candles; do not synthesize bars across gaps or source limits.
    source_frames: dict[str, pd.DataFrame] = {}
    source_ema: dict[str, pd.DataFrame] = {}
    source_hourly: dict[str, pd.DataFrame] = {}
    for symbol in sorted(funded.symbol.unique()):
        hourly_bars, bars = examples.original.parent.repair.market.frames(sources[symbol])
        bars = bars[["open", "high", "low", "close"]].astype(float).sort_index()
        if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
            raise ValueError(f"Invalid source 12H index for {symbol}")
        ema = _ema_points_12h(bars.reset_index().rename(columns={"index": "time"}).to_dict("records"))
        ema.index = bars.index
        source_frames[symbol] = bars
        source_ema[symbol] = ema[["ema20", "ema50", "count", "ready"]]
        source_hourly[symbol] = hourly_bars[["low", "high"]]

    positions = funded.sort_values(["entry_ms"], kind="stable")
    # Use a deterministic tie-breaker even when two different symbols enter on one hour.
    positions = positions.assign(_event=positions.index.astype(str)).sort_values(["entry_ms", "_event"], kind="stable")
    records: list[dict[str, Any]] = []
    event_payloads: list[tuple[str, dict[str, Any]]] = []
    chart_specs: list[tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.DataFrame]] = []
    symbol_windows: dict[str, list[tuple[int, int]]] = {}

    for number, (event_id, position) in enumerate(positions.drop(columns="_event").iterrows(), 1):
        stable_id = f"cloud-v17-{number:06d}"
        path = primary.loc[event_id]
        symbol = str(position.symbol)
        entry_ms, exit_ms = int(position.entry_ms), int(position.exit_bar_ms)
        start = entry_ms - BEFORE * STEP
        end = (exit_ms // STEP) * STEP + AFTER * STEP
        bars = source_frames[symbol]
        ema = source_ema[symbol]
        chart_fills = fills[fills.event_id.eq(event_id)].copy()
        if chart_fills.empty:
            raise ValueError(f"Missing native actual order rows for {event_id}")
        if not np.isclose(float(chart_fills.underlying_cash.sum()), float(position.cash_pnl), rtol=1e-9, atol=1e-6):
            raise ValueError(f"Per-trade native cash differs for {event_id}")
        # The primary funded runner exits at the frozen path's H1 bar and price.
        entry_rows = chart_fills[chart_fills.action.eq("entry")]
        if entry_rows.empty or not np.allclose(entry_rows.fill_price, float(path.entry_price), rtol=1e-9, atol=1e-9):
            raise ValueError(f"Native entry receipts differ from selected path for {event_id}")
        exits = chart_fills[~chart_fills.action.isin(["entry", "trim"])].copy()
        child_exits = []
        for r in exits.itertuples(index=False):
            selected_row = data["selected"].set_index(["event_id", "slot"]).loc[(event_id, int(r.child_slot))]
            child_exits.append({
                "hourMs": int(r.hour_ms), "phase": str(r.phase), "childSlot": int(r.child_slot),
                "reason": str(r.action), "reasonLabel": _format_reason(str(r.action)),
                "price": _num(r.fill_price), "cashPnl": _num(r.underlying_cash),
                "tpR": float(selected_row.tp_r), "entryOrder": _num(selected_row.entry_order),
            })
        child_reasons = sorted(set(item["reason"] for item in child_exits))
        reasons = child_reasons[0] if len(child_reasons) == 1 else "mixed_child_exits"
        fees = float(chart_fills.fees.sum())
        notional = float(position.notional)
        symbol_data_url = f"symbols/{_safe_symbol(symbol)}.json.gz"
        event_url = f"events/{stable_id}.json.gz"
        chart_url = f"charts/{stable_id}.svg"
        record = {
            "id": stable_id, "eventId": str(event_id), "symbol": symbol, "side": int(position.side),
            "entryMs": entry_ms, "entryAt": _utc(entry_ms), "entryPrice": float(path.entry_price),
            "exitMs": exit_ms, "exitAt": _utc(exit_ms), "exitPrice": float(path.exit_price),
            "exitTimePrecision": "1h_bar", "exitPhase": str(path.exit_phase),
            "netPnl": float(position.cash_pnl), "netReturn": float(position.net_return),
            "netReturnPct": float(position.net_return * 100), "reason": reasons,
            "reasonLabel": "、".join(_format_reason(v) for v in child_reasons),
            "exitReason": str(path.exit_reason), "exitReasonLabel": _format_reason(str(path.exit_reason)),
            "exitReasons": child_reasons, "notional": notional, "fees": fees,
            "exposure": float(position.exposure), "chartUrl": chart_url, "dataUrl": event_url,
            "searchText": f"{stable_id} {event_id} {symbol} {('long' if int(position.side) == 1 else 'short')} {_utc(entry_ms)} {reasons}",
            "windowStartMs": start, "windowEndMs": end,
        }
        records.append(record)
        symbol_windows.setdefault(symbol, []).append((start, end))

        # Use only tp_r=0/extra=false as the one stable main exit field. Keep every other actual child exit.
        main_selected = data["selected"][(data["selected"].event_id.eq(event_id)) & data["selected"].slot.mod(4).eq(3)]
        if len(main_selected) != 1:
            raise ValueError(f"Expected one funded runner slot for {event_id}")
        main_slot = int(main_selected.slot.iat[0])
        if not any(item["childSlot"] == main_slot for item in child_exits):
            raise ValueError(f"Funded runner exit receipt missing for {event_id}")
        main_exit = next(item for item in child_exits if item["childSlot"] == main_slot)
        if main_exit["hourMs"] != exit_ms or not np.isclose(main_exit["price"], float(path.exit_price), rtol=1e-9, atol=1e-9):
            raise ValueError(f"Funded runner native exit differs from frozen selected path for {event_id}")

        # A Monday rail is computed from the exact archived hourly source; missing bars remain missing.
        week = week_window(entry_ms - STEP)
        monday = bars.iloc[:0]
        # The OHLC chart source is 12H; use the matching actual 1H quote source for Monday levels.
        monday = source_hourly[symbol].loc[int(week["start"]):int(week["end"]) - 1, ["low", "high"]]
        monday_count = int(len(monday))
        if monday_count == 24:
            low, high = float(monday.low.min()), float(monday.high.max())
            rail = low - .618 * (high - low) if int(position.side) == 1 else high + .618 * (high - low)
            expected_stop = rail * (1 - int(position.side) * stop_pct_for(symbol))
            if not np.isclose(expected_stop, float(path.stop_price), rtol=1e-8, atol=1e-9):
                raise ValueError(f"Frozen Monday rail differs from selected stop for {event_id}")
        else:
            rail = float(path.stop_price)
        record["mondayBarCount"] = monday_count
        record["nativeOrderCount"] = int(len(chart_fills))
        record["trimCount"] = int(chart_fills.action.eq("trim").sum())
        record["childExitCount"] = int(len(child_exits))

        # Requested timestamps include possible absent source bars; store actual OHLC only in shared symbol data.
        expected = np.arange(start, end + STEP, STEP, dtype=np.int64)
        available = set(int(t) for t in bars.index)
        missing = _missing_ranges(expected, available)
        event = {
            "schemaVersion": 1, "id": stable_id, "eventId": str(event_id), "symbol": symbol,
            "symbolDataUrl": f"../{symbol_data_url}", "side": int(position.side),
            "entryMs": entry_ms, "entryPrice": float(path.entry_price), "exitMs": exit_ms,
            "exitPrice": float(path.exit_price), "exitTimePrecision": "1h_bar",
            "exitPhase": str(path.exit_phase), "exitReason": str(path.exit_reason),
            "netPnl": float(position.cash_pnl), "netReturn": float(position.net_return),
            "notional": notional, "fees": fees, "exposure": float(position.exposure),
            "window": {"timeframe": "12h", "beforeEntryBars": BEFORE, "afterFundedExitBars": AFTER,
                       "startMs": start, "endMs": end, "expectedBars": int(len(expected)),
                       "availableBars": int(sum(1 for t in expected if int(t) in available)),
                       "missingBars": int(sum(1 for t in expected if int(t) not in available)),
                       "missingRanges": missing},
            "mondayRail": {"level": float(rail), "barCount": monday_count,
                           "expectedBars": 24, "complete": bool(monday_count == 24),
                           "source": "exact hourly OHLC window; no gaps filled"},
            "fills": _fill_rows(chart_fills), "childExits": child_exits,
            "costs": {"feeTotal": fees, "feeRows": int(len(chart_fills)),
                      "ratesFromFrozenPath": sorted(float(v) for v in tape.loc[tape.event_id.eq(event_id), "exit_fee_rate"].drop_duplicates())},
        }
        event_payloads.append((event_url, event))
        chart_specs.append((record, bars, ema, chart_fills, path, monday))

    # Source files are scoped to the union of requested chart windows for each symbol.
    symbol_manifest: dict[str, Any] = {}
    for symbol, windows in sorted(symbol_windows.items()):
        bars, ema = source_frames[symbol], source_ema[symbol]
        keep = np.zeros(len(bars), dtype=bool)
        for start, end in windows:
            keep |= (bars.index.to_numpy() >= start) & (bars.index.to_numpy() <= end)
        source_rows = []
        for stamp, row, em in zip(bars.index.to_numpy()[keep], bars.loc[keep].itertuples(index=False),
                                  ema.loc[keep].itertuples(index=False)):
            source_rows.append([int(stamp), _num(row.open), _num(row.high), _num(row.low), _num(row.close),
                                _num(em.ema20), _num(em.ema50), int(em.count), bool(em.ready)])
        url = f"symbols/{_safe_symbol(symbol)}.json.gz"
        _json_gzip(OUT / url, {"schemaVersion": 1, "symbol": symbol, "timeframe": "12h",
                               "columns": ["timeMs", "open", "high", "low", "close", "ema20", "ema50", "emaCount", "emaReady"],
                               "sourceCoverageStartMs": int(bars.index.min()) if len(bars) else None,
                               "sourceCoverageEndMs": int(bars.index.max()) if len(bars) else None,
                               "rows": source_rows})
        symbol_manifest[symbol] = {"dataUrl": url, "barCount": len(source_rows),
                                   "sourceCoverageStartMs": int(bars.index.min()) if len(bars) else None,
                                   "sourceCoverageEndMs": int(bars.index.max()) if len(bars) else None}

    for url, event in event_payloads:
        _json_gzip(OUT / url, event)

    chart_audits = {}
    for record, bars, ema, chart_fills, path, monday in chart_specs:
        # Chart coverage is built from the real 12H source only, with a visible empty slot for every gap.
        meta = _draw_chart(record, bars, ema, chart_fills, path, monday, OUT / record["chartUrl"])
        record["chartAudit"] = meta
        chart_audits[record["id"]] = meta

    fees = float(fills.fees.sum())
    if not np.isclose(float(funded.cash_pnl.sum()), native_cash.sum(), rtol=1e-9, atol=1e-6):
        raise ValueError("Full funded cash does not equal all native fill cash")
    manifest = {
        "schemaVersion": 1, "strategyId": "cloud-v17-history", "title": "Cloud V1.7 · 反向资金账户历史单",
        "generatedAt": pd.Timestamp.now(tz="UTC").isoformat(), "tradeCount": len(records),
        "account": {"source": "exp-cloud-close-alt-season-delivery-20261109-v1/opposite/funded_inverse_1.5x.csv.gz",
                    "exposure": 1.5, "startBalance": 1000.0, "fundedRows": len(funded),
                    "netPnl": float(funded.cash_pnl.sum()), "endingBalance": float(1000.0 + funded.cash_pnl.sum()),
                    "fundedNotional": float(funded.notional.sum()), "totalFees": fees,
                    "feeTreatment": "actual native order receipt fees; no added fee or slippage",
                    "funding": "omitted in frozen source"},
        "costs": {"makerFeeRate": 0.0002, "takerFeeRate": 0.0005,
                  "basis": "per actual fill receipt; source delivery settlements retain their frozen fee rate",
                  "slippage": 0.0},
        "source": {"paths": ["opposite/funded_inverse_1.5x.csv.gz", "opposite/selected_paths.csv.gz"],
                   "fills": "native VectorBT receipts from cloud_close_inverse_examples receipt callback",
                   "bars": "frozen OKX 12H OHLC sources; requested missing timestamps remain absent"},
        "chartWindow": {"timeframe": "12h", "beforeEntryBars": BEFORE, "afterFundedExitBars": AFTER,
                        "timezoneLabels": "Asia/Shanghai", "missingBars": "shown as empty shaded time; never forward-filled"},
        "symbols": symbol_manifest, "records": records,
    }
    _json_write(OUT / "manifest.json", manifest)
    return {"manifest": str(OUT / "manifest.json"), "tradeCount": len(records),
            "nativeOrderRows": len(fills), "charts": len(chart_specs),
            "nativeCashPnl": float(native_cash.sum()), "fees": fees,
            "endingBalance": float(1000. + native_cash.sum()),
            "missingChartBars": int(sum(v["missingBars"] for v in chart_audits.values()))}


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
