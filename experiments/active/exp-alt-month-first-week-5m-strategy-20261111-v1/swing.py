"""First-week upper-rail reclaim swing candidate on archived native 5m bars.

This runner varies only the VectorBT holding horizon (1h, 24h, 7d). Frozen
signal and matched-control clocks enter short at the next observed open, target
the near-side first-week high capped to 4% below the actual slipped entry, and
use a fixed 5% stop. Shared account sizing is delegated to ``account.py``.
"""
from __future__ import annotations

import gc
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from account import run_account
from engine import simulate_batch

DATA = ROOT / "data/research/month-first-week-5m-strategy-20261111-v1"
WINDOW_PATH = DATA / "swing_windows.npy"
ROSTER_PATH = DATA / "swing_roster.csv.gz"
CONFIG_PATH = HERE / "config.json"
BAR_MS = 5 * 60_000
WINDOW_BARS = 7 * 24 * 12
HORIZONS = {"1h": 12, "24h": 24 * 12, "7d": WINDOW_BARS}
SLIPPAGE_BPS = (1, 5)
TAKER_FEE_BPS = 5.0
LIMIT_FEE_BPS = 2.0
STOP_PCT = 0.05
TRADE_FRACTION = 0.05
MAX_SLOTS = 10
BATCH_SIZE = 64
OHLC_COLUMNS = ("ts_ms", "open", "high", "low", "close")


def _periods(config: dict) -> list[tuple[str, int, int]]:
    return [
        (
            "pre_2025",
            pd.Timestamp("2022-02-01T00:00:00Z").value // 1_000_000,
            pd.Timestamp("2025-01-01T00:00:00Z").value // 1_000_000,
        ),
        (
            "2025",
            pd.Timestamp("2025-01-01T00:00:00Z").value // 1_000_000,
            pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000,
        ),
        (
            "2026_jan_oct",
            pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000,
            pd.Timestamp(config["end_exclusive_utc"]).value // 1_000_000,
        ),
        (
            "full",
            pd.Timestamp(config["start_utc"]).value // 1_000_000,
            pd.Timestamp(config["end_exclusive_utc"]).value // 1_000_000,
        ),
    ]


def _load_roster() -> tuple[pd.DataFrame, np.ndarray]:
    if not ROSTER_PATH.is_file() or not WINDOW_PATH.is_file():
        raise FileNotFoundError(f"Expected prepared swing inputs: {ROSTER_PATH} and {WINDOW_PATH}")
    roster = pd.read_csv(ROSTER_PATH)
    windows = np.load(WINDOW_PATH, mmap_mode="r")
    required = {
        "window_idx", "window_key", "event_id", "parent_event_id", "candidate_id", "symbol", "side",
        "target_price", "entry_ms", "entry_open", "kind", "matched", "entry_after_near",
    }
    missing = sorted(required - set(roster.columns))
    if missing:
        raise ValueError(f"swing roster missing columns: {missing}")
    if windows.ndim != 3 or windows.shape[1:] != (WINDOW_BARS, len(OHLC_COLUMNS)):
        raise ValueError(
            f"swing windows must have shape (N,{WINDOW_BARS},5), got {windows.shape}"
        )
    if len(windows) != len(roster):
        raise ValueError("swing windows and roster row counts differ")
    if not roster.candidate_id.is_unique or not roster.window_idx.is_unique:
        raise ValueError("candidate_id and window_idx must each be unique")
    window_idx = pd.to_numeric(roster.window_idx, errors="raise").to_numpy(dtype=np.int64)
    if not np.array_equal(window_idx, np.arange(len(roster), dtype=np.int64)):
        raise ValueError("swing_windows.npy rows must be in roster order and indexed from zero")
    if not roster.kind.isin(("signal", "placebo")).all():
        raise ValueError("kind must be signal or placebo")
    if not roster.side.astype(str).str.lower().eq("upper").all():
        raise ValueError("This candidate is fixed to the first upper-rail reclaim short")
    for column in ("matched", "entry_after_near"):
        normalized = roster[column].astype(str).str.strip().str.lower()
        if not normalized.isin(("true", "false", "1", "0")).all():
            raise ValueError(f"{column} must contain explicit booleans")
        roster[column] = normalized.isin(("true", "1"))
    if not roster.entry_after_near.eq(False).all():
        raise ValueError("Roster contains an entry after the first-week high was already reached")
    for column in ("window_key", "event_id", "parent_event_id", "candidate_id", "symbol"):
        if roster[column].isna().any():
            raise ValueError(f"{column} cannot contain null values")
        roster[column] = roster[column].astype(str)
        if roster[column].eq("").any():
            raise ValueError(f"{column} cannot contain empty values")
    return roster, windows


