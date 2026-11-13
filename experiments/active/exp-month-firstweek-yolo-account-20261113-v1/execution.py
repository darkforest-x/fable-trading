"""Native Binance 5m execution for the monthly first-week YOLO account.

The caller supplies already-confirmed signal clocks, a frozen near-edge target,
and one per-event OHLC tape. NautilusTrader determines entries, protective stop
and target fills, exit order, fees, and 5m path excursions. A source tape is
extended only from the registered local Binance bar loader. Missing bars are
never synthesized; after a true gap the position is censored at its last
continuous close.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib.util
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
STEP_MS = 5 * 60_000
HOLD_HOURS = 168
HOLD_BARS = HOLD_HOURS * 60 // 5
MIN_COVERAGE_BARS = 1
MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
BAR_LOADER_PATH = HERE.parent / "exp-month-firstweek-intraday-retest-20261112-v1" / "bars.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class PreparedEvent:
    source: dict[str, Any]
    bars: np.ndarray
    entry_ms: int
    entry_price: float
    stop_price: float
    target_price: float
    censored: bool
    cutoff_reason: str


def _load_bar_module():
    if not BAR_LOADER_PATH.is_file():
        raise FileNotFoundError(f"Registered local Binance bar loader missing: {BAR_LOADER_PATH}")
    name = "month_firstweek_yolo_local_bars"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, BAR_LOADER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load local bar loader: {BAR_LOADER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _side_sign(value: Any) -> int:
    text = str(value).strip().lower()
    if text in {"long", "buy", "1", "+1"}:
        return 1
    if text in {"short", "sell", "-1"}:
        return -1
    raise ValueError(f"Unrecognized side: {value!r}")


def _validate_rows(rows: np.ndarray, label: str) -> np.ndarray:
    a = np.asarray(rows, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] < 5:
        raise ValueError(f"{label}: expected [open_ms, open, high, low, close], got {a.shape}")
    a = a[:, :5]
    if not len(a):
        return a
    ts, px = a[:, 0], a[:, 1:]
    if (not np.isfinite(a).all() or not np.equal(ts, np.floor(ts)).all()
            or (ts.astype(np.int64) % STEP_MS).any() or (np.diff(ts) <= 0).any()):
        raise ValueError(f"{label}: timestamps must be unique, increasing, integer 5m UTC opens")
    if ((px <= 0).any() or (px[:, 1] < np.maximum.reduce((px[:, 0], px[:, 2], px[:, 3]))).any()
            or (px[:, 2] > np.minimum.reduce((px[:, 0], px[:, 1], px[:, 3]))).any()):
        raise ValueError(f"{label}: invalid OHLC values")
    return a


@lru_cache(maxsize=32)
def _registered_month(symbol: str, month: str) -> np.ndarray:
    """Read registered local bars and fill their missing opens from cached day tapes."""
    module = _load_bar_module()
    base = _validate_rows(module.load_bars(symbol, month), f"{symbol}/{month}")
    base_by_time = {int(row[0]): row[1:] for row in base}
    added_by_time: dict[int, np.ndarray] = {}
    execution_dir = ROOT / "data/research/month-firstweek-yolo-account-20261113-v1/execution_days" / symbol
    if execution_dir.is_dir():
        for path in sorted(execution_dir.glob(f"{month}-*.npy")):
            daily = _validate_rows(np.load(path, mmap_mode="r", allow_pickle=False), str(path))
            for row in daily:
                ts = int(row[0])
                # The monthly loader is authoritative wherever it has a bar.
                # Daily slices are consulted solely for missing timestamps.
                if ts in base_by_time:
                    continue
                old = added_by_time.get(ts)
                if old is not None and not np.allclose(old, row[1:], rtol=1e-12, atol=1e-12):
                    raise ValueError(f"local execution-day OHLC conflicts at {symbol}/{ts}")
                added_by_time[ts] = row[1:].copy()
    base_by_time.update(added_by_time)
    if not base_by_time:
        return np.empty((0, 5), dtype=np.float64)
    times = np.array(sorted(base_by_time), dtype=np.int64)
    out = np.column_stack((times, np.stack([base_by_time[int(ts)] for ts in times])))
    return _validate_rows(out, f"{symbol}/{month}/merged_local_sources")


def _merge_rows(pieces: list[tuple[str, np.ndarray]], *, lower: int, upper: int) -> np.ndarray:
    selected: list[tuple[str, np.ndarray]] = []
    for label, rows in pieces:
        if not len(rows):
            continue
        mask = (rows[:, 0] >= lower) & (rows[:, 0] <= upper)
        if mask.any():
            selected.append((label, rows[mask]))
    if not selected:
        return np.empty((0, 5), dtype=np.float64)
    raw = np.concatenate([rows for _, rows in selected], axis=0)
    raw = raw[np.argsort(raw[:, 0], kind="stable")]
    times, first, counts = np.unique(raw[:, 0], return_index=True, return_counts=True)
    for ts, start, count in zip(times, first, counts):
        if count > 1 and not np.allclose(raw[start:start + count, 1:], raw[start, 1:], rtol=1e-12, atol=1e-12):
            labels = [label for label, rows in selected if np.any(rows[:, 0] == ts)]
            raise ValueError(f"conflicting OHLC at {int(ts)} for {labels}")
    return raw[first]


def _load_event_window(row: dict[str, Any]) -> tuple[PreparedEvent | None, dict[str, Any] | None]:
    trade_id = str(row["trade_id"])
    symbol = str(row["symbol"]).upper()
    entry_ms = int(row["confirm_close_ms"])
    side = _side_sign(row["side"])
    target = float(row["near_edge"])
    if not np.isfinite(target) or target <= 0:
        raise ValueError(f"{trade_id}: near_edge must be finite and positive")
    if entry_ms % STEP_MS:
        raise ValueError(f"{trade_id}: confirm_close_ms is off the 5m UTC boundary")

    path = Path(str(row["tape_path"]))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        return None, dict(trade_id=trade_id, pool=str(row["pool"]), symbol=symbol,
                          skip_reason="missing_event_tape", tape_path=str(path))
    source = _validate_rows(np.load(path, mmap_mode="r", allow_pickle=False), str(path))
    # The read timestamp is the completed 5m close and also the next bar open.
    # Verify both the just-completed signal candle and the executable open exist.
    source_times = source[:, 0].astype(np.int64)
    signal_i = int(np.searchsorted(source_times, entry_ms - STEP_MS))
    entry_source_i = int(np.searchsorted(source_times, entry_ms))
    if (signal_i >= len(source_times) or source_times[signal_i] != entry_ms - STEP_MS
            or entry_source_i >= len(source_times) or source_times[entry_source_i] != entry_ms):
        return None, dict(trade_id=trade_id, pool=str(row["pool"]), symbol=symbol,
                          skip_reason="confirmation_or_next_open_missing", tape_path=str(path))

    last_open = entry_ms + (HOLD_BARS - 1) * STEP_MS
    pieces = [("event_tape", source)]
    loader = _load_bar_module()
    first_month = pd.Timestamp(entry_ms, unit="ms", tz="UTC").strftime("%Y-%m")
    last_month = pd.Timestamp(last_open, unit="ms", tz="UTC").strftime("%Y-%m")
    for period in pd.period_range(first_month, last_month, freq="M"):
        month_rows = _registered_month(symbol, str(period))
        if len(month_rows):
            pieces.append((f"registered:{period}", month_rows))
    window = _merge_rows(pieces, lower=entry_ms, upper=last_open)
    if not len(window) or int(window[0, 0]) != entry_ms:
        return None, dict(trade_id=trade_id, pool=str(row["pool"]), symbol=symbol,
                          skip_reason="entry_open_missing_from_5m_tape", tape_path=str(path))

    expected = entry_ms + np.arange(HOLD_BARS, dtype=np.int64) * STEP_MS
    count = min(len(window), HOLD_BARS)
    mismatch = np.flatnonzero(window[:count, 0].astype(np.int64) != expected[:count])
    if len(mismatch):
        count = int(mismatch[0])
    bars = window[:count].copy()
    entry = float(bars[0, 1])
    if side * (target - entry) <= 0:
        return None, dict(trade_id=trade_id, pool=str(row["pool"]), symbol=symbol,
                          skip_reason="target_at_or_behind_entry", entry_ms=entry_ms,
                          entry_px=entry, target_px=target, tape_path=str(path))
    stop = entry * (1.0 - side * 0.05)
    censored = count < HOLD_BARS
    cutoff_reason = "missing_or_ended_5m_source_before_168h" if censored else ""
    source_copy = dict(row)
    source_copy.update(symbol=symbol, side_sign=side, signal_entry_ms=entry_ms,
                       event_tape_path=str(path), observed_window_bars=count,
                       max_window_bars=HOLD_BARS)
    return PreparedEvent(source_copy, bars, entry_ms, entry, stop, target, censored, cutoff_reason), None


def _price_grid(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=np.float64)
    if not len(values) or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Price grid requires finite positive prices")
    for digits in range(16):
        tick = 10.0 ** -digits
        quotients = values / tick
        if np.max(np.abs(quotients - np.rint(quotients))) <= 1e-5:
            return tick
    raise ValueError(f"Cannot represent prices on a decimal grid: {values.min()} .. {values.max()}")


def _excursions(bars: np.ndarray, side: int, entry: float, exit_px: float,
                exit_phase: int) -> tuple[float, float]:
    """Observed MFE/MAE on the adapter's adverse-first 5m path prefix."""
    prices = [entry, exit_px]
    if len(bars) > 1:
        prices.extend([float(bars[:-1, 2].max()), float(bars[:-1, 3].min())])
    columns = [1, 3, 2, 4] if side > 0 else [1, 2, 3, 4]
    prices.extend(float(v) for v in bars[-1, columns[:min(4, max(0, int(exit_phase)))]] )
    signed = side * (np.asarray(prices, dtype=np.float64) / entry - 1.0) * 100.0
    return max(0.0, float(signed.max())), max(0.0, float(-signed.min()))


