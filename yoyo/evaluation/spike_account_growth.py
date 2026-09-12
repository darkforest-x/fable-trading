"""Causal shared-account accounting for already-produced SPIKE trade ledgers.

The input is one row per candidate trade.  Required columns are ``entry_time``,
``exit_time``, ``base_asset``, ``entry_price``, ``initial_risk`` and ``side``.
``trade_id`` is optional but strongly recommended for an unambiguous stable
same-timestamp ordering.  A trade supplies either its already-costed
``net_return`` or an ``exit_price`` from which the price return is calculated.
``censored`` is optional and marks a boundary observation rather than a
realized exit.

Admission is causal: at an entry timestamp it uses only the balance from exits
at that timestamp or earlier and risk/notional already reserved by positions
opened earlier.  ``net_return`` and exit fields are read only when that trade
exits.  The deterministic hash used to order simultaneous candidates includes
no outcome fields.  This is a research cashbook, not exchange margin or
liquidation simulation; funding, maintenance margin and intrabar marks are out
of scope.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any

import numpy as np
import pandas as pd


_REQUIRED = {"entry_time", "exit_time", "base_asset", "entry_price", "initial_risk", "side"}
_LEDGER_COLUMNS = [
    "trade_id", "input_index", "entry_time", "exit_time", "base_asset", "side", "censored",
    "selection_hash", "selected", "rejection_reason", "boundary_mark", "entry_balance",
    "sizing_balance", "target_risk_capital", "quantity", "notional", "initial_risk_fraction",
    "risk_fraction", "gross_leverage_after_entry", "portfolio_risk_after_entry", "realized_pnl",
    "raw_balance_after_exit", "balance_after_exit", "exit_r_multiple",
]


def _timestamp_series(values: pd.Series, name: str) -> pd.Series:
    """Return finite UTC timestamps, treating naive input as UTC for CSV parity."""
    result = pd.to_datetime(values, utc=True, errors="coerce")
    if result.isna().any():
        raise ValueError(f"{name} must contain finite timestamps")
    return result


def _identity(
    trade_id: object, base_asset: object, entry_time: pd.Timestamp, side: float,
    entry_price: float, initial_risk: float, input_index: object,
) -> str:
    """Use only identity and entry-known fields when ordering simultaneous rows."""
    if pd.notna(trade_id):
        return f"id:{trade_id}"
    return "|".join((
        str(base_asset), str(entry_time), str(side), format(float(entry_price), ".17g"),
        format(float(initial_risk), ".17g"), str(input_index),
    ))


def _stable_hash(seed: int | str, *identity: object) -> str:
    return hashlib.sha256(f"spike-shared-account-v1|{seed}|{_identity(*identity)}".encode()).hexdigest()


def _empty_ledger() -> pd.DataFrame:
    return pd.DataFrame(columns=_LEDGER_COLUMNS)


def simulate_shared_account(
    trades: pd.DataFrame,
    *,
    sizing: str,
    risk_fraction: float,
    initial_balance: float = 1000.0,
    portfolio_risk_cap: float = 0.10,
    gross_leverage_cap: float = 3.0,
    entry_floor_fraction: float = 0.20,
    seed: int | str = 0,
) -> dict[str, Any]:
    """Simulate one shared account from a unified, time-ordered trade table.

    ``sizing`` is ``"compound"`` (risk dollars are a fraction of the current
    realized balance) or ``"fixed"`` (a fraction of ``initial_balance``).
    At entry, initial stop risk across open positions may not exceed 10% of
    current balance and their entry notionals may not exceed 3x current
    balance, by default.  Balance is updated only at exits.  Exit-time losses
    are not clipped to one R, so a price gap can exceed the originally reserved
    risk.  Once balance reaches the 20%-of-initial floor, admission is latched
    off while existing positions continue to their supplied exits.

    Censored rows may be admitted and reserve capacity until their supplied
    boundary time, then produce a zero-PnL ``boundary_mark``.  They remain
    visible in the ledger and never become realized wins or losses.  Returned
    values are ``summary``, an all-candidate ``ledger``, an exit-time
    ``equity_curve``, and calendar-day ``daily_realized_pnl``.
    """
    if sizing not in {"compound", "fixed"}:
        raise ValueError("sizing must be 'compound' or 'fixed'")
    numeric_settings = (risk_fraction, initial_balance, portfolio_risk_cap, gross_leverage_cap, entry_floor_fraction)
    if not all(math.isfinite(float(value)) for value in numeric_settings):
        raise ValueError("account settings must be finite")
    if not 0 < risk_fraction < 1 or initial_balance <= 0 or not 0 < portfolio_risk_cap <= 1:
        raise ValueError("risk fractions must lie in (0, 1) and initial_balance must be positive")
    if gross_leverage_cap <= 0 or not 0 < entry_floor_fraction <= 1:
        raise ValueError("caps must be positive and entry floor must lie in (0, 1]")
    missing = _REQUIRED - set(trades.columns)
    if missing:
        raise ValueError(f"trades missing required columns: {sorted(missing)}")

    # Keep only the cashbook contract.  Real V1/V7 ledgers contain many
    # descriptive columns, and copying all of them for 100k candidates makes
    # the event engine needlessly memory-bound.
    source_columns = ["entry_time", "exit_time", "base_asset", "entry_price", "initial_risk", "side"]
    source_columns += [name for name in ("trade_id", "censored", "net_return", "exit_price") if name in trades]
    source = trades.loc[:, source_columns].copy()
    source.insert(0, "input_index", trades.index.to_numpy(copy=False))
    source["entry_time"] = _timestamp_series(source["entry_time"], "entry_time")
    source["exit_time"] = _timestamp_series(source["exit_time"], "exit_time")
    if "censored" not in source:
        source["censored"] = False
    source["censored"] = source["censored"].fillna(False).astype(bool)
    for name in ("entry_price", "initial_risk", "side"):
        source[name] = pd.to_numeric(source[name], errors="coerce")
    if (source["entry_time"] > source["exit_time"]).any():
        raise ValueError("exit_time must not precede entry_time")
    if (not np.isfinite(source[["entry_price", "initial_risk", "side"]].to_numpy(dtype=float)).all()
            or (source["entry_price"] <= 0).any() or (source["initial_risk"] <= 0).any()
            or not source["side"].isin((-1, 1)).all()):
        raise ValueError("entry_price and initial_risk must be positive; side must be -1 or 1")
    if source["base_asset"].isna().any() or source["base_asset"].astype(str).eq("").any():
        raise ValueError("base_asset is required for the one-position-per-asset limit")
    if "net_return" in source:
        source["net_return"] = pd.to_numeric(source["net_return"], errors="coerce")
    elif "exit_price" in source:
        source["exit_price"] = pd.to_numeric(source["exit_price"], errors="coerce")
        if (not np.isfinite(source["exit_price"]).all()) or (source["exit_price"] <= 0).any():
            raise ValueError("exit_price must be finite and positive when net_return is absent")
    else:
        raise ValueError("trades requires net_return or exit_price")
    if "trade_id" not in source:
        source["trade_id"] = [f"row-{i}" for i in range(len(source))]
    if source["trade_id"].duplicated().any():
        raise ValueError("trade_id must be unique")
    source["selection_hash"] = [
        _stable_hash(seed, trade_id, base_asset, entry_time, side, entry_price, initial_risk, input_index)
        for trade_id, base_asset, entry_time, side, entry_price, initial_risk, input_index in zip(
            source["trade_id"], source["base_asset"], source["entry_time"], source["side"],
            source["entry_price"], source["initial_risk"], source["input_index"],
        )
    ]
    source = source.sort_values(["entry_time", "selection_hash"], kind="mergesort")

    # Make the event schedule once.  The prior implementation selected
    # ``source.loc[source.entry_time == stamp]`` at every timestamp, which is
    # quadratic for a large cross-market ledger.  Candidates are already in
    # the exact (entry_time, outcome-independent hash) order needed below.
    entry_groups: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    times: set[pd.Timestamp] = set()
    for candidate in source.to_dict("records"):
        entry_groups.setdefault(candidate["entry_time"], []).append(candidate)
        times.add(candidate["entry_time"])
        times.add(candidate["exit_time"])
    del source

    balance = float(initial_balance)
    entry_floor = float(initial_balance * entry_floor_fraction)
    floor_triggered = False
    bankrupt = False
    open_positions: dict[str, dict[str, Any]] = {}
    exit_groups: dict[pd.Timestamp, list[dict[str, Any]]] = {}
    active_assets: set[str] = set()
    ledger: dict[str, dict[str, Any]] = {}
    curve_rows: list[dict[str, Any]] = []

    def process_exits(exiting: list[dict[str, Any]]) -> tuple[float, int, int]:
        """Realize an already-open time group, returning its curve deltas."""
        nonlocal balance, bankrupt, floor_triggered
        exiting.sort(key=lambda position: position["selection_hash"])
        realized = 0.0
        exits = 0
        boundary_marks = 0
        for position in exiting:
            open_positions.pop(position["trade_id"])
            active_assets.remove(position["base_asset"])
            row = ledger[position["trade_id"]]
            if position["censored"]:
                row.update(boundary_mark=True, realized_pnl=0.0, raw_balance_after_exit=balance,
                           balance_after_exit=balance, exit_r_multiple=np.nan)
                boundary_marks += 1
                continue
            if "net_return" in position and math.isfinite(float(position["net_return"])):
                pnl = position["notional"] * float(position["net_return"])
            else:
                pnl = position["quantity"] * position["side"] * (position["exit_price"] - position["entry_price"])
            raw_balance = balance + pnl
            balance = max(0.0, raw_balance)
            bankrupt = bankrupt or balance <= 0.0
            floor_triggered = floor_triggered or balance < entry_floor
            row.update(realized_pnl=pnl, raw_balance_after_exit=raw_balance, balance_after_exit=balance,
                       exit_r_multiple=pnl / position["target_risk_capital"])
            realized += pnl
            exits += 1
        return realized, exits, boundary_marks

    for stamp in sorted(times):
        # Exit before entry is deliberate: its realized balance and released
        # capacity are available to a candidate opening at exactly this stamp.
        exiting = exit_groups.pop(stamp, [])
        realized, exits, boundary_marks = process_exits(exiting)

        for candidate in entry_groups.get(stamp, ()):
            trade_id = candidate["trade_id"]
            base_asset = str(candidate["base_asset"])
            initial_risk_fraction = float(candidate["initial_risk"] / candidate["entry_price"])
            sizing_balance = balance if sizing == "compound" else float(initial_balance)
            target_risk = sizing_balance * float(risk_fraction)
            quantity = target_risk / float(candidate["initial_risk"])
            notional = quantity * float(candidate["entry_price"])
            reserved_risk = sum(item["target_risk_capital"] for item in open_positions.values())
            reserved_notional = sum(item["notional"] for item in open_positions.values())
            reason: str | None = None
            if floor_triggered or balance < entry_floor:
                reason = "entry_floor"
            elif base_asset in active_assets:
                reason = "base_asset_open"
            elif reserved_risk + target_risk > balance * portfolio_risk_cap + 1e-12:
                reason = "portfolio_risk_cap"
            elif reserved_notional + notional > balance * gross_leverage_cap + 1e-12:
                reason = "gross_leverage_cap"
            common = dict(
                trade_id=trade_id, input_index=candidate["input_index"], entry_time=stamp,
                exit_time=candidate["exit_time"], base_asset=base_asset, side=int(candidate["side"]),
                censored=bool(candidate["censored"]), selection_hash=candidate["selection_hash"],
                selected=reason is None, rejection_reason=reason, boundary_mark=False,
                entry_balance=balance, sizing_balance=sizing_balance, target_risk_capital=target_risk,
                quantity=quantity if reason is None else np.nan, notional=notional if reason is None else np.nan,
                initial_risk_fraction=initial_risk_fraction, risk_fraction=float(risk_fraction),
                gross_leverage_after_entry=np.nan, portfolio_risk_after_entry=np.nan,
                realized_pnl=np.nan, raw_balance_after_exit=np.nan, balance_after_exit=np.nan,
                exit_r_multiple=np.nan,
            )
            ledger[trade_id] = common
            if reason is not None:
                continue
            position = {**candidate, **common}
            open_positions[trade_id] = position
            exit_groups.setdefault(position["exit_time"], []).append(position)
            active_assets.add(base_asset)
            common["gross_leverage_after_entry"] = (reserved_notional + notional) / balance
            common["portfolio_risk_after_entry"] = (reserved_risk + target_risk) / balance

        # An intrabar stop may share its bar-open timestamp with its own entry.
        # Its outcome is deliberately unavailable to the candidates above, but
        # it must settle before the next timestamp and release its reservation.
        immediate_realized, immediate_exits, immediate_boundary = process_exits(exit_groups.pop(stamp, []))
        realized += immediate_realized
        exits += immediate_exits
        boundary_marks += immediate_boundary
        if exiting or immediate_exits or immediate_boundary:
            curve_rows.append(dict(time=stamp, balance=balance, realized_pnl=realized, exits=exits,
                                   boundary_marks=boundary_marks, floor_triggered=floor_triggered))

    ledger_frame = pd.DataFrame(list(ledger.values()), columns=_LEDGER_COLUMNS)
    if len(ledger_frame):
        ledger_frame = ledger_frame.sort_values(["entry_time", "selection_hash"], kind="mergesort").reset_index(drop=True)
    else:
        ledger_frame = _empty_ledger()
    curve = pd.DataFrame(curve_rows, columns=["time", "balance", "realized_pnl", "exits", "boundary_marks", "floor_triggered"])
    if len(curve):
        curve["time"] = pd.to_datetime(curve["time"], utc=True)
        curve = curve.sort_values("time", kind="mergesort").reset_index(drop=True)
    realized_curve = curve.loc[curve["exits"].gt(0)].copy() if len(curve) else curve.copy()
    if len(realized_curve):
        realized_curve["date"] = realized_curve["time"].dt.normalize()
        daily = realized_curve.groupby("date", as_index=False).agg(realized_pnl=("realized_pnl", "sum"), exits=("exits", "sum"))
        all_days = pd.DataFrame({"date": pd.date_range(daily.date.min(), daily.date.max(), freq="D", tz="UTC")})
        daily = all_days.merge(daily, on="date", how="left").fillna({"realized_pnl": 0.0, "exits": 0})
        balance_by_date = realized_curve.assign(date=realized_curve["time"].dt.normalize()).groupby("date")["balance"].last()
        daily["balance"] = daily["date"].map(balance_by_date).ffill().fillna(initial_balance)
        daily["exits"] = daily["exits"].astype(int)
    else:
        daily = pd.DataFrame(columns=["date", "realized_pnl", "exits", "balance"])
    rejected = ledger_frame.loc[~ledger_frame.selected] if len(ledger_frame) else ledger_frame
    selected = ledger_frame.loc[ledger_frame.selected] if len(ledger_frame) else ledger_frame
    summary = {
        "sizing": sizing, "risk_fraction": float(risk_fraction), "initial_balance": float(initial_balance),
        "final_balance": balance, "net_pnl": balance - initial_balance,
        "net_return": balance / initial_balance - 1.0, "portfolio_risk_cap": float(portfolio_risk_cap),
        "gross_leverage_cap": float(gross_leverage_cap), "entry_floor": entry_floor,
        "floor_triggered": floor_triggered, "bankrupt": bankrupt, "candidates": int(len(ledger_frame)),
        "selected": int(len(selected)), "rejected": int(len(rejected)),
        "closed": int(selected.selected.sum() - selected.boundary_mark.sum()) if len(selected) else 0,
        "censored_boundary": int(selected.boundary_mark.sum()) if len(selected) else 0,
        "rejection_reasons": dict(Counter(rejected.rejection_reason.dropna())) if len(rejected) else {},
        "max_gross_leverage": float(selected.gross_leverage_after_entry.max()) if len(selected) else 0.0,
        "max_portfolio_initial_risk": float(selected.portfolio_risk_after_entry.max()) if len(selected) else 0.0,
    }
    return {"summary": summary, "ledger": ledger_frame, "equity_curve": curve, "daily_realized_pnl": daily}
