"""Run the fixed 20% target / 3% or 5% stop study with VectorBT native OHLC stops.

Each input row is one independent candidate and may carry several selection
flags. H1 OHLC comes only from ``census12.read_raw``. ``OHLCSTX`` receives the
entry bar with ``wait=0`` and ``is_open_safe=True``, so protection starts in the
entry hour. Its native stop choice is pessimistic when both intrabar barriers
are touched. Opening gaps are checked first: a gap through SL exits at that
open as taker; a gap through TP exits at the resting target as maker, matching
the existing VectorBT engine convention. A full 45-day window exits at the
last H1 close. Series-end and data-gap positions without a prior exit remain
open and are recorded with their last known mark, never force-closed.

Run one candidate at a time, e.g.:
    /Users/zhangzc/.local/bin/vectorbt-python <this-file> --stop-pct 5

VectorBT OHLCSTX finds stop/target events in bounded batches. VectorBT
Portfolio.from_orders then records one independent base-unit entry/exit path
per signal; cash is not shared between trades. This is a trade ledger, not a
funded multi-position account simulation.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from vectorbt.signals.enums import StopType


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from census12 import read_raw  # noqa: E402


SIGNALS = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/strategy_v1/signals.csv.gz"
OUT = SIGNALS.parent
HOUR_MS = 3_600_000
HORIZON_HOURS = 45 * 24
TARGET_PCT = 0.20
TAKER_FEE = 0.0005
MAKER_FEE = 0.0002
DEFAULT_BATCH_SIZE = 128
PERIOD_CUTOFF_MS = int(pd.Timestamp("2025-01-01T00:00:00Z").timestamp() * 1000)
# Same source census cutoff as scan.py; retain only fully closed H1 candles.
DATA_CUTOFF_MS = int(pd.Timestamp("2026-11-11T10:30:00Z").timestamp() * 1000)
REQUIRED_COLUMNS = {
    "symbol", "month", "side", "entry_ms", "entry_price", "rail",
    "baseline", "first_wick", "retest",
}


def load_signals() -> pd.DataFrame:
    if not SIGNALS.is_file():
        raise FileNotFoundError(f"signal input not found: {SIGNALS}")
    frame = pd.read_csv(SIGNALS)
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"signal input missing columns: {sorted(missing)}")
    for flag in ("baseline", "first_wick", "retest"):
        vals = pd.to_numeric(frame[flag], errors="raise")
        if not vals.isin((0, 1)).all():
            raise ValueError(f"{flag} must contain only 0/1")
        frame[flag] = vals.astype(bool)
    if {"rank_12h", "body_inside"} <= set(frame.columns):
        frame["owner_style"] = (
            (pd.to_numeric(frame["rank_12h"], errors="raise").eq(1)
             & pd.to_numeric(frame["body_inside"], errors="raise").eq(1))
            | frame["retest"]
        )
    else:
        frame["owner_style"] = False
    if not frame["side"].isin(("long", "short")).all():
        raise ValueError("side must be long or short")
    frame["entry_ms"] = pd.to_numeric(frame["entry_ms"], errors="raise").astype("int64")
    frame["entry_price"] = pd.to_numeric(frame["entry_price"], errors="raise").astype(float)
    frame["rail"] = pd.to_numeric(frame["rail"], errors="raise").astype(float)
    if (frame["entry_ms"] % HOUR_MS != 0).any():
        raise ValueError("entry_ms must align to an H1 open")
    if (~np.isfinite(frame["entry_price"])).any() or (frame["entry_price"] <= 0).any():
        raise ValueError("entry_price must be finite and positive")
    if not frame["baseline"].all():
        raise ValueError("the supplied event ledger is expected to contain baseline rows only")
    frame.insert(0, "signal_row", np.arange(len(frame), dtype=np.int64))
    return frame


def validate_raw(symbol: str, raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if raw.ndim != 2 or raw.shape[1] < 5:
        raise ValueError(f"{symbol}: read_raw must return [open_ms, open, high, low, close]")
    times = raw[:, 0].astype(np.int64)
    if len(times) and ((np.diff(times) <= 0).any()):
        raise ValueError(f"{symbol}: H1 timestamps are not strictly increasing")
    return raw, times


def window_for_event(
    raw: np.ndarray, times: np.ndarray, entry_ms: int, entry_price: float
) -> dict:
    pos = int(np.searchsorted(times, entry_ms))
    if pos >= len(times) or int(times[pos]) != entry_ms:
        return {"pos": -1, "valid_n": 0, "path_status": "MISSING_ENTRY_H1"}
    o = float(raw[pos, 1])
    if not math.isfinite(o) or o <= 0:
        return {"pos": pos, "valid_n": 0, "path_status": "INVALID_ENTRY_OHLC"}
    if not math.isclose(o, entry_price, rel_tol=1e-10, abs_tol=1e-10):
        return {"pos": pos, "valid_n": 0, "path_status": "ENTRY_OPEN_MISMATCH",
                "raw_open": o}

    end = min(pos + HORIZON_HOURS, len(raw))
    bars = raw[pos:end, 1:5]
    bad_ohlc = ~np.isfinite(bars).all(axis=1)
    sane = ((bars[:, 0] > 0) & (bars[:, 1] >= np.maximum(bars[:, 0], bars[:, 3]))
            & (bars[:, 2] <= np.minimum(bars[:, 0], bars[:, 3])))
    invalid = np.flatnonzero(bad_ohlc | ~sane)
    gap_edges = np.flatnonzero(np.diff(times[pos:end]) != HOUR_MS)
    gap_start = int(gap_edges[0] + 1) if len(gap_edges) else None
    invalid_start = int(invalid[0]) if len(invalid) else None

    first_bad = HORIZON_HOURS
    status = "FULL_45D"
    if invalid_start is not None and invalid_start < first_bad:
        first_bad = invalid_start
        status = "INVALID_H1_BAR"
    if gap_start is not None and gap_start < first_bad:
        first_bad = gap_start
        status = "H1_GAP"
    if len(bars) < HORIZON_HOURS and len(bars) < first_bad:
        first_bad = len(bars)
        status = "SERIES_END"
    valid_n = min(first_bad, HORIZON_HOURS)
    if valid_n <= 0:
        return {"pos": pos, "valid_n": 0, "path_status": status, "raw_open": o}
    return {"pos": pos, "valid_n": valid_n, "path_status": status, "raw_open": o}


def init_outcome(rec: dict, path_status: str, stop_pct: float) -> dict:
    side_sign = 1 if rec["side"] == "long" else -1
    return {
        "signal_row": int(rec["signal_row"]),
        "trade_id": f"{rec['symbol']}|{rec['month']}|{rec['side']}|{int(rec['entry_ms'])}|{int(rec['signal_row'])}",
        "symbol": rec["symbol"], "month": rec["month"], "side": rec["side"],
        "entry_ms": int(rec["entry_ms"]), "entry_price": float(rec["entry_price"]),
        "rail": float(rec["rail"]), "stop_pct": stop_pct, "target_pct": TARGET_PCT,
        "baseline": bool(rec["baseline"]), "first_wick": bool(rec["first_wick"]),
        "retest": bool(rec["retest"]), "owner_style": bool(rec["owner_style"]),
        "exit_ms": np.nan, "exit_bar_open_ms": np.nan, "exit_price": np.nan,
        "exit_reason": None, "exit_time_basis": None, "closed_flag": False, "path_status": path_status,
        "data_status": path_status, "last_mark_ms": np.nan, "last_mark_price": np.nan,
        "entry_fee_rate": TAKER_FEE, "exit_fee_rate": np.nan,
        "entry_fee_amount": np.nan, "exit_fee_amount": np.nan,
        "gross_return": np.nan, "fee_return": np.nan, "net_return": np.nan,
        "mark_gross_return": np.nan, "mark_net_return_after_entry_fee": np.nan,
        "holding_hours": np.nan, "mfe_pct": np.nan,
        "mfe_includes_exit_bar": False,
    }


def build_symbol_paths(
    frame: pd.DataFrame, symbol: str, stop_pct: float, batch_size: int
) -> list[dict]:
    raw = read_raw(symbol)
    raw = raw[(raw[:, 0].astype(np.int64) + HOUR_MS) <= DATA_CUTOFF_MS]
    raw, times = validate_raw(symbol, raw)
    events = frame.to_dict(orient="records")
    valid_events: list[dict] = []
    outcomes: dict[int, dict] = {}
    for rec in events:
        window = window_for_event(raw, times, int(rec["entry_ms"]), float(rec["entry_price"]))
        outcome = init_outcome(rec, window["path_status"], stop_pct)
        if window["valid_n"] == 0:
            outcomes[int(rec["signal_row"])] = outcome
            continue
        rec["_pos"] = int(window["pos"])
        rec["_valid_n"] = int(window["valid_n"])
        rec["_path_status"] = window["path_status"]
        valid_events.append(rec)

    for begin in range(0, len(valid_events), batch_size):
        batch = valid_events[begin:begin + batch_size]
        n_cols = len(batch)
        o = np.full((HORIZON_HOURS, n_cols), np.nan, dtype=np.float64)
        h = np.full_like(o, np.nan)
        l = np.full_like(o, np.nan)
        c = np.full_like(o, np.nan)
        entry = np.empty(n_cols, dtype=np.float64)
        side = np.empty(n_cols, dtype=np.int8)
        for j, rec in enumerate(batch):
            n = int(rec["_valid_n"])
            pos = int(rec["_pos"])
            rows = raw[pos:pos + n, 1:5]
            o[:n, j], h[:n, j], l[:n, j], c[:n, j] = rows.T
            entry[j] = float(rec["entry_price"])
            side[j] = 1 if rec["side"] == "long" else -1

        entries = np.zeros((HORIZON_HOURS, n_cols), dtype=bool)
        entries[0, :] = True
        out = vbt.OHLCSTX.run(
            entries, o, h, l, c,
            sl_stop=stop_pct,
            sl_trail=False,
            tp_stop=TARGET_PCT,
            reverse=(side < 0),
            wait=0,
            is_open_safe=True,
            pick_first=True,
        )
        exits = np.asarray(out.exits, dtype=bool)
        stops = np.asarray(out.stop_price, dtype=np.float64)
        kinds = np.asarray(out.stop_type, dtype=np.float64)
        if exits.shape != (HORIZON_HOURS, n_cols):
            raise AssertionError(f"{symbol}: unexpected OHLCSTX output shape {exits.shape}")

        stop_level = entry * (1.0 - side * stop_pct)
        target_level = entry * (1.0 + side * TARGET_PCT)
        open_sl = ((side[None, :] > 0) & (o <= stop_level[None, :])) | (
            (side[None, :] < 0) & (o >= stop_level[None, :])
        )
        open_tp = ((side[None, :] > 0) & (o >= target_level[None, :])) | (
            (side[None, :] < 0) & (o <= target_level[None, :])
        )
        open_hit = open_sl | open_tp
        open_any = open_hit.any(axis=0)
        open_ix = np.where(open_any, open_hit.argmax(axis=0), HORIZON_HOURS)
        open_is_sl = np.zeros(n_cols, dtype=bool)
        open_cols = np.flatnonzero(open_any)
        if len(open_cols):
            open_is_sl[open_cols] = open_sl[open_ix[open_cols], open_cols]
        stop_hits = exits.any(axis=0)
        stop_ix = np.where(stop_hits, exits.argmax(axis=0), HORIZON_HOURS)

        exit_ix = np.full(n_cols, HORIZON_HOURS, dtype=np.int64)
        exit_reason = np.full(n_cols, "", dtype=object)
        exit_px = np.full(n_cols, np.nan, dtype=np.float64)
        is_open_exit = np.zeros(n_cols, dtype=bool)
        for j, rec in enumerate(batch):
            oh_ix = int(open_ix[j])
            native_ix = int(stop_ix[j])
            if oh_ix <= native_ix and oh_ix < HORIZON_HOURS:
                # At an opening gap, process the executable open before the
                # remaining OHLC range of the same hour.
                exit_ix[j] = oh_ix
                is_open_exit[j] = True
                if open_is_sl[j]:
                    exit_reason[j] = "STOP_LOSS_OPEN"
                    exit_px[j] = float(o[oh_ix, j])
                else:
                    exit_reason[j] = "TAKE_PROFIT_OPEN"
                    exit_px[j] = float(target_level[j])
            elif native_ix < HORIZON_HOURS:
                exit_ix[j] = native_ix
                native_type = int(kinds[native_ix, j])
                if native_type == int(StopType.StopLoss):
                    exit_reason[j] = "STOP_LOSS"
                    # OHLCSTX emits the fixed barrier; market stops that gap
                    # through it are handled by the opening branch above.
                    exit_px[j] = float(stops[native_ix, j])
                elif native_type == int(StopType.TakeProfit):
                    exit_reason[j] = "TAKE_PROFIT"
                    exit_px[j] = float(target_level[j])
                else:
                    raise AssertionError(f"{symbol}: unknown OHLCSTX stop type {native_type}")

            n = int(rec["_valid_n"])
            path_status = str(rec["_path_status"])
            if exit_ix[j] < HORIZON_HOURS:
                ix = int(exit_ix[j])
                outcome = init_outcome(rec, path_status, stop_pct)
                outcome.update(
                    exit_ms=int(rec["entry_ms"]) + (ix + 1) * HOUR_MS,
                    exit_bar_open_ms=int(rec["entry_ms"]) + ix * HOUR_MS,
                    exit_price=float(exit_px[j]),
                    exit_reason=str(exit_reason[j]),
                    exit_time_basis="trigger_h1_close_for_account_release",
                    closed_flag=True,
                    data_status=("FULL_45D" if path_status == "FULL_45D"
                                 else f"CLOSED_BEFORE_{path_status}"),
                    last_mark_ms=int(rec["entry_ms"]) + (ix + 1) * HOUR_MS,
                    last_mark_price=float(c[ix, j]),
                    holding_hours=ix + 1,
                    mfe_includes_exit_bar=False,
                )
                outcomes[int(rec["signal_row"])] = outcome
            elif n == HORIZON_HOURS:
                ix = HORIZON_HOURS - 1
                outcome = init_outcome(rec, path_status, stop_pct)
                outcome.update(
                    exit_ms=int(rec["entry_ms"]) + HORIZON_HOURS * HOUR_MS,
                    exit_bar_open_ms=int(rec["entry_ms"]) + ix * HOUR_MS,
                    exit_price=float(c[ix, j]),
                    exit_reason="TIME_45D",
                    exit_time_basis="45d_window_end",
                    closed_flag=True,
                    data_status="FULL_45D",
                    last_mark_ms=int(rec["entry_ms"]) + HORIZON_HOURS * HOUR_MS,
                    last_mark_price=float(c[ix, j]),
                    holding_hours=HORIZON_HOURS,
                    mfe_includes_exit_bar=True,
                )
            else:
                outcome = init_outcome(rec, path_status, stop_pct)
                last_ix = n - 1
                outcome.update(
                    exit_reason=("GAP_CENSORED" if path_status == "H1_GAP" else
                                 "SERIES_END_CENSORED" if path_status == "SERIES_END" else
                                 "INVALID_H1_CENSORED"),
                    data_status=("GAP_CENSORED" if path_status == "H1_GAP" else
                                 "SERIES_END_CENSORED" if path_status == "SERIES_END" else
                                 "INVALID_H1_CENSORED"),
                    last_mark_ms=int(rec["entry_ms"]) + (last_ix + 1) * HOUR_MS,
                    last_mark_price=float(c[last_ix, j]),
                    holding_hours=n,
                    mfe_includes_exit_bar=True,
                )

            # MFE is conservative and H1-resolution. For a stop, prior full
            # bars plus the current open are known before the stop event; do
            # not count later movement in that H1 candle. A TP exits at its
            # limit, so its MFE is exactly the target even on a favorable gap.
            # Time exits and censored marks include completed bars through
            # their close.
            if exit_ix[j] < HORIZON_HOURS and is_open_exit[j]:
                mfe_end = int(exit_ix[j]) - 1
                favorable_high = h[:mfe_end + 1, j].copy() if mfe_end >= 0 else np.empty(0)
                favorable_low = l[:mfe_end + 1, j].copy() if mfe_end >= 0 else np.empty(0)
                open_px = float(o[int(exit_ix[j]), j])
                favorable_high = np.append(favorable_high, open_px)
                favorable_low = np.append(favorable_low, open_px)
            elif exit_ix[j] < HORIZON_HOURS:
                mfe_end = int(exit_ix[j]) - 1
                favorable_high = h[:mfe_end + 1, j].copy() if mfe_end >= 0 else np.empty(0)
                favorable_low = l[:mfe_end + 1, j].copy() if mfe_end >= 0 else np.empty(0)
                open_px = float(o[int(exit_ix[j]), j])
                favorable_high = np.append(favorable_high, open_px)
                favorable_low = np.append(favorable_low, open_px)
            else:
                mfe_end = int(exit_ix[j]) if exit_ix[j] < HORIZON_HOURS else n - 1
                favorable_high = h[:mfe_end + 1, j].copy()
                favorable_low = l[:mfe_end + 1, j].copy()
            if side[j] > 0:
                mfe_price = (max(entry[j], float(np.nanmax(favorable_high)))
                             if len(favorable_high) else entry[j])
                outcome["mfe_pct"] = max(0.0, (mfe_price / entry[j] - 1.0) * 100.0)
            else:
                mfe_price = (min(entry[j], float(np.nanmin(favorable_low)))
                             if len(favorable_low) else entry[j])
                outcome["mfe_pct"] = max(0.0, (1.0 - mfe_price / entry[j]) * 100.0)
            if str(exit_reason[j]).startswith("TAKE_PROFIT"):
                outcome["mfe_pct"] = TARGET_PCT * 100.0
            outcomes[int(rec["signal_row"])] = outcome

        print(f"[{stop_pct:.0%}] {symbol}: processed {begin + n_cols}/{len(valid_events)} valid entries", flush=True)

    return [outcomes[int(row_id)] for row_id in frame["signal_row"].to_numpy()]


def add_vectorbt_accounting(ledger: pd.DataFrame) -> pd.DataFrame:
    n = len(ledger)
    if n == 0:
        return ledger
    ledger = ledger.copy()
    closed = ledger["closed_flag"].to_numpy(dtype=bool)
    ledger["exit_fee_rate"] = np.where(
        closed,
        np.where(ledger["exit_reason"].str.startswith("TAKE_PROFIT", na=False), MAKER_FEE, TAKER_FEE),
        np.nan,
    )
    entry = ledger["entry_price"].to_numpy(dtype=np.float64)
    side = np.where(ledger["side"].eq("long").to_numpy(), 1.0, -1.0)
    mark = ledger["last_mark_price"].to_numpy(dtype=np.float64)
    exit_px = ledger["exit_price"].to_numpy(dtype=np.float64)
    exit_fee_rate = ledger["exit_fee_rate"].to_numpy(dtype=np.float64)
    # For open/censored paths the second order is a no-op at the last observed
    # mark; only the entry is charged. Every column has its own initial cash.
    end_price = np.where(closed, exit_px, np.where(np.isfinite(mark), mark, entry))
    price = np.vstack((entry, end_price))
    size = np.vstack((side, np.where(closed, -side, 0.0)))
    fees = np.vstack((np.full(n, TAKER_FEE), np.where(closed, exit_fee_rate, TAKER_FEE)))
    init_cash = np.maximum(price[0], price[1]) * 4.0 + 1.0
    portfolio = vbt.Portfolio.from_orders(
        price,
        size=size,
        price=price,
        size_type="amount",
        direction="both",
        fees=fees,
        init_cash=init_cash,
        cash_sharing=False,
        allow_partial=False,
        raise_reject=True,
        max_orders=n + int(closed.sum()),
        freq="1h",
    )
    orders = portfolio.orders.records.sort_values(["col", "idx"], kind="stable")
    entries = orders[orders["idx"].eq(0)]
    exits = orders[orders["idx"].eq(1)]
    if len(entries) != n or len(exits) != int(closed.sum()):
        raise AssertionError("VectorBT did not record the expected entry/exit order count")
    if entries["col"].duplicated().any() or exits["col"].duplicated().any():
        raise AssertionError("VectorBT produced duplicate fills for an independent trade column")
    entry_fee = np.full(n, np.nan)
    exit_fee = np.full(n, np.nan)
    entry_fee[entries["col"].to_numpy(dtype=int)] = entries["fees"].to_numpy(dtype=float)
    exit_fee[exits["col"].to_numpy(dtype=int)] = exits["fees"].to_numpy(dtype=float)
    ledger["entry_fee_amount"] = entry_fee
    ledger["exit_fee_amount"] = exit_fee
    # Closed-trade PnL comes from VectorBT's native records, including the
    # per-fill fee schedule. Use it to derive gross/fee/net return per entry
    # notional, and retain mark-only fields for still-open rows.
    trade_records = portfolio.trades.records
    closed_records = trade_records[trade_records["status"].eq(1)]
    if len(closed_records) != int(closed.sum()):
        raise AssertionError(
            f"VectorBT closed {len(closed_records)} trades; expected {int(closed.sum())}"
        )
    for trade in closed_records.itertuples(index=False):
        col = int(trade.col)
        fee_paid = float(trade.entry_fees + trade.exit_fees)
        gross_pnl = float(trade.pnl) + fee_paid
        ledger.at[col, "gross_return"] = gross_pnl / entry[col]
        ledger.at[col, "fee_return"] = fee_paid / entry[col]
        ledger.at[col, "net_return"] = float(trade.pnl) / entry[col]
    ledger["mark_gross_return"] = np.where(
        ~closed & np.isfinite(mark), side * (mark / entry - 1.0), np.nan
    )
    ledger["mark_net_return_after_entry_fee"] = np.where(
        ~closed & np.isfinite(mark),
        ledger["mark_gross_return"].to_numpy(dtype=float) - TAKER_FEE,
        np.nan,
    )
    if not np.allclose(ledger.loc[closed, "entry_fee_rate"], TAKER_FEE):
        raise AssertionError("VectorBT entry fee classification changed")
    return ledger


def summarize(ledger: pd.DataFrame, stop_pct: float) -> pd.DataFrame:
    frame = ledger.copy()
    frame["period"] = np.where(frame["entry_ms"] < PERIOD_CUTOFF_MS, "before_2025", "2025_and_after")
    groups = ("baseline", "first_wick", "retest", "owner_style")
    periods = ("all", "before_2025", "2025_and_after")
    rows: list[dict] = []
    for group in groups:
        for period in periods:
            selected = frame[frame[group]]
            if period != "all":
                selected = selected[selected["period"].eq(period)]
            closed = selected[selected["closed_flag"]]
            net = closed["net_return"].dropna()
            mfe = selected["mfe_pct"].dropna()
            counts = selected["exit_reason"].value_counts()
            rows.append({
                "strategy_group": group,
                "period": period,
                "stop_pct": stop_pct,
                "signal_count": int(len(selected)),
                "completed_count": int(len(closed)),
                "censored_count": int((~selected["closed_flag"]).sum()),
                "h1_gap_censored_count": int(selected["exit_reason"].eq("GAP_CENSORED").sum()),
                "series_end_censored_count": int(selected["exit_reason"].eq("SERIES_END_CENSORED").sum()),
                "invalid_data_censored_count": int(selected["exit_reason"].eq("INVALID_H1_CENSORED").sum()),
                "stop_loss_count": int(selected["exit_reason"].str.startswith("STOP_LOSS", na=False).sum()),
                "take_profit_count": int(selected["exit_reason"].str.startswith("TAKE_PROFIT", na=False).sum()),
                "time_45d_count": int(selected["exit_reason"].eq("TIME_45D").sum()),
                "win_rate_net": float((net > 0).mean()) if len(net) else np.nan,
                "mean_net_return_pct": float(net.mean() * 100.0) if len(net) else np.nan,
                "median_net_return_pct": float(net.median() * 100.0) if len(net) else np.nan,
                "mean_mfe_pct": float(mfe.mean()) if len(mfe) else np.nan,
                "median_mfe_pct": float(mfe.median()) if len(mfe) else np.nan,
                "p90_mfe_pct": float(mfe.quantile(0.90)) if len(mfe) else np.nan,
                "max_mfe_pct": float(mfe.max()) if len(mfe) else np.nan,
                "mean_holding_hours_completed": float(closed["holding_hours"].mean()) if len(closed) else np.nan,
                "vectorbt_version": vbt.__version__,
            })
    return pd.DataFrame(rows)


def run(stop_pct: float, batch_size: int) -> None:
    signals = load_signals()
    print(f"VectorBT {vbt.__version__}; stop={stop_pct:.0%}; signals={len(signals)}; batch={batch_size}", flush=True)
    ledger_rows: list[dict] = [None] * len(signals)  # type: ignore[list-item]
    by_symbol = signals.groupby("symbol", sort=True, observed=True)
    for i, (symbol, group) in enumerate(by_symbol, start=1):
        out = build_symbol_paths(group.reset_index(drop=True), str(symbol), stop_pct, batch_size)
        # group.reset_index retains signal_row values; the returned records are
        # ordered exactly like that per-symbol group.
        for record in out:
            ledger_rows[int(record["signal_row"])] = record
        if i % 20 == 0 or i == len(by_symbol):
            print(f"[{stop_pct:.0%}] symbols {i}/{len(by_symbol)}", flush=True)
    if any(row is None for row in ledger_rows):
        raise AssertionError("some input signals did not receive an outcome row")
    ledger = pd.DataFrame(ledger_rows)
    invalid_entries = ledger["path_status"].isin(
        ("MISSING_ENTRY_H1", "INVALID_ENTRY_OHLC", "ENTRY_OPEN_MISMATCH")
    )
    if invalid_entries.any():
        counts = ledger.loc[invalid_entries, "path_status"].value_counts().to_dict()
        raise ValueError(f"signal entries do not match valid closed H1 opens: {counts}")
    ledger = add_vectorbt_accounting(ledger)
    # Restore stable input order and keep the downstream account interface
    # explicit; flags may select shared rows into multiple independent groups.
    ledger = ledger.sort_values("signal_row", kind="stable").reset_index(drop=True)
    summary = summarize(ledger, stop_pct)
    suffix = f"sl{int(round(stop_pct * 100))}"
    trades_path = OUT / f"fixed_trades_{suffix}.csv.gz"
    summary_path = OUT / f"fixed_summary_{suffix}.csv"
    ledger.to_csv(trades_path, index=False, compression="gzip")
    summary.to_csv(summary_path, index=False)
    print(f"wrote {trades_path}", flush=True)
    print(f"wrote {summary_path}", flush=True)
    print(summary.to_string(index=False), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop-pct", type=float, required=True, choices=(3.0, 5.0),
                        help="fixed stop percentage, one candidate per run")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    if args.batch_size <= 0:
        raise ValueError("batch-size must be positive")
    run(args.stop_pct / 100.0, args.batch_size)


if __name__ == "__main__":
    main()
