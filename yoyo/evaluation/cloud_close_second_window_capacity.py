"""Measure next-12H capacity and as-of decision state for cap-bound OKX parents.

The only selected rows are those whose saved funded entry notional actually
reached the already-known 10% prior-minute turnover cap in either saved cost
scenario. Decision features use completed H1/H1-minute data through the next
12H close only; no order or portfolio is replayed.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from zipfile import BadZipFile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_cash_giveback as giveback
from yoyo.evaluation import cloud_close_prior_minute_liquidity as liquidity


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-second-window-capacity-20261109-v1"
CONFIG = EXP / "config.json"
MEDIAN = EXP.parent / "exp-cloud-close-vix-priority-medians-20261109-v1"
VIX = EXP.parent / "exp-cloud-close-vix-priority-20261109-v1"
CASH = EXP.parent / "exp-cloud-close-cash-giveback-20261109-v1"
FUNDED_PATHS = VIX / "aligned/selected_paths.csv.gz"
SOURCE_ROWS = MEDIAN / "source_rows.csv.gz"
HOUR_MS = giveback.H
STEP_MS = giveback.STEP
CAP_FRACTION = 0.10
TOL_ATOL_USDT = 1e-6
TOL_RTOL = 1e-8
TAKER = float(giveback.queue.repair.wallet.TAKER)
EXPECTED_PARENTS = 1585
SCENARIOS = {
    "fee_only": dict(funded=MEDIAN / "aligned/funded_median.csv.gz",
                     orders=CASH / "orders_median.csv.gz", market_slip=0.0),
    "stress_5bp": dict(funded=MEDIAN / "aligned/funded_median_stress.csv.gz",
                        orders=CASH / "orders_median_stress.csv.gz", market_slip=0.0005),
}


def _load_inputs() -> tuple[pd.DataFrame, dict, dict, pd.DataFrame, dict]:
    """Read and reconcile only the already-funded ledgers and original capacity feature."""
    with CONFIG.open() as stream:
        config = json.load(stream)
    paths = pd.read_csv(FUNDED_PATHS, dtype={"event_id": str, "parent_id": str, "symbol": str},
                        float_precision="round_trip")
    paths = paths.loc[paths.tp_r.eq(0)].set_index("event_id", verify_integrity=True)
    if paths.is_control.any():
        raise ValueError("Frozen VIX runner paths unexpectedly contain controls")
    source = pd.read_csv(SOURCE_ROWS, dtype={"event_id": str, "symbol": str},
                         float_precision="round_trip").set_index("event_id", verify_integrity=True)
    if not {"prior_status", "prior_median"}.issubset(source.columns):
        raise ValueError("Frozen original prior-minute source lacks required capacity fields")

    funds: dict[str, pd.DataFrame] = {}
    orders: dict[str, pd.DataFrame] = {}
    max_cash_error: dict[str, float] = {}
    max_unit_error: dict[str, float] = {}
    max_entry_notional_error: dict[str, float] = {}
    binding: dict[str, pd.Series] = {}
    baseline_ids: set[str] | None = None
    for label, spec in SCENARIOS.items():
        f = pd.read_csv(spec["funded"], index_col="event_id", dtype={"parent_id": str, "symbol": str},
                        float_precision="round_trip")
        if f.index.has_duplicates or len(f) != EXPECTED_PARENTS:
            raise ValueError(f"Unexpected {label} funded parent grain: {len(f)}")
        ids = set(f.index.astype(str))
        if baseline_ids is None:
            baseline_ids = ids
        elif ids != baseline_ids:
            raise ValueError("Fee-only and +5bp funded parent rosters differ")
        if not ids.issubset(paths.index) or not ids.issubset(source.index):
            raise ValueError(f"{label} ledger has an orphan event_id")
        p = paths.reindex(f.index)
        s = source.reindex(f.index)
        for key in ("entry_ms", "symbol", "side", "parent_id"):
            if key in f and not np.array_equal(f[key].to_numpy(), p[key].to_numpy()):
                raise ValueError(f"Frozen {label} funded ledger and runner path disagree on {key}")
        if not np.array_equal(f.entry_ms.to_numpy(), s.entry_ms.to_numpy()) or not np.array_equal(
                f.symbol.to_numpy(), s.symbol.to_numpy()):
            raise ValueError(f"Frozen {label} funded ledger and original capacity sources disagree")
        if not s.prior_status.eq("observed").all() or not np.isfinite(s.prior_median).all() or (s.prior_median <= 0).any():
            raise ValueError("Original entry-cap median is incomplete or nonpositive")

        o = pd.read_csv(spec["orders"], dtype={"underlying_event_id": str}, float_precision="round_trip")
        required = {"underlying_event_id", "hour", "stage", "col", "underlying_cash", "net_units", "fees"}
        if not required.issubset(o.columns):
            raise ValueError(f"{label} saved native orders lack required columns")
        o["hour"] = pd.to_numeric(o.hour, errors="raise").astype("int64")
        if o.duplicated(["underlying_event_id", "hour", "stage", "col"]).any():
            raise ValueError(f"Duplicate saved native order identity in {label}")
        if set(o.underlying_event_id) != ids:
            raise ValueError(f"{label} native order event roster differs from funded account")
        sums = o.groupby("underlying_event_id")[["underlying_cash", "net_units"]].sum().reindex(f.index)
        cash_err = (sums.underlying_cash - f.cash_pnl).abs()
        if cash_err.isna().any() or cash_err.max() > 1e-5:
            raise ValueError(f"Saved {label} order cash does not reconcile to funded parent PnL")
        entry_units = o.loc[o.stage.eq(0)].groupby("underlying_event_id").net_units.sum().reindex(f.index)
        unit_tol = np.maximum(1e-9, entry_units.abs().to_numpy() * 1e-8)
        unit_err = sums.net_units.abs().to_numpy()
        if entry_units.isna().any() or (unit_err > unit_tol).any():
            raise ValueError(f"Saved {label} native order units do not return flat at the funded exit")
        side = p.side.to_numpy(dtype=float)
        price = p.entry_price.to_numpy(dtype=float)
        reconstructed_notional = entry_units.to_numpy(dtype=float) * price * (1.0 + side * spec["market_slip"])
        entry_error = np.abs(reconstructed_notional - f.notional.to_numpy(dtype=float))
        if not np.isfinite(entry_error).all() or entry_error.max() > 1e-6:
            raise ValueError(f"Saved {label} stage-0 orders do not reconcile to actual funded entry notional")

        cap = CAP_FRACTION * s.prior_median.astype(float)
        tolerance = np.maximum(TOL_ATOL_USDT, TOL_RTOL * cap.to_numpy(dtype=float))
        binding[label] = pd.Series(np.abs(f.notional.to_numpy(dtype=float) - cap.to_numpy(dtype=float)) <= tolerance,
                                   index=f.index, name=f"cap_binding_{label}")
        max_cash_error[label] = float(cash_err.max())
        max_unit_error[label] = float(unit_err.max())
        max_entry_notional_error[label] = float(entry_error.max())
        funds[label], orders[label] = f, o

    if baseline_ids is None or len(baseline_ids) != EXPECTED_PARENTS:
        raise ValueError("No frozen funded parents loaded")
    binding_frame = pd.concat(binding.values(), axis=1)
    binding_frame["cap_binding_any"] = binding_frame.any(axis=1)
    selected = binding_frame.index[binding_frame.cap_binding_any]
    if len(selected) != config["candidate_definition"]["expected_union"]:
        raise ValueError(f"Cap-bound union changed: {len(selected)}")
    counts = {key: int(value.sum()) for key, value in binding.items()}
    if counts != {"fee_only": config["candidate_definition"]["expected_fee_only_bound"],
                  "stress_5bp": config["candidate_definition"]["expected_stress_5bp_bound"]}:
        raise ValueError(f"Cost-specific cap-bound roster changed: {counts}")
    union = pd.DataFrame(index=selected)
    for label, value in binding.items():
        union[f"cap_binding_{label}"] = value.reindex(selected).astype(bool)
        union[f"original_notional_{label}_usdt"] = funds[label].notional.reindex(selected).astype(float)
    union["cap_binding_any"] = True
    union = union.join(source[["prior_status", "prior_median", "prior_mean"]].rename(columns={
        "prior_status": "original_capacity_status", "prior_median": "prior_60m_median_usdt",
        "prior_mean": "prior_60m_mean_usdt"}))
    union = union.join(paths[["parent_id", "symbol", "side", "entry_ms", "entry_price"]].rename(columns={
        "entry_ms": "original_entry_ms", "entry_price": "original_entry_price_usdt"}))
    union["original_cap_10pct_usdt"] = CAP_FRACTION * union.prior_60m_median_usdt
    union["second_entry_ms"] = union.original_entry_ms.astype("int64") + STEP_MS
    union["second_entry_utc"] = pd.to_datetime(union.second_entry_ms, unit="ms", utc=True).astype(str)
    if (union.original_entry_ms.astype("int64") % STEP_MS != 0).any():
        raise ValueError("Frozen parents do not lie on the expected UTC 12H grid")
    source_checks = dict(
        funded_parents=EXPECTED_PARENTS,
        native_runner_paths=len(paths),
        original_source_rows=len(source),
        cap_bound_by_cost=counts,
        cap_bound_union=len(union),
        fee_only_only=int((binding_frame.cap_binding_fee_only & ~binding_frame.cap_binding_stress_5bp).sum()),
        stress_only=int((binding_frame.cap_binding_stress_5bp & ~binding_frame.cap_binding_fee_only).sum()),
        bound_in_both=int((binding_frame.cap_binding_fee_only & binding_frame.cap_binding_stress_5bp).sum()),
        max_order_cash_reconciliation_error_usdt=max_cash_error,
        max_final_residual_units=max_unit_error,
        max_entry_notional_reconciliation_error_usdt=max_entry_notional_error,
        original_capacity_status=s.prior_status.value_counts().to_dict())
    union["_event_sort_key"] = union.index.astype(str)
    union = union.sort_values(["original_entry_ms", "_event_sort_key"]).drop(columns="_event_sort_key")
    return union, funds, orders, paths, source_checks


def _bar(quotes, symbol: str, clock_ms: int) -> tuple[str, dict]:
    clock, values = quotes(symbol)
    ix = int(np.searchsorted(clock, clock_ms))
    if ix >= len(clock) or int(clock[ix]) != int(clock_ms):
        return "missing_native_h1", {}
    o, h, lo, c = map(float, values[ix, :4])
    if (not np.isfinite([o, h, lo, c]).all() or min(o, h, lo, c) <= 0 or
            lo > min(o, c) or h < max(o, c)):
        return "invalid_native_h1", {}
    return "observed", {"open": o, "high": h, "low": lo, "close": c}


def _capacity_window(symbol: str, second_entry_ms: int, owner_event_id: str, quotes) -> dict:
    """Reuse an exact symbol/clock cache; its old event_id is never the panel identity."""
    hour = int(second_entry_ms) - HOUR_MS
    cache_file = liquidity.CACHE / f"{symbol}-{hour}.json"
    cache_reused = cache_file.is_file()
    record = liquidity.prior(SimpleNamespace(event_id=owner_event_id, symbol=symbol,
                                             entry_ms=int(second_entry_ms)), quotes)
    if record.get("symbol") != symbol or int(record.get("entry_ms", -1)) != int(second_entry_ms):
        raise ValueError(f"Prior-minute cache clock mismatch for {symbol} {second_entry_ms}")
    status = str(record.get("status", "missing_status"))
    median = record.get("median_usdt")
    mean = record.get("mean_usdt")
    zero_minutes = record.get("zero_minutes")
    last_minute = record.get("last_minute_open_ms")
    minutes = 60 if status == "observed" else None
    if status == "observed":
        if median is None or mean is None or last_minute is None:
            raise ValueError(f"Observed prior-hour cache has incomplete metrics for {symbol} {second_entry_ms}")
        if not np.isfinite([median, mean]).all() or float(median) < 0 or float(mean) < 0:
            raise ValueError(f"Invalid prior-hour turnover for {symbol} {second_entry_ms}")
        if int(float(last_minute)) != int(second_entry_ms) - 60_000:
            raise ValueError(f"Prior-minute window includes a not-yet-closed candle for {symbol} {second_entry_ms}")
    return dict(
        capacity_window_id=f"{symbol}|{int(second_entry_ms)}",
        symbol=symbol,
        second_entry_ms=int(second_entry_ms),
        capacity_window_start_ms=hour,
        capacity_window_end_exclusive_ms=int(second_entry_ms),
        capacity_status=status,
        capacity_minutes=minutes,
        capacity_last_minute_open_ms=(int(float(last_minute)) if last_minute is not None else None),
        capacity_zero_minutes=(int(float(zero_minutes)) if zero_minutes is not None else None),
        prior_60m_median_usdt=(float(median) if median is not None else None),
        prior_60m_mean_usdt=(float(mean) if mean is not None else None),
        second_entry_limit_10pct_usdt=(CAP_FRACTION * float(median)
                                       if status == "observed" and median is not None else None),
        capacity_minute_source=record.get("source"),
        capacity_volume_source=record.get("volume_source"),
        capacity_cache_reused=bool(cache_reused))


def _position_snapshot(orders: pd.DataFrame, fund: pd.Series, side: int, *, decision_hour_ms: int,
                       close_price: float | None, market_slip: float, label: str) -> dict:
    fills = orders.loc[orders.hour <= int(decision_hour_ms)]
    cash = float(fills.underlying_cash.sum())
    units = float(fills.net_units.sum())
    opening = orders.loc[(orders.hour == int(fund.entry_ms)) & orders.stage.eq(0), "net_units"].sum()
    unit_tol = max(1e-12, abs(float(opening)) * 1e-9)
    if units < -unit_tol:
        raise ValueError(f"Negative remaining native units at decision for {fund.name} ({label})")
    units = 0.0 if abs(units) <= unit_tol else units
    status = "live_at_decision_close" if units > 0 else "flat_by_decision_close"
    values = dict(
        position_status=status,
        net_units_at_decision=units,
        realized_cash_at_decision_usdt=cash,
        fees_paid_through_decision_usdt=float(fills.fees.sum()),
        decision_mark_pnl_before_exit_cost_usdt=None,
        decision_net_pnl_after_costs_usdt=None,
        net_positive_after_costs=None)
    if close_price is not None:
        values["decision_mark_pnl_before_exit_cost_usdt"] = float(cash + side * units * close_price)
        liquidation_price = float(close_price * (1.0 - side * market_slip))
        values["decision_net_pnl_after_costs_usdt"] = float(
            cash + (side - TAKER) * units * liquidation_price)
        values["net_positive_after_costs"] = bool(values["decision_net_pnl_after_costs_usdt"] > 1e-9)
    return {f"{label}_{key}": value for key, value in values.items()}


def _price_reference_ids(paths: pd.DataFrame) -> dict[tuple[str, int], list[str]]:
    result: dict[tuple[str, int], list[str]] = {}
    for row in paths.itertuples():
        result.setdefault((str(row.symbol), int(row.entry_ms)), []).append(str(row.Index))
    return result


def _source_row(row: pd.Series, window: dict, close_status: str, close_bar: dict,
                open_status: str, open_bar: dict, refs: list[str]) -> dict:
    second = int(row.second_entry_ms)
    decision_open = second - HOUR_MS
    data_status = window["capacity_status"]
    if data_status == "observed" and (close_status != "observed" or open_status != "observed"):
        data_status = close_status if close_status != "observed" else open_status
    result = dict(
        event_id=str(row.name),
        parent_id=str(row.parent_id),
        symbol=str(row.symbol),
        side=int(row.side),
        original_entry_ms=int(row.original_entry_ms),
        original_entry_price_usdt=float(row.original_entry_price_usdt),
        second_entry_ms=second,
        second_entry_utc=str(row.second_entry_utc),
        decision_h1_open_ms=decision_open,
        decision_close_ms=second,
        decision_h1_close_status=close_status,
        decision_h1_close_price_usdt=(close_bar.get("close") if close_bar else None),
        second_h1_open_status=open_status,
        second_open_price_usdt=(open_bar.get("open") if open_bar else None),
        price_clock_reference_count=len(refs),
        price_clock_reference_event_ids=";".join(refs),
        original_capacity_status=str(row.original_capacity_status),
        original_prior_60m_median_usdt=float(row.prior_60m_median_usdt),
        original_cap_10pct_usdt=float(row.original_cap_10pct_usdt),
        original_notional_fee_only_usdt=float(row.original_notional_fee_only_usdt),
        original_notional_stress_5bp_usdt=float(row.original_notional_stress_5bp_usdt),
        cap_binding_fee_only=bool(row.cap_binding_fee_only),
        cap_binding_stress_5bp=bool(row.cap_binding_stress_5bp),
        cap_binding_any=bool(row.cap_binding_any),
        capacity_window_id=window["capacity_window_id"],
        capacity_window_start_ms=window["capacity_window_start_ms"],
        capacity_window_end_exclusive_ms=window["capacity_window_end_exclusive_ms"],
        capacity_status=window["capacity_status"],
        capacity_minutes=window["capacity_minutes"],
        capacity_last_minute_open_ms=window["capacity_last_minute_open_ms"],
        capacity_zero_minutes=window["capacity_zero_minutes"],
        prior_60m_median_usdt=window["prior_60m_median_usdt"],
        prior_60m_mean_usdt=window["prior_60m_mean_usdt"],
        second_entry_limit_10pct_usdt=window["second_entry_limit_10pct_usdt"],
        capacity_minute_source=window["capacity_minute_source"],
        capacity_volume_source=window["capacity_volume_source"],
        capacity_cache_reused=window["capacity_cache_reused"],
        status=data_status)
    return result


def preflight() -> dict:
    rows, _, _, _, checks = _load_inputs()
    result = dict(source_checks=checks, selected_panel_rows=len(rows),
                  output="second_window_capacity.csv.gz", account_replay=False,
                  status="preflight_ok_no_second_window_collection")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def rescore_existing() -> dict:
    """Refresh capacity/position intersections from saved panel rows; do not refetch or replay."""
    summary_path = EXP / "summary.json"
    panel_path = EXP / "second_window_capacity.csv.gz"
    receipt_path = EXP / "source_receipts.csv"
    if not summary_path.is_file() or not panel_path.is_file() or not receipt_path.is_file():
        raise FileNotFoundError("Frozen second-window result, receipts, and summary are all required")
    summary = json.loads(summary_path.read_text())
    frame = pd.read_csv(panel_path, dtype={"event_id": str, "symbol": str}, float_precision="round_trip")
    receipts = pd.read_csv(receipt_path, dtype={"symbol": str}, float_precision="round_trip")
    if (frame.event_id.duplicated().any() or len(frame) != summary["second_window"]["candidate_parent_rows"]
            or receipts.duplicated(["symbol", "second_entry_ms"]).any()
            or len(receipts) != summary["second_window"]["unique_symbol_clock_windows"]):
        raise ValueError("Saved second-window panel or source receipt grain changed")
    capacity_status = frame.capacity_status.fillna("missing_status").astype(str)
    expected_status = capacity_status.value_counts().to_dict()
    if {str(k): int(v) for k, v in expected_status.items()} != summary["second_window"]["capacity_status_counts"]:
        raise ValueError("Saved capacity statuses differ from the frozen summary")
    state, intersections = {}, {}
    for label in ("fee_only", "stress_5bp"):
        position = frame[f"{label}_position_status"].fillna("missing_status").astype(str)
        positive = frame[f"{label}_net_positive_after_costs"].fillna(False).astype(bool)
        live = position.eq("live_at_decision_close")
        flat = position.eq("flat_by_decision_close")
        live_positive = live & positive
        observed = capacity_status.eq("observed")
        state[label] = {
            "live_at_decision_close": int(live.sum()),
            "positive_net_pnl_after_costs": int(positive.sum()),
            "live_and_positive": int(live_positive.sum()),
            "flat_by_decision_close": int(flat.sum()),
        }
        intersections[label] = {
            "live_positive_with_observed_capacity": int((live_positive & observed).sum()),
            "live_positive_capacity_unverified": int((live_positive & ~observed).sum()),
            "flat_by_decision_close": int(flat.sum()),
            "positive_net_pnl_after_costs": int(positive.sum()),
        }
    non_observed = frame.loc[~capacity_status.eq("observed"), [
        "event_id", "symbol", "second_entry_ms", "capacity_status",
        "fee_only_position_status", "fee_only_net_positive_after_costs",
        "stress_5bp_position_status", "stress_5bp_net_positive_after_costs",
    ]].sort_values(["second_entry_ms", "symbol"])
    summary["second_window"]["position_and_mark_state"] = state
    summary["second_window"]["position_and_capacity_intersections"] = intersections
    summary["second_window"]["capacity_unknown_parent_rows"] = non_observed.to_dict(orient="records")
    summary["second_window"]["cache_reused_windows"] = int(receipts.capacity_cache_reused.fillna(False).astype(bool).sum())
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return summary


def run(workers: int = 4) -> dict:
    if workers < 1 or workers > 4:
        raise ValueError("Use at most four workers for the native archive cache")
    if (EXP / "summary.json").exists():
        raise FileExistsError("Completed next-window capacity source panel exists")
    with CONFIG.open() as stream:
        config = json.load(stream)
    _validate_config(config)
    panel, funds, orders, paths, checks = _load_inputs()
    EXP.mkdir(parents=True, exist_ok=True)
    quotes, _ = giveback.queue.repair.checked_quotes()
    liquidity.CACHE.mkdir(parents=True, exist_ok=True)
    refs = _price_reference_ids(paths)

    unique_windows = (panel[["symbol", "second_entry_ms"]].drop_duplicates()
                      .sort_values(["second_entry_ms", "symbol"]).to_records(index=False))
    def collect(item):
        symbol, clock = str(item[0]), int(item[1])
        owner_event_id = str(panel.loc[(panel.symbol.eq(symbol)) & panel.second_entry_ms.eq(clock)].index[0])
        for attempt in range(3):
            try:
                return _capacity_window(symbol, clock, owner_event_id, quotes)
            except (requests.RequestException, BadZipFile, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    windows = {}
    receipts = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, record in enumerate(pool.map(collect, unique_windows), 1):
            key = (record["symbol"], record["second_entry_ms"])
            if key in windows:
                raise ValueError(f"Duplicate minute source task: {key}")
            windows[key] = record
            receipts.append(record)
            if i % 100 == 0 or i == len(unique_windows):
                print(f"validated unique second-window minute sources {i}/{len(unique_windows)}", flush=True)

    output = []
    decision_statuses = {name: {"live_at_decision_close": 0, "positive_net_pnl_after_costs": 0,
                                "live_and_positive": 0, "flat_by_decision_close": 0}
                         for name in SCENARIOS}
    for event_id, row in panel.iterrows():
        symbol, second = str(row.symbol), int(row.second_entry_ms)
        close_status, close_bar = _bar(quotes, symbol, second - HOUR_MS)
        open_status, open_bar = _bar(quotes, symbol, second)
        reference_ids = refs.get((symbol, second), [])
        if open_status == "observed":
            for ref_id in reference_ids:
                ref_price = float(paths.at[ref_id, "entry_price"])
                if not np.isclose(ref_price, open_bar["open"], rtol=1e-8, atol=1e-12):
                    raise ValueError(f"Same-symbol/time frozen path disagrees with native H1 open: {ref_id}")
        record = _source_row(row, windows[(symbol, second)], close_status, close_bar,
                             open_status, open_bar, reference_ids)
        decision_hour = second - HOUR_MS
        for label, spec in SCENARIOS.items():
            fund = funds[label].loc[event_id]
            order_rows = orders[label].loc[orders[label].underlying_event_id.eq(event_id)]
            snapshot = _position_snapshot(order_rows, fund, int(row.side),
                                          decision_hour_ms=decision_hour,
                                          close_price=(close_bar.get("close") if close_status == "observed" else None),
                                          market_slip=float(spec["market_slip"]), label=label)
            record.update(snapshot)
            state = decision_statuses[label]
            if snapshot[f"{label}_position_status"] == "live_at_decision_close":
                state["live_at_decision_close"] += 1
                if snapshot[f"{label}_net_positive_after_costs"]:
                    state["live_and_positive"] += 1
            else:
                state["flat_by_decision_close"] += 1
            if snapshot[f"{label}_net_positive_after_costs"]:
                state["positive_net_pnl_after_costs"] += 1
        output.append(record)

    frame = pd.DataFrame(output)
    if len(frame) != len(panel) or frame.event_id.duplicated().any() or set(frame.event_id) != set(panel.index):
        raise ValueError("Second-window panel failed one-row-per-original-parent reconciliation")
    source_file = EXP / "second_window_capacity.csv.gz"
    temp_file = source_file.with_name(source_file.stem + ".tmp.gz")
    frame.to_csv(temp_file, index=False, compression="gzip", float_format="%.17g")
    temp_file.replace(source_file)
    pd.DataFrame(receipts).sort_values(["second_entry_ms", "symbol"]).to_csv(
        EXP / "source_receipts.csv", index=False, float_format="%.17g")

    statuses = frame.status.value_counts(dropna=False).to_dict()
    capacity_status = frame.capacity_status.value_counts(dropna=False).to_dict()
    close_status = frame.decision_h1_close_status.value_counts(dropna=False).to_dict()
    open_status = frame.second_h1_open_status.value_counts(dropna=False).to_dict()
    summary = dict(
        scope="Source and as-of decision-state panel for the union of original fee-only / +5bp parents whose first funded notional actually bound the 10% original prior-60-minute median cap. No new order or account simulation.",
        source_checks=checks,
        second_window=dict(
            candidate_parent_rows=len(frame),
            unique_symbol_clock_windows=len(receipts),
            window_definition="Exactly 60 confirmed native OKX 1m bars opening in [second_entry_ms - 60 minutes, second_entry_ms); native minute source validates against the H1 bar ending at second_entry_ms.",
            cache_reused_windows=int(sum(bool(x["capacity_cache_reused"]) for x in receipts)),
            capacity_status_counts={str(k): int(v) for k, v in capacity_status.items()},
            combined_status_counts={str(k): int(v) for k, v in statuses.items()},
            decision_h1_close_status_counts={str(k): int(v) for k, v in close_status.items()},
            second_h1_open_status_counts={str(k): int(v) for k, v in open_status.items()},
            position_and_mark_state=decision_statuses),
        timing_policy=("The decision snapshot consumes fills through the H1 bar that closes at second_entry_ms. "
                       "It does not consume fills at the following H1 open; downstream account logic must preserve its existing same-open sequence."),
        limitations=["Prior 60m quote volume is a turnover proxy, not order-book depth or guaranteed fill capacity.",
                     "The second open's price is read at its native H1 timestamp; it is not treated as a new event identity.",
                     "The saved source panel includes all cap-bound rows, including flat or nonpositive decision states. No later exit or outcome is used for candidate filtering."],
        artifacts={"candidate_panel": str(source_file.relative_to(ROOT)),
                   "unique_source_receipts": str((EXP / "source_receipts.csv").relative_to(ROOT))})
    (EXP / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                  allow_nan=False) + "\n")
    print(json.dumps(summary["second_window"], ensure_ascii=False, indent=2), flush=True)
    return summary


def _validate_config(config: dict) -> None:
    if config.get("candidate_definition", {}).get("expected_union") != 1064:
        raise ValueError("Frozen cap-bound union expectation changed")
    if config.get("decision_and_capacity_clock", {}).get("second_entry_ms") != "original_entry_ms + 12 hours":
        raise ValueError("Next-window decision clock changed")


def main() -> None:
    parser = argparse.ArgumentParser()
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--preflight", action="store_true")
    command.add_argument("--run", action="store_true")
    command.add_argument("--rescore-existing", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.rescore_existing:
        print(json.dumps(rescore_existing()["second_window"], ensure_ascii=False, indent=2))
    else:
        run(args.workers)


if __name__ == "__main__":
    main()
