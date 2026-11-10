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


def _safe_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", symbol)


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


def _market_frames(symbols: set[str], sources: dict[str, Any]):
    bars_by_symbol: dict[str, pd.DataFrame] = {}
    ema_by_symbol: dict[str, pd.DataFrame] = {}
    hourly_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol in sorted(symbols):
        if symbol not in sources:
            continue
        hourly, bars = examples.original.parent.repair.market.frames(sources[symbol])
        bars = bars[["open", "high", "low", "close"]].astype(float).sort_index()
        if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
            raise ValueError(f"Invalid source 12H index for {symbol}")
        ema = _ema_points_12h(bars.reset_index().to_dict("records"))
        ema.index = bars.index
        bars_by_symbol[symbol] = bars
        ema_by_symbol[symbol] = ema[["ema20", "ema50", "count", "ready"]]
        hourly_by_symbol[symbol] = hourly[["low", "high"]]
    return bars_by_symbol, ema_by_symbol, hourly_by_symbol


def _benchmark_return(bars_by_symbol: dict[str, pd.DataFrame], symbol: str,
                      signal_ms: int) -> dict[str, Any]:
    bars = bars_by_symbol.get(symbol)
    if bars is None:
        return {"available": False, "changePct": None, "reason": "benchmark_source_missing"}
    start_ms = signal_ms - 60 * STEP
    expected = np.arange(start_ms, signal_ms + STEP, STEP, dtype=np.int64)
    closes = bars.close.reindex(pd.Index(expected))
    missing = [int(t) for t in expected[closes.isna().to_numpy()]]
    if missing:
        return {"available": False, "changePct": None, "startMs": start_ms,
                "endMs": signal_ms, "expectedIntervals": 60, "missingBars": len(missing),
                "reason": "not_61_contiguous_12h_closes"}
    return {"available": True, "changePct": float((closes.iat[-1] / closes.iat[0] - 1.) * 100.),
            "startMs": start_ms, "endMs": signal_ms, "expectedIntervals": 60,
            "startClose": float(closes.iat[0]), "endClose": float(closes.iat[-1]),
            "reason": None}


