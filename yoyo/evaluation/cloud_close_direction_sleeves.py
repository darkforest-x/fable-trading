"""Fund the frozen OKX 12H target-expiry strategy in separate long/short wallets.

The two registered allocations assign the original nine equal slots as 4+5 or
5+4. Each side is run through the existing cash-sharing VectorBT adapter with
1000 normalized USDT and its own fixed slot count, then every monetary output is
scaled linearly by slots/9. This makes each side's actual opening wallet equal
to its share of the original 1000 USDT, while keeping the original initial
notional at 1000/9 per slot. The two scaled hourly portfolios are added at each
common timestamp; their multiples are never multiplied.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_target_expiry as expiry
from yoyo.evaluation import cloud_close_thirds as thirds


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-direction-sleeves-20261109-v1"
TAKER = wallet.TAKER
SHARE = 2.0 / 3.0
SLOT_TOTAL = 9
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00Z"),
    "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
    "2026": ("2026-01-01T00:00Z", None),
}
ARMS = {
    "long4_short5": {"long": 4, "short": 5},
    "long5_short4": {"long": 5, "short": 4},
}
MONEY_COLUMNS = ("cash_pnl", "notional", "quantity")
EQUITY_COLUMNS = ("nav", "open_nav", "open_exit_nav", "lower_nav", "upper_nav")


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(path)


def _path_net_return(leg: pd.DataFrame) -> np.ndarray:
    """One full-notional leg return, including fee on the actual exit notional."""
    entry = leg.entry_price.to_numpy(dtype=float)
    exit_ = leg.exit_price.to_numpy(dtype=float)
    return (leg.side.to_numpy(dtype=float) * (exit_ - entry) / entry
            - TAKER - leg.exit_fee_rate.to_numpy(dtype=float) * (exit_ / entry))


def _frozen_baseline(config: dict, source_paths: pd.DataFrame) -> tuple[dict, dict]:
    """Reconcile the published baseline ledger to frozen native legs and OHLC marks."""
    base = ROOT / config["inputs"]["frozen_target_expiry_dir"]
    funded_path = base / "funded_OKX.csv.gz"
    hourly_path = base / "hourly_OKX.csv.gz"
    summary_path = base / "summary.json"
    if not all(path.is_file() for path in (funded_path, hourly_path, summary_path)):
        raise FileNotFoundError("Frozen OKX target-expiry funded/hourly/summary files are required")
    funded = pd.read_csv(funded_path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                         float_precision="round_trip")
    hourly = pd.read_csv(hourly_path, index_col=0, parse_dates=True,
                         float_precision="round_trip")
    hourly.index = pd.to_datetime(hourly.index, utc=True)
    frozen_summary = json.loads(summary_path.read_text(encoding="utf-8"))["OKX"]["full"]
    expected_n = int(config["inputs"]["expected_frozen_actual_positions"])
    terminal_tolerance = float(config["inputs"]["frozen_final_tolerance_usdt"])
    if len(funded) != expected_n or funded.event_id.duplicated().any():
        raise ValueError(f"Frozen target-expiry actual roster changed: {len(funded)}")
    if not funded.side.isin((-1, 1)).all():
        raise ValueError("Frozen funded ledger has invalid side values")
    if not np.isclose(float(frozen_summary["final_usdt"]),
                      float(config["inputs"]["expected_frozen_final_usdt"]), rtol=0,
                      atol=terminal_tolerance):
        raise ValueError("Frozen target-expiry baseline terminal differs from its registered value")

    actual_runner = source_paths[(~source_paths.is_control) & source_paths.tp_r.eq(0)].copy()
    selected, _ = wallet.admit(actual_runner, SLOT_TOTAL)
    if set(selected.event_id.astype(str)) != set(funded.event_id.astype(str)):
        raise ValueError("Frozen baseline event roster differs from the source V1.7 9-slot admission")

    runner = actual_runner.set_index("event_id", drop=False)
    target = source_paths[(~source_paths.is_control) & source_paths.tp_r.eq(2)].set_index("event_id", drop=False)
    rows = funded.set_index("event_id", drop=False)
    event_ids = rows.index.astype(str)
    if not set(event_ids).issubset(runner.index.astype(str)) or not set(event_ids).issubset(target.index.astype(str)):
        raise ValueError("Frozen account event lacks one of its native child legs")
    r = runner.loc[event_ids].reset_index(drop=True)
    t = target.loc[event_ids].reset_index(drop=True)
    f = rows.loc[event_ids].reset_index(drop=True)
    for column in ("side", "entry_ms", "entry_price"):
        if not np.array_equal(pd.to_numeric(r[column]).to_numpy(), pd.to_numeric(t[column]).to_numpy()):
            raise ValueError(f"Native target and runner legs disagree on {column}")
    if not np.array_equal(r.symbol.astype(str).to_numpy(), t.symbol.astype(str).to_numpy()):
        raise ValueError("Native target and runner legs disagree on symbol")
    for column in ("symbol", "side", "entry_ms"):
        left = r[column].astype(str).to_numpy() if column == "symbol" else pd.to_numeric(r[column]).to_numpy()
        right = f[column].astype(str).to_numpy() if column == "symbol" else pd.to_numeric(f[column]).to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Frozen funded ledger differs from native source path identity: {column}")
    expected_exit_ms = np.maximum(r.exit_bar_ms.to_numpy(dtype="int64"),
                                  t.exit_bar_ms.to_numpy(dtype="int64"))
    if not np.array_equal(expected_exit_ms, pd.to_numeric(f.exit_bar_ms).to_numpy(dtype="int64")):
        raise ValueError("Frozen funded end time differs from the native child-leg end times")
    raw_return = _path_net_return(r) * (1.0 - SHARE) + _path_net_return(t) * SHARE
    return_error = np.abs(raw_return - pd.to_numeric(f.net_return).to_numpy(dtype=float))
    if not np.allclose(raw_return, f.net_return.to_numpy(dtype=float), rtol=1e-10, atol=1e-11):
        raise ValueError("Frozen target-expiry funded returns do not reconcile to native 2/3 + runner legs")
    if not np.isclose(1000.0 + float(funded.cash_pnl.sum()), float(hourly.nav.iloc[-1]),
                      rtol=1e-9, atol=0.01):
        raise ValueError("Frozen target-expiry cash and terminal hourly mark do not reconcile")
    metrics = {period: wallet.summarize(funded, hourly, *limits)
               for period, limits in PERIODS.items()}
    audit = {
        "frozen_funded_positions": len(funded),
        "frozen_side_counts": {"long": int(funded.side.eq(1).sum()),
                               "short": int(funded.side.eq(-1).sum())},
        "frozen_terminal_usdt_from_cash": 1000.0 + float(funded.cash_pnl.sum()),
        "frozen_terminal_usdt_from_hourly_nav": float(hourly.nav.iloc[-1]),
        "frozen_cash_nav_delta_usdt": 1000.0 + float(funded.cash_pnl.sum()) - float(hourly.nav.iloc[-1]),
        "frozen_source_summary_final_usdt": float(frozen_summary["final_usdt"]),
        "native_path_return_max_abs_error": float(return_error.max(initial=0.0)),
        "native_path_return_rows_checked": len(return_error),
        "native_runner_source": "experiments/active/exp-cloud-close-reward-20261109-v1/paths.csv.gz (tp_r=0)",
        "native_target_source": "same source paths rotated with the frozen 12h expiry rule",
        "funded_source": str(funded_path.relative_to(ROOT)),
        "hourly_source": str(hourly_path.relative_to(ROOT)),
        "prior_direction_study": {
            "source": "experiments/active/exp-cloud-close-direction-history-20261109-v1/summary.json",
            "equivalent_independent_side_wallets": False,
            "reason": "It gates positions by past directional expectancy and funds one 1000U account; it does not isolate long and short capital.",
        },
    }
    return metrics, audit


def _scale_account(positions: pd.DataFrame, equity: pd.DataFrame, weight: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not (0 < weight <= 1):
        raise ValueError("Sleeve capital weight must be in (0, 1]")
    scaled_positions = positions.copy()
    for column in MONEY_COLUMNS:
        if column in scaled_positions:
            scaled_positions[column] = pd.to_numeric(scaled_positions[column]) * weight
    if {"notional", "cash_pnl"}.issubset(scaled_positions):
        scaled_positions["net_return"] = scaled_positions.cash_pnl / scaled_positions.notional
    scaled_equity = equity.copy()
    for column in EQUITY_COLUMNS:
        scaled_equity[column] = scaled_equity[column] * weight
    return scaled_positions, scaled_equity


def _sleeve_metrics(positions: pd.DataFrame, equity: pd.DataFrame,
                    weight: float) -> dict:
    """Period metrics are scale-invariant except terminal USDT."""
    values = {period: wallet.summarize(positions, equity, *limits)
              for period, limits in PERIODS.items()}
    for metrics in values.values():
        metrics["final_usdt"] *= weight
    return values


def _run_sleeve(events: pd.DataFrame, side: int, slots: int, weight: float,
                slip: float, *, start: int, end: int) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    selected = events[events.side.eq(side)].copy()
    runner = selected[selected.tp_r.eq(0)].copy()
    target = selected[selected.tp_r.eq(2)].copy()
    if runner.empty or target.empty or set(runner.event_id) != set(target.event_id):
        raise ValueError(f"Missing native target/runner pair for side {side}")
    data = split.pair_data(runner, target, slots, start=start, end=end)
    runner_slot = data["selected"].loc[data["selected"].tp_r.eq(0), ["event_id", "slot"]].copy()
    runner_slot["slot"] = (runner_slot.slot.astype(int) // 2)
    slot_by_event = runner_slot.set_index("event_id").slot
    _, positions, equity = split.simulate(data, SHARE, slip=slip)
    positions = positions.reset_index()
    positions["slot"] = positions.event_id.map(slot_by_event).astype("int64")
    positions["direction_sleeve"] = "long" if side > 0 else "short"
    positions["capital_weight"] = weight
    positions["starting_capital_usdt"] = 1000.0 * weight
    positions["sleeve_slots"] = slots
    if positions.slot.isna().any() or (positions.slot >= slots).any():
        raise ValueError("VectorBT funded position has no valid sleeve slot")
    normalized_cash = 1000.0 + float(positions.cash_pnl.sum())
    if not np.isclose(normalized_cash, float(equity.nav.iloc[-1]), rtol=1e-9, atol=0.01):
        raise ValueError("Normalized VBT sleeve does not reconcile before linear capital scaling")
    scaled, scaled_equity = _scale_account(positions, equity, weight)
    actual_start = 1000.0 * weight
    if not np.isclose(actual_start + float(scaled.cash_pnl.sum()), float(scaled_equity.nav.iloc[-1]),
                      rtol=1e-9, atol=0.01):
        raise ValueError("Scaled sleeve cash and NAV do not reconcile")
    min_scaled_notional = float(scaled.notional.min())
    # The shared callback suppresses only sub-1e-8 normalized-USD orders. With
    # two child legs, a combined scaled notional above 3.1e-8 keeps either child
    # above that numeric floor in the actual sleeve. There is no exchange lot
    # or commission minimum in this research adapter.
    if min_scaled_notional <= 3.1e-8:
        raise ValueError("A funded scaled position approaches the wallet's numerical zero-order cutoff")
    metrics = _sleeve_metrics(positions, equity, weight)
    audit = {
        "side": "long" if side > 0 else "short",
        "slots": slots,
        "capital_weight": weight,
        "initial_capital_usdt": actual_start,
        "target_initial_slot_gross_usdt": 1000.0 / SLOT_TOTAL,
        "positions_funded": len(scaled),
        "minimum_scaled_position_notional_usdt": min_scaled_notional,
        "numerical_zero_order_floor_usdt": 1e-8,
        "unique_slots_used": int(scaled.slot.nunique()),
        "unquoted_position_hours": int(data["gap_marks"]),
        "admission_rejections": data["skips"],
        "scaled_cash_nav_delta_usdt": actual_start + float(scaled.cash_pnl.sum()) - float(scaled_equity.nav.iloc[-1]),
        "normalized_vbt_initial_usdt": 1000.0,
    }
    del data
    return metrics, scaled, scaled_equity, audit


def _combine(arms: dict[str, tuple[dict, pd.DataFrame, pd.DataFrame, dict]],
             start: int, end: int) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    present = [value for value in arms.values() if value is not None]
    if len(present) != 2:
        raise ValueError("Both direction sleeves must be represented in every arm")
    positions = pd.concat([item[1] for item in present], ignore_index=True, sort=False)
    if positions.event_id.duplicated().any():
        raise ValueError("Long and short accounts funded the same event")
    long_eq, short_eq = present[0][2], present[1][2]
    if not long_eq.index.equals(short_eq.index):
        raise ValueError("Direction sleeves do not share an identical hourly mark grid")
    equity = long_eq.add(short_eq)
    initial = float(positions.starting_capital_usdt.drop_duplicates().sum())
    if not np.isclose(initial, 1000.0, rtol=0, atol=1e-10):
        raise ValueError(f"Sleeve initial capital does not sum to 1000U: {initial}")
    if not np.isclose(initial + float(positions.cash_pnl.sum()), float(equity.nav.iloc[-1]),
                      rtol=1e-9, atol=0.01):
        raise ValueError("Combined direction account cash does not reconcile to synchronous NAV")
    metrics = {period: wallet.summarize(positions, equity, *limits)
               for period, limits in PERIODS.items()}
    audit = {
        "positions_funded": len(positions),
        "long_positions": int(positions.side.eq(1).sum()),
        "short_positions": int(positions.side.eq(-1).sum()),
        "initial_capital_usdt": initial,
        "final_cash_usdt": initial + float(positions.cash_pnl.sum()),
        "final_synchronous_nav_usdt": float(equity.nav.iloc[-1]),
        "cash_nav_delta_usdt": initial + float(positions.cash_pnl.sum()) - float(equity.nav.iloc[-1]),
        "hourly_marks": len(equity),
    }
    return metrics, positions, equity, audit


def _account(events: pd.DataFrame, arm: dict, slip: float = 0., *,
             start: int = wallet.START, end: int = wallet.END) -> dict:
    outputs = {}
    for direction, side in (("long", 1), ("short", -1)):
        slots = int(arm[direction])
        weight = slots / SLOT_TOTAL
        result = _run_sleeve(events, side, slots, weight, slip, start=start, end=end)
        outputs[direction] = result
    metrics, positions, equity, audit = _combine(outputs, start, end)
    return {"metrics": metrics, "positions": positions, "equity": equity,
            "sleeves": outputs, "audit": audit}


def _passes(metrics: dict, gates: dict) -> bool:
    return (
        int(metrics.get("trades") or 0) >= int(gates["minimum_trades"])
        and metrics.get("win_pct") is not None
        and metrics["win_pct"] >= float(gates["minimum_win_pct"])
        and float(metrics.get("payoff") or 0) >= float(gates["minimum_equal_notional_payoff"])
        and float(metrics.get("payoff_usdt") or 0) >= float(gates["minimum_funded_usdt_payoff"])
        and float(metrics.get("dd_bound_pct") or np.inf) <= float(gates["maximum_conservative_dd_pct"])
    )


def _metric_rows(arm_name: str, cohort: str, sleeve: str, metrics: dict,
                 initial_usdt: float) -> list[dict]:
    return [dict(arm=arm_name, cohort=cohort, sleeve=sleeve, period=period,
                 starting_capital_usdt=initial_usdt, **values)
            for period, values in metrics.items()]


def _eligible_source(config: dict) -> tuple[pd.DataFrame, dict]:
    expected_source = str((thirds.wallet.EXP / "paths.csv.gz").relative_to(ROOT))
    if config["inputs"]["source_path_file"] != expected_source:
        raise ValueError("Config source path differs from the frozen V1.7 path file")
    paths = thirds.okx_paths()
    paths = paths[paths.tp_r.isin([0, 2])].copy()
    if paths.duplicated(["event_id", "tp_r"]).any() or not paths.groupby("event_id").size().eq(2).all():
        raise ValueError("Corrected OKX source must contain paired runner and 2R paths")
    if not paths.entry_order.notna().all() or not np.isfinite(paths.entry_order.to_numpy(dtype=float)).all():
        raise ValueError("V1.7 prior-ATR entry priority is missing")
    rotated, decisions = expiry.time_in_force(paths, defer=False, hours=12)
    if not rotated.groupby("event_id").size().eq(2).all():
        raise ValueError("Target expiry transformation did not preserve a two-leg full position")
    if not set(rotated.tp_r.unique()).issubset({0, 2}):
        raise ValueError("Unexpected target-expiry child leg")
    actual_parent_ids = set(rotated.loc[~rotated.is_control, "parent_id"].astype(str))
    control_parent_ids = set(rotated.loc[rotated.is_control, "parent_id"].astype(str))
    matched_control_count = len(actual_parent_ids & control_parent_ids)
    audit = {
        "candidate_actual_events": int((~paths.is_control).sum() // 2),
        "candidate_controls": int(paths.is_control.sum() // 2),
        "parent_matched_control_events": matched_control_count,
        "controls_without_eligible_actual_parent": len(control_parent_ids - actual_parent_ids),
        "source_paths": len(paths),
        "target_expiry_decisions": int(len(decisions)),
        "late_2r_limits_replaced_by_runner": int(decisions.choice.eq("full_runner").sum()),
        "12h_expiry_hours": 12,
        "eligible_path_builder": "cloud_close_thirds.okx_paths() (corrected V1.7 90d roster and low-prior-ATR priority)",
        "target_expiry_transform": "cloud_close_target_expiry.time_in_force(..., defer=False, hours=12)",
    }
    return rotated, audit


def _write_account(output_dir: Path, arm_name: str, cohort: str, account: dict) -> dict:
    audit = {"combined": account["audit"], "sleeves": {}}
    account["positions"].to_csv(output_dir / f"funded_{arm_name}_{cohort}_combined.csv.gz",
                                 index=False, compression="gzip")
    account["equity"].to_csv(output_dir / f"hourly_{arm_name}_{cohort}_combined.csv.gz",
                              index_label="hour_end_utc")
    for direction, (metrics, positions, equity, sleeve_audit) in account["sleeves"].items():
        positions.to_csv(output_dir / f"funded_{arm_name}_{cohort}_{direction}.csv.gz",
                         index=False, compression="gzip")
        equity.to_csv(output_dir / f"hourly_{arm_name}_{cohort}_{direction}.csv.gz",
                      index_label="hour_end_utc")
        audit["sleeves"][direction] = sleeve_audit
    return audit


def generate(config_path: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["execution"]["target_expiry_hours"] != 12 or config["account"]["total_slots"] != 9:
        raise ValueError("This study is frozen to the source 12h target expiry and nine total slots")
    if not np.isclose(float(config["account"]["starting_usdt"]), 1000.0):
        raise ValueError("Total initial capital must remain 1000 USDT")
    output_dir = ROOT / config["outputs"]["directory"]
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "summary.json").exists():
        raise FileExistsError("Refusing to overwrite existing direction-sleeve results")

    paths, source_audit = _eligible_source(config)
    baseline_metrics, baseline_audit = _frozen_baseline(config, paths)
    rows = []
    for period, values in baseline_metrics.items():
        rows.append(dict(arm="frozen_shared_9slot", cohort="actual", sleeve="combined",
                         period=period, starting_capital_usdt=1000.0, **values))

    actual = paths[~paths.is_control].copy()
    controls = paths[paths.is_control & paths.parent_id.isin(actual.parent_id)].copy()
    paired_actual = actual[actual.parent_id.isin(controls.parent_id)].copy()
    if not controls.parent_id.isin(actual.parent_id).all():
        raise ValueError("Control pool is not parent-matched to actual V1.7 events")
    if not np.array_equal(
            controls.groupby("parent_id").side.first().sort_index().to_numpy(),
            actual.set_index("parent_id").loc[controls.parent_id.unique()].groupby(level=0).side.first().sort_index().to_numpy()):
        raise ValueError("Matched controls must retain their parent's original trade side")

    arm_audit = {}
    print("Funding independent OKX long/short sleeves with the existing VectorBT cash-sharing adapter",
          flush=True)
    for arm_name, arm in ARMS.items():
        result = _account(actual, arm)
        for direction, (metrics, _, _, audit) in result["sleeves"].items():
            rows.extend(_metric_rows(arm_name, "actual", direction, metrics,
                                     1000.0 * arm[direction] / SLOT_TOTAL))
        rows.extend(_metric_rows(arm_name, "actual", "combined", result["metrics"], 1000.0))
        dev_pass = _passes(result["metrics"]["dev"], config["selection"]["gates"])
        arm_audit[arm_name] = {
            "slots": arm,
            "initial_capital_usdt": {side: 1000.0 * slots / SLOT_TOTAL for side, slots in arm.items()},
            "combined": result["audit"],
            "sleeves": {side: sleeve[3] for side, sleeve in result["sleeves"].items()},
            "development_gate_pass": dev_pass,
            "checks_run": [],
        }
        _write_account(output_dir, arm_name, "actual", result)
        del result
        gc.collect()
        if dev_pass:
            for cohort, sample, slip in (
                ("paired_actual", paired_actual, 0.0),
                ("paired_control", controls, 0.0),
                ("stress_5bp_actual", actual, 0.0005),
            ):
                checked = _account(sample, arm, slip=slip)
                for direction, (metrics, _, _, _) in checked["sleeves"].items():
                    rows.extend(_metric_rows(arm_name, cohort, direction, metrics,
                                             1000.0 * arm[direction] / SLOT_TOTAL))
                rows.extend(_metric_rows(arm_name, cohort, "combined", checked["metrics"], 1000.0))
                arm_audit[arm_name]["checks_run"].append(cohort)
                _write_account(output_dir, arm_name, cohort, checked)
                del checked
                gc.collect()

    metrics_frame = pd.DataFrame(rows)
    metrics_frame.to_csv(output_dir / "account_metrics.csv", index=False)
    summary = {
        "experiment_id": config["id"],
        "generator": str(Path(__file__).relative_to(ROOT)),
        "vectorbt_version": __import__("vectorbt").__version__,
        "source_audit": source_audit,
        "frozen_baseline": {"audit": baseline_audit, "metrics": baseline_metrics},
        "direction_sleeves": arm_audit,
        "method": {
            "independence": "Long and short candidates are admitted in different cash-sharing VectorBT accounts; each account compounds its own marked NAV and has no transfers.",
            "slots": "Compare the pre-registered 4-long/5-short and 5-long/4-short assignments. Each side starts with slots/9 of 1000U, so every initial slot has the original 1000/9 collateral budget; no forced 500/500 split.",
            "linear_scale": "Each account is run by the existing VectorBT wallet with 1000 normalized U and its side slot count. The position, cash-flow and mark outputs are scaled by slots/9 to produce the actual sleeve capital. Existing fills use continuous quantities, proportional maker/taker fees, no fixed fee, no exchange minimum amount, and no contract-lot rounding. The adapter's only numerical zero-order cutoff is 1e-8 normalized USDT; the builder fails closed if any funded scaled full position is too small to keep either child leg above it. Entry/exit order decisions and collateral ratios are otherwise invariant under this scale.",
            "combined": "Synchronous hourly NAV/open/upper/lower marks and funded cash flows are added across the two sleeves; final totals are never obtained by multiplying multiples.",
            "exits": "Reuse the original V1.7 native 2R target path only if filled before the next 12H expiry; otherwise preserve the original runner stop/EMA exit.",
            "fees": {"market_per_fill": TAKER, "target_limit_exit": "native path maker rate 0.02%", "extra_stress_market_slippage_per_fill": 0.0005},
            "funding": "Not modeled.",
        },
        "selection": {
            "dev_period": "dev",
            "gates": config["selection"]["gates"],
            "control_and_5bp_runs_only_for_each_arm_that_passes_dev": True,
        },
        "outputs": {"directory": str(output_dir.relative_to(ROOT)),
                    "account_metrics": str((output_dir / "account_metrics.csv").relative_to(ROOT)),
                    "summary": str((output_dir / "summary.json").relative_to(ROOT)),
                    "audit": str((output_dir / "audit.json").relative_to(ROOT))},
    }
    _write_json(output_dir / "summary.json", summary)
    _write_json(output_dir / "audit.json", {
        "experiment_id": config["id"], "source": source_audit,
        "frozen_baseline": baseline_audit, "arms": arm_audit,
        "linearity_assumptions": summary["method"]["linear_scale"],
    })
    print(metrics_frame[metrics_frame.period.eq("all")].to_string(index=False), flush=True)
    return summary


def _self_check() -> None:
    assert ARMS["long4_short5"] == {"long": 4, "short": 5}
    assert ARMS["long5_short4"] == {"long": 5, "short": 4}
    for arm in ARMS.values():
        assert sum(arm.values()) == SLOT_TOTAL
        assert np.isclose(sum(1000.0 * slots / SLOT_TOTAL for slots in arm.values()), 1000.0)
        assert all(np.isclose((1000.0 * slots / SLOT_TOTAL) / slots, 1000.0 / SLOT_TOTAL)
                   for slots in arm.values())
    gross = 100.0 * (1.2 - 1.0)
    fee = 100.0 * 0.0005 + 120.0 * 0.0005
    normalized_pnl = gross - fee
    scale = 4.0 / 9.0
    assert np.isclose((1000.0 * scale) + normalized_pnl * scale,
                      (1000.0 + normalized_pnl) * scale)
    assert np.isclose(_path_net_return(pd.DataFrame({"entry_price": [1.0], "exit_price": [1.2],
                                                     "side": [1], "exit_fee_rate": [TAKER]}))[0],
                      0.2 - 0.0005 - 0.0005 * 1.2)


def smoke(config_path: Path) -> dict:
    """Fund at most three real events per side to exercise VBT and scaling."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    paths, source_audit = _eligible_source(config)
    actual = paths[~paths.is_control].copy()
    runner = actual[actual.tp_r.eq(0)]
    samples = []
    for side in (1, -1):
        sample = runner[runner.side.eq(side)].sort_values(
            ["entry_ms", "entry_order", "symbol", "event_id"], kind="stable").head(3)
        if sample.empty:
            raise ValueError(f"No real smoke paths for side {side}")
        samples.append(set(sample.event_id.astype(str)))
    chosen = actual[actual.event_id.astype(str).isin(samples[0] | samples[1])].copy()
    # Keep the full time grid so the wallet's fixed all/dev/2025/2026 summary
    # windows all have observed marks, while funding only six small examples.
    result = _account(chosen, ARMS["long4_short5"])
    initial_slot_target = 1000.0 / SLOT_TOTAL / (1.0 + TAKER)
    initial_slot_notional = {}
    for direction, (_, positions, _, _) in result["sleeves"].items():
        first = positions.sort_values(["entry_ms", "slot"], kind="stable").iloc[0]
        initial_slot_notional[direction] = float(first.notional)
        if not np.isclose(float(first.notional), initial_slot_target, rtol=1e-8, atol=1e-8):
            raise ValueError(f"{direction} first funded slot differs from the original 1000/9 budget")
    return {
        "status": "passed",
        "source_audit": source_audit,
        "smoke_candidate_events": int(chosen.event_id.nunique()),
        "smoke_long_positions": int(result["audit"]["long_positions"]),
        "smoke_short_positions": int(result["audit"]["short_positions"]),
        "initial_capital_usdt": result["audit"]["initial_capital_usdt"],
        "final_cash_usdt": result["audit"]["final_cash_usdt"],
        "final_synchronous_nav_usdt": result["audit"]["final_synchronous_nav_usdt"],
        "cash_nav_delta_usdt": result["audit"]["cash_nav_delta_usdt"],
        "sleeve_initial_capital": {side: sleeve[3]["initial_capital_usdt"]
                                    for side, sleeve in result["sleeves"].items()},
        "sleeve_slot_notional_target_usdt": 1000.0 / SLOT_TOTAL,
        "first_funded_slot_notional_after_entry_fee_usdt": initial_slot_notional,
        "expected_first_slot_notional_after_entry_fee_usdt": initial_slot_target,
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--smoke", action="store_true",
                       help="Fund only a few real paths and validate sleeve scaling")
    modes.add_argument("--run", action="store_true",
                       help="Generate the frozen full study after builder/config commit")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    if args.self_check:
        _self_check()
        print("self-check: passed (4/5 and 5/4 capital, equal starting slot budget, proportional costs)")
    elif args.smoke:
        _self_check()
        print(json.dumps(smoke(config_path), ensure_ascii=False, indent=2, allow_nan=False))
    else:
        _self_check()
        result = generate(config_path)
        print(json.dumps({"experiment_id": result["experiment_id"],
                          "development_gate_pass": {key: value["development_gate_pass"]
                                                     for key, value in result["direction_sleeves"].items()}},
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
