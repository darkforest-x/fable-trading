"""Replay fixed take-profit levels on the 17 valid OKX post-score entries.

Inputs are the archived score publication, the prior post-score candidate set,
the original touch-candle wick stop, and read-only local 1m OHLC cache files.
Each take-profit level and the no-target control gets its own chronological,
alphabetically tie-broken single-position schedule. Native OHLC barrier matching
uses monday_wick_r_engine.first_hit; VectorBT Portfolio.from_orders owns fees,
position PnL, and the execution ledger. This script performs no network access.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
EXP = Path(__file__).resolve().parent
SOURCE = ROOT / "experiments/active/exp-monday-reclaimed-wick-okx-20261115-v1"
CODE_REL = str((EXP / "okx_run_target_win.py").relative_to(ROOT))
CONFIG_REL = str((EXP / "okx_config.json").relative_to(ROOT))
ONE_MINUTE_MS = 60_000
TAKER = 0.0005
MAKER = 0.0002
SLIP = 0.001
TARGETS = (1.0, 1.5, 2.0, 3.0, 5.0, 10.0)

sys.dont_write_bytecode = True
os.environ["NUMBA_CACHE_DIR"] = str(EXP / "okx_numba_cache")
sys.path.insert(0, str(ROOT))
import vectorbt as vbt  # noqa: E402
from yoyo.evaluation.monday_wick_r_engine import first_hit  # noqa: E402


def require_committed() -> None:
    for rel in (CODE_REL, CONFIG_REL):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        changed = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT,
        )
        if tracked.returncode or changed.returncode:
            raise RuntimeError(f"Commit the scoped builder/config before execution: {rel}")


def _ceil_minute(ms: int) -> int:
    return ((int(ms) + ONE_MINUTE_MS - 1) // ONE_MINUTE_MS) * ONE_MINUTE_MS


def load_candidates() -> tuple[list[dict], dict]:
    outcomes = pd.read_csv(SOURCE / "post-score-outcomes.csv")
    six_hour = outcomes.loc[outcomes.horizon_h.eq(6)].copy()
    if six_hour.id.duplicated().any():
        raise ValueError("Expected one 6h post-score outcome row per signal ID")
    if not six_hour.full_clock.astype(bool).all():
        raise ValueError("The candidate source contains an incomplete 6h clock")

    selected = pd.read_csv(SOURCE / "selection.csv")
    selected = selected.loc[selected.status.eq("selected")].copy()
    selected["id"] = selected.id.astype(int)
    six_hour["id"] = six_hour.id.astype(int)
    by_id = selected.set_index("id", verify_integrity=True)

    candidates = []
    coverage = {"candidate_count": len(six_hour), "readable_paths": [], "missing_paths": []}
    for outcome in six_hour.sort_values(["entry_ms", "symbol", "id"]).itertuples(index=False):
        if outcome.id not in by_id.index:
            raise ValueError(f"Post-score candidate {outcome.id} is missing from selection.csv")
        src = by_id.loc[outcome.id]
        if str(src.symbol) != str(outcome.symbol) or int(src.side) != int(outcome.side):
            raise ValueError(f"Signal identity mismatch for {outcome.id}")
        entry_ms = int(outcome.entry_ms)
        if entry_ms != _ceil_minute(int(src.scored_ms)):
            raise ValueError(f"Post-score entry time does not match the recorded score for {outcome.id}")
        path = ROOT / str(src.path)
        if not path.is_file():
            coverage["missing_paths"].append(str(src.path))
            continue

        bars = pd.read_csv(path)
        required = {"open_time", "open", "high", "low", "close"}
        if not required.issubset(bars.columns):
            raise ValueError(f"Cache columns missing for {outcome.symbol}")
        bars["open_time"] = bars.open_time.astype(np.int64)
        bars["close_time"] = bars.open_time + ONE_MINUTE_MS
        coverage["readable_paths"].append({
            "path": str(src.path), "rows": int(len(bars)),
            "first_open_ms": int(bars.open_time.iloc[0]),
            "last_close_ms": int(bars.close_time.iloc[-1]),
        })

        hits = np.flatnonzero(bars.close_time.to_numpy(np.int64) == entry_ms)
        if len(hits) != 1:
            raise ValueError(f"No unique completed 1m entry bar for {outcome.symbol} {entry_ms}")
        entry_i = int(hits[0])
        entry_price = float(bars.close.iloc[entry_i])
        side = int(src.side)
        stop = float(src.stop)
        if side * (entry_price - stop) <= 0:
            raise ValueError(f"Delayed entry is already beyond the original stop: {outcome.symbol}")

        touch_ms = int(src.entry_ms)
        waiting = bars.loc[(bars.close_time > touch_ms) & (bars.close_time <= entry_ms)]
        crossed_while_waiting = bool(
            (waiting.low <= stop).any() if side == 1 else (waiting.high >= stop).any()
        )
        if crossed_while_waiting:
            raise ValueError(f"Previously valid post-score candidate now crosses its stop: {outcome.symbol}")

        end_i = entry_i + 360
        if end_i >= len(bars):
            raise ValueError(f"Insufficient 6h local cache for {outcome.symbol}")
        after = bars.open_time.iloc[entry_i + 1:end_i + 1].to_numpy(np.int64)
        expected = np.arange(entry_ms, entry_ms + 360 * ONE_MINUTE_MS, ONE_MINUTE_MS, dtype=np.int64)
        if not np.array_equal(after, expected):
            raise ValueError(f"Gap in the required 6h OHLC window for {outcome.symbol}")

        candidates.append({
            "id": int(outcome.id), "symbol": str(outcome.symbol), "side": side,
            "score": float(src.score), "scored_ms": int(src.scored_ms),
            "touch_entry_ms": touch_ms, "entry_ms": entry_ms,
            "entry_price": entry_price, "stop": stop, "tick": float(src.tick),
            "path": str(src.path), "entry_i": entry_i, "end_i": end_i,
            "bars": bars,
        })

    if coverage["missing_paths"]:
        return candidates, coverage
    if len(candidates) != 17:
        raise ValueError(f"Expected 17 valid post-score candidates; local cache supplied {len(candidates)}")
    return candidates, coverage


def _tick_target(entry: float, side: int, pct: float, tick: float) -> float:
    raw = entry * (1.0 + side * pct / 100.0)
    units = raw / tick
    if side > 0:
        count = math.ceil(units - 1e-10)
    else:
        count = math.floor(units + 1e-10)
    return float(count * tick)


def simulate_barriers(candidate: dict, target_pct: float | None) -> dict:
    bars = candidate["bars"]
    raw = bars[["open", "high", "low", "close"]].to_numpy(dtype=np.float64)
    padded = np.vstack((raw[0], raw))
    o = np.ascontiguousarray(padded[:, 0:1])
    h = np.ascontiguousarray(padded[:, 1:2])
    l = np.ascontiguousarray(padded[:, 2:3])
    c = np.ascontiguousarray(padded[:, 3:4])
    start = candidate["entry_i"] + 2
    end = candidate["end_i"] + 1
    side = candidate["side"]
    entry = candidate["entry_price"]
    stop = candidate["stop"]
    target = (
        _tick_target(entry, side, target_pct, candidate["tick"])
        if target_pct is not None else np.nan
    )
    po = np.empty((len(o), 1), dtype=np.float64)
    ty = np.empty((len(o), 1), dtype=np.int64)
    scratch = np.empty(1, dtype=np.int64)
    hit_i, hit_price, kind, ambiguous = first_hit(
        o, h, l, c, start, end, entry, stop, target, side, po, ty, scratch,
    )
    if kind == 1:
        reason, exit_i, exit_ref = "stop", int(hit_i) - 1, float(hit_price)
    elif kind == 4:
        reason, exit_i, exit_ref = "take_profit", int(hit_i) - 1, float(hit_price)
    else:
        reason = "time"
        exit_i = candidate["end_i"]
        exit_ref = float(bars.close.iloc[exit_i])

    exit_open_ms = int(bars.open_time.iloc[exit_i])
    exit_close_ms = exit_open_ms + ONE_MINUTE_MS
    gap_exit = False
    if reason == "stop":
        gap_exit = side * (float(bars.open.iloc[exit_i]) - stop) <= 0
    elif reason == "take_profit":
        gap_exit = side * (float(bars.open.iloc[exit_i]) - target) >= 0
    available_ms = exit_open_ms if gap_exit else exit_close_ms
    return {
        "id": candidate["id"], "symbol": candidate["symbol"],
        "side": side, "score": candidate["score"], "entry_ms": candidate["entry_ms"],
        "entry_price": entry, "stop": stop, "target_pct": target_pct,
        "target_price": target if target_pct is not None else np.nan,
        "exit_ms": exit_close_ms, "exit_available_ms": available_ms,
        "exit_bar_open_ms": exit_open_ms, "exit_price": exit_ref,
        "exit_reason": reason, "ambiguous_same_bar": bool(ambiguous),
        "gap_exit": gap_exit, "bars_held": exit_i - candidate["entry_i"],
        "selected": True,
    }


def schedule_one(target_pct: float | None, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    scenario = "no_tp" if target_pct is None else f"tp_{target_pct:g}pct"
    busy_until = -1
    decisions, trades = [], []
    ordered = sorted(candidates, key=lambda x: (x["entry_ms"], x["symbol"], x["id"]))
    for candidate in ordered:
        if candidate["entry_ms"] <= busy_until:
            decisions.append({
                "scenario": scenario, "id": candidate["id"], "symbol": candidate["symbol"],
                "entry_ms": candidate["entry_ms"], "decision": "skipped_busy",
                "busy_until_ms": busy_until,
            })
            continue
        trade = simulate_barriers(candidate, target_pct)
        trade["scenario"] = scenario
        trades.append(trade)
        decisions.append({
            "scenario": scenario, "id": candidate["id"], "symbol": candidate["symbol"],
            "entry_ms": candidate["entry_ms"], "decision": "entered",
            "busy_until_ms": busy_until,
        })
        # A new entry is admitted only at a strictly later timestamp than the
        # prior exit; same-time events remain resolved by alphabetical order.
        busy_until = int(trade["exit_available_ms"])
    return decisions, trades


def account_with_vectorbt(scenario: str, trades: list[dict]) -> tuple[list[dict], list[dict]]:
    if not trades:
        return trades, []
    side = np.asarray([x["side"] for x in trades], dtype=np.float64)
    entry_ref = np.asarray([x["entry_price"] for x in trades], dtype=np.float64)
    exit_ref = np.asarray([x["exit_price"] for x in trades], dtype=np.float64)
    entry_fill = entry_ref.copy()
    exit_type = np.asarray([MAKER if x["exit_reason"] == "take_profit" else TAKER for x in trades])
    exit_fill = exit_ref.copy()
    quantity = 1.0 / entry_ref  # 1 quote-currency unit of reference entry notional
    prices = np.vstack((entry_fill, exit_fill))
    sizes = np.vstack((side * quantity, -side * quantity))
    fees = np.vstack((np.full(len(trades), TAKER), exit_type))
    fixed_fees = np.vstack((np.full(len(trades), SLIP), np.where(exit_type == MAKER, 0.0, SLIP)))
    initial_cash = np.maximum(entry_fill, exit_fill) * quantity * 4.0
    pf = vbt.Portfolio.from_orders(
        prices, size=sizes, price=prices, size_type="amount", direction="both",
        fees=fees, fixed_fees=fixed_fees, init_cash=initial_cash, cash_sharing=False, allow_partial=False,
        raise_reject=True, max_orders=2 * len(trades),
    )
    trade_records = pf.trades.records.sort_values("col").reset_index(drop=True)
    if len(trade_records) != len(trades) or not trade_records.status.eq(1).all():
        raise AssertionError(f"VectorBT did not close every position in {scenario}")

    for i, (trade, record) in enumerate(zip(trades, trade_records.itertuples(index=False))):
        entry_fee = float(entry_fill[i] * quantity[i] * TAKER)
        exit_fee = float(exit_fill[i] * quantity[i] * exit_type[i])
        fee_pct = (entry_fee + exit_fee) * 100.0
        entry_slip_pct = float(fixed_fees[0, i]) * 100.0
        exit_slip_pct = float(fixed_fees[1, i]) * 100.0
        slip_pct = entry_slip_pct + exit_slip_pct
        gross_pct = side[i] * (exit_ref[i] - entry_ref[i]) / entry_ref[i] * 100.0
        net_pct = float(record.pnl) * 100.0
        if not np.isclose(net_pct, gross_pct - fee_pct - slip_pct, atol=1e-8, rtol=1e-8):
            raise AssertionError(f"Portfolio PnL disagrees with recorded fills for {trade['id']}")
        trade.update(
            entry_fill=float(entry_fill[i]), exit_fill=float(exit_fill[i]),
            entry_fee_rate=TAKER, exit_fee_rate=float(exit_type[i]),
            entry_fee_pct=entry_fee * 100.0, exit_fee_pct=exit_fee * 100.0,
            gross_pct=float(gross_pct), fee_pct=float(fee_pct), slippage_pct=float(slip_pct),
            net_pct=float(net_pct), win=bool(net_pct > 0),
        )

    order_records = pf.orders.records.copy().sort_values(["col", "idx"]).reset_index(drop=True)
    fills = []
    for row in order_records.itertuples(index=False):
        trade = trades[int(row.col)]
        entry_order = int(row.idx) == 0
        fills.append({
            "scenario": scenario, "id": trade["id"], "symbol": trade["symbol"],
            "order_role": "entry" if entry_order else "exit",
            "fee_type": "taker" if entry_order or trade["exit_reason"] != "take_profit" else "maker",
            "reference_price": trade["entry_price"] if entry_order else trade["exit_price"],
            "fill_price": float(row.price), "size": float(row.size),
            "fee_rate": TAKER if entry_order or trade["exit_reason"] != "take_profit" else MAKER,
            "fees": float(row.fees),
            "fixed_fees_slippage": SLIP if entry_order or trade["exit_reason"] != "take_profit" else 0.0,
            "vectorbt_col": int(row.col), "vectorbt_idx": int(row.idx),
        })
    return trades, fills


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def main() -> None:
    require_committed()
    if vbt.__version__ != "1.2.0":
        raise RuntimeError(f"Expected isolated VectorBT 1.2.0, found {vbt.__version__}")
    candidates, coverage = load_candidates()
    if coverage["missing_paths"]:
        raise FileNotFoundError(f"Read-only local OHLC missing: {coverage['missing_paths']}")

    decisions, all_trades, fills, summaries = [], [], [], []
    for target_pct in (None, *TARGETS):
        scenario = "no_tp" if target_pct is None else f"tp_{target_pct:g}pct"
        scenario_decisions, scenario_trades = schedule_one(target_pct, candidates)
        scenario_trades, scenario_fills = account_with_vectorbt(scenario, scenario_trades)
        decisions.extend(scenario_decisions)
        all_trades.extend(scenario_trades)
        fills.extend(scenario_fills)
        values = np.asarray([x["net_pct"] for x in scenario_trades], dtype=float)
        summaries.append({
            "scenario": scenario,
            "target_pct": target_pct,
            "candidate_n": len(candidates),
            "entered_n": len(scenario_trades),
            "skipped_busy_n": len(candidates) - len(scenario_trades),
            "take_profit_n": sum(x["exit_reason"] == "take_profit" for x in scenario_trades),
            "stop_n": sum(x["exit_reason"] == "stop" for x in scenario_trades),
            "six_hour_exit_n": sum(x["exit_reason"] == "time" for x in scenario_trades),
            "ambiguous_same_bar_n": sum(x["ambiguous_same_bar"] for x in scenario_trades),
            "win_n": int((values > 0).sum()),
            "win_rate_pct": float(100.0 * (values > 0).mean()) if len(values) else None,
            "mean_net_pct": float(values.mean()) if len(values) else None,
            "median_net_pct": float(np.median(values)) if len(values) else None,
            "mean_gross_pct": float(np.mean([x["gross_pct"] for x in scenario_trades])) if scenario_trades else None,
            "mean_fee_pct": float(np.mean([x["fee_pct"] for x in scenario_trades])) if scenario_trades else None,
            "mean_slippage_pct": float(np.mean([x["slippage_pct"] for x in scenario_trades])) if scenario_trades else None,
        })

    EXP.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(decisions).to_csv(EXP / "okx_candidate_decisions.csv", index=False)
    pd.DataFrame(all_trades).to_csv(EXP / "okx_trades.csv", index=False)
    pd.DataFrame(fills).to_csv(EXP / "okx_fills.csv", index=False)
    config = json.loads((EXP / "okx_config.json").read_text())
    result = {
        "experiment_id": config["experiment_id"],
        "source_experiment_id": config["source_experiment_id"],
        "vectorbt_version": vbt.__version__,
        "first_hit": "yoyo.evaluation.monday_wick_r_engine.first_hit",
        "portfolio": "Portfolio.from_orders; 1 quote-currency unit reference notional per accepted trade; no compounding",
        "local_data_coverage": coverage,
        "scenarios": summaries,
        "fees_and_slippage": config["costs"],
        "limitations": [
            "17 post-score events are a small exploratory sample, not a validated target rule.",
            "Same-minute stop/TP double touches are resolved to the original stop first.",
            "The single-slot sequence is replayed separately for each target; no funded account or compounding path is modeled.",
            "Funding, contract lot conversion, liquidation, and spread are excluded.",
        ],
    }
    (EXP / "okx_summary.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2) + "\n")
    print(pd.DataFrame(summaries).to_string(index=False))
    print(f"saved={EXP.relative_to(ROOT)} candidates={len(candidates)} vectorbt={vbt.__version__}")


if __name__ == "__main__":
    main()