def _entry_evidence(event_id: str, symbol: str, side: int, entry_ms: int, path: pd.Series,
                    tape: pd.DataFrame, bars_by_symbol: dict[str, pd.DataFrame],
                    ema_by_symbol: dict[str, pd.DataFrame],
                    hourly_by_symbol: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Expose only frozen source bars completed by entry; return percentages use 60 exact 12H intervals."""
    signal_ms = int(entry_ms) - STEP
    bars, ema = bars_by_symbol.get(symbol), ema_by_symbol.get(symbol)
    signal_exists = bars is not None and signal_ms in bars.index
    signal = bars.loc[signal_ms] if signal_exists else None
    ema_exists = ema is not None and signal_ms in ema.index
    ema_row = ema.loc[signal_ms] if ema_exists else None
    ema_ready = bool(ema_row.ready) if ema_exists else False
    signal_ohlc = ({name: float(signal[name]) for name in ("open", "high", "low", "close")}
                   if signal_exists else None)
    ema20 = float(ema_row.ema20) if ema_exists else None
    ema50 = float(ema_row.ema50) if ema_exists else None

    week = week_window(signal_ms)
    hourly = hourly_by_symbol.get(symbol)
    monday = (hourly.loc[int(week["start"]):int(week["end"]) - 1]
              if hourly is not None else pd.DataFrame(columns=["low", "high"]))
    monday_count = int(len(monday))
    rails = None
    selected_rail = None
    close_inside = wick_cross = stop_matches_rail = None
    if monday_count == 24:
        monday_low, monday_high = float(monday.low.min()), float(monday.high.max())
        span = monday_high - monday_low
        lower, upper = monday_low - .618 * span, monday_high + .618 * span
        selected_rail = lower if int(side) == 1 else upper
        rails = {"week": str(week["week"]), "mondayBarCount": monday_count,
                 "lowerPrice": lower, "upperPrice": upper,
                 "selectedSideRailPrice": selected_rail,
                 "source": "24 observed hourly OHLC bars; no missing bar filled"}
        if signal_exists:
            close_inside = bool(lower < float(signal.close) < upper)
            wick_cross = bool(float(signal.low) < lower if int(side) == 1 else float(signal.high) > upper)
        fixed_stop = float(path.stop_price)
        expected_stop = selected_rail * (1. - int(side) * stop_pct_for(symbol))
        stop_matches_rail = bool(np.isclose(expected_stop, fixed_stop, rtol=1e-8, atol=1e-9))

    ema_direction = (bool((ema20 - ema50) * int(side) > 0)
                     if ema20 is not None and ema50 is not None else None)
    fixed_stop = float(path.stop_price)
    entry_price = float(path.entry_price)
    two_r = entry_price + 2. * (entry_price - fixed_stop)
    tp2 = tape[tape.event_id.eq(event_id) & tape.tp_r.eq(2)]
    if len(tp2) != 1:
        raise ValueError(f"Expected one tp_r=2 source path for entry evidence: {event_id}")
    tp2 = tp2.iloc[0]
    target_hit = str(tp2.exit_reason).startswith("take_profit")
    two_r_source_check = (bool(np.isclose(two_r, float(tp2.exit_price), rtol=1e-8, atol=1e-9))
                          if target_hit else None)
    checks = {
        "signalBarPresent": bool(signal_exists), "emaDirectionMatchesSide": ema_direction,
        "signalCloseInsideRails": close_inside, "signalWickCrossesSideRail": wick_cross,
        "fixedStopMatchesRail": stop_matches_rail,
        "tp2ExitMatchesCalculatedPriceWhenHit": two_r_source_check,
    }
    direction_valid = bool(signal_exists and ema_ready and ema_direction is True and close_inside is True
                           and wick_cross is True and stop_matches_rail is True)
    return {
        "timeframe": "12h", "signalBarOpenMs": signal_ms, "signalBarCloseMs": int(entry_ms),
        "signalOhlc": signal_ohlc,
        "ema": {"ema20": ema20, "ema50": ema50, "readyAtSignal": ema_ready,
                "directionMatchesSide": ema_direction},
        "rails": rails, "mondayBarCount": monday_count,
        "fixedStopPrice": fixed_stop, "fixedStopPct": float(stop_pct_for(symbol)),
        "twoRPrice": float(two_r), "twoRFormula": "entryPrice + 2 * (entryPrice - fixedStopPrice)",
        "twoRReferencePath": {"exitReason": str(tp2.exit_reason), "exitPrice": float(tp2.exit_price),
                              "targetHit": bool(target_hit), "priceMatchesWhenHit": two_r_source_check},
        "benchmark30d": {"intervals": 60, "timeframe": "12h",
                          "formula": "(close_at_signal / close_60_intervals_before - 1) * 100",
                          "ETH": _benchmark_return(bars_by_symbol, "ETH", signal_ms),
                          "BTC": _benchmark_return(bars_by_symbol, "BTC", signal_ms)},
        "validation": {**checks, "sourceDirectionValid": direction_valid},
    }


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
    source_frames, source_ema, source_hourly = _market_frames(
        set(funded.symbol.unique()) | {"BTC", "ETH"}, sources)

    positions = funded.sort_values(["entry_ms"], kind="stable")
    # Use a deterministic tie-breaker even when two different symbols enter on one hour.
    positions = positions.assign(_event=positions.index.astype(str)).sort_values(["entry_ms", "_event"], kind="stable")
    records: list[dict[str, Any]] = []
    event_payloads: list[tuple[str, dict[str, Any]]] = []
    entry_evidence_summaries: list[dict[str, Any]] = []
    symbol_windows: dict[str, list[tuple[int, int]]] = {}

    for number, (event_id, position) in enumerate(positions.drop(columns="_event").iterrows(), 1):
        stable_id = f"cloud-v17-{number:06d}"
        path = primary.loc[event_id]
        symbol = str(position.symbol)
        entry_ms, exit_ms = int(position.entry_ms), int(position.exit_bar_ms)
        start = entry_ms - BEFORE * STEP
        end = (exit_ms // STEP) * STEP + AFTER * STEP
        bars = source_frames[symbol]
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
        source_paths = tape[tape.event_id.eq(event_id)]
        source_path_flags = [{
            "tpR": float(r.tp_r), "sameBarAmbiguous": bool(r.same_bar_ambiguous),
            "gapFlag": bool(r.gap_flag), "exitReason": str(r.exit_reason),
            "exitMs": int(r.exit_bar_ms), "exitPhase": str(r.exit_phase),
        } for r in source_paths.itertuples(index=False)]
        if len(source_path_flags) != 2:
            raise ValueError(f"Expected two frozen target paths for {event_id}")
        same_bar_ambiguous = any(item["sameBarAmbiguous"] for item in source_path_flags)
        source_gap = any(item["gapFlag"] for item in source_path_flags)
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
            "sameBarAmbiguous": same_bar_ambiguous, "sourceGapFlag": source_gap,
            "sourcePathFlags": source_path_flags,
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
        entry_evidence = _entry_evidence(event_id, symbol, int(position.side), entry_ms, path, tape,
                                         source_frames, source_ema, source_hourly)
        entry_evidence_summaries.append(entry_evidence)
        record["entryEvidenceValid"] = bool(entry_evidence["validation"]["sourceDirectionValid"])

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
            "sourcePathFlags": source_path_flags, "sameBarAmbiguous": same_bar_ambiguous,
            "sourceGapFlag": source_gap,
            "entryEvidence": entry_evidence,
            "fills": _fill_rows(chart_fills), "childExits": child_exits,
            "costs": {"feeTotal": fees, "feeRows": int(len(chart_fills)),
                      "ratesFromFrozenPath": sorted(float(v) for v in tape.loc[tape.event_id.eq(event_id), "exit_fee_rate"].drop_duplicates())},
        }
        event_payloads.append((event_url, event))
        record["chartAudit"] = {key: event["window"][key] for key in ("expectedBars", "availableBars", "missingBars", "missingRanges")}

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
                        "timezoneLabels": "Asia/Shanghai", "missingBars": "empty time slots; never forward-filled"},
        "entryEvidenceSpec": {
            "signalBar": "12H candle opening at entryMs minus 12 hours; values use its completed close",
            "rails": "weekly 1.618 bands from 24 observed Monday H1 bars",
            "benchmark30d": "exactly 60 consecutive 12H intervals, signal close divided by close 60 intervals earlier minus one; missing intervals yield null",
            "twoR": "entryPrice + 2 * (entryPrice - fixedStopPrice); checked against tp_r=2 source exit when its target was hit",
            "directionValidation": "EMA direction, signal close inside the rails, side-specific wick crossing, and stop-to-rail relation",
        },
        "dataQuality": {
            "frozenSourcePathRows": int(len(tape)),
            "sameBarAmbiguousTrades": int(sum(bool(r["sameBarAmbiguous"]) for r in records)),
            "sourceGapFlagTrades": int(sum(bool(r["sourceGapFlag"]) for r in records)),
            "incompleteMondayWindows": int(sum(int(r["mondayBarCount"]) != 24 for r in records)),
            "tradesWithMissing12hBars": int(sum(int(r["chartAudit"]["missingBars"]) > 0 for r in records)),
            "missing12hBars": int(sum(int(r["chartAudit"]["missingBars"]) for r in records)),
            "entryEvidenceTrades": int(sum(bool(r["entryEvidenceValid"]) for r in records)),
            "entryEvidenceDirectionFailures": int(sum(not bool(r["entryEvidenceValid"]) for r in records)),
            "benchmark30dMissingTrades": int(sum(not (e["benchmark30d"]["BTC"]["available"]
                                                       and e["benchmark30d"]["ETH"]["available"])
                                                  for e in entry_evidence_summaries)),
        },
        "symbols": symbol_manifest, "records": records,
    }
    _json_write(OUT / "manifest.json", manifest)
    redraw_saved_charts()
    return {"manifest": str(OUT / "manifest.json"), "tradeCount": len(records),
            "nativeOrderRows": len(fills), "charts": len(records),
            "nativeCashPnl": float(native_cash.sum()), "fees": fees,
            "endingBalance": float(1000. + native_cash.sum()),
            "missingChartBars": int(sum(r["chartAudit"]["missingBars"] for r in records))}


def add_entry_evidence_to_existing_bundle() -> dict[str, Any]:
    """Attach frozen signal evidence to generated event JSON without replaying any account decisions."""
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    funded = pd.read_csv(examples.BASE / "funded_inverse_1.5x.csv.gz", index_col="event_id",
                         float_precision="round_trip")
    tape = pd.read_csv(examples.BASE / "selected_paths.csv.gz", dtype={"symbol": str},
                       float_precision="round_trip")
    _, _, sources = examples.original.source.inputs()
    symbols = set(funded.symbol.unique()) | {"BTC", "ETH"}
    bars_by_symbol, ema_by_symbol, hourly_by_symbol = _market_frames(symbols, sources)
    valid_count = 0
    benchmark_missing = 0
    tp2_checked = tp2_mismatch = 0
    for record in manifest["records"]:
        event_id = str(record["eventId"])
        if event_id not in funded.index:
            raise ValueError(f"Manifest event is not in the frozen funded file: {event_id}")
        position = funded.loc[event_id]
        path_rows = tape[tape.event_id.eq(event_id) & tape.tp_r.eq(0)]
        if len(path_rows) != 1:
            raise ValueError(f"Expected one primary frozen path for entry evidence: {event_id}")
        evidence = _entry_evidence(event_id, str(position.symbol), int(position.side), int(position.entry_ms),
                                   path_rows.iloc[0], tape, bars_by_symbol, ema_by_symbol, hourly_by_symbol)
        event_path = OUT / record["dataUrl"]
        with gzip.open(event_path, "rt", encoding="utf-8") as stream:
            event = json.load(stream)
        if event["id"] != record["id"] or event["eventId"] != event_id:
            raise ValueError(f"Per-trade JSON identity mismatch: {record['id']}")
        event["entryEvidence"] = evidence
        _json_gzip(event_path, event)
        record["entryEvidenceValid"] = bool(evidence["validation"]["sourceDirectionValid"])
        valid_count += int(record["entryEvidenceValid"])
        benchmark_missing += int(not (evidence["benchmark30d"]["BTC"]["available"]
                                      and evidence["benchmark30d"]["ETH"]["available"]))
        check = evidence["twoRReferencePath"]["priceMatchesWhenHit"]
        if check is not None:
            tp2_checked += 1
            tp2_mismatch += int(not check)
    manifest["entryEvidenceSpec"] = {
        "signalBar": "12H candle opening at entryMs minus 12 hours; values use its completed close",
        "rails": "weekly 1.618 bands from 24 observed Monday H1 bars",
        "benchmark30d": "exactly 60 consecutive 12H intervals, signal close divided by close 60 intervals earlier minus one; missing intervals yield null",
        "twoR": "entryPrice + 2 * (entryPrice - fixedStopPrice); checked against tp_r=2 source exit when its target was hit",
        "directionValidation": "EMA direction, signal close inside the rails, side-specific wick crossing, and stop-to-rail relation",
    }
    quality = manifest.setdefault("dataQuality", {})
    quality["entryEvidenceTrades"] = valid_count
    quality["entryEvidenceDirectionFailures"] = len(manifest["records"]) - valid_count
    quality["benchmark30dMissingTrades"] = benchmark_missing
    quality["twoRSourceTargetChecks"] = tp2_checked
    quality["twoRSourceTargetMismatches"] = tp2_mismatch
    manifest["entryEvidenceUpdatedAt"] = pd.Timestamp.now(tz="UTC").isoformat()
    _json_write(manifest_path, manifest)
    return {"manifest": str(manifest_path), "entryEvidenceRows": len(manifest["records"]),
            "directionValid": valid_count, "directionFailures": len(manifest["records"]) - valid_count,
            "benchmark30dMissing": benchmark_missing, "tp2TargetChecks": tp2_checked,
            "tp2TargetMismatches": tp2_mismatch}


def redraw_saved_charts():
    """Capture the shared replay renderer and package its images without rerunning the backtest."""
    import os
    import subprocess
    import tempfile
    from yoyo.evaluation.cloud_replay_exports import publish
    origin = os.environ.get("FABLE_REPLAY_ORIGIN", "http://127.0.0.1:8766")
    with tempfile.TemporaryDirectory(prefix="spike-replay-export-") as directory:
        subprocess.run(["node", str(ROOT / "scripts/export_cloud_replay.cjs"), origin, directory], check=True)
        publish(Path(directory))
    return {"charts": len(json.loads((OUT / "manifest.json").read_text())["records"])}


if __name__ == "__main__":
    import sys
    if "--charts-only" in sys.argv:
        result = redraw_saved_charts()
    else:
        result = add_entry_evidence_to_existing_bundle() if "--entry-evidence-only" in sys.argv else build()
    print(json.dumps(result, ensure_ascii=False, indent=2))
