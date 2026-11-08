"""Compare 6h, fixed 12/24h, and 6h-close conditional exits for freq36_ALT.

The 239 admitted event times and their initial altcoin members are frozen from
the frequency study. Actual fixed-horizon member outcomes reuse the existing
VectorBT payoff cache. The six-hour decision uses the close of the final bar
inside the first six hours; a non-positive member exits at the next six-hour
open, while a positive member may continue to the chosen 12/24h open. Random
controls reuse the existing 2026 same-symbol, same-time-stratum draws and are
re-priced through native VectorBT orders for all three fixed horizons.
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_match_exits as match_stats
from yoyo.evaluation import monday_breadth_opt_payoffs as payoffs


ROOT = Path(__file__).resolve().parents[2]
FREQ = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
PARENT = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
OUT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1/alt"
CANDIDATE = "freq36_ALT"
TAKER = 0.0005
FEE_PER_SIDE_PP = TAKER * 100
HOLDS = (6, 12, 24)
STRATEGIES = (
    "baseline_6h",
    "fixed_12h",
    "fixed_24h",
    "close6_green_to_12h",
    "close6_green_to_24h",
)
ALT_EXCLUDED = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "USTCUSDT"}
EPOCH_END = {
    0: pd.Timestamp("2023-01-01", tz="UTC"),
    1: pd.Timestamp("2025-01-01", tz="UTC"),
    2: pd.Timestamp("2026-01-01", tz="UTC"),
}
OOS_START = pd.Timestamp("2026-01-01", tz="UTC")
OOS_END = pd.Timestamp("2026-10-28", tz="UTC")
FUNDING_POLICY = {0: SimpleNamespace(take_fraction=0.0)}


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unavailable"


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


def _utc_column(frame: pd.DataFrame, name: str) -> None:
    frame[name] = pd.to_datetime(frame[name], utc=True)


def load_locked_members() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rules = pd.read_csv(FREQ / "fixed/locked_rules.csv")
    rule = rules[rules.candidate_id.eq(CANDIDATE)].copy()
    if len(rule) != 1:
        raise ValueError(f"Expected one locked rule for {CANDIDATE}, got {len(rule)}")
    r = rule.iloc[0]
    if (str(r.asset) != "ALT" or int(r.minutes) != 5 or int(r.wait_min) != 0
            or int(r.hold_hours) != 6):
        raise ValueError(f"Locked candidate identity changed: {r.to_dict()}")

    events = pd.read_parquet(FREQ / "fixed/locked_events.parquet")
    events = events[events.candidate_id.eq(CANDIDATE)].copy()
    for col in ("known", "decision"):
        _utc_column(events, col)
    events = events.sort_values("decision", kind="stable").reset_index(drop=True)
    if len(events) != 239 or events.decision.duplicated().any():
        raise ValueError(f"Expected 239 unique locked entries, found {len(events)}")
    if not events.epoch.isin([0, 1, 2, 3]).all():
        raise ValueError("Unexpected evaluation epoch")
    if not (events.decision == events.known).all():
        raise ValueError("freq36_ALT wait is zero; decision must equal known")
    events["event_key"] = [f"{CANDIDATE}|{t.isoformat()}" for t in events.decision]
    events["period_name"] = events.epoch.map({0: "pre2023", 1: "2023_2024", 2: "2025", 3: "2026"})

    all_members = pd.read_csv(
        PARENT / "features/members.csv.gz",
        usecols=["minutes", "known", "symbol"], parse_dates=["known"],
    )
    all_members = all_members[(all_members.minutes.eq(5)) & all_members.known.isin(events.known)]
    all_members = all_members[~all_members.symbol.isin(ALT_EXCLUDED)].copy()
    all_members["known"] = pd.to_datetime(all_members.known, utc=True)
    if all_members.duplicated(["known", "symbol"]).any():
        raise ValueError("Duplicate locked constituent row")
    event_ids = events[["known", "decision", "event_key", "epoch", "period_name"]]
    members = all_members.merge(event_ids, on="known", how="inner", validate="many_to_one")
    members = members.rename(columns={"symbol": "symbol"}).sort_values(
        ["decision", "symbol"], kind="stable"
    ).reset_index(drop=True)
    size = members.groupby("event_key").symbol.nunique()
    if len(size) != len(events) or (size <= 0).any():
        raise ValueError("Every locked event must preserve its full initial ALT member set")
    events["expected_members"] = events.event_key.map(size).astype(int)
    members["expected_members"] = members.event_key.map(size).astype(int)
    return rule, events, members


def load_member_payoff_cache(members: pd.DataFrame) -> pd.DataFrame:
    """Read existing native VectorBT 6/12/24h constituent fills without replay."""
    cache_root = payoffs.RESEARCH / "constituents" / "minutes=5"
    fields = [
        "known", "wait_min", "hold_hours", "symbol", "decision", "entry_time", "exit_time",
        "entry_open", "exit_open", "gross_return_pct", "net_return_pct",
        "path_complete", "endpoint_available",
    ]
    pieces = []
    needed = set(members.known.unique())
    for symbol in sorted(members.symbol.unique()):
        folder = cache_root / f"symbol={symbol}"
        paths = sorted(folder.glob("part-*.parquet"))
        if not paths:
            continue
        for path in paths:
            part = pd.read_parquet(
                path, columns=fields,
                filters=[("wait_min", "=", 0), ("hold_hours", "in", list(HOLDS))],
            )
            if part.empty:
                continue
            part = part[(part.symbol.eq(symbol)) & part.known.isin(needed)]
            if not part.empty:
                pieces.append(part)
    if not pieces:
        raise FileNotFoundError(f"No constituent payoff rows found under {cache_root}")
    raw = pd.concat(pieces, ignore_index=True)
    raw["known"] = pd.to_datetime(raw.known, utc=True)
    raw["decision"] = pd.to_datetime(raw.decision, utc=True)
    raw["entry_time"] = pd.to_datetime(raw.entry_time, utc=True)
    raw["exit_time"] = pd.to_datetime(raw.exit_time, utc=True)
    raw["hold_hours"] = pd.to_numeric(raw.hold_hours, errors="raise").astype(int)
    raw = raw[raw.wait_min.eq(0)]
    if raw.duplicated(["known", "symbol", "hold_hours"]).any():
        raise ValueError("Native member cache has duplicate (known, symbol, hold) rows")

    keys = ["known", "symbol"]
    measures = ["entry_open", "exit_open", "gross_return_pct", "net_return_pct",
                "path_complete", "endpoint_available"]
    pivoted = raw.pivot(index=keys, columns="hold_hours", values=measures)
    pivoted.columns = [f"{field}_{int(hours)}h" for field, hours in pivoted.columns]
    pivoted = pivoted.reset_index()
    exits = raw[raw.hold_hours.eq(6)][keys + ["decision", "entry_time"]].rename(
        columns={"decision": "cache_decision", "entry_time": "cache_entry_time"}
    )
    pivoted = pivoted.merge(exits, on=keys, how="left", validate="one_to_one")
    base = members.merge(pivoted, on=keys, how="left", validate="one_to_one")
    base["cache_rows_6_12_24"] = base[[f"net_return_pct_{h}h" for h in HOLDS]].notna().all(axis=1)
    base["cache_time_parity"] = (
        base.cache_decision.eq(base.decision) & base.cache_entry_time.eq(base.decision)
    )
    if base.cache_time_parity.isna().any() or not base.cache_time_parity.all():
        raise ValueError("Cached member entry time differs from the locked 5m decision")
    return base


def load_control_matches(events: pd.DataFrame) -> pd.DataFrame:
    path = FREQ / "controls/fixed_matches.parquet"
    matches = pd.read_parquet(path)
    matches = matches[(matches.candidate_id.eq(CANDIDATE)) & matches.epoch.eq(3)
                      & matches.source.eq("search")].copy()
    if matches.empty:
        raise FileNotFoundError(f"No locked OOS random controls for {CANDIDATE}")
    _utc_column(matches, "decision")
    _utc_column(matches, "random_entry")
    event_cols = events[["event_key", "decision", "expected_members"]]
    matches = matches.merge(event_cols, on="decision", how="left", validate="many_to_one")
    if matches.event_key.isna().any():
        raise ValueError("Random control decision does not belong to the frozen event set")
    if matches.duplicated(["event_key", "symbol", "draw"]).any():
        raise ValueError("Duplicate event/member/draw in existing random controls")
    if not (matches.member_expected_n.astype(int) == matches.expected_members.astype(int)).all():
        raise ValueError("Existing random-control member denominator differs from frozen members")
    return matches.sort_values(["decision", "symbol", "draw"], kind="stable").reset_index(drop=True)


def load_quotes_for_entries(members: pd.DataFrame, controls: pd.DataFrame) -> tuple[pd.DataFrame, dict, list[dict]]:
    """Load cached local 5m OHLC once per symbol for actual/control entry clocks."""
    actual_requests = members[["symbol", "decision"]].rename(columns={"decision": "entry_time"})
    random_requests = controls[["symbol", "random_entry"]].rename(columns={"random_entry": "entry_time"})
    requests = pd.concat([actual_requests, random_requests], ignore_index=True).drop_duplicates()
    requests["entry_time"] = pd.to_datetime(requests.entry_time, utc=True)
    quote_rows: list[dict] = []
    oos_paths: dict[tuple[str, pd.Timestamp], np.ndarray] = {}
    receipts: list[dict] = []
    symbols = list(requests.groupby("symbol", sort=True))
    for n, (symbol, task) in enumerate(symbols, 1):
        times = pd.DatetimeIndex(task.entry_time.unique()).sort_values()
        intervals = [(stamp, stamp + pd.Timedelta(hours=24)) for stamp in times]
        frame, proof = payoffs.load_full_ohlc(
            str(symbol), times.min(), times.max() + pd.Timedelta(hours=24),
            needed_intervals=intervals, repair_missing=False,
        )
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f"Invalid local quote clock for {symbol}")
        receipts.append({"symbol": str(symbol), **proof})
        for entry_time in times:
            grid = pd.date_range(entry_time, periods=24 * 12 + 1, freq="5min")
            path = frame.reindex(grid)
            op = path.open.to_numpy(dtype=float)
            cl = path.close.to_numpy(dtype=float)
            row = {
                "symbol": str(symbol), "entry_time": entry_time,
                "entry_open": op[0] if len(op) else np.nan,
                "close_6h": cl[71] if len(cl) > 71 else np.nan,
                "exit_open_6h": op[72] if len(op) > 72 else np.nan,
                "exit_open_12h": op[144] if len(op) > 144 else np.nan,
                "exit_open_24h": op[288] if len(op) > 288 else np.nan,
                "path_rows_24h": int(path[["open", "high", "low", "close"]].notna().all(axis=1).sum()),
                "path24_complete": bool(path.iloc[:288][["open", "high", "low", "close"]].notna().all().all()
                                         and np.isfinite(op[288])),
            }
            if row["entry_open"] > 0 and np.isfinite(row["close_6h"]):
                row["close_6h_return_pct"] = 100 * (row["close_6h"] / row["entry_open"] - 1)
            else:
                row["close_6h_return_pct"] = np.nan
            for h in HOLDS:
                x = row[f"exit_open_{h}h"]
                row[f"gross_return_pct_{h}h"] = (
                    100 * (x / row["entry_open"] - 1)
                    if row["entry_open"] > 0 and np.isfinite(x) else np.nan
                )
                row[f"after6_return_pct_{h}h"] = (
                    100 * (x / row["close_6h"] - 1)
                    if row["close_6h"] > 0 and np.isfinite(x) else np.nan
                )
            quote_rows.append(row)
            if entry_time.year == 2026:
                # Offset 0 is the entry open; later snapshots use the last
                # completed close; offset 288 is the 24h liquidation open.
                marks = np.full(24 * 12 + 1, np.nan, dtype=float)
                marks[0] = op[0]
                marks[1:288] = cl[:287]
                marks[288] = op[288]
                oos_paths[(str(symbol), entry_time)] = marks
        if n % 50 == 0 or n == len(symbols):
            print(f"loaded local OHLC {n}/{len(symbols)} symbols", flush=True)
    quotes = pd.DataFrame(quote_rows)
    if quotes.duplicated(["symbol", "entry_time"]).any():
        raise ValueError("Duplicate quote row")
    return quotes, oos_paths, receipts


def add_actual_close_decisions(members: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    q = quotes.rename(columns={"entry_time": "decision"})
    out = members.merge(q, on=["symbol", "decision"], how="left", validate="one_to_one")
    if out.entry_open.notna().any():
        for h in HOLDS:
            source_entry = out[f"entry_open_{h}h"] if f"entry_open_{h}h" in out else out.entry_open_6h
            if source_entry.notna().any():
                drift = (out.entry_open - source_entry).abs() / out.entry_open.replace(0, np.nan)
                if drift.dropna().gt(1e-10).any():
                    raise ValueError("Price source entry open does not match the cached VectorBT entry")
    out["close6_green"] = out.close_6h_return_pct.gt(0)
    out["close6_decision_available"] = out.close_6h_return_pct.notna()
    for h in HOLDS:
        out[f"gross_{h}h"] = out[f"gross_return_pct_{h}h"]
        out[f"net_{h}h"] = out[f"net_return_pct_{h}h"]
        out[f"exit_price_{h}h"] = out[f"exit_open_{h}h"]
    return out


def native_random_fills(controls: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    """Run all new random-control fixed exits through native VectorBT orders."""
    import vectorbt as vbt

    unique = controls[["symbol", "random_entry"]].drop_duplicates().rename(
        columns={"random_entry": "entry_time"}
    )
    unique = unique.merge(quotes, on=["symbol", "entry_time"], how="left", validate="one_to_one")
    for h in HOLDS:
        exit_price = unique[f"exit_open_{h}h"].to_numpy(dtype=float)
        entry = unique.entry_open.to_numpy(dtype=float)
        valid = np.isfinite(entry) & (entry > 0) & np.isfinite(exit_price) & (exit_price > 0)
        net = np.full(len(unique), np.nan)
        if valid.any():
            net[valid] = payoffs._vbt_net_returns(entry[valid], exit_price[valid])
        unique[f"gross_{h}h"] = unique[f"gross_return_pct_{h}h"]
        unique[f"net_{h}h"] = net
        unique[f"exit_time_{h}h"] = unique.entry_time + pd.to_timedelta(h, unit="h")
    unique["engine"] = f"VectorBT Portfolio.from_orders {vbt.__version__}; one unit, cash_sharing=False"
    unique["fee_per_side"] = TAKER
    unique["fill_complete_6_12_24"] = unique[[f"net_{h}h" for h in HOLDS]].notna().all(axis=1)
    return unique


def _strategy_horizon(strategy: str, green: pd.Series) -> pd.Series:
    if strategy == "baseline_6h":
        return pd.Series(6, index=green.index, dtype="float64")
    if strategy == "fixed_12h":
        return pd.Series(12, index=green.index, dtype="float64")
    if strategy == "fixed_24h":
        return pd.Series(24, index=green.index, dtype="float64")
    if strategy == "close6_green_to_12h":
        return pd.Series(np.where(green, 12, 6), index=green.index, dtype="float64")
    if strategy == "close6_green_to_24h":
        return pd.Series(np.where(green, 24, 6), index=green.index, dtype="float64")
    raise ValueError(strategy)


def actual_strategy_ledger(members: pd.DataFrame) -> pd.DataFrame:
    pieces = []
    for strategy in STRATEGIES:
        part = members.copy()
        part["strategy"] = strategy
        part["selected_exit_hours"] = _strategy_horizon(strategy, part.close6_green)
        if strategy.startswith("close6_"):
            part.loc[~part.close6_decision_available, "selected_exit_hours"] = np.nan
        part["selected_exit_time"] = part.decision + pd.to_timedelta(part.selected_exit_hours, unit="h")
        for kind in ("gross", "net"):
            values = np.full(len(part), np.nan, dtype=float)
            exits = np.full(len(part), np.nan, dtype=float)
            for h in HOLDS:
                mask = part.selected_exit_hours.eq(h)
                values[mask] = part.loc[mask, f"{kind}_{h}h"].to_numpy(dtype=float)
                exits[mask] = part.loc[mask, f"exit_price_{h}h"].to_numpy(dtype=float)
            part[f"selected_{kind}_pct"] = values
            part["selected_exit_open"] = exits
        part["member_outcome_complete"] = part.selected_net_pct.notna() & part.selected_gross_pct.notna()
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


def _months_overlapping(start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    start = pd.Timestamp(start).tz_convert("UTC")
    end = pd.Timestamp(end).tz_convert("UTC")
    if end < start:
        raise ValueError("Funding end precedes entry")
    return list(pd.period_range(start.tz_localize(None), end.tz_localize(None), freq="M").astype(str))


def _actual_settlements(history: pd.DataFrame, start: pd.Timestamp,
                        end: pd.Timestamp) -> pd.DataFrame:
    """Return observed settlement clocks; do not assume an 8h schedule."""
    start = pd.Timestamp(start).tz_convert("UTC")
    end = pd.Timestamp(end).tz_convert("UTC")
    if end < start:
        raise ValueError("Funding end precedes entry")
    if history.empty:
        return history
    return history.loc[(history.index >= start) & (history.index <= end)]


def _interval_history_complete(history: pd.DataFrame, start: pd.Timestamp,
                               end: pd.Timestamp) -> bool:
    """Require finite observed marks and bracketing clocks on the actual schedule."""
    if history.empty:
        return False
    start = pd.Timestamp(start).tz_convert("UTC")
    end = pd.Timestamp(end).tz_convert("UTC")
    local = history.sort_index()
    inside = local.loc[(local.index >= start) & (local.index <= end)]
    if len(inside) and not (np.isfinite(inside.realized_rate).all()
                            and np.isfinite(inside.mark_price).all()):
        return False
    before, after = local.loc[local.index < start], local.loc[local.index > end]
    if before.empty or after.empty:
        return False
    observed = pd.concat([before.tail(1), inside, after.head(1)])
    diffs = local.index.to_series().diff().dropna().dt.total_seconds().to_numpy() / 3600
    gaps = observed.index.to_series().diff().dropna().dt.total_seconds().to_numpy() / 3600
    if not len(diffs) or not len(gaps):
        return False
    cadence = float(np.median(diffs))
    # Gaps exceeding this bound trigger an official monthly archive check. This
    # detects a missing settlement while allowing ordinary schedule changes.
    return bool(gaps.max() <= max(1.75 * cadence, cadence + 1.0))


def _funding_rates_for_needed_intervals(
        needed: dict[str, dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]]) -> tuple[dict, pd.DataFrame]:
    histories: dict[str, pd.DataFrame] = {}
    receipts: list[dict] = []
    symbols = sorted(needed)
    for i, symbol in enumerate(symbols, 1):
        touched = sorted(needed[symbol])
        # Load neighboring months locally so positions close to a month edge
        # can be verified against the symbol's actual settlement cadence.
        month_periods = [pd.Period(m, freq="M") for m in touched]
        local_months = sorted({str(p + offset) for p in month_periods for offset in (-1, 0, 1)})
        local_parts = []
        for month in local_months:
            local = funding.rates(symbol, months=(month,), local_only=True)
            if not local.empty:
                local_parts.append(local[local.index.strftime("%Y-%m") == month])
        local_history = pd.concat(local_parts).sort_index() if local_parts else pd.DataFrame()
        if not local_history.empty:
            local_history = local_history[~local_history.index.duplicated(keep="last")]
        public_parts = []
        for month in touched:
            intervals = needed[symbol][month]
            unverified = [(a, b) for a, b in intervals
                          if not _interval_history_complete(local_history, a, b)]
            public = pd.DataFrame()
            if unverified:
                # Query only a touched symbol-month that local history cannot
                # verify; official clocks may be 1h, 4h, 8h, or a changed schedule.
                public = funding.archive_month(symbol, month, local_only=False)
                if not public.empty:
                    public = public[public.index.strftime("%Y-%m") == month]
                    public_parts.append(public)
            combined = pd.concat([local_history, *public_parts]).sort_index() if not local_history.empty or public_parts else pd.DataFrame()
            if not combined.empty:
                combined = combined[~combined.index.duplicated(keep="last")]
            missing = [(a, b) for a, b in intervals
                       if not _interval_history_complete(combined, a, b)]
            settlement_rows = [len(_actual_settlements(combined, a, b)) for a, b in intervals] if not combined.empty else []
            month_rows = combined[combined.index.strftime("%Y-%m") == month] if not combined.empty else combined
            receipts.append({
                "symbol": symbol, "month": month,
                "required_intervals": len(intervals),
                "intervals_with_observed_settlement": int(sum(n > 0 for n in settlement_rows)),
                "observed_settlement_rows_across_intervals": int(sum(settlement_rows)),
                "unverified_intervals": [f"{a.isoformat()}..{b.isoformat()}" for a, b in missing],
                "public_archive_checked": bool(unverified),
                "public_archive_supplemented": bool(not public.empty),
                "observed_cadence_hours_median": float(
                    month_rows.index.to_series().diff().dropna().dt.total_seconds().median() / 3600
                ) if len(month_rows) > 1 else np.nan,
                "mark_source_proxy_rows": int(month_rows.price_proxy.fillna(False).sum())
                    if not month_rows.empty and "price_proxy" in month_rows else 0,
            })
        history = pd.concat([local_history, *public_parts]).sort_index() if not local_history.empty or public_parts else pd.DataFrame()
        if not history.empty:
            history = history[~history.index.duplicated(keep="last")]
        histories[symbol] = history
        if i % 50 == 0 or i == len(symbols):
            print(f"loaded 2026 funding {i}/{len(symbols)} symbols", flush=True)
    return histories, pd.DataFrame(receipts)


def _funding_valid(history: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    return _interval_history_complete(history, start, end)


def funding_for_fill_rows(fills: pd.DataFrame, histories: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = fills.copy().reset_index(drop=True)
    out["funding_cost_units"] = np.nan
    out["funding_pct"] = np.nan
    out["funded_net_pct"] = np.nan
    out["funding_complete"] = False
    for symbol, idx in out.groupby("symbol", sort=False).groups.items():
        h = histories.get(str(symbol), pd.DataFrame())
        group = out.loc[idx]
        complete_rows = []
        for row_i, row in group.iterrows():
            if not np.isfinite(row.net_pct) or row.entry_time.year != 2026:
                continue
            if _funding_valid(h, row.entry_time, row.exit_time):
                complete_rows.append(row_i)
        if not complete_rows:
            continue
        frame = out.loc[complete_rows].copy()
        native = pd.DataFrame({
            "entry_time": frame.entry_time,
            "exit_time": frame.exit_time,
            "first_exit_time": frame.exit_time,
            "entry_open": frame.entry_open,
            "exit_legs": 1,
            "policy_id": 0,
        })
        cost = funding.charged(native, h, FUNDING_POLICY)
        cost_pct = 100 * cost / frame.entry_open.to_numpy(dtype=float)
        out.loc[complete_rows, "funding_cost_units"] = cost
        out.loc[complete_rows, "funding_pct"] = cost_pct
        out.loc[complete_rows, "funded_net_pct"] = frame.net_pct.to_numpy(dtype=float) - cost_pct
        out.loc[complete_rows, "funding_complete"] = np.isfinite(cost_pct)
    return out


def make_funding_fill_table(actual: pd.DataFrame, random: pd.DataFrame) -> pd.DataFrame:
    actual_oos = actual[actual.epoch.eq(3)].copy()
    a_rows = []
    for h in HOLDS:
        part = actual_oos[["event_key", "decision", "symbol", "epoch", f"entry_open_{h}h",
                           f"exit_open_{h}h", f"gross_return_pct_{h}h", f"net_return_pct_{h}h"]].copy()
        part = part.rename(columns={
            "decision": "entry_time", f"entry_open_{h}h": "entry_open",
            f"exit_open_{h}h": "exit_open", f"gross_return_pct_{h}h": "gross_pct",
            f"net_return_pct_{h}h": "net_pct",
        })
        part["horizon"] = h
        part["exit_time"] = part.entry_time + pd.to_timedelta(h, unit="h")
        part["fill_side"] = "actual"
        a_rows.append(part)
    r_rows = []
    for h in HOLDS:
        part = random[["symbol", "entry_time", f"entry_open", f"exit_open_{h}h", f"gross_{h}h", f"net_{h}h"]].copy()
        part = part.rename(columns={f"exit_open_{h}h": "exit_open", f"gross_{h}h": "gross_pct", f"net_{h}h": "net_pct"})
        part["horizon"] = h
        part["exit_time"] = part.entry_time + pd.to_timedelta(h, unit="h")
        part["fill_side"] = "random"
        part["event_key"] = ""
        part["epoch"] = 3
        r_rows.append(part)
    return pd.concat(a_rows + r_rows, ignore_index=True, sort=False)


def _event_return_rows(member_ledger: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    group_cols = ["strategy", "event_key", "decision", "epoch", "period_name", "expected_members"]
    records = []
    for key, group in member_ledger.groupby(group_cols, sort=False):
        strategy, event_key, decision, epoch, period_name, expected = key
        complete = group.member_outcome_complete & group.selected_exit_hours.notna()
        complete_group = group[complete]
        all_complete = len(complete_group) == int(expected)
        record = {
            "strategy": strategy, "event_key": event_key, "decision": decision,
            "epoch": int(epoch), "period_name": period_name,
            "expected_members": int(expected), "observed_members": int(complete.sum()),
            "member_coverage_pct": 100 * int(complete.sum()) / int(expected),
            "all_members_complete": bool(all_complete),
            "net_pct": float(complete_group.selected_net_pct.mean()) if all_complete else np.nan,
            "gross_pct": float(complete_group.selected_gross_pct.mean()) if all_complete else np.nan,
            "member_win_pct": float(100 * complete_group.selected_net_pct.gt(0).mean()) if all_complete else np.nan,
            "member_gross_win_pct": float(100 * complete_group.selected_gross_pct.gt(0).mean()) if all_complete else np.nan,
            "exit_6h_members": int(group.selected_exit_hours.eq(6).sum()),
            "exit_12h_members": int(group.selected_exit_hours.eq(12).sum()),
            "exit_24h_members": int(group.selected_exit_hours.eq(24).sum()),
            "exit_6h_pct": 100 * int(group.selected_exit_hours.eq(6).sum()) / int(expected),
            "exit_12h_pct": 100 * int(group.selected_exit_hours.eq(12).sum()) / int(expected),
            "exit_24h_pct": 100 * int(group.selected_exit_hours.eq(24).sum()) / int(expected),
            "max_selected_hold_hours": float(group.selected_exit_hours.max()) if group.selected_exit_hours.notna().any() else np.nan,
        }
        records.append(record)
    return pd.DataFrame(records)


def _fold_metrics(event_rows: pd.DataFrame, baseline: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for strategy in STRATEGIES:
        candidate = event_rows[event_rows.strategy.eq(strategy)].copy()
        for epoch, period in [(0, "pre2023"), (1, "2023_2024"), (2, "2025"), (3, "2026")]:
            sub = candidate[candidate.epoch.eq(epoch)].copy()
            if epoch < 3:
                # Purge the whole maximum horizon from each candidate fold.
                max_h = {"baseline_6h": 6, "fixed_12h": 12, "fixed_24h": 24,
                         "close6_green_to_12h": 12, "close6_green_to_24h": 24}[strategy]
                end = EPOCH_END[epoch]
                sub = sub[sub.decision + pd.to_timedelta(max_h, unit="h") < end]
            sub = sub[sub.all_members_complete].copy()
            paired = sub.merge(
                baseline[["event_key", "net_pct", "all_members_complete"]].rename(columns={"net_pct": "baseline_net_pct"}),
                on="event_key", how="inner", validate="one_to_one",
            )
            paired = paired[paired.all_members_complete_y] if "all_members_complete_y" in paired else paired
            paired = paired.dropna(subset=["net_pct", "baseline_net_pct"]).copy()
            delta = paired.net_pct - paired.baseline_net_pct
            clipped = delta.clip(-20, 20)
            lcb = float(clipped.mean() - 1.28 * clipped.std(ddof=1) / np.sqrt(len(clipped))) if len(clipped) > 1 else np.nan
            rows.append({
                "strategy": strategy, "epoch": epoch, "period": period,
                "n_policy_events": int(len(sub)), "n_paired_events": int(len(paired)),
                "months": int(paired.decision.dt.strftime("%Y-%m").nunique()) if len(paired) else 0,
                "mean_net_pct": float(sub.net_pct.mean()) if len(sub) else np.nan,
                "median_net_pct": float(sub.net_pct.median()) if len(sub) else np.nan,
                "event_win_pct": float(100 * sub.net_pct.gt(0).mean()) if len(sub) else np.nan,
                "member_win_pct": float(sub.member_win_pct.mean()) if len(sub) else np.nan,
                "mean_paired_baseline_net_pct": float(paired.baseline_net_pct.mean()) if len(paired) else np.nan,
                "mean_paired_delta_pp": float(delta.mean()) if len(delta) else np.nan,
                "mean_clipped_paired_delta_pp": float(clipped.mean()) if len(clipped) else np.nan,
                "clipped_paired_delta_lcb_pp": lcb,
            })
    return pd.DataFrame(rows)


def _holm(values: pd.Series) -> pd.Series:
    out = pd.Series(np.nan, index=values.index, dtype=float)
    finite = values.dropna().sort_values(kind="stable")
    m = len(finite)
    if not m:
        return out
    adjusted = np.maximum.accumulate([(m - i) * p for i, p in enumerate(finite.to_numpy())]).clip(0, 1)
    out.loc[finite.index] = adjusted
    return out


def _month_p(group: pd.DataFrame, diff_col: str) -> float:
    part = group.dropna(subset=[diff_col]).copy()
    if part.empty:
        return np.nan
    diffs = part.set_index("decision")[diff_col]
    return float(match_stats.paired_month_p(diffs))


def selection_and_pvalues(fold_summary: pd.DataFrame, event_rows: pd.DataFrame,
                          control_pairs: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    variants = [s for s in STRATEGIES if s != "baseline_6h"]
    dev = fold_summary[(fold_summary.period.isin(["pre2023", "2023_2024", "2025"]))
                       & fold_summary.strategy.isin(variants)]
    pivot = dev.pivot(index="strategy", columns="period", values="mean_paired_delta_pp")
    lcb = dev.pivot(index="strategy", columns="period", values="clipped_paired_delta_lcb_pp")
    selection = pd.DataFrame(index=variants)
    for period in ("pre2023", "2023_2024", "2025"):
        selection[f"delta_{period}_pp"] = pivot.get(period)
        selection[f"lcb_{period}_pp"] = lcb.get(period)
    selection["all_three_mean_deltas_positive"] = selection[
        ["delta_pre2023_pp", "delta_2023_2024_pp", "delta_2025_pp"]
    ].gt(0).all(axis=1)
    selection["positive_fold_count"] = selection[
        ["delta_pre2023_pp", "delta_2023_2024_pp", "delta_2025_pp"]
    ].gt(0).sum(axis=1)
    selection["min_clipped_paired_delta_lcb_pp"] = selection[
        ["lcb_pre2023_pp", "lcb_2023_2024_pp", "lcb_2025_pp"]
    ].min(axis=1)
    selection["selection_class"] = np.where(
        selection.all_three_mean_deltas_positive, "all_three_development_folds_positive", "diagnostic_only"
    )
    gates = selection[selection.all_three_mean_deltas_positive]
    if len(gates):
        chosen = gates.sort_values(
            ["min_clipped_paired_delta_lcb_pp"], ascending=False, kind="stable"
        ).index[0]
        status = "selected_by_all_three_positive_then_min_clipped_paired_delta_lcb"
    else:
        chosen = selection.sort_values(
            ["positive_fold_count", "min_clipped_paired_delta_lcb_pp"],
            ascending=[False, False], kind="stable",
        ).index[0]
        status = "no_candidate_passed_three_fold_mean_gate; best_diagnostic_only"
    selection["selected_for_locked_2026_review"] = selection.index.eq(chosen)
    selection["selection_status"] = status

    # The selection p-values are descriptive after the preregistered historical
    # ranking; the 2026 p-values are the locked, non-selection period.
    dev_p = {}
    oos_base_p = {}
    for strategy in variants:
        candidate = event_rows[event_rows.strategy.eq(strategy) & event_rows.all_members_complete].copy()
        baseline = event_rows[event_rows.strategy.eq("baseline_6h")][["event_key", "decision", "epoch", "net_pct"]]
        paired = candidate.merge(baseline.rename(columns={"net_pct": "baseline_net_pct"}),
                                 on=["event_key", "decision", "epoch"], how="inner", validate="one_to_one")
        dev_pair = paired[paired.epoch.lt(3)].copy()
        dev_pair["delta"] = dev_pair.net_pct - dev_pair.baseline_net_pct
        dev_p[strategy] = _month_p(dev_pair, "delta")
        oos_pair = paired[paired.epoch.eq(3)].copy()
        oos_pair["delta"] = oos_pair.net_pct - oos_pair.baseline_net_pct
        oos_base_p[strategy] = _month_p(oos_pair, "delta")
    selection["p_month_dev_vs_baseline"] = pd.Series(dev_p)
    selection["p_holm_dev_vs_baseline"] = _holm(selection.p_month_dev_vs_baseline)
    selection["p_month_2026_vs_baseline"] = pd.Series(oos_base_p)
    selection["p_holm_2026_vs_baseline"] = _holm(selection.p_month_2026_vs_baseline)

    random_p = {}
    for basis in ("fee_net", "funded_net"):
        eligible = control_pairs[(control_pairs.basis.eq(basis)) & control_pairs.eligible_80pct]
        p = eligible.groupby("strategy", sort=False).apply(
            lambda g: _month_p(g, "paired_delta_pp")
        )
        random_p[basis] = p
        selection[f"p_month_2026_vs_random_{basis}"] = p
        family = pd.Series({s: p.get(s, np.nan) for s in STRATEGIES})
        selection[f"p_holm_2026_vs_random_{basis}"] = _holm(family).reindex(selection.index)
    selection = selection.reset_index(names="strategy")
    lock = {
        "candidate_id": CANDIDATE,
        "frozen_entries": 239,
        "development_periods_only": ["pre2023", "2023_2024", "2025"],
        "selection_rule": "Prefer candidates with candidate-minus-baseline event-mean net deltas >0 in all three folds; rank those by minimum fold clipped paired-difference LCB. If none pass, report best diagnostic by positive-fold count then the same minimum LCB.",
        "paired_difference_lcb": "Clip event-level candidate-minus-same-event/same-members baseline deltas to +/-20 percentage points; LCB = mean - 1.28*SE.",
        "2026_used_for_selection": False,
        "selected_candidate": chosen,
        "selection_status": status,
        "entry_spacing_min_hours": 24,
        "maximum_member_hold_hours": 24,
        "max_hold_compatible_with_original_entry_interval": True,
        "fold_boundary_purge": "For each fold, candidate positions must finish strictly before the next fold boundary using the candidate's maximum possible hold (6/12/24h). All 239 entries remain in the raw ledger; boundary-censored event returns are excluded only from that fold metric.",
    }
    return selection, lock


def build_control_pairs(controls: pd.DataFrame, random_fills: pd.DataFrame,
                        actual_ledger: pd.DataFrame, funding_fills: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    fills = funding_fills[funding_fills.fill_side.eq("random")].copy()
    fills = fills[["symbol", "entry_time", "horizon", "funded_net_pct", "funding_complete"]]
    fill_wide_funded = fills.pivot(index=["symbol", "entry_time"], columns="horizon", values="funded_net_pct")
    fill_wide_ok = fills.pivot(index=["symbol", "entry_time"], columns="horizon", values="funding_complete")
    fill_wide_funded.columns = [f"random_funded_{int(c)}h" for c in fill_wide_funded.columns]
    fill_wide_ok.columns = [f"random_funding_ok_{int(c)}h" for c in fill_wide_ok.columns]
    wide = fill_wide_funded.join(fill_wide_ok).reset_index()
    qcols = ["symbol", "entry_time", "close_6h_return_pct", "entry_open"]
    qcols += [f"net_{h}h" for h in HOLDS]
    qcols += [f"gross_{h}h" for h in HOLDS]
    q = random_fills[qcols]
    control = controls.merge(q, left_on=["symbol", "random_entry"], right_on=["symbol", "entry_time"],
                             how="left", validate="many_to_one")
    control = control.merge(wide, on=["symbol", "entry_time"], how="left", validate="many_to_one")
    control["close6_green"] = control.close_6h_return_pct.gt(0)
    for strategy in STRATEGIES:
        holds = _strategy_horizon(strategy, control.close6_green)
        control[f"random_exit_hours_{strategy}"] = holds
        control[f"random_net_{strategy}"] = np.nan
        control[f"random_funded_{strategy}"] = np.nan
        control[f"draw_complete_price_{strategy}"] = False
        control[f"draw_complete_funding_{strategy}"] = False
        for h in HOLDS:
            mask = holds.eq(h)
            control.loc[mask, f"random_net_{strategy}"] = control.loc[mask, f"net_{h}h"]
            control.loc[mask, f"random_funded_{strategy}"] = control.loc[mask, f"random_funded_{h}h"]
            control.loc[mask, f"draw_complete_price_{strategy}"] = (
                control.loc[mask, f"net_{h}h"].notna()
                & (control.loc[mask, "close_6h_return_pct"].notna()
                   if strategy.startswith("close6_") else True)
            )
            control.loc[mask, f"draw_complete_funding_{strategy}"] = (
                control.loc[mask, f"random_funded_{h}h"].notna()
                & control.loc[mask, f"random_funding_ok_{h}h"].fillna(False)
                & (control.loc[mask, "close_6h_return_pct"].notna()
                   if strategy.startswith("close6_") else True)
            )
    actual_by_member = actual_ledger.pivot(
        index=["event_key", "symbol"], columns="strategy", values=["selected_net_pct", "selected_funded_net_pct"]
    )
    actual_by_member.columns = [f"actual_{metric}_{strategy}" for metric, strategy in actual_by_member.columns]
    actual_by_member = actual_by_member.reset_index()
    rows = []
    pair_member_rows = []
    for (event_key, symbol), group in control.groupby(["event_key", "symbol"], sort=False):
        rec = {"event_key": event_key, "symbol": symbol}
        for strategy in STRATEGIES:
            price_draws = group[group[f"draw_complete_price_{strategy}"]]
            funded_draws = group[group[f"draw_complete_funding_{strategy}"]]
            rec[f"n_draws_price_{strategy}"] = len(price_draws)
            rec[f"n_draws_funded_{strategy}"] = len(funded_draws)
            rec[f"random_net_{strategy}"] = (
                float(price_draws[f"random_net_{strategy}"].mean()) if len(price_draws) else np.nan
            )
            rec[f"random_funded_net_{strategy}"] = (
                float(funded_draws[f"random_funded_{strategy}"].mean()) if len(funded_draws) else np.nan
            )
        pair_member_rows.append(rec)
    random_member = pd.DataFrame(pair_member_rows)
    random_member = random_member.merge(actual_by_member, on=["event_key", "symbol"], how="left", validate="one_to_one")
    expected = controls.groupby("event_key").expected_members.first().to_dict()
    decision_map = controls.groupby("event_key").decision.first().to_dict()
    for strategy in STRATEGIES:
        for basis, actual_col, random_col in [
            ("fee_net", f"actual_selected_net_pct_{strategy}", f"random_net_{strategy}"),
            ("funded_net", f"actual_selected_funded_net_pct_{strategy}", f"random_funded_net_{strategy}"),
        ]:
            for event_key, group in random_member.groupby("event_key", sort=False):
                support = group.dropna(subset=[actual_col, random_col]).copy()
                n_expected = int(expected[event_key])
                coverage = len(support) / n_expected if n_expected else 0.0
                draw_col = f"n_draws_{'price' if basis == 'fee_net' else 'funded'}_{strategy}"
                rows.append({
                    "strategy": strategy, "basis": basis, "event_key": event_key,
                    "decision": decision_map[event_key], "expected_members": n_expected,
                    "matched_members": int(len(support)), "coverage_pct": 100 * coverage,
                    "eligible_80pct": bool(coverage >= 0.80),
                    "n_control_draws": int(support[f"n_draws_price_{strategy}"].sum()) if len(support) else 0,
                    "n_funded_draws": int(support[f"n_draws_funded_{strategy}"].sum()) if len(support) else 0,
                    "actual_pct": float(support[actual_col].mean()) if len(support) else np.nan,
                    "random_pct": float(support[random_col].mean()) if len(support) else np.nan,
                    "paired_delta_pp": float((support[actual_col] - support[random_col]).mean()) if len(support) else np.nan,
                })
    pair_rows = pd.DataFrame(rows)
    # A per-draw raw ledger preserves every existing same-coin random time.
    raw = control.merge(actual_by_member, on=["event_key", "symbol"], how="left", validate="many_to_one")
    return pair_rows, raw


def _period_gate(decision: pd.Timestamp, epoch: int, max_hours: int) -> bool:
    if epoch == 3:
        return decision >= OOS_START and decision < OOS_END and decision + pd.Timedelta(hours=max_hours) < OOS_END
    return decision + pd.Timedelta(hours=max_hours) < EPOCH_END[epoch]


def constituent_behavior(member_ledger: pd.DataFrame, members: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base = members.copy()
    base["green6"] = base.close6_green
    for epoch, period in [(0, "pre2023"), (1, "2023_2024"), (2, "2025"), (3, "2026"), (None, "all_239")]:
        sub = base if epoch is None else base[base.epoch.eq(epoch)]
        if epoch is not None and epoch < 3:
            sub = sub[sub.decision + pd.Timedelta(hours=24) < EPOCH_END[epoch]]
        green = sub[sub.green6.eq(True)].copy()
        for h in (12, 24):
            end_return = green[f"gross_return_pct_{h}h"]
            post6 = green[f"after6_return_pct_{h}h"]
            valid = end_return.notna() & post6.notna() & green.close_6h_return_pct.notna()
            g = green[valid]
            end_return = g[f"gross_return_pct_{h}h"]
            close6 = g.close_6h_return_pct
            change = end_return - close6
            giveback = close6 - end_return
            ratio = 100 * end_return / close6.replace(0, np.nan)
            event_stats = g.assign(
                continued_up=change.gt(0), gave_back=change.lt(0), reversed= end_return.le(0),
                gain_retained_pct=ratio, giveback_pp=giveback, post6_change_pp=change,
            ).groupby("event_key").agg(
                continued_up=("continued_up", "mean"), gave_back=("gave_back", "mean"),
                reversed=("reversed", "mean"), mean_change_pp=("post6_change_pp", "mean"),
                mean_giveback_pp=("giveback_pp", "mean"), mean_gain_retained_pct=("gain_retained_pct", "mean"),
            )
            rows.append({
                "period": period, "horizon_hours": h,
                "six_hour_winner_members": int(len(g)),
                "events_with_winners": int(g.event_key.nunique()) if len(g) else 0,
                "six_hour_close_mean_pct": float(close6.mean()) if len(g) else np.nan,
                "horizon_gross_mean_pct": float(end_return.mean()) if len(g) else np.nan,
                "post6_return_from_close_mean_pct": float(post6[valid].mean()) if valid.any() else np.nan,
                "continued_higher_share_pct_member_weighted": float(100 * change.gt(0).mean()) if len(g) else np.nan,
                "lower_than_6h_close_share_pct_member_weighted": float(100 * change.lt(0).mean()) if len(g) else np.nan,
                "horizon_return_nonpositive_share_pct_member_weighted": float(100 * end_return.le(0).mean()) if len(g) else np.nan,
                "mean_6h_gain_given_back_pp": float(giveback.mean()) if len(g) else np.nan,
                "mean_6h_gain_retained_pct": float(ratio.mean()) if len(g) else np.nan,
                "continued_higher_share_pct_event_weighted": float(100 * event_stats.continued_up.mean()) if len(event_stats) else np.nan,
                "gave_back_share_pct_event_weighted": float(100 * event_stats.gave_back.mean()) if len(event_stats) else np.nan,
                "nonpositive_horizon_share_pct_event_weighted": float(100 * event_stats.reversed.mean()) if len(event_stats) else np.nan,
                "mean_post6_change_pp_event_weighted": float(event_stats.mean_change_pp.mean()) if len(event_stats) else np.nan,
                "mean_giveback_pp_event_weighted": float(event_stats.mean_giveback_pp.mean()) if len(event_stats) else np.nan,
                "mean_gain_retained_pct_event_weighted": float(event_stats.mean_gain_retained_pct.mean()) if len(event_stats) else np.nan,
            })
    return pd.DataFrame(rows)


def _funding_cost_at(history: pd.DataFrame, start: pd.Timestamp, asof: pd.Timestamp) -> float:
    if not _interval_history_complete(history, start, asof):
        return np.nan
    rows = _actual_settlements(history, start, asof)
    if rows.empty:
        return 0.0
    cost = (rows.realized_rate * rows.mark_price).to_numpy(dtype=float)
    if not np.isfinite(cost).all():
        return np.nan
    total = float(cost.sum())
    # Keep the existing conservative boundary policy: negative funding credits
    # on entry and liquidation bars are not relied on.
    if start in rows.index:
        i = rows.index.get_loc(start)
        total -= min(float(cost[i]), 0.0)
    if asof != start and asof in rows.index:
        i = rows.index.get_loc(asof)
        total -= min(float(cost[i]), 0.0)
    return total


def build_oos_trajectory(actual_ledger: pd.DataFrame, oos_paths: dict,
                          histories: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    trajectories = []
    summaries = []
    max_hours = {"baseline_6h": 6, "fixed_12h": 12, "fixed_24h": 24,
                 "close6_green_to_12h": 12, "close6_green_to_24h": 24}
    for strategy in STRATEGIES:
        group = actual_ledger[(actual_ledger.strategy.eq(strategy)) & actual_ledger.epoch.eq(3)].copy()
        group = group.sort_values(["decision", "symbol"], kind="stable")
        cumulative = 0.0
        peak = 0.0
        max_dd_pp = 0.0
        max_dd_pct = 0.0
        full_event_count = 0
        for event_key, event in group.groupby("event_key", sort=False):
            decision = event.decision.iloc[0]
            valid_members = []
            for row in event.itertuples(index=False):
                path = oos_paths.get((str(row.symbol), row.decision))
                if (path is None or not np.isfinite(row.selected_net_pct)
                        or int(row.selected_exit_hours) not in HOLDS):
                    continue
                steps = int(row.selected_exit_hours) * 12
                if not np.isfinite(path[:steps + 1]).all():
                    continue
                if not getattr(row, "funding_complete", False) or not np.isfinite(getattr(row, "selected_funded_net_pct", np.nan)):
                    # Retain fee-net path; funded path will be marked incomplete.
                    funded_complete = False
                else:
                    funded_complete = True
                valid_members.append((row, path, steps, funded_complete))
            coverage = len(valid_members) / int(event.expected_members.iloc[0]) if len(event) else 0.0
            all_prices = len(valid_members) == int(event.expected_members.iloc[0])
            max_steps = int(max_hours[strategy]) * 12
            if valid_members:
                maximum = max(x[2] for x in valid_members)
            else:
                maximum = max_steps
            event_complete = all_prices
            if event_complete:
                full_event_count += 1
            for basis in ("net", "funded"):
                prior_cum = cumulative
                prior_peak = peak
                complete_basis = event_complete and (basis == "net" or all(x[3] for x in valid_members))
                for step in range(maximum + 1):
                    clock = decision + pd.Timedelta(minutes=5 * step)
                    member_marks = []
                    active = 0
                    for row, path, exit_step, fund_ok in valid_members:
                        if step >= exit_step:
                            value = row.selected_net_pct if basis == "net" else row.selected_funded_net_pct
                        else:
                            active += 1
                            mark_price = path[step]
                            value = 100 * (mark_price / row.entry_open - 1) - 2 * FEE_PER_SIDE_PP
                            if basis == "funded":
                                if not fund_ok:
                                    value = np.nan
                                else:
                                    cost = _funding_cost_at(
                                        histories.get(str(row.symbol), pd.DataFrame()),
                                        row.decision, clock,
                                    )
                                    value -= 100 * cost / row.entry_open if np.isfinite(cost) else np.nan
                        member_marks.append(value)
                    mark = float(np.mean(member_marks)) if member_marks and np.isfinite(member_marks).all() else np.nan
                    account_value = prior_cum + mark if np.isfinite(mark) else np.nan
                    if np.isfinite(account_value):
                        current_peak = max(prior_peak, account_value)
                        dd_pp = current_peak - account_value
                        denom = 100 + current_peak
                        dd_pct = 100 * dd_pp / denom if denom > 0 else np.nan
                        max_dd_pp = max(max_dd_pp, dd_pp) if basis == "net" else max_dd_pp
                        max_dd_pct = max(max_dd_pct, dd_pct) if basis == "net" and np.isfinite(dd_pct) else max_dd_pct
                    else:
                        current_peak, dd_pp, dd_pct = prior_peak, np.nan, np.nan
                    trajectories.append({
                        "strategy": strategy, "basis": basis, "event_key": event_key,
                        "decision": decision, "clock": clock, "elapsed_hours": step / 12,
                        "expected_members": int(event.expected_members.iloc[0]),
                        "observed_members": int(len(valid_members)), "member_coverage_pct": 100 * coverage,
                        "event_path_complete": bool(complete_basis), "open_member_positions": active,
                        "event_mark_pct": mark,
                        "fixed_notional_cumulative_pnl_pp": account_value,
                        "drawdown_from_peak_pp": dd_pp,
                        "drawdown_from_peak_pct_initial_100": dd_pct,
                    })
                if event_complete and np.isfinite(event.selected_net_pct).all():
                    cumulative += float(event.selected_net_pct.mean())
                    peak = max(peak, cumulative)
                # A funded account path is tracked separately and does not
                # overwrite the no-funding trajectory's fixed-notional path.
        for basis in ("net", "funded"):
            part = pd.DataFrame([r for r in trajectories if r["strategy"] == strategy and r["basis"] == basis])
            if part.empty:
                continue
            dd = part.dropna(subset=["drawdown_from_peak_pp"])
            summaries.append({
                "strategy": strategy, "basis": basis,
                "oos_events": int(group.event_key.nunique()),
                "full_price_path_events": int(full_event_count),
                "trajectory_rows": int(len(part)),
                "max_marked_drawdown_pp": float(dd.drawdown_from_peak_pp.max()) if len(dd) else np.nan,
                "max_marked_drawdown_pct_initial_100": float(dd.drawdown_from_peak_pct_initial_100.max()) if len(dd) else np.nan,
                "complete_path_coverage_pct": 100 * full_event_count / group.event_key.nunique() if group.event_key.nunique() else 0,
            })
    return pd.DataFrame(trajectories), pd.DataFrame(summaries)


def run(*, prices_only: bool = False) -> dict:
    started = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    rule, events, members = load_locked_members()
    matches = (pd.DataFrame(columns=["symbol", "random_entry"])
               if prices_only else load_control_matches(events))
    cached = load_member_payoff_cache(members)
    quotes, oos_paths, price_receipts = load_quotes_for_entries(members, matches)
    actual = add_actual_close_decisions(cached, quotes)
    actual.to_parquet(OUT / "actual_member_price_inputs.parquet", index=False)
    if oos_paths:
        path_rows = [
            {"symbol": symbol, "entry_time": entry, "step_5m": step, "mark_price": price}
            for (symbol, entry), values in oos_paths.items()
            for step, price in enumerate(values)
        ]
        pd.DataFrame(path_rows).to_parquet(OUT / "actual_oos_path_marks.parquet", index=False)
    with gzip.open(OUT / "actual_price_source_receipts.json.gz", "wt", encoding="utf-8") as f:
        json.dump({"symbols_loaded": len(price_receipts), "local_only": True,
                   "source_receipts": price_receipts}, f, indent=2, ensure_ascii=False, default=str)

    # The full raw ledger retains every frozen member even when one of its price
    # endpoints or six-hour close is unavailable.
    actual_ledger = actual_strategy_ledger(actual)
    actual_ledger.to_parquet(OUT / "member_strategy_returns.parquet", index=False)

    # Reaggregate the unchanged 6h baseline against the published frequency lock.
    baseline = _event_return_rows(actual_ledger, events)
    base_rows = baseline[baseline.strategy.eq("baseline_6h")]
    locked = events[["event_key", "net_pct"]].rename(columns={"net_pct": "locked_baseline_net_pct"})
    parity = base_rows.merge(locked, on="event_key", how="left", validate="one_to_one")
    parity["difference_pp"] = parity.net_pct - parity.locked_baseline_net_pct
    parity["matches_locked"] = parity.difference_pp.abs().le(1e-6)
    parity.to_csv(OUT / "baseline_parity.csv", index=False)
    if parity.all_members_complete.all() and not parity.matches_locked.all():
        raise ValueError("Reaggregated 6h basket differs from the locked candidate returns")

    # Materialize a fee-only checkpoint before the slower 2026 random/funding
    # audit. It covers all locked events and is reused when the full audit runs.
    price_event_rows = _event_return_rows(actual_ledger, events)
    price_folds = _fold_metrics(price_event_rows, base_rows[["event_key", "net_pct", "all_members_complete"]])
    price_event_rows.to_csv(OUT / "event_results_price_only.csv.gz", index=False, compression="gzip")
    price_folds.to_csv(OUT / "fold_summary_price_only.csv", index=False)
    behavior = constituent_behavior(actual, members)
    behavior.to_csv(OUT / "six_hour_winner_followthrough.csv", index=False)
    behavior_detail_cols = ["event_key", "decision", "epoch", "symbol", "close_6h_return_pct", "close6_green",
                            "gross_return_pct_6h", "gross_return_pct_12h", "gross_return_pct_24h",
                            "after6_return_pct_12h", "after6_return_pct_24h"]
    actual[behavior_detail_cols].to_parquet(OUT / "constituent_post6_returns.parquet", index=False)
    price_selection, price_lock = selection_and_pvalues(
        price_folds, price_event_rows,
        pd.DataFrame(columns=["strategy", "basis", "eligible_80pct", "paired_delta_pp", "decision"]),
    )
    price_selection.to_csv(OUT / "selection_price_only.csv", index=False)
    price_metrics = price_event_rows.groupby("strategy", as_index=False).agg(
        events=("event_key", "nunique"), complete_events=("all_members_complete", "sum"),
        mean_event_net_pct=("net_pct", "mean"), median_event_net_pct=("net_pct", "median"),
        event_win_pct=("net_pct", lambda x: 100 * x.gt(0).mean()),
        member_exit_6h_pct=("exit_6h_pct", "mean"), member_exit_12h_pct=("exit_12h_pct", "mean"),
        member_exit_24h_pct=("exit_24h_pct", "mean"),
    )
    price_metrics.to_csv(OUT / "price_stage_summary.csv", index=False)
    price_stage = {
        "locked_entries": len(events), "locked_constituent_rows": len(members),
        "price_cache_rows_6_12_24_complete": int(cached.cache_rows_6_12_24.sum()),
        "six_hour_close_available_rows": int(actual.close6_decision_available.sum()),
        "baseline_all_members_complete_events": int(base_rows.all_members_complete.sum()),
        "baseline_parity_failures": int((~parity.matches_locked).sum()),
        "price_stage_summary": price_metrics.to_dict("records"),
        "development_folds": price_folds[price_folds.period.isin(["pre2023", "2023_2024", "2025"])]
            [["strategy", "period", "n_paired_events", "mean_paired_delta_pp", "clipped_paired_delta_lcb_pp"]]
            .to_dict("records"),
        "selected_by_development_only": price_lock["selected_candidate"],
        "selection_status": price_lock["selection_status"],
        "uses_2026_for_selection": False,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    _json(OUT / "price_stage.json", price_stage)
    print("PRICE_STAGE_COMPLETE " + json.dumps(price_stage, ensure_ascii=False, default=str), flush=True)
    if prices_only:
        return price_stage

    native_random = native_random_fills(matches, quotes)
    native_random.to_parquet(OUT / "control_native_vectorbt_fills.parquet", index=False)

    # Build 2026 native-fill funding rows. Historical folds use the same native
    # 5bp-per-side cached price returns as the baseline; funding is reported for
    # 2026 only, matching the existing audit contract.
    funding_fill_table = make_funding_fill_table(actual, native_random)
    required: dict[str, dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]] = {}
    oos_fills = funding_fill_table[
        funding_fill_table.fill_side.eq("actual") | funding_fill_table.fill_side.eq("random")
    ].copy()
    for row in oos_fills[oos_fills.entry_time.dt.year.eq(2026) & oos_fills.net_pct.notna()].itertuples():
        for month in _months_overlapping(row.entry_time, row.exit_time):
            by_month = required.setdefault(str(row.symbol), {}).setdefault(month, [])
            interval = (row.entry_time, row.exit_time)
            if interval not in by_month:
                by_month.append(interval)
    histories, funding_receipt = _funding_rates_for_needed_intervals(required)
    funding_fills = funding_for_fill_rows(funding_fill_table, histories)
    funding_fills.to_parquet(OUT / "funding_fill_ledger_2026.parquet", index=False)
    funding_receipt.to_csv(OUT / "funding_coverage.csv", index=False)

    # Attach the six/12/24h funded member outcomes; this provides both common
    # cost assumptions and a same-member comparison for every strategy.
    actual_funded = funding_fills[funding_fills.fill_side.eq("actual")].copy()
    actual_funded = actual_funded[["event_key", "symbol", "horizon", "funding_pct", "funded_net_pct", "funding_complete"]]
    actual_funded_wide = actual_funded.pivot(index=["event_key", "symbol"], columns="horizon",
                                             values=["funding_pct", "funded_net_pct", "funding_complete"])
    actual_funded_wide.columns = [f"{name}_{int(h)}h" for name, h in actual_funded_wide.columns]
    actual_funded_wide = actual_funded_wide.reset_index()
    actual = actual.merge(actual_funded_wide, on=["event_key", "symbol"], how="left", validate="one_to_one")
    actual["funded_close6_green"] = actual.close6_green
    actual_ledger = actual_strategy_ledger(actual)
    actual_ledger["selected_funded_net_pct"] = np.nan
    actual_ledger["funding_complete"] = False
    for h in HOLDS:
        mask = actual_ledger.selected_exit_hours.eq(h)
        actual_ledger.loc[mask, "selected_funded_net_pct"] = actual_ledger.loc[mask, f"funded_net_pct_{h}h"].to_numpy(dtype=float)
        actual_ledger.loc[mask, "funding_complete"] = actual_ledger.loc[mask, f"funding_complete_{h}h"].fillna(False).to_numpy(dtype=bool)
    actual_ledger.to_parquet(OUT / "member_strategy_returns.parquet", index=False)

    # Build the corresponding wide random rows for matching on the exact same
    # member set; the random 6/12/24h positions are already native VectorBT.
    random_funded = funding_fills[funding_fills.fill_side.eq("random")].copy()
    random_funded.to_parquet(OUT / "random_funding_fill_ledger.parquet", index=False)
    control_pairs, control_raw = build_control_pairs(matches, native_random, actual_ledger, funding_fills)
    control_raw.to_parquet(OUT / "control_member_draws.parquet", index=False)
    control_pairs.to_csv(OUT / "control_paired_events.csv", index=False)

    # Regenerate event values after funding attachment; pre-2026 returns and
    # entry/member identity stay byte-for-byte sourced from the original cache.
    event_rows = _event_return_rows(actual_ledger, events)
    funded_event_rows = actual_ledger.groupby(
        ["strategy", "event_key", "decision", "epoch"], as_index=False
    ).agg(funded_net_pct=("selected_funded_net_pct", "mean"), funded_members=("selected_funded_net_pct", "count"),
          funding_complete_members=("funding_complete", "sum"))
    event_rows = event_rows.merge(funded_event_rows, on=["strategy", "event_key", "decision", "epoch"],
                                  how="left", validate="one_to_one")
    event_rows["funding_member_coverage_pct"] = 100 * event_rows.funding_complete_members / event_rows.expected_members
    event_rows.loc[event_rows.funding_member_coverage_pct.lt(99.999), "funded_net_pct"] = np.nan
    event_rows.to_csv(OUT / "event_results.csv.gz", index=False, compression="gzip")

    baseline_rows = event_rows[event_rows.strategy.eq("baseline_6h")][
        ["event_key", "net_pct", "all_members_complete"]
    ].rename(columns={"net_pct": "baseline_net_pct"})
    fold_summary = _fold_metrics(event_rows, baseline_rows.rename(columns={"baseline_net_pct": "net_pct"}))
    fold_summary.to_csv(OUT / "fold_summary.csv", index=False)
    selection, selection_lock = selection_and_pvalues(fold_summary, event_rows, control_pairs)
    selection.to_csv(OUT / "selection.csv", index=False)
    _json(OUT / "selection.json", selection_lock)

    # Verify the baseline 2026 funding result against the already audited event
    # returns, without using that audit to select a policy.
    audited = pd.read_parquet(FREQ / "audit/fixed/all_rule_and_account_events.parquet")
    audited = audited[audited.candidate_id.eq(CANDIDATE) & audited.epoch.eq(3)][
        ["decision", "funded_pct"]
    ].copy()
    _utc_column(audited, "decision")
    computed_base = event_rows[event_rows.strategy.eq("baseline_6h") & event_rows.epoch.eq(3)][
        ["decision", "funded_net_pct", "funding_member_coverage_pct"]
    ]
    funded_parity = computed_base.merge(audited, on="decision", how="left", validate="one_to_one")
    funded_parity["difference_pp"] = funded_parity.funded_net_pct - funded_parity.funded_pct
    funded_parity["matches_audit_when_complete"] = (
        funded_parity.funding_member_coverage_pct.ge(99.999)
        & funded_parity.difference_pp.abs().le(1e-5)
    )
    funded_parity.to_csv(OUT / "funding_baseline_parity.csv", index=False)

    trajectory, risk_summary = build_oos_trajectory(actual_ledger, oos_paths, histories)
    trajectory.to_csv(OUT / "oos_trajectory.csv.gz", index=False, compression="gzip")
    risk_summary.to_csv(OUT / "oos_drawdown.csv", index=False)

    price_receipt = {
        "symbols_loaded": len(price_receipts),
        "local_only": True,
        "source_receipts": price_receipts,
    }
    with gzip.open(OUT / "price_source_receipts.json.gz", "wt", encoding="utf-8") as f:
        json.dump(price_receipt, f, indent=2, ensure_ascii=False, default=str)

    coverage = {
        "candidate_id": CANDIDATE,
        "locked_entries": int(len(events)),
        "locked_members": int(len(members)),
        "unique_member_symbols": int(members.symbol.nunique()),
        "cache_6_12_24_rows_complete": int(cached.cache_rows_6_12_24.sum()),
        "cache_member_rows": int(len(cached)),
        "close6_decision_available": int(actual.close6_decision_available.sum()),
        "random_control_rows": int(len(matches)),
        "random_control_unique_entries": int(len(native_random)),
        "random_control_unique_symbols": int(matches.symbol.nunique()),
        "random_native_all_horizons_complete": int(native_random.fill_complete_6_12_24.sum()),
        "actual_oos_member_events": int(members[members.epoch.eq(3)].shape[0]),
        "actual_oos_unique_events": int(events.epoch.eq(3).sum()),
        "actual_oos_events_with_funding_parity": int(funded_parity.matches_audit_when_complete.sum()),
        "actual_oos_events_total_funding_parity": int(len(funded_parity)),
        "control_eligible_events_fee_net": int(control_pairs[(control_pairs.basis.eq("fee_net")) & control_pairs.eligible_80pct].event_key.nunique()),
        "control_total_events_fee_net": int(control_pairs.basis.eq("fee_net").event_key.nunique()),
        "control_eligible_events_funded_net": int(control_pairs[(control_pairs.basis.eq("funded_net")) & control_pairs.eligible_80pct].event_key.nunique()),
        "control_total_events_funded_net": int(control_pairs.basis.eq("funded_net").event_key.nunique()),
    }
    _json(OUT / "coverage.json", coverage)

    manifest = {
        "experiment_id": "exp-monday-breadth-extension-20261108-v1/alt",
        "candidate_id": CANDIDATE,
        "builder": "yoyo/evaluation/monday_breadth_extension_alt.py",
        "builder_commit_at_run": _git_head(),
        "source_lock": str((FREQ / "fixed/locked_events.parquet").relative_to(ROOT)),
        "native_member_payoff_cache": str(payoffs.RESEARCH.relative_to(ROOT)),
        "native_control_fill_engine": "VectorBT Portfolio.from_orders via monday_breadth_opt_payoffs._vbt_net_returns",
        "fees": {"entry_taker": TAKER, "exit_taker": TAKER, "funding_reported_separately": True},
        "entry_clock": "locked decision open; known + 0 min",
        "selector": "At t+6h, compare the close of the last 5m bar inside [entry, entry+6h) with the entry open. Strictly positive stays to the policy horizon; otherwise exit at the t+6h next 5m open.",
        "random_control": "Existing freq36_ALT 2026 same-coin, UTC-month/6h-block, prior-volatility/trend, monitor-schedule matched random timestamps; same timestamp and selected exit rule; 80% expected-member support; both sides use 5bp per fill and paired funding when complete.",
        "funding": "2026 settlement-rate × mark-price; local_only first, targeted public market archive fill only for required missing symbol/settlement months; missing settlements remain missing, never zero-filled.",
        "entry_gap_hours_min": float(events.decision.sort_values().diff().dropna().dt.total_seconds().min() / 3600),
        "maximum_hold_hours": 24,
        "split_purge": "max possible horizon ends strictly before fold boundary",
        "selection_contract": selection_lock,
        "outputs": sorted(p.name for p in OUT.iterdir() if p.is_file() and p.name != "manifest.json"),
        "rows": coverage,
        "elapsed_seconds": round(time.time() - started, 3),
    }
    _json(OUT / "manifest.json", manifest)
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), **coverage,
                      "selected": selection_lock["selected_candidate"],
                      "selection_status": selection_lock["selection_status"],
                      "elapsed_seconds": manifest["elapsed_seconds"]}, indent=2), flush=True)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="materialize the frozen 6/12/24h comparison")
    parser.add_argument("--prices-only", action="store_true", help="materialize all constituent price outcomes first")
    args = parser.parse_args()
    if not (args.run or args.prices_only):
        raise SystemExit("Use --prices-only or --run to execute the frozen local-data study")
    run(prices_only=args.prices_only)
