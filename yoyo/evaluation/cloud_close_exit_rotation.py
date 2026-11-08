"""Causal choice between the frozen V1.7 2/3 scaleout and full runner.

Each venue/cohort gets two independent 1000-USDT VectorBT shadow accounts on
the same admitted event and slot roster: 2/3 at the native 2R limit plus the
runner, and 100% on the native stop/EMA runner. At each event's own 12H
confirmation/open, compare their trailing 90-day marked-NAV returns ending
12 hours earlier. The most recent and 90-day-start H1 closes must have exact
native quotes for every position still open at that mark. Missing history or
boundary evidence deterministically falls back to the 2/3 scaleout.

The selected arm is applied once to that event. Full-runner events reuse their
runner path for both child legs (2/3 + 1/3), preserving the native VectorBT
cash-sharing and fee machinery. No independent fill accounting is performed.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout
from yoyo.evaluation import cloud_close_thirds as thirds


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-exit-rotation-20261109-v1"
SLOTS = 9
PARTIAL_FRACTION = 2.0 / 3.0
HOUR_MS = wallet.HOUR
DAY_MS = 24 * HOUR_MS
LOOKBACK_MS = 90 * DAY_MS
LOOKBACK_LAG_MS = 12 * HOUR_MS
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00Z"),
    "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
    "2026": ("2026-01-01T00:00Z", None),
}
METRIC_COLUMNS = (
    "final_usdt", "multiple", "dd_mark_pct", "dd_bound_pct", "trades",
    "win_pct", "payoff", "payoff_usdt", "median_net_pct",
)
STATIC_REFERENCE = {
    "OKX": {
        "funded": "experiments/active/exp-cloud-close-eligibility-repair-20261109-v1/thirds/funded_OKX.csv.gz",
        "hourly": "experiments/active/exp-cloud-close-eligibility-repair-20261109-v1/thirds/hourly_OKX.csv.gz",
        "metrics": "experiments/active/exp-cloud-close-eligibility-repair-20261109-v1/thirds/account_metrics.csv",
        "scenario": "actual",
        "venue_column": "venue",
    },
    "Binance": {
        "funded": "experiments/active/exp-cloud-close-thirds-20261109-v1/funded_Binance.csv.gz",
        "hourly": "experiments/active/exp-cloud-close-thirds-20261109-v1/hourly_Binance.csv.gz",
        "metrics": "experiments/active/exp-cloud-close-thirds-20261109-v1/account_metrics.csv",
        "scenario": "actual",
        "venue_column": "venue",
    },
}
STATIC_TRADE_COLUMNS = (
    "event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms",
    "cash_pnl", "notional", "net_return",
)
EQUITY_COLUMNS = ("nav", "open_nav", "open_exit_nav", "lower_nav", "upper_nav")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if value is None or pd.isna(value):
        return None
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(payload), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def _assert_builder_committed(config_path: Path) -> None:
    source_rel = str(Path(__file__).resolve().relative_to(ROOT))
    config_rel = str(config_path.resolve().relative_to(ROOT))
    for path in (source_rel, config_rel):
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", source_rel, config_rel],
                             cwd=ROOT).returncode
    if changed:
        raise RuntimeError("Commit the frozen builder and config before official account runs")


def _validate_config(config: dict) -> None:
    account = config["account"]
    selector = config["selector"]
    if account["slots"] != SLOTS or not np.isclose(account["partial_2r_fraction"], PARTIAL_FRACTION):
        raise ValueError("Config changed the frozen nine-slot 2/3 account")
    if selector["lookback_days"] != 90 or selector["safe_lag_hours"] != 12:
        raise ValueError("Config changed the fixed 90-day / 12-hour selector clock")
    if selector["missing_or_tied_rule"] != "fallback to 2/3 scaleout":
        raise ValueError("Config changed the preregistered fallback rule")
    if set(config["venues"]) != {"OKX", "Binance"}:
        raise ValueError("Both venues must be included")
    for venue, expected in (("OKX", (11780, 10816)), ("Binance", (22623, 20588))):
        item = config["venues"][venue]
        if (item["expected_actual_opportunities"], item["expected_paired_controls"]) != expected:
            raise ValueError(f"Unexpected frozen {venue} event universe")


def _venue_clock(venue: str) -> tuple[int, int]:
    if venue == "OKX":
        return wallet.START, wallet.END
    return int(binance.venue.START), int(binance.venue.END)


def _quote_loader(venue: str):
    return wallet.quotes if venue == "OKX" else binance.quotes


def _load_paths(venue: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if venue == "OKX":
        paths = thirds.okx_paths()
        repair.check_parent_sets(paths)
    elif venue == "Binance":
        paths = binance.assembled(set(binance.patch.affected_tickers()))
    else:
        raise ValueError(f"Unknown venue: {venue}")
    paths = paths.loc[paths.tp_r.isin([0, 2])].copy()
    required = {"event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms",
                "entry_price", "exit_price", "exit_phase", "exit_fee_rate", "tp_r",
                "is_control", "entry_order"}
    missing = required - set(paths.columns)
    if missing:
        raise ValueError(f"{venue} paths miss required fields: {sorted(missing)}")
    if paths.duplicated(["event_id", "tp_r"]).any():
        raise ValueError(f"{venue}: duplicate event/tp path")
    if not paths.groupby("event_id").tp_r.apply(lambda series: set(series) == {0, 2}).all():
        raise ValueError(f"{venue}: every event must have both runner and native 2R paths")
    if not paths.groupby("event_id").is_control.nunique().eq(1).all():
        raise ValueError(f"{venue}: signal/control legs are mixed")
    for column in ("entry_ms", "exit_bar_ms"):
        paths[column] = pd.to_numeric(paths[column], errors="raise").astype("int64")
    if paths.entry_order.isna().any():
        raise ValueError(f"{venue}: low-prior-ATR entry priority is missing")

    runner = paths.loc[paths.tp_r.eq(0)]
    actual_ids = set(runner.loc[~runner.is_control, "parent_id"])
    actual = paths.loc[~paths.is_control].copy()
    controls = paths.loc[paths.is_control & paths.parent_id.isin(actual_ids)].copy()
    actual_events = runner.loc[~runner.is_control]
    control_events = runner.loc[runner.is_control & runner.parent_id.isin(actual_ids)]
    if actual_events.parent_id.duplicated().any() or control_events.parent_id.duplicated().any():
        raise ValueError(f"{venue}: expected one real and at most one control per parent")
    expected = (11780, 10816) if venue == "OKX" else (22623, 20588)
    observed = (len(actual_events), len(control_events))
    if observed != expected:
        raise ValueError(f"{venue} event roster changed: {observed}, expected {expected}")
    if not actual.groupby("event_id").entry_ms.nunique().eq(1).all():
        raise ValueError(f"{venue}: inconsistent event entry clock")
    return paths, actual, controls


def _prepare_partial(venue: str, events: pd.DataFrame) -> dict:
    start, end = _venue_clock(venue)
    return scaleout.pair_data(
        events.loc[events.tp_r.eq(0)], events.loc[events.tp_r.eq(2)], SLOTS,
        start=start, end=end, quote_loader=_quote_loader(venue),
    )


def _prepare_runner(venue: str, events: pd.DataFrame) -> dict:
    start, end = _venue_clock(venue)
    return wallet.prepare(
        events.loc[events.tp_r.eq(0)], SLOTS, start=start, end=end,
        quote_loader=_quote_loader(venue),
    )


def _selected_slots(data: dict, *, partial: bool) -> dict[str, int]:
    selected = data["selected"]
    if partial:
        selected = selected.assign(wallet_slot=selected.slot // 2)
        sizes = selected.groupby("event_id").wallet_slot.nunique()
        if not sizes.eq(1).all() or not selected.groupby("event_id").size().eq(2).all():
            raise ValueError("2/3 shadow roster does not have exactly two child legs per event")
        mapping = selected.drop_duplicates("event_id").set_index("event_id").wallet_slot
    else:
        if selected.event_id.duplicated().any():
            raise ValueError("Full-runner shadow has duplicate selected event")
        mapping = selected.set_index("event_id").slot
    return {str(event_id): int(slot) for event_id, slot in mapping.items()}


def _assert_same_admissions(partial: dict, runner: dict, venue: str, cohort: str) -> dict[str, int]:
    partial_slots = _selected_slots(partial, partial=True)
    runner_slots = _selected_slots(runner, partial=False)
    if partial_slots != runner_slots:
        raise ValueError(f"{venue}/{cohort}: static shadows do not share event and slot roster")
    return runner_slots


def _assert_cash(trades: pd.DataFrame, equity: pd.DataFrame, venue: str, label: str) -> None:
    if trades.empty or equity.empty:
        raise ValueError(f"{venue}/{label}: empty funded ledger")
    if not np.isclose(1000.0 + trades.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-9, atol=0.005):
        raise ValueError(f"{venue}/{label}: position cash PnL does not reconcile to final NAV")
    if trades.event_id.duplicated().any():
        raise ValueError(f"{venue}/{label}: funded positions contain duplicate event ids")


def _period_metrics(positions: pd.DataFrame, equity: pd.DataFrame) -> dict[str, dict]:
    return {name: wallet.summarize(positions, equity, *limits)
            for name, limits in PERIODS.items()}


def _run_static(venue: str, events: pd.DataFrame, policy: str) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    if policy == "partial_2_3":
        data = _prepare_partial(venue, events)
        metrics, positions, equity = scaleout.simulate(data, PARTIAL_FRACTION)
        positions = positions.reset_index()
        slots = _selected_slots(data, partial=True)
        positions["slot"] = positions.event_id.map(slots).astype("int64")
    elif policy == "full_runner":
        data = _prepare_runner(venue, events)
        positions, equity = wallet.account(data)
        metrics = _period_metrics(positions, equity)
        slots = _selected_slots(data, partial=False)
    else:
        raise ValueError(policy)
    _assert_cash(positions, equity, venue, policy)
    if set(positions.event_id) != set(slots):
        raise ValueError(f"{venue}/{policy}: funded events differ from selected slots")
    return metrics, positions, equity, data


def _align_trades(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(STATIC_TRADE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Funded ledger lacks columns {sorted(missing)}")
    out = frame.loc[:, STATIC_TRADE_COLUMNS].copy()
    if out.event_id.duplicated().any():
        raise ValueError("Funded ledger has duplicate event ids")
    return out.sort_values("event_id").reset_index(drop=True)


def _compare_trades(actual: pd.DataFrame, reference: pd.DataFrame) -> dict:
    a, b = _align_trades(actual), _align_trades(reference)
    if not a.event_id.equals(b.event_id):
        raise ValueError("Static 2/3 event IDs differ from the corrected reference")
    exact_cols = ("parent_id", "symbol", "side", "entry_ms", "exit_bar_ms")
    for column in exact_cols:
        if not a[column].equals(b[column]):
            raise ValueError(f"Static 2/3 reference differs in {column}")
    numeric_cols = ("cash_pnl", "notional", "net_return")
    errors = {}
    for column in numeric_cols:
        delta = np.abs(a[column].to_numpy(float) - b[column].to_numpy(float))
        errors[column] = float(delta.max(initial=0.0))
        if not np.allclose(a[column], b[column], rtol=1e-9, atol=1e-6):
            raise ValueError(f"Static 2/3 per-trade {column} differs from corrected reference")
    return {"events": int(len(a)), "max_absolute_error": errors}


def _equity_frame(equity: pd.DataFrame) -> pd.DataFrame:
    result = equity.loc[:, EQUITY_COLUMNS].copy()
    result.index = pd.DatetimeIndex(result.index).tz_convert("UTC").as_unit("ms")
    if not result.index.is_unique or not result.index.is_monotonic_increasing:
        raise ValueError("Hourly NAV marks must be a unique ordered clock")
    return result


def _compare_equity(actual: pd.DataFrame, reference: pd.DataFrame) -> dict:
    a, b = _equity_frame(actual), _equity_frame(reference)
    if not a.index.equals(b.index):
        raise ValueError("Static 2/3 hourly clock differs from corrected reference")
    errors = {}
    for column in EQUITY_COLUMNS:
        delta = np.abs(a[column].to_numpy(float) - b[column].to_numpy(float))
        errors[column] = float(delta.max(initial=0.0))
        if not np.allclose(a[column], b[column], rtol=1e-9, atol=1e-6):
            raise ValueError(f"Static 2/3 hourly {column} differs from corrected reference")
    return {"hours": int(len(a)), "max_absolute_error": errors}


def _compare_metrics(venue: str, current: dict[str, dict]) -> dict:
    spec = STATIC_REFERENCE[venue]
    table = pd.read_csv(ROOT / spec["metrics"])
    selection = table.loc[table.scenario.eq(spec["scenario"])]
    if "venue" in selection:
        selection = selection.loc[selection.venue.eq(venue)]
    if selection.period.duplicated().any():
        raise ValueError(f"{venue}: corrected static metric reference has duplicate periods")
    reference = selection.set_index("period")
    errors = {}
    for period, metrics in current.items():
        if period not in reference.index:
            raise ValueError(f"{venue}: corrected static metric reference lacks {period}")
        for name in METRIC_COLUMNS:
            left = float(metrics[name])
            right = float(reference.loc[period, name])
            errors[f"{period}.{name}"] = abs(left - right)
            if not np.isclose(left, right, rtol=1e-9, atol=1e-6):
                raise ValueError(f"{venue}: static 2/3 {period}.{name} differs from reference")
    return {"metrics_checked": len(errors), "max_absolute_error": max(errors.values(), default=0.0)}


def _verify_partial_reference(venue: str, positions: pd.DataFrame,
                              equity: pd.DataFrame, metrics: dict,
                              slots: dict[str, int]) -> dict:
    spec = STATIC_REFERENCE[venue]
    reference_trades = pd.read_csv(ROOT / spec["funded"], dtype={"event_id": str, "parent_id": str})
    reference_equity = pd.read_csv(ROOT / spec["hourly"], parse_dates=["hour_end_utc"])
    reference_equity = reference_equity.set_index("hour_end_utc")
    reference_equity.index = pd.DatetimeIndex(reference_equity.index).tz_convert("UTC").as_unit("ms")
    trade_check = _compare_trades(positions, reference_trades)
    hour_check = _compare_equity(equity, reference_equity)
    metric_check = _compare_metrics(venue, metrics)
    if set(reference_trades.event_id) != set(slots):
        raise ValueError(f"{venue}: selected slots differ from the corrected 2/3 funded roster")
    return {"passed": True, "per_trade": trade_check, "all_hourly_marks": hour_check,
            "metrics": metric_check, "slot_roster": len(slots)}


def _nav_lookup(equity: pd.DataFrame) -> dict[int, float]:
    hourly = _equity_frame(equity)
    # DatetimeIndex.asi8 follows the index's storage unit under pandas 3. After
    # _equity_frame pins it to ms, the values already are milliseconds.
    clock = hourly.index.as_unit("ms").asi8
    nav = hourly.nav.to_numpy(dtype=np.float64)
    return {int(stamp): float(value) for stamp, value in zip(clock, nav)}


def _active_intervals(selected_runner_paths: pd.DataFrame,
                      selected_ids: set[str]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    frame = selected_runner_paths.loc[selected_runner_paths.event_id.isin(selected_ids),
                                      ["event_id", "symbol", "entry_ms", "exit_bar_ms"]].copy()
    if frame.event_id.duplicated().any() or set(frame.event_id) != selected_ids:
        raise ValueError("Cannot prove a one-to-one selected runner interval roster")
    frame["close_end_ms"] = frame.exit_bar_ms.astype("int64") + HOUR_MS
    out = {}
    for symbol, group in frame.groupby("symbol", sort=False):
        group = group.sort_values("entry_ms")
        entries = group.entry_ms.to_numpy(dtype=np.int64)
        ends = group.close_end_ms.to_numpy(dtype=np.int64)
        if len(entries) > 1 and np.any(entries[1:] < ends[:-1]):
            raise ValueError(f"Selected runner intervals overlap on one contract: {symbol}")
        out[str(symbol)] = (entries, ends)
    return out


def _active_symbols(intervals: dict[str, tuple[np.ndarray, np.ndarray]], boundary_ms: int) -> list[str]:
    active = []
    for symbol, (entries, ends) in intervals.items():
        idx = int(np.searchsorted(entries, boundary_ms, side="left") - 1)
        if idx >= 0 and int(ends[idx]) > boundary_ms:
            active.append(symbol)
    return sorted(active)


def _quote_mark_evidence(venue: str, boundary_ms: int,
                         intervals: dict[str, tuple[np.ndarray, np.ndarray]],
                         quote_clock_cache: dict[str, np.ndarray]) -> dict:
    active = _active_symbols(intervals, boundary_ms)
    bar_open_ms = boundary_ms - HOUR_MS
    missing = []
    loader = _quote_loader(venue)
    for symbol in active:
        if symbol not in quote_clock_cache:
            try:
                clock, _ = loader(symbol)
                quote_clock_cache[symbol] = np.asarray(clock, dtype=np.int64)
            except (OSError, KeyError, ValueError):
                quote_clock_cache[symbol] = np.empty(0, dtype=np.int64)
        clock = quote_clock_cache[symbol]
        idx = int(np.searchsorted(clock, bar_open_ms))
        if idx >= len(clock) or int(clock[idx]) != bar_open_ms:
            missing.append(symbol)
    return {
        "boundary_ms": int(boundary_ms),
        "last_completed_h1_open_ms": int(bar_open_ms),
        "active_symbols": active,
        "missing_symbol_quotes": missing,
        "observed": not missing,
    }


def _choose_arm(venue: str, entry_ms: int, partial_nav: dict[int, float],
                runner_nav: dict[int, float],
                intervals: dict[str, tuple[np.ndarray, np.ndarray]],
                quote_clock_cache: dict[str, np.ndarray]) -> dict:
    entry_ms = int(entry_ms)
    result = {
        "entry_ms": entry_ms,
        "lookback_start_ms": None,
        "lookback_end_ms": entry_ms - LOOKBACK_LAG_MS,
        "safe_lag_hours": 12,
        "partial_nav_start": None,
        "partial_nav_end": None,
        "runner_nav_start": None,
        "runner_nav_end": None,
        "partial_return_90d": None,
        "runner_return_90d": None,
        "start_quote_evidence": {},
        "end_quote_evidence": {},
        "choice": "partial_2_3",
        "choice_reason": "insufficient_history_or_boundary_evidence",
        "selection_evidence_valid": False,
    }
    if entry_ms % HOUR_MS or entry_ms % (12 * HOUR_MS):
        result["choice_reason"] = "invalid_confirmation_clock_fallback"
        return result
    end_ms = entry_ms - LOOKBACK_LAG_MS
    start_ms = end_ms - LOOKBACK_MS
    result["lookback_start_ms"] = int(start_ms)
    p0, p1 = partial_nav.get(start_ms), partial_nav.get(end_ms)
    r0, r1 = runner_nav.get(start_ms), runner_nav.get(end_ms)
    result["partial_nav_start"], result["partial_nav_end"] = p0, p1
    result["runner_nav_start"], result["runner_nav_end"] = r0, r1
    result["start_quote_evidence"] = _quote_mark_evidence(
        venue, start_ms, intervals, quote_clock_cache,
    )
    result["end_quote_evidence"] = _quote_mark_evidence(
        venue, end_ms, intervals, quote_clock_cache,
    )
    values = (p0, p1, r0, r1)
    if any(value is None or not np.isfinite(value) or value <= 0 for value in values):
        result["choice_reason"] = "missing_or_nonpositive_shadow_nav_fallback"
        return result
    if (not result["start_quote_evidence"]["observed"]
            or not result["end_quote_evidence"]["observed"]):
        result["choice_reason"] = "unverified_native_h1_boundary_fallback"
        return result
    p_return, r_return = p1 / p0 - 1.0, r1 / r0 - 1.0
    result["partial_return_90d"] = float(p_return)
    result["runner_return_90d"] = float(r_return)
    result["selection_evidence_valid"] = True
    if r_return > p_return + 1e-12:
        result["choice"] = "full_runner"
        result["choice_reason"] = "full_runner_higher_trailing_90d_nav_return"
    else:
        result["choice"] = "partial_2_3"
        result["choice_reason"] = "partial_leads_or_returns_tie"
    return result


def _save_account(venue: str, cohort: str, policy: str, positions: pd.DataFrame,
                  equity: pd.DataFrame) -> None:
    positions.to_csv(EXP / f"funded_{venue}_{cohort}_{policy}.csv.gz", index=False)
    equity.to_csv(EXP / f"hourly_{venue}_{cohort}_{policy}.csv.gz", index_label="hour_end_utc")


def _static_shadow_cohort(venue: str, cohort: str, events: pd.DataFrame,
                          audit: dict, metric_rows: list[dict]) -> pd.DataFrame:
    runner_paths = events.loc[events.tp_r.eq(0)].copy()
    partial_metrics, partial_positions, partial_equity, partial_data = _run_static(
        venue, events, "partial_2_3",
    )
    runner_metrics, runner_positions, runner_equity, runner_data = _run_static(
        venue, events, "full_runner",
    )
    slots = _assert_same_admissions(partial_data, runner_data, venue, cohort)
    if partial_data["skips"] != runner_data["skips"]:
        raise ValueError(f"{venue}/{cohort}: static shadows rejected different candidates")
    if len(partial_positions) != len(runner_positions) or set(partial_positions.event_id) != set(runner_positions.event_id):
        raise ValueError(f"{venue}/{cohort}: static shadows funded different positions")
    partial_positions["slot"] = partial_positions.event_id.map(slots).astype("int64")
    runner_positions["slot"] = runner_positions.event_id.map(slots).astype("int64")

    static_reference = None
    if cohort == "actual":
        static_reference = _verify_partial_reference(
            venue, partial_positions, partial_equity, partial_metrics, slots,
        )
    audit[f"{venue}.{cohort}"] = {
        "input_events": int(runner_paths.event_id.nunique()),
        "funded_positions": int(len(runner_positions)),
        "shared_event_slot_roster": int(len(slots)),
        "admission_rejections": {
            str(key): int(value) for key, value in partial_data["skips"].items()
        },
        "unquoted_position_hours": {
            "partial_2_3": int(partial_data["gap_marks"]),
            "full_runner": int(runner_data["gap_marks"]),
        },
        "corrected_partial_reference": static_reference,
        "runner_curve_reuse": "not reused; existing candidates have different exits, eligibility, or source cutoff",
    }
    for policy, metrics in (("partial_2_3", partial_metrics), ("full_runner", runner_metrics)):
        for period, values in metrics.items():
            metric_rows.append({"venue": venue, "cohort": cohort,
                                "policy": policy, "period": period, **values})
    _save_account(venue, cohort, "shadow_partial_2_3", partial_positions, partial_equity)
    _save_account(venue, cohort, "shadow_full_runner", runner_positions, runner_equity)

    selected_ids = set(runner_data["selected"].event_id.astype(str))
    intervals = _active_intervals(runner_paths, selected_ids)
    partial_nav, runner_nav = _nav_lookup(partial_equity), _nav_lookup(runner_equity)
    if not partial_nav or not runner_nav:
        raise ValueError(f"{venue}/{cohort}: shadow NAV clock is empty")
    quote_cache: dict[str, np.ndarray] = {}
    rows = []
    for row in runner_paths.itertuples(index=False):
        choice = _choose_arm(venue, int(row.entry_ms), partial_nav, runner_nav,
                             intervals, quote_cache)
        start_quote = choice.pop("start_quote_evidence")
        end_quote = choice.pop("end_quote_evidence")
        rows.append({
            "event_id": row.event_id,
            "parent_id": row.parent_id,
            "symbol": row.symbol,
            "side": int(row.side),
            "is_control": bool(row.is_control),
            "entry_ms": int(row.entry_ms),
            "slot_funded_in_shadows": slots.get(str(row.event_id)),
            "start_boundary_ms": start_quote.get("boundary_ms"),
            "start_last_h1_open_ms": start_quote.get("last_completed_h1_open_ms"),
            "start_active_symbols": json.dumps(start_quote.get("active_symbols", [])),
            "start_missing_quote_symbols": json.dumps(start_quote.get("missing_symbol_quotes", [])),
            "start_boundary_observed": start_quote.get("observed", False),
            "end_boundary_ms": end_quote.get("boundary_ms"),
            "end_last_h1_open_ms": end_quote.get("last_completed_h1_open_ms"),
            "end_active_symbols": json.dumps(end_quote.get("active_symbols", [])),
            "end_missing_quote_symbols": json.dumps(end_quote.get("missing_symbol_quotes", [])),
            "end_boundary_observed": end_quote.get("observed", False),
            **choice,
        })
    choices = pd.DataFrame(rows)
    if choices.event_id.duplicated().any() or set(choices.event_id) != set(runner_paths.event_id):
        raise ValueError(f"{venue}/{cohort}: choice evidence is not one row per candidate event")
    valid_rows = choices.loc[choices.selection_evidence_valid]
    if valid_rows.empty or valid_rows[["partial_return_90d", "runner_return_90d"]].isna().any().any():
        raise ValueError(f"{venue}/{cohort}: no valid causal trailing-90d comparison was produced")
    choices.to_csv(EXP / f"choice_evidence_{venue}_{cohort}.csv.gz", index=False)
    counts = choices.choice.value_counts().to_dict()
    reasons = choices.choice_reason.value_counts().to_dict()
    audit[f"{venue}.{cohort}"].update({
        "choice_counts": {str(key): int(value) for key, value in counts.items()},
        "choice_reasons": {str(key): int(value) for key, value in reasons.items()},
        "valid_90d_boundary_evidence": int(choices.selection_evidence_valid.sum()),
        "fallback_count": int((~choices.selection_evidence_valid).sum()),
    })
    del partial_data, runner_data, partial_positions, runner_positions
    del partial_equity, runner_equity, partial_metrics, runner_metrics, quote_cache
    gc.collect()
    return choices


def _shadow_manifest_path() -> Path:
    return EXP / "shadow_manifest.json"


def _run_shadows(config_path: Path, config: dict) -> None:
    manifest_path = _shadow_manifest_path()
    if manifest_path.exists():
        raise FileExistsError(f"Static shadow phase already completed: {manifest_path}")
    audit = {
        "config_id": config["id"],
        "vectorbt_version": wallet.vbt.__version__,
        "selection_rule": config["selector"],
        "runner_reuse_review": {
            "OKX": "cloud_close_leadership fraction=1.0 is a 100% 2R target (cloud_close_slots maps it to tp_r=2); equity-stop dd0 is a 2R lock-profit account.",
            "Binance": "cloud_close_binance_transfer runner uses the earlier 2026-09-24 cutoff and pre-gap-repair event set; it is not the corrected full-period baseline.",
            "decision": "Recompute both true full-runner shadows with the corrected source paths and VectorBT wallet.",
        },
        "static_partial_reference_checks": {},
        "cohorts": {},
    }
    metric_rows = []
    input_counts = {}
    for venue in ("OKX", "Binance"):
        paths, actual, controls = _load_paths(venue)
        input_counts[venue] = {
            "eligible_actual": int(actual.loc[actual.tp_r.eq(0)].event_id.nunique()),
            "eligible_controls": int(controls.loc[controls.tp_r.eq(0)].event_id.nunique()),
            "paired_parent_ids": int(len(set(actual.loc[actual.tp_r.eq(0), "parent_id"])
                                          & set(controls.loc[controls.tp_r.eq(0), "parent_id"]))),
        }
        for cohort, events in (("actual", actual), ("control", controls)):
            _static_shadow_cohort(venue, cohort, events, audit["cohorts"], metric_rows)
        del paths, actual, controls
        if venue == "OKX" and hasattr(wallet.quotes, "cache_clear"):
            wallet.quotes.cache_clear()
        if venue == "Binance" and hasattr(binance.quotes, "cache_clear"):
            binance.quotes.cache_clear()
        gc.collect()

    pd.DataFrame(metric_rows).to_csv(EXP / "shadow_metrics.csv", index=False)
    audit["input_counts"] = input_counts
    _write_json(EXP / "shadow_audit.json", audit)
    manifest = {
        "config_id": config["id"],
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "input_counts": input_counts,
        "choice_evidence_files": [
            f"experiments/active/{EXP.name}/choice_evidence_{venue}_{cohort}.csv.gz"
            for venue in ("OKX", "Binance") for cohort in ("actual", "control")
        ],
        "shadow_audit": f"experiments/active/{EXP.name}/shadow_audit.json",
        "status": "shadows_and_choice_evidence_frozen",
    }
    _write_json(manifest_path, manifest)


def _load_choices(venue: str, cohort: str, expected_ids: set[str]) -> pd.DataFrame:
    path = EXP / f"choice_evidence_{venue}_{cohort}.csv.gz"
    if not path.is_file():
        raise FileNotFoundError(f"Run --phase shadows first; missing {path}")
    choices = pd.read_csv(path, dtype={"event_id": str, "parent_id": str})
    if choices.event_id.duplicated().any() or set(choices.event_id) != expected_ids:
        raise ValueError(f"{venue}/{cohort}: saved decisions differ from current frozen event ids")
    if not choices.choice.isin(["partial_2_3", "full_runner"]).all():
        raise ValueError(f"{venue}/{cohort}: invalid selected arm")
    return choices


def _rotated_paths(events: pd.DataFrame, choices: pd.DataFrame) -> pd.DataFrame:
    runner = events.loc[events.tp_r.eq(0)].copy()
    target = events.loc[events.tp_r.eq(2)].copy()
    choice = choices.set_index("event_id").choice
    event_ids = set(runner.event_id.astype(str))
    if set(target.event_id.astype(str)) != event_ids or set(choice.index.astype(str)) != event_ids:
        raise ValueError("Rotation choices or native exit paths do not cover every event")
    full_runner_ids = set(choice.index[choice.eq("full_runner")].astype(str))
    native_targets = target.loc[~target.event_id.astype(str).isin(full_runner_ids)].copy()
    runner_copy = runner.loc[runner.event_id.astype(str).isin(full_runner_ids)].copy()
    runner_copy["tp_r"] = 2
    # Full-runner entries use two child legs on the same actual runner exit.
    # Fees, phases, gap flags, ambiguity and observed exit prices stay native.
    rotated_target = pd.concat([native_targets, runner_copy], ignore_index=True)
    if rotated_target.event_id.duplicated().any() or set(rotated_target.event_id.astype(str)) != event_ids:
        raise ValueError("Each event must have exactly one selected target/runner-copy leg")
    rotated = pd.concat([runner, rotated_target], ignore_index=True)
    if rotated.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Rotation introduced duplicate event/tp legs")
    return rotated


def _run_rotation_account(venue: str, cohort: str, events: pd.DataFrame,
                          choices: pd.DataFrame, *, slip: float = 0.0,
                          expected_slots: dict[str, int] | None = None) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    rotated = _rotated_paths(events, choices)
    data = _prepare_partial(venue, rotated)
    selected = _selected_slots(data, partial=True)
    if expected_slots is not None and selected != expected_slots:
        raise ValueError(f"{venue}/{cohort}: rotation changed signal admission or slot assignment")
    metrics, positions, equity = scaleout.simulate(data, PARTIAL_FRACTION, slip)
    positions = positions.reset_index()
    _assert_cash(positions, equity, venue, f"rotation_{cohort}_slip{slip:g}")
    choices_by_event = choices.set_index("event_id").choice
    positions = positions.copy()
    positions["rotation_choice"] = positions.event_id.map(choices_by_event)
    positions["slot"] = positions.event_id.map(selected).astype("int64")
    if positions.rotation_choice.isna().any():
        raise ValueError("Funded rotation position lacks its frozen choice")
    return metrics, positions, equity, data


def _run_rotation(config_path: Path, config: dict) -> None:
    manifest = json.loads(_shadow_manifest_path().read_text())
    if manifest["config_sha256"] != hashlib.sha256(config_path.read_bytes()).hexdigest():
        raise ValueError("Config changed since static shadows and choices were frozen")
    current_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if manifest["source_commit"] != current_commit:
        raise ValueError("Builder commit changed since static shadows and choices were frozen")
    summary_path = EXP / "rotation_summary.json"
    if summary_path.exists():
        raise FileExistsError(f"Rotation account phase already completed: {summary_path}")

    result_rows = []
    rotation_meta = {}
    for venue in ("OKX", "Binance"):
        paths, actual, controls = _load_paths(venue)
        actual_events = actual.loc[actual.tp_r.eq(0)]
        control_events = controls.loc[controls.tp_r.eq(0)]
        actual_ids = set(actual_events.event_id.astype(str))
        control_ids = set(control_events.event_id.astype(str))
        choices_actual = _load_choices(venue, "actual", actual_ids)
        choices_control = _load_choices(venue, "control", control_ids)
        slot_actual = pd.read_csv(EXP / f"funded_{venue}_actual_shadow_full_runner.csv.gz")
        slot_control = pd.read_csv(EXP / f"funded_{venue}_control_shadow_full_runner.csv.gz")
        actual_slots = dict(zip(slot_actual.event_id.astype(str), slot_actual.slot.astype(int)))
        control_slots = dict(zip(slot_control.event_id.astype(str), slot_control.slot.astype(int)))

        scenarios = [
            ("rotation_actual", actual, choices_actual, actual_slots, 0.0, True),
            ("rotation_control", controls, choices_control, control_slots, 0.0, True),
        ]
        pair_parents = set(actual_events.parent_id.astype(str)) & set(control_events.parent_id.astype(str))
        paired_actual = actual.loc[actual.parent_id.astype(str).isin(pair_parents)].copy()
        paired_control = controls.loc[controls.parent_id.astype(str).isin(pair_parents)].copy()
        scenarios.extend([
            ("rotation_paired_actual", paired_actual,
             choices_actual[choices_actual.event_id.isin(set(paired_actual.event_id))],
             None, 0.0, False),
            ("rotation_paired_control", paired_control,
             choices_control[choices_control.event_id.isin(set(paired_control.event_id))],
             None, 0.0, False),
            ("rotation_stress_5bp", actual, choices_actual, actual_slots, 0.0005, False),
        ])
        rotation_meta[venue] = {
            "actual_opportunities": int(actual_events.event_id.nunique()),
            "eligible_controls": int(control_events.event_id.nunique()),
            "paired_parent_ids": int(len(pair_parents)),
            "actual_unmatched": int(actual_events.parent_id.astype(str).nunique() - len(pair_parents)),
            "control_unmatched": int(control_events.parent_id.astype(str).nunique() - len(pair_parents)),
            "chosen_arms_actual": {str(k): int(v) for k, v in choices_actual.choice.value_counts().items()},
            "chosen_arms_control": {str(k): int(v) for k, v in choices_control.choice.value_counts().items()},
        }
        for label, pool, choices, slots, slip, save in scenarios:
            metrics, positions, equity, data = _run_rotation_account(
                venue, label, pool, choices, slip=slip, expected_slots=slots,
            )
            result_rows.extend({"venue": venue, "scenario": label, "slippage": slip,
                                "period": period, **values}
                               for period, values in metrics.items())
            if save:
                positions.to_csv(EXP / f"funded_{venue}_{label}.csv.gz", index=False)
                equity.to_csv(EXP / f"hourly_{venue}_{label}.csv.gz", index_label="hour_end_utc")
            if label == "rotation_actual":
                result_rows.extend({"venue": venue, "scenario": "shadow_reference_partial",
                                    "slippage": 0.0, "period": period, **values}
                                   for period, values in _period_metrics(
                                       pd.read_csv(EXP / f"funded_{venue}_actual_shadow_partial_2_3.csv.gz"),
                                       pd.read_csv(EXP / f"hourly_{venue}_actual_shadow_partial_2_3.csv.gz",
                                                   parse_dates=["hour_end_utc"]).set_index("hour_end_utc"),
                                   ).items())
            del metrics, positions, equity, data
            gc.collect()
        del paths, actual, controls, paired_actual, paired_control
        if venue == "OKX" and hasattr(wallet.quotes, "cache_clear"):
            wallet.quotes.cache_clear()
        if venue == "Binance" and hasattr(binance.quotes, "cache_clear"):
            binance.quotes.cache_clear()
        gc.collect()

    metrics_frame = pd.DataFrame(result_rows)
    metrics_frame.to_csv(EXP / "account_metrics.csv", index=False)
    rotation_summary = {
        "config_id": config["id"],
        "source_commit": current_commit,
        "static_shadow_manifest": str(_shadow_manifest_path().relative_to(ROOT)),
        "periods": list(PERIODS),
        "venues": rotation_meta,
        "accounts": metrics_frame.loc[metrics_frame.period.eq("all")].to_dict("records"),
        "fees": {"path_fees_unchanged": True, "additional_market_slippage_stress": 0.0005},
        "training_eligible": False,
        "production_eligible": False,
    }
    _write_json(summary_path, rotation_summary)
    print(metrics_frame.loc[metrics_frame.period.eq("all")].to_string(index=False), flush=True)


def _mini_paths(start_ms: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, tuple[np.ndarray, np.ndarray]]]:
    records = []
    bars = {}
    specs = (("AAA", 1, 100.0, 106.0), ("BBB", -1, 100.0, 94.0))
    for idx, (symbol, side, entry, runner_exit) in enumerate(specs):
        event_id = f"mini-{symbol}"
        parent_id = f"parent-{symbol}"
        entry_ms = start_ms
        stop = entry - side * 5.0
        runner_bar = start_ms + 2 * HOUR_MS
        target_bar = start_ms + HOUR_MS
        common = {
            "event_id": event_id, "parent_id": parent_id, "symbol": symbol,
            "side": side, "is_control": False, "entry_ms": entry_ms,
            "entry_price": entry, "stop_price": stop, "entry_order": float(idx),
            "same_bar_ambiguous": False, "gap_flag": False,
        }
        records.append({
            **common, "tp_r": 0, "exit_bar_ms": runner_bar,
            "exit_price": runner_exit, "exit_phase": "close",
            "exit_fee_rate": wallet.TAKER, "exit_reason": "ema_flip",
        })
        records.append({
            **common, "tp_r": 2, "exit_bar_ms": target_bar,
            "exit_price": entry + side * 10.0, "exit_phase": "intrabar",
            "exit_fee_rate": 0.0002, "exit_reason": "take_profit_2r",
        })
        clock = start_ms + np.arange(4, dtype=np.int64) * HOUR_MS
        opens = np.array([entry, entry + side * 1.0, entry + side * 2.0, runner_exit], dtype=float)
        highs = np.maximum(opens, np.r_[opens[1:], opens[-1]]) + 1.0
        lows = np.minimum(opens, np.r_[opens[1:], opens[-1]]) - 1.0
        prices = np.column_stack((opens, highs, lows, opens))
        bars[symbol] = (clock, prices)
    return pd.DataFrame(records), pd.DataFrame(), bars


def mini_check() -> None:
    """Exercise the per-event target substitution through the real VectorBT wallet."""
    start_ms = wallet.START // HOUR_MS * HOUR_MS
    events, _, bars = _mini_paths(start_ms)

    def quote_loader(symbol):
        return bars[symbol]

    runner = events.loc[events.tp_r.eq(0)].copy()
    full_data = wallet.prepare(runner, 2, start=start_ms, end=start_ms + 4 * HOUR_MS,
                               quote_loader=quote_loader)
    full_positions, full_equity = wallet.account(full_data)
    full_slots = _selected_slots(full_data, partial=False)
    all_runner_choices = pd.DataFrame({
        "event_id": runner.event_id,
        "choice": "full_runner",
    })
    dynamic_full = _rotated_paths(events, all_runner_choices)
    full_pair_data = scaleout.pair_data(
        dynamic_full.loc[dynamic_full.tp_r.eq(0)], dynamic_full.loc[dynamic_full.tp_r.eq(2)],
        2, start=start_ms, end=start_ms + 4 * HOUR_MS, quote_loader=quote_loader,
    )
    pair_slots = _assert_same_admissions(full_pair_data, full_data, "MINI", "actual")
    if pair_slots != full_slots:
        raise AssertionError("Full-runner two-leg copies changed the nine-slot admission rule")
    pair_legs, pair_equity = wallet.account(
        full_pair_data, weights=(PARTIAL_FRACTION, 1.0 - PARTIAL_FRACTION),
    )
    left = full_positions.set_index("event_id").sort_index()
    if not pair_legs.groupby("event_id").size().eq(2).all():
        raise AssertionError("Full-runner copy did not fund exactly two child legs per event")
    right = pair_legs.groupby("event_id", sort=False).agg(
        cash_pnl=("cash_pnl", "sum"), notional=("notional", "sum"),
    ).sort_index()
    if not np.array_equal(left.index, right.index):
        raise AssertionError("Full-runner copy changed funded event ids")
    if not np.allclose(left.cash_pnl, right.cash_pnl, rtol=1e-9, atol=1e-7):
        raise AssertionError("Two-leg runner copy differs from one-leg full runner PnL")
    if not np.allclose(left.notional, right.notional, rtol=1e-9, atol=1e-7):
        raise AssertionError("Two-leg runner copy differs from full-runner notional")
    if not np.allclose(full_equity.nav, pair_equity.nav, rtol=1e-9, atol=1e-6):
        raise AssertionError("Two-leg runner copy differs from full-runner hourly NAV")

    partial_choice = pd.DataFrame({"event_id": runner.event_id, "choice": "partial_2_3"})
    native_paths = _rotated_paths(events, partial_choice)
    target_prices = native_paths.loc[native_paths.tp_r.eq(2)].set_index("event_id").exit_price
    original_target_prices = events.loc[events.tp_r.eq(2)].set_index("event_id").exit_price
    if not target_prices.equals(original_target_prices):
        raise AssertionError("Partial choice did not retain its native 2R target path")

    start = start_ms - LOOKBACK_MS - LOOKBACK_LAG_MS
    end = start + LOOKBACK_MS
    partial_nav = {start: 1000.0, end: 1100.0}
    runner_nav = {start: 1000.0, end: 1200.0}
    choice = _choose_arm("OKX", end + LOOKBACK_LAG_MS, partial_nav,
                         runner_nav, {}, {})
    if choice["choice"] != "full_runner":
        raise AssertionError("A higher fully observed trailing shadow return must select runner")
    tie = _choose_arm("OKX", end + LOOKBACK_LAG_MS, partial_nav,
                      {start: 1000.0, end: 1100.0}, {}, {})
    if tie["choice"] != "partial_2_3":
        raise AssertionError("A tied shadow return must fall back to partial 2/3")
    no_history = _choose_arm("OKX", end + LOOKBACK_LAG_MS, {}, {}, {}, {})
    if no_history["choice"] != "partial_2_3" or no_history["selection_evidence_valid"]:
        raise AssertionError("Missing lookback NAV must fail closed to partial 2/3")
    missing_quote_intervals = {
        "FAKE": (np.array([start - HOUR_MS], dtype=np.int64),
                 np.array([end + HOUR_MS], dtype=np.int64)),
    }
    missing_quote = _choose_arm(
        "OKX", end + LOOKBACK_LAG_MS, partial_nav, runner_nav,
        missing_quote_intervals, {"FAKE": np.empty(0, dtype=np.int64)},
    )
    if (missing_quote["choice"] != "partial_2_3"
            or missing_quote["choice_reason"] != "unverified_native_h1_boundary_fallback"):
        raise AssertionError("Missing exact H1 boundary quote must fail closed")
    mark_index = pd.to_datetime([start, end], unit="ms", utc=True).as_unit("ms")
    marked = pd.DataFrame({
        "nav": [1000.0, 1100.0],
        "open_nav": [1000.0, 1100.0],
        "open_exit_nav": [1000.0, 1100.0],
        "lower_nav": [1000.0, 1100.0],
        "upper_nav": [1000.0, 1100.0],
    }, index=mark_index)
    indexed_nav = _nav_lookup(marked)
    if set(indexed_nav) != {start, end} or indexed_nav[end] != 1100.0:
        raise AssertionError("Timezone-aware H1 close labels must remain exact millisecond keys")
    indexed_choice = _choose_arm(
        "OKX", end + LOOKBACK_LAG_MS, indexed_nav,
        {start: 1000.0, end: 1200.0}, {}, {},
    )
    if indexed_choice["choice"] != "full_runner" or not indexed_choice["selection_evidence_valid"]:
        raise AssertionError("Exact millisecond NAV keys must yield a genuine 90-day comparison")
    print("exit-rotation VectorBT mini-check: PASS")


def run(config_path: Path, phase: str) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _validate_config(config)
    _assert_builder_committed(config_path)
    if phase in ("shadows", "all"):
        _run_shadows(config_path, config)
    if phase in ("rotation", "all"):
        _run_rotation(config_path, config)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    parser.add_argument("--phase", choices=("shadows", "rotation", "all"), default="shadows")
    parser.add_argument("--mini-check", action="store_true",
                        help="run only a tiny synthetic VectorBT path/account fixture")
    args = parser.parse_args()
    if args.mini_check:
        mini_check()
        return
    run(args.config.resolve(), args.phase)


if __name__ == "__main__":
    main()
