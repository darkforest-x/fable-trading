"""Run the H4 rail-target ledger through the frozen V1 account rules."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
from functools import lru_cache
import importlib.util
from pathlib import Path
import sys
from typing import Callable

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
HOUR_MS = 3_600_000
REPLAY_DIR = ROOT / "experiments/active/exp-month-firstweek-all-replay-20261111-v1"
V1_DIR = ROOT / "experiments/active/exp-month-firstweek-h4-strategy-20261112-v1"
V1_ACCOUNT_DIR = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/h4_strategy/account_168h"
TARGET_DIR = ROOT / "data/research/alt-isolated-12h-wicks-20261111/expanded12/all_replay_v1/h4_rail_target"
FILLS_PATH = TARGET_DIR / "fills.csv"
OUTPUT_DIR = TARGET_DIR / "account_168h"
COMPARISON_PATH = HERE / "account_comparison.csv"

if str(REPLAY_DIR) not in sys.path:
    sys.path.insert(0, str(REPLAY_DIR))

_spec = importlib.util.spec_from_file_location("h4_strategy_v1_account", V1_DIR / "account.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load V1 account module from {V1_DIR}")
ACCOUNT = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ACCOUNT
_spec.loader.exec_module(ACCOUNT)


def _holding_windows(frame: pd.DataFrame, cutoff_ms: int) -> dict[str, list[tuple[int, int]]]:
    """Merge entry-to-exit ranges, including the H1 bar before and after each."""
    by_symbol: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in frame.itertuples(index=False):
        entry = int(row.entry_ms)
        if entry >= cutoff_ms:
            continue
        end = min(int(row.exit_ms), cutoff_ms)
        by_symbol[str(row.symbol)].append((entry - HOUR_MS, end + HOUR_MS))

    merged: dict[str, list[tuple[int, int]]] = {}
    for symbol, windows in by_symbol.items():
        windows.sort()
        combined: list[list[int]] = []
        for start, end in windows:
            if combined and start <= combined[-1][1] + HOUR_MS:
                combined[-1][1] = max(combined[-1][1], end)
            else:
                combined.append([start, end])
        merged[symbol] = [(start, end) for start, end in combined]
    return merged


def cached_raw_loader(
    source_loader: Callable[[str], np.ndarray], frame: pd.DataFrame, cutoff_ms: int
) -> Callable[[str], np.ndarray]:
    """Read each contract once and cache its complete relevant H1 windows."""
    windows = _holding_windows(frame, cutoff_ms)

    @lru_cache(maxsize=None)
    def load(symbol: str) -> np.ndarray:
        raw = np.asarray(source_loader(str(symbol)), dtype=np.float64)
        if raw.ndim != 2 or raw.shape[1] < 5:
            raise ValueError(f"{symbol}: expected H1 [ms,open,high,low,close,...]")
        clocks = raw[:, 0]
        if not np.isfinite(clocks).all() or not np.equal(clocks, np.rint(clocks)).all():
            raise ValueError(f"{symbol}: H1 timestamps must be finite integer milliseconds")
        opens = clocks.astype(np.int64)
        if (np.diff(opens) <= 0).any():
            raise ValueError(f"{symbol}: H1 timestamps must be strictly increasing")

        pieces = []
        for start, end in windows.get(str(symbol), []):
            left = int(np.searchsorted(opens, start, side="left"))
            right = int(np.searchsorted(opens, end, side="right"))
            if right > left:
                pieces.append(raw[left:right, :5])
        if not pieces:
            return np.empty((0, 5), dtype=np.float64)
        # Slicing source rows preserves complete OHLC bars and real H1 gaps.
        return np.concatenate(pieces, axis=0).copy()

    return load


def _reference_check() -> tuple[pd.DataFrame, pd.DataFrame]:
    summary = pd.read_csv(V1_ACCOUNT_DIR / "account_summary.csv", float_precision="round_trip")
    orders = pd.read_csv(V1_ACCOUNT_DIR / "account_orders.csv", float_precision="round_trip")
    accepted_status = {"closed", "period_end_valuation", "open_marked_at_cutoff"}
    for row in summary.itertuples(index=False):
        selected = orders[
            orders["sample"].astype(str).eq(str(row.sample))
            & orders["account_variant"].astype(str).eq(str(row.account_variant))
        ]
        funded = selected["account_status"].astype(str).isin(accepted_status).sum()
        if int(funded) != int(row.funded_trade_count) or int(row.accepted_entries) != int(row.funded_trade_count):
            raise AssertionError(f"V1 funded count mismatch: {row.sample}/{row.account_variant}")
        # V1's saved 168h paths have no open positions at cutoff.
        if int(row.open_marked_at_cutoff) == 0 and not np.isclose(
            float(row.initial_equity_u) + float(row.realized_net_pnl_u),
            float(row.final_equity_u), rtol=1e-9, atol=1e-6,
        ):
            raise AssertionError(f"V1 1000U + recorded net PnL mismatch: {row.sample}/{row.account_variant}")
    return summary, orders


def _comparison_rows(
    v1_summary: pd.DataFrame, v1_orders: pd.DataFrame,
    target_summary: pd.DataFrame, target_orders: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    for ledger, summary, orders in (
        ("v1", v1_summary, v1_orders),
        ("h4_rail_target", target_summary, target_orders),
    ):
        for metric in summary.itertuples(index=False):
            selected = orders[
                orders["sample"].astype(str).eq(str(metric.sample))
                & orders["account_variant"].astype(str).eq(str(metric.account_variant))
            ]
            reasons = getattr(metric, "rejection_reasons", {})
            if not isinstance(reasons, dict):
                try:
                    reasons = ast.literal_eval(str(reasons))
                except (ValueError, SyntaxError):
                    reasons = {}
            budget_skips = 0
            if str(metric.account_variant) == "floor30":
                budget_skips = int(reasons.get("fixed_stop_stress_breaches_floor", 0))
                budget_skips += int(reasons.get("at_or_below_70pct_floor", 0))
            funded = selected[selected["account_status"].astype(str).isin(
                {"closed", "period_end_valuation", "open_marked_at_cutoff"}
            )]
            last_entry = ""
            if not funded.empty:
                last_ms = int(pd.to_numeric(funded["entry_ms"], errors="raise").max())
                last_entry = pd.Timestamp(last_ms, unit="ms", tz="UTC").isoformat()
            rows.append({
                "hold_hours": 168,
                "ledger": ledger,
                "sample": str(metric.sample),
                "account": str(metric.account_variant),
                "funded": int(metric.funded_trade_count),
                "settled": int(metric.normal_closed_trades),
                "terminal_marks": int(metric.period_end_valuation_trades),
                "fees_u": float(metric.entry_fees_u) + float(metric.exit_fees_u),
                "final_equity_u": float(metric.final_equity_u),
                "net_return_pct": float(metric.total_return_pct),
                "max_h1_marked_dd_pct": float(metric.max_drawdown_hourly_close_pct),
                "max_intrahour_dd_bound_pct": float(metric.max_drawdown_h1_ohlc_envelope_pct),
                "skipped_by_drawdown_budget": budget_skips,
                "last_funded_entry_utc": last_entry,
            })
    return pd.DataFrame(rows)


def run() -> None:
    v1_summary, v1_orders = _reference_check()
    replay = ACCOUNT._replay_module()
    if not FILLS_PATH.is_file():
        raise FileNotFoundError(f"Target fills are missing: {FILLS_PATH}")
    frame = ACCOUNT._validate_fills(pd.read_csv(FILLS_PATH, float_precision="round_trip"))
    cutoff_ms = int(replay.CUTOFF) // HOUR_MS * HOUR_MS
    result = ACCOUNT.run(
        fills_path=FILLS_PATH,
        cutoff_ms=cutoff_ms,
        raw_loader=cached_raw_loader(replay.read_raw, frame, cutoff_ms),
        output_dir=OUTPUT_DIR,
    )
    comparison = _comparison_rows(v1_summary, v1_orders, result["summary"], result["orders"])
    COMPARISON_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = COMPARISON_PATH.with_name(COMPARISON_PATH.name + ".tmp")
    comparison.to_csv(tmp, index=False)
    tmp.replace(COMPARISON_PATH)
    print(f"account outputs: {OUTPUT_DIR}")
    print(f"comparison: {COMPARISON_PATH}")


def self_check() -> None:
    """Synthetic-only check that the cache preserves gaps and avoids rereads."""
    bars = np.column_stack((
        np.asarray([0, 1, 3, 4], dtype=np.int64) * HOUR_MS,
        np.tile(np.asarray([[100.0, 102.0, 98.0, 101.0]]), (4, 1)),
    ))
    calls = 0

    def source_loader(_: str) -> np.ndarray:
        nonlocal calls
        calls += 1
        return bars

    frame = pd.DataFrame([{"symbol": "A", "entry_ms": HOUR_MS, "exit_ms": 3 * HOUR_MS}])
    loader = cached_raw_loader(source_loader, frame, 5 * HOUR_MS)
    first = loader("A")
    second = loader("A")
    assert calls == 1 and np.array_equal(first, second)
    assert np.array_equal(first[:, 0], bars[:, 0])
    print("synthetic cache check passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="Run the synthetic cache check only")
    args = parser.parse_args()
    self_check() if args.self_check else run()