def _audit_matched_pairs(rows: pd.DataFrame, *, require_complete: bool) -> tuple[dict, pd.DataFrame]:
    """Check control pairing, symbol identity and 7d non-overlap by parent event."""
    matched = rows.loc[rows.matched.astype(bool)].copy()
    if matched.empty:
        return {
            "matched_signal_pairs": 0,
            "matched_placebo_pairs": 0,
            "pair_violations": 0,
            "minimum_pair_gap_days": None,
        }, pd.DataFrame(columns=["parent_event_id", "violation"])

    matched["entry_ms"] = pd.to_numeric(matched.entry_ms, errors="raise").astype("int64")
    violations = []
    gaps_days = []
    for parent_event_id, group in matched.groupby("parent_event_id", sort=False):
        signals = group.loc[group.kind.eq("signal")]
        controls = group.loc[group.kind.eq("placebo")]
        reasons = []
        if len(signals) != 1 or len(controls) != 1:
            reasons.append(f"expected_one_signal_and_placebo:{len(signals)}:{len(controls)}")
        else:
            signal = signals.iloc[0]
            control = controls.iloc[0]
            if signal.symbol != control.symbol:
                reasons.append("symbol_mismatch")
            if signal.event_id != control.event_id:
                reasons.append("event_id_mismatch")
            gap_ms = abs(int(signal.entry_ms) - int(control.entry_ms))
            gaps_days.append(gap_ms / (24 * 60 * 60_000))
            if gap_ms < WINDOW_BARS * BAR_MS:
                reasons.append("7d_windows_overlap")
        if reasons:
            violations.append({"parent_event_id": parent_event_id, "violation": ";".join(reasons)})

    audit = {
        "matched_signal_pairs": int(matched.kind.eq("signal").sum()),
        "matched_placebo_pairs": int(matched.kind.eq("placebo").sum()),
        "pair_violations": int(len(violations)),
        "minimum_pair_gap_days": float(min(gaps_days)) if gaps_days else None,
    }
    violations_df = pd.DataFrame(violations, columns=["parent_event_id", "violation"])
    if require_complete and not violations_df.empty:
        raise ValueError(
            "Matched signal/control roster is not one-to-one, same-symbol, and 7d non-overlapping: "
            f"{violations_df.head(5).to_dict('records')}"
        )
    return audit, violations_df