def _load_native():
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    return ExitPolicy, run_native


def execute(signals: pd.DataFrame) -> dict[str, Any]:
    """Run the requested pools once through native fills and return their tapes."""
    required = {"trade_id", "symbol", "side", "confirm_close_ms", "pool", "near_edge", "tape_path"}
    missing = sorted(required - set(signals.columns))
    if missing:
        raise ValueError(f"signals missing required columns: {missing}")
    events = signals.copy().reset_index(drop=True)
    if events[list(required)].isna().any().any():
        raise ValueError("signals contain null execution fields")
    if events.trade_id.astype(str).duplicated().any():
        raise ValueError("trade_id must be unique across the submitted pools")
    events["trade_id"] = events.trade_id.astype(str)
    if events.pool.astype(str).isin({"", "nan"}).any():
        raise ValueError("pool names must be nonempty")

    prepared: list[PreparedEvent] = []
    skips: list[dict[str, Any]] = []
    for row in events.to_dict("records"):
        event, skipped = _load_event_window(row)
        if event is not None:
            prepared.append(event)
        if skipped is not None:
            skips.append(skipped)

    ExitPolicy, run_native = _load_native()
    trade_rows: list[dict[str, Any]] = []
    native_fills: list[pd.DataFrame] = []
    tapes: dict[str, np.ndarray] = {}
    audit_groups: list[dict[str, Any]] = []
    by_group: dict[tuple[str, int], list[PreparedEvent]] = {}
    for event in prepared:
        side = int(event.source["side_sign"])
        by_group.setdefault((str(event.source["symbol"]), side), []).append(event)

    for (symbol, side), group in sorted(by_group.items()):
        group.sort(key=lambda e: (e.entry_ms, str(e.source["trade_id"])))
        tapes.update({str(e.source["trade_id"]): e.bars for e in group})
        combined = np.concatenate([e.bars for e in group], axis=0)
        combined = combined[np.argsort(combined[:, 0], kind="stable")]
        times, first, counts = np.unique(combined[:, 0], return_index=True, return_counts=True)
        for ts, start, count in zip(times, first, counts):
            if count > 1 and not np.allclose(combined[start:start + count, 1:], combined[start, 1:], rtol=1e-12, atol=1e-12):
                raise ValueError(f"{symbol}: event tapes conflict at {int(ts)}")
        raw = combined[first]
        open_ms = raw[:, 0].astype(np.int64)
        frame = pd.DataFrame(raw[:, 1:5], columns=["open", "high", "low", "close"],
                             index=pd.to_datetime(open_ms + STEP_MS, unit="ms", utc=True))
        index_by_ms = {int(ts): i for i, ts in enumerate(open_ms)}
        order_rows: list[dict[str, Any]] = []
        metadata: dict[str, PreparedEvent] = {}
        prices = [raw[:, 1:5].ravel()]
        for event in group:
            entry_i = index_by_ms.get(event.entry_ms)
            end_open = int(event.bars[-1, 0])
            end_i = index_by_ms.get(end_open)
            if entry_i is None or end_i is None:
                raise AssertionError(f"{event.source['trade_id']}: event timestamps lost during native merge")
            trade_id = str(event.source["trade_id"])
            metadata[trade_id] = event
            order_rows.append(dict(
                trade_id=trade_id, entry_i=entry_i, end_i=end_i,
                entry=event.entry_price, stop=event.stop_price,
                side=side, risk=abs(event.entry_price - event.stop_price),
                target_price=event.target_price, signal_entry_ms=event.entry_ms,
                pool=str(event.source["pool"]), symbol=symbol,
            ))
            prices.extend([np.array([event.stop_price, event.target_price])])
        tick = _price_grid(np.concatenate(prices))
        policy = ExitPolicy(
            trail_arm_r=math.inf, opposite_hour_exit=False, round_trip_cost=0.0,
            target_from_entry=True, intrabar_path="OLHC" if side > 0 else "OHLC",
            monotone_segments=True, fill_maker_fee=MAKER_FEE, fill_taker_fee=TAKER_FEE,
        )
        base_symbol = symbol[:-4] if symbol.endswith("USDT") else symbol
        rows, fills, native_audit = run_native(
            frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
            pd.DataFrame(order_rows), policy, tick=tick, market_tick=tick,
            symbol=base_symbol, execution_minutes=5, entry_order_mode="market_open",
        )
        if len(rows) != len(group):
            raise RuntimeError(f"{symbol}/{side}: native count {len(rows)} != prepared {len(group)}")
        if len(fills):
            native_fills.append(fills.assign(batch_symbol=symbol, batch_side=side))
        entry_fee = fills[fills.phase.astype(str).eq("entry")].set_index("trade_id").commission
        exit_fee = fills[~fills.phase.astype(str).eq("entry")].groupby("trade_id").commission.sum()
        fill_qty = fills[fills.phase.astype(str).eq("entry")].set_index("trade_id").quantity
        for result in rows.itertuples(index=False):
            event = metadata[str(result.trade_id)]
            side_sign = int(event.source["side_sign"])
            exit_index = int(result.exit_i)
            exit_open_ms = int(open_ms[exit_index])
            exit_phase = int(result.exit_path_phase)
            exit_ms = (exit_open_ms + STEP_MS) if exit_phase != 0 else exit_open_ms
            exit_reason = {1: "stop", 4: "target", 0: "timeout"}.get(int(result.reason))
            if exit_reason is None:
                raise RuntimeError(f"{result.trade_id}: unknown native exit reason {result.reason}")
            is_cutoff = bool(event.censored and exit_reason == "timeout")
            if is_cutoff:
                exit_reason = "cutoff_mark"
            local_exit_i = int(np.searchsorted(event.bars[:, 0].astype(np.int64), exit_open_ms))
            if (local_exit_i >= len(event.bars) or int(event.bars[local_exit_i, 0]) != exit_open_ms):
                raise AssertionError(f"{result.trade_id}: native exit bar missing from its event tape")
            path_to_exit = event.bars[:local_exit_i + 1]
            mfe_pct, mae_pct = _excursions(path_to_exit, side_sign, float(result.entry),
                                            float(result.exit_price), exit_phase)
            ref_qty = float(fill_qty[str(result.trade_id)])
            entry_fee_u = float(entry_fee[str(result.trade_id)])
            native_exit_fee_u = float(exit_fee[str(result.trade_id)])
            entry_fee_rate = entry_fee_u / (ref_qty * float(result.entry))
            exit_fee_rate = native_exit_fee_u / (ref_qty * float(result.exit_price))
            expected_exit_rate = MAKER_FEE if exit_reason == "target" else TAKER_FEE
            if not np.isclose(entry_fee_rate, TAKER_FEE, rtol=1e-8, atol=1e-10):
                raise AssertionError(f"{result.trade_id}: native entry fee rate {entry_fee_rate}")
            if not is_cutoff and not np.isclose(exit_fee_rate, expected_exit_rate, rtol=1e-8, atol=1e-10):
                raise AssertionError(f"{result.trade_id}: native exit fee rate {exit_fee_rate}")
            row = dict(event.source)
            row.update(
                entry_ms=event.entry_ms, entry_px=float(result.entry), stop_px=event.stop_price,
                target_px=event.target_price, side="long" if side_sign > 0 else "short",
                qty=ref_qty, native_qty=ref_qty,
                exit_ms=exit_ms, exit_px=float(result.exit_price), exit_i=local_exit_i,
                exit_phase=exit_phase, exit_reason=exit_reason, native_exit_reason=str(result.reason),
                is_censored=int(is_cutoff), cutoff_reason=event.cutoff_reason if is_cutoff else "",
                entry_fee_u=entry_fee_u, native_exit_fee_u=native_exit_fee_u,
                exit_fee_u=0.0 if is_cutoff else native_exit_fee_u,
                native_fee_u=entry_fee_u + native_exit_fee_u,
                entry_fee_rate=entry_fee_rate,
                exit_fee_rate=0.0 if is_cutoff else exit_fee_rate,
                mfe_pct=mfe_pct, mae_pct=mae_pct,
                both_barriers=int(result.ambiguous),
                framework_position_id=str(result.framework_position_id),
                framework_engine=str(native_audit.get("engine", "")),
                framework_version=str(native_audit.get("version", "")),
            )
            trade_rows.append(row)
        audit_groups.append(dict(symbol=symbol, side="long" if side > 0 else "short",
                                 positions=int(native_audit["positions"]), fills=int(native_audit["fills"]),
                                 market_tick=tick, protection_tick=tick,
                                 intrabar_path=native_audit["intrabar_path"],
                                 ambiguous_bars=int(sum(int(x.ambiguous) for x in rows.itertuples()))))

    trades = pd.DataFrame(trade_rows)
    if len(trades) and trades.trade_id.duplicated().any():
        raise AssertionError("native fills produced duplicate trade_id")
    fills_frame = pd.concat(native_fills, ignore_index=True) if native_fills else pd.DataFrame()
    return {
        "trades": trades.sort_values(["entry_ms", "pool", "symbol", "trade_id"]).reset_index(drop=True)
        if len(trades) else trades,
        "native_fills": fills_frame,
        "skips": pd.DataFrame(skips),
        "bars_by_trade_id": tapes,
        "audit": {
            "engine": "NautilusTrader",
            "framework_version": "1.231.0",
            "prepared_signals": len(events),
            "native_positions": len(trades),
            "pretrade_skips": len(skips),
            "native_groups": audit_groups,
            "maker_fee": MAKER_FEE,
            "taker_fee": TAKER_FEE,
            "holding_bars": HOLD_BARS,
            "minimum_required_contiguous_bars": MIN_COVERAGE_BARS,
        },
    }