def _eligible_roster(roster: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Freeze one common sample set that remains valid under the 5bp stress entry."""
    rows = roster.copy()
    for column in ("target_price", "entry_open", "entry_ms"):
        rows[column] = pd.to_numeric(rows[column], errors="raise")
    if not np.isfinite(rows[["target_price", "entry_open", "entry_ms"]].to_numpy(float)).all():
        raise ValueError("Roster target and entry values must be finite")
    if (rows[["target_price", "entry_open"]] <= 0).any().any():
        raise ValueError("Roster target and entry prices must be positive")
    rows["entry_ms"] = rows.entry_ms.astype("int64")
    # A short market entry fills below the observed open. Use the 5bp stress
    # entry to keep one event roster shared by both slip levels and all holds.
    worst_entry_fill = rows.entry_open * (1.0 - max(SLIPPAGE_BPS) / 10_000.0)
    eligible = rows.target_price < worst_entry_fill
    counts = {
        "roster_rows": int(len(rows)),
        "signal_roster_rows": int(rows.kind.eq("signal").sum()),
        "placebo_roster_rows": int(rows.kind.eq("placebo").sum()),
        "matched_signal_rows": int((rows.kind.eq("signal") & rows.matched.astype(bool)).sum()),
        "excluded_target_not_favorable_at_5bp": int((~eligible).sum()),
    }
    rows = rows.loc[eligible].reset_index(drop=True)
    if rows.empty:
        raise ValueError("No candidate remains after the common 5bp entry-target check")
    return rows, counts


def _validate_windows(rows: pd.DataFrame, windows: np.ndarray) -> None:
    """Verify each chosen window is complete native 5m OHLC with an exact entry open."""
    for lo in range(0, len(rows), BATCH_SIZE):
        batch = rows.iloc[lo : lo + BATCH_SIZE]
        for row in batch.itertuples(index=False):
            values = np.asarray(windows[int(row.window_idx)])
            if not np.isfinite(values).all() or (values[:, 1:] <= 0).any():
                raise ValueError(f"Incomplete or invalid archived 5m bars: {row.candidate_id}")
            ts = values[:, 0].astype(np.int64)
            if not np.all(np.diff(ts) == BAR_MS):
                raise ValueError(f"Non-contiguous 5m bars: {row.candidate_id}")
            op, hi, low, close = values[:, 1:].T
            if ((hi < np.maximum.reduce((op, low, close)))
                    | (low > np.minimum.reduce((op, hi, close)))).any():
                raise ValueError(f"Invalid 5m OHLC ordering: {row.candidate_id}")
            if ts[0] != int(row.entry_ms):
                raise ValueError(f"Entry clock differs from archive window: {row.candidate_id}")
            if not np.isclose(op[0], float(row.entry_open), rtol=1e-10, atol=1e-12):
                raise ValueError(f"Entry open differs from archive window: {row.candidate_id}")


def _native_paths(
    roster: pd.DataFrame,
    windows: np.ndarray,
    *,
    horizon: str,
    slippage_bps: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fills_parts = []
    marks_parts = []
    horizon_bars = HORIZONS[horizon]
    started = time.perf_counter()
    for lo in range(0, len(roster), BATCH_SIZE):
        batch = roster.iloc[lo : lo + BATCH_SIZE].copy()
        actual_entry = batch.entry_open.to_numpy(dtype=float) * (
            1.0 - slippage_bps / 10_000.0
        )
        capped_target = np.maximum(batch.target_price.to_numpy(dtype=float), actual_entry * 0.96)
        if (capped_target >= actual_entry).any():
            bad = batch.loc[capped_target >= actual_entry, "candidate_id"].head().tolist()
            raise ValueError(f"Capped target is not favorable for short entries: {bad}")

        event_frames = {}
        engine_candidates = batch[["candidate_id", "symbol", "side", "window_key"]].copy()
        engine_candidates["event_id"] = engine_candidates.window_key.astype(str)
        engine_candidates["entry_idx"] = 0
        engine_candidates["target_price"] = capped_target
        engine_candidates = engine_candidates.drop(columns="window_key")
        for row in batch.itertuples(index=False):
            key = str(row.window_key)
            if key in event_frames:
                raise ValueError(f"Duplicate engine window key in batch: {key}")
            event_frames[key] = pd.DataFrame(
                np.asarray(windows[int(row.window_idx)]), columns=OHLC_COLUMNS, copy=False
            )

        fills, marks = simulate_batch(
            event_frames,
            engine_candidates,
            tp_mode="target_distance",
            tp_value=1.0,
            sl_pct=STOP_PCT,
            horizon_bars=horizon_bars,
            slippage_bps=slippage_bps,
            fee_market_bps=TAKER_FEE_BPS,
            fee_limit_bps=LIMIT_FEE_BPS,
            bar_count=WINDOW_BARS,
        )
        if len(fills) != len(batch) or not fills.candidate_id.is_unique:
            raise AssertionError("VectorBT swing path cardinality differs from this batch")
        meta = batch[[
            "candidate_id", "event_id", "parent_event_id", "symbol", "kind", "matched",
            "entry_open", "target_price",
        ]].rename(columns={"target_price": "near_side_target_price"})
        meta["capped_target"] = capped_target
        fills = fills.drop(
            columns=["event_id", "symbol", "near_side_target_price"], errors="ignore"
        ).merge(
            meta, on="candidate_id", how="left", validate="one_to_one"
        )
        marks = marks.drop(columns=["event_id", "symbol"], errors="ignore").merge(
            meta[["candidate_id", "event_id", "parent_event_id", "symbol", "kind", "matched"]],
            on="candidate_id", how="left", validate="many_to_one",
        )
        fills["horizon"] = horizon
        fills["horizon_bars"] = horizon_bars
        fills["market_slippage_bps"] = slippage_bps
        marks["horizon"] = horizon
        marks["market_slippage_bps"] = slippage_bps
        fills_parts.append(fills)
        marks_parts.append(marks)
        done = min(lo + len(batch), len(roster))
        print(
            f"VectorBT {horizon} {slippage_bps}bp: {done}/{len(roster)} candidates, "
            f"{len(marks)} active marks in last batch, {time.perf_counter()-started:.1f}s",
            flush=True,
        )
    return pd.concat(fills_parts, ignore_index=True), pd.concat(marks_parts, ignore_index=True)


def _account_pools(fills: pd.DataFrame, marks: pd.DataFrame) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    signal_ids = set(fills.loc[fills.kind.eq("signal"), "candidate_id"])
    matched_ids = set(fills.loc[fills.kind.eq("signal") & fills.matched.astype(bool), "candidate_id"])
    placebo_ids = set(fills.loc[fills.kind.eq("placebo"), "candidate_id"])
    return {
        "signal_all": (fills[fills.candidate_id.isin(signal_ids)], marks[marks.candidate_id.isin(signal_ids)]),
        "signal_matched": (fills[fills.candidate_id.isin(matched_ids)], marks[marks.candidate_id.isin(matched_ids)]),
        "placebo": (fills[fills.candidate_id.isin(placebo_ids)], marks[marks.candidate_id.isin(placebo_ids)]),
    }


def run() -> dict:
    config = json.loads(CONFIG_PATH.read_text())
    roster, windows = _load_roster()
    pair_audit, _ = _audit_matched_pairs(roster, require_complete=True)
    roster, sample_counts = _eligible_roster(roster)
    eligible_pair_audit, _ = _audit_matched_pairs(roster, require_complete=True)
    _validate_windows(roster, windows)
    print(
        f"Common 7d-complete roster: {len(roster)} ({roster.kind.value_counts().to_dict()})",
        flush=True,
    )
    periods = _periods(config)
    metric_rows = []
    independent_fills = []
    funded_trade_parts = []
    equity_parts = []
    all_timing = {}

    # Signals and placebo clocks are processed separately so the largest
    # 7-day mark path is released before the other arm is materialized.
    for horizon in HORIZONS:
        for slip in SLIPPAGE_BPS:
            scenario_start = time.perf_counter()
            for kind in ("signal", "placebo"):
                pool = roster[roster.kind.eq(kind)].reset_index(drop=True)
                if pool.empty:
                    continue
                fills, marks = _native_paths(
                    pool, windows, horizon=horizon, slippage_bps=slip
                )
                independent_fills.append(fills)
                all_timing[f"{kind}_{horizon}_{slip}bp"] = {
                    "candidates": int(len(pool)),
                    "engine_trades": int(len(fills)),
                    "active_mark_rows": int(len(marks)),
                }
                matched_ids = set(
                    fills.loc[fills.matched.astype(bool), "candidate_id"]
                ) if kind == "signal" else set()
                account_pools = {kind: (fills, marks)}
                if kind == "signal":
                    account_pools = {
                        "signal_all": (fills, marks),
                        "signal_matched": (
                            fills[fills.candidate_id.isin(matched_ids)],
                            marks[marks.candidate_id.isin(matched_ids)],
                        ),
                    }
                for pool_name, (pool_fills, pool_marks) in account_pools.items():
                    for period, start_ms, end_ms in periods:
                        result = run_account(
                            pool_fills,
                            pool_marks,
                            period="full",
                            period_start_ms=start_ms,
                            period_end_ms=end_ms,
                            initial_cash=1_000.0,
                            max_slots=MAX_SLOTS,
                            per_trade_fraction=TRADE_FRACTION,
                            slippage_bps=0.0,
                        )
                        summary = dict(result["summary"])
                        summary.update(
                            horizon=horizon,
                            horizon_bars=HORIZONS[horizon],
                            market_slippage_bps=slip,
                            pool=pool_name,
                            period=period,
                            candidates=int(len(pool_fills)),
                            matched_events=(int(len(matched_ids)) if kind == "signal" else int(len(pool_fills))),
                        )
                        metric_rows.append(summary)
                        if not result["funded_trades"].empty:
                            trades_out = result["funded_trades"].copy()
                            trades_out["horizon"] = horizon
                            trades_out["horizon_bars"] = HORIZONS[horizon]
                            trades_out["market_slippage_bps"] = slip
                            trades_out["pool"] = pool_name
                            trades_out["period"] = period
                            funded_trade_parts.append(trades_out)
                        if not result["equity_5m"].empty:
                            equity_out = result["equity_5m"].reset_index()
                            equity_out["horizon"] = horizon
                            equity_out["horizon_bars"] = HORIZONS[horizon]
                            equity_out["market_slippage_bps"] = slip
                            equity_out["pool"] = pool_name
                            equity_out["period"] = period
                            equity_parts.append(equity_out)
                        print(
                            f"Account {pool_name} {horizon} {slip}bp {period}: "
                            f"{summary['trades_funded']} funded, "
                            f"{summary['net_return_pct']:.2f}% return, "
                            f"{summary['max_dd_intrabar_pct']:.2f}% intrabar DD",
                            flush=True,
                        )
                del fills, marks
                gc.collect()
            print(
                f"Scenario {horizon}/{slip}bp completed in {time.perf_counter()-scenario_start:.1f}s",
                flush=True,
            )

    metrics = pd.DataFrame(metric_rows)
    fills_all = pd.concat(independent_fills, ignore_index=True) if independent_fills else pd.DataFrame()
    funded_all = pd.concat(funded_trade_parts, ignore_index=True) if funded_trade_parts else pd.DataFrame()
    equity_all = pd.concat(equity_parts, ignore_index=True) if equity_parts else pd.DataFrame()
    metrics.to_csv(HERE / "swing_account_metrics.csv", index=False)
    fills_all.to_csv(HERE / "swing_independent_trades.csv.gz", index=False)
    funded_all.to_csv(HERE / "swing_funded_trades.csv.gz", index=False)
    equity_all.to_csv(DATA / "swing_equity_5m.csv.gz", index=False)
    output = {
        "strategy": "first upper-rail reclaim, next-open short",
        "source_windows": str(WINDOW_PATH.relative_to(ROOT)),
        "source_roster": str(ROSTER_PATH.relative_to(ROOT)),
        "equity_curve": str((DATA / "swing_equity_5m.csv.gz").relative_to(ROOT)),
        "entry": "first strict close back inside the upper first-week rail within touch bar through the next five 5m bars; short at following open",
        "target": "max(first-week high, actual slipped entry * 0.96); limit fee 2bp",
        "stop": "fixed 5% adverse from actual slipped entry; market fee 5bp",
        "market_slippage_bps": list(SLIPPAGE_BPS),
        "holding_bars": HORIZONS,
        "account": {"initial_usdt": 1_000, "per_trade_fraction": TRADE_FRACTION, "max_slots": MAX_SLOTS},
        "periods": [{"period": name, "start_ms": start, "end_exclusive_ms": end} for name, start, end in periods],
        "sample": sample_counts,
        "matched_pair_audit": pair_audit,
        "eligible_matched_pair_audit": eligible_pair_audit,
        "scenarios": all_timing,
        "engine": "VectorBT Portfolio.from_order_func native OHLC callback; account is VectorBT cash-sharing callback; no funding",
        "holding_window_note": "Only continuous complete 5m windows are included. Seven-day exits may cross the NY month boundary; the frozen first-week target remains active.",
        "cap_note": "The same 5bp-feasible candidate set is shared by all horizons and both slippage levels. Target cap is recomputed from each actual slipped entry fill.",
    }
    (HERE / "swing_summary.json").write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    return output


if __name__ == "__main__":
    run()
