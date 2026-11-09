"""Attribute frozen aligned median-cap OKX cash flows to their actual native child exits.

This reads the already funded parent ledger and native VectorBT order log. It
does not run a portfolio. Entry fees are allocated to realized exit quantities
at each child's actual fill; exit fees are already included in native cash.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_cash_giveback as frozen


ROOT = frozen.queue.repair.wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-target-leg-attribution-20261109-v1"
CONFIG = EXP / "config.json"
FUNDED_PATH = frozen.SOURCE / "aligned/funded_median.csv.gz"
HOURLY_PATH = frozen.SOURCE / "aligned/hourly_median.csv.gz"
ORDERS_PATH = frozen.EXP / "orders_median.csv.gz"
LEGACY_DIRECTORY_DOUBLINGS_PATH = (
    ROOT / "experiments/active/exp-cloud-close-double-directory-20261109-v1/doubling_fills.csv"
)
LEGACY_DOUBLE_LIMITS_PATH = (
    ROOT / "experiments/active/exp-cloud-close-double-limit-20261109-v1/doublings_OKX.csv"
)
CHILD_NAMES = {
    0: "target_2R_double_candidate",
    1: "target_2R_native",
    2: "runner_double_candidate",
    3: "runner_native",
}
STAGE_NAMES = {
    0: "entry",
    1: "open_exit",
    2: "hourly_single_symbol_trim",
    3: "intrabar_or_close_exit",
}
PERIODS = {
    "through_2024": (None, 1_735_689_600_000),
    "2025": (1_735_689_600_000, 1_767_225_600_000),
    "2026": (1_767_225_600_000, None),
}
REQUIRED_ORDERS = {
    "underlying_event_id", "hour", "stage", "col", "underlying_cash", "net_units", "fees"
}
REQUIRED_FUNDED = {"event_id", "entry_ms", "notional", "cash_pnl"}
PATH_FIELDS = [
    "event_id", "parent_id", "slot", "symbol", "side", "tp_r", "entry_ms",
    "exit_bar_ms", "entry_price", "exit_price", "stop_price", "exit_fee_rate",
    "exit_reason", "exit_phase", "same_bar_ambiguous", "gap_flag",
]
TOL = 1e-8


def _read_inputs():
    """Reassemble only frozen native child paths and join saved actual orders."""
    data, _market, _quotes, _parents, _caps, _ratios = frozen.account()
    if not CONFIG.is_file():
        raise FileNotFoundError(CONFIG)
    funded = pd.read_csv(FUNDED_PATH, dtype={"event_id": str}, float_precision="round_trip")
    orders = pd.read_csv(ORDERS_PATH, dtype={"underlying_event_id": str}, float_precision="round_trip")
    if not REQUIRED_FUNDED.issubset(funded.columns) or not REQUIRED_ORDERS.issubset(orders.columns):
        raise ValueError("Frozen funded ledger or native order-log schema changed")
    if funded.event_id.duplicated().any():
        raise ValueError("Frozen funded parent ledger has duplicate event IDs")
    if orders.duplicated(["underlying_event_id", "hour", "stage", "col"]).any():
        raise ValueError("Native order log has duplicate event/hour/stage/column keys")
    if not orders.stage.isin(STAGE_NAMES).all():
        raise ValueError("Unexpected native VectorBT order stage")

    selected = data["selected"].copy()
    missing_path_fields = set(PATH_FIELDS) - set(selected.columns)
    if missing_path_fields:
        raise ValueError(f"Native child path schema missing: {sorted(missing_path_fields)}")
    selected["slot"] = pd.to_numeric(selected.slot, errors="raise").astype("int64")
    selected["child"] = selected.slot.mod(4).astype("int64")
    selected["child_origin"] = selected.child.map(CHILD_NAMES)
    if selected.child_origin.isna().any():
        raise ValueError("Native four-child slot mapping changed")

    funded_ids = set(funded.event_id)
    order_ids = set(orders.underlying_event_id)
    if funded_ids != order_ids:
        raise ValueError(
            f"Frozen parent/order roster differs: funded-only={len(funded_ids-order_ids)}, "
            f"orders-only={len(order_ids-funded_ids)}"
        )
    children = selected[selected.event_id.isin(funded_ids)].copy()
    if set(children.event_id) != funded_ids or len(children) != 4 * len(funded):
        raise ValueError("Frozen native paths do not have four selected children per funded parent")
    if children.duplicated(["event_id", "slot"]).any():
        raise ValueError("Native selected child path key is not unique")
    child_counts = children.groupby("event_id").child.nunique()
    if not child_counts.eq(4).all():
        raise ValueError("A funded parent is missing a native child origin")
    tp_by_child = children.groupby("child").tp_r.agg(lambda x: set(x.astype(float))).to_dict()
    if tp_by_child != {0: {2.0}, 1: {2.0}, 2: {0.0}, 3: {0.0}}:
        raise ValueError(f"Native target/runner child routing changed: {tp_by_child}")

    fills = orders.merge(
        children[PATH_FIELDS + ["child", "child_origin"]],
        left_on=["underlying_event_id", "col"],
        right_on=["event_id", "slot"],
        how="left",
        validate="many_to_one",
    )
    if fills.symbol.isna().any() or len(fills) != len(orders):
        raise ValueError("Native fills do not map one-to-one to the frozen actual child paths")
    if not np.array_equal(fills.underlying_event_id.to_numpy(), fills.event_id.to_numpy()):
        raise ValueError("Order event IDs changed in child-path join")

    hourly = pd.read_csv(HOURLY_PATH, index_col=0, parse_dates=True, float_precision="round_trip")
    if "nav" not in hourly or hourly.empty:
        raise ValueError("Frozen median-cap hourly account is unavailable")
    return funded.set_index("event_id", verify_integrity=True), orders, children, fills, hourly, selected


def _exit_type(row):
    reason = str(row.exit_reason)
    if reason.startswith("take_profit"):
        price = float(row.exit_price)
        entry = float(row.entry_price)
        side = int(row.side)
        risk = abs(entry - float(row.stop_price))
        r2 = entry + side * 2.0 * risk
        double = 2.0 * entry
        is_double_candidate = int(row.child) in (0, 2) and side == 1
        at_double = is_double_candidate and np.isclose(price, double, rtol=1e-9, atol=1e-12)
        at_r2 = int(row.tp_r) == 2 and np.isclose(price, r2, rtol=1e-9, atol=1e-12)
        if at_double and at_r2:
            return "absolute_2x_and_relative_2R_coincident"
        if at_double:
            return "absolute_2x_entry_target"
        if at_r2:
            return "relative_plus_2R_target"
        if int(row.tp_r) == 0 and np.isclose(price, entry, rtol=1e-9, atol=1e-12):
            return "zero_R_take_profit"
        return "unclassified_take_profit"
    if "ema_flip" in reason:
        return "cloud_exit"
    if "stop" in reason:
        return "protective_stop"
    if reason == "delisting_settlement":
        return "delisting_settlement"
    if reason == "series_tail_close":
        return "series_tail_close"
    if reason in {"data_gap_markout", "stale_12h_markout", "reclaim_failed_at_close"}:
        return reason
    return "other_native_exit"


def _actual_fill_prices(fills):
    out = fills.copy()
    out["fill_notional"] = (out.underlying_cash + out.fees).abs()
    out["fill_units"] = out.net_units.abs()
    if out.fill_units.le(0).any() or not np.isfinite(out.fill_notional).all():
        raise ValueError("Native order contains a zero-unit or non-finite fill")
    out["actual_fill_price"] = out.fill_notional / out.fill_units
    out["stage_name"] = out.stage.map(STAGE_NAMES)
    return out


def _attribute(funded, fills, *, sample_ids=None):
    """Allocate each child opening cash basis by actual closed units."""
    actual_ids = set(funded.index) if sample_ids is None else set(sample_ids)
    part = fills[fills.underlying_event_id.isin(actual_ids)].copy()
    part = _actual_fill_prices(part)
    part["realized_net_pnl"] = 0.0
    part["realized_gross_pnl"] = 0.0
    part["allocated_entry_cash"] = 0.0
    part["allocated_entry_fee"] = 0.0
    part["closed_fraction_of_child"] = 0.0
    part["exit_type"] = "entry"
    part["fill_role"] = np.where(part.net_units.gt(0), "entry", "closing_fill")

    if (part.loc[part.stage.eq(0), "net_units"] <= 0).any():
        raise ValueError("Stage-zero fill is not an opening quantity")
    if (part.loc[part.stage.eq(2), "net_units"] >= 0).any():
        raise ValueError("Stage-two trim is not a quantity reduction")
    if part.loc[part.stage.eq(2), "stage_name"].ne("hourly_single_symbol_trim").any():
        raise ValueError("Stage-two trim label changed")

    child_rows = []
    for (event_id, slot), group in part.groupby(["event_id", "slot"], sort=False):
        group = group.sort_values(["hour", "stage"], kind="mergesort")
        opening = group[(group.stage == 0) & group.net_units.gt(0)]
        closing = group[group.net_units.lt(0)]
        if len(opening) != 1 or closing.empty:
            raise ValueError(f"Expected one opening and one or more closes for {event_id}/{slot}")
        initial_units = float(opening.net_units.sum())
        opening_cash = float(opening.underlying_cash.sum())
        opening_fees = float(opening.fees.sum())
        if initial_units <= 0:
            raise ValueError(f"Nonpositive opening units for {event_id}/{slot}")

        running_units = 0.0
        realized_net = 0.0
        realized_gross = 0.0
        trim_units = 0.0
        terminal_units = 0.0
        for ix, row in group.iterrows():
            if row.net_units > 0:
                running_units += float(row.net_units)
                continue
            close_units = -float(row.net_units)
            tol = max(1e-10, initial_units * 1e-9)
            if close_units - running_units > tol:
                raise ValueError(f"Child sold more units than it held: {event_id}/{slot}")
            fraction = close_units / initial_units
            allocated_cash = opening_cash * fraction
            allocated_entry_fee = opening_fees * fraction
            net_pnl = float(row.underlying_cash) + allocated_cash
            gross_pnl = net_pnl + allocated_entry_fee + float(row.fees)
            if int(row.stage) == 2:
                exit_type = "hourly_single_symbol_trim"
                fill_role = "partial_exit"
                trim_units += close_units
            elif int(row.stage) in (1, 3):
                exit_type = _exit_type(row)
                fill_role = "terminal_exit"
                terminal_units += close_units
            else:
                raise ValueError(f"Unexpected closing stage {row.stage} for {event_id}/{slot}")
            part.at[ix, "realized_net_pnl"] = net_pnl
            part.at[ix, "realized_gross_pnl"] = gross_pnl
            part.at[ix, "allocated_entry_cash"] = allocated_cash
            part.at[ix, "allocated_entry_fee"] = allocated_entry_fee
            part.at[ix, "closed_fraction_of_child"] = fraction
            part.at[ix, "exit_type"] = exit_type
            part.at[ix, "fill_role"] = fill_role
            realized_net += net_pnl
            realized_gross += gross_pnl
            running_units -= close_units

        residual = float(group.net_units.sum())
        if abs(residual) > max(1e-9, initial_units * 1e-8):
            raise ValueError(f"Child units do not reconcile for {event_id}/{slot}: {residual}")
        order_cash = float(group.underlying_cash.sum())
        if not np.isclose(realized_net, order_cash, rtol=1e-10, atol=1e-8):
            raise ValueError(f"Allocated child cash differs for {event_id}/{slot}: {realized_net} vs {order_cash}")
        meta = group.iloc[0]
        terminal = group[group.stage.isin([1, 3])]
        if len(terminal) != 1:
            raise ValueError(f"Expected one terminal fill per child: {event_id}/{slot}")
        terminal = terminal.iloc[0]
        expected_stage = 1 if terminal.exit_phase == "open" else 3
        if int(terminal.stage) != expected_stage:
            raise ValueError(
                f"Terminal stage/phase mismatch for {event_id}/{slot}: "
                f"stage={terminal.stage}, phase={terminal.exit_phase}"
            )
        if not np.isclose(terminal.actual_fill_price, terminal.exit_price, rtol=1e-8, atol=1e-10):
            raise ValueError(f"Native terminal execution price differs from selected child path: {event_id}/{slot}")
        child_rows.append({
            "event_id": event_id,
            "parent_id": meta.parent_id,
            "symbol": meta.symbol,
            "side": int(meta.side),
            "entry_ms": int(meta.entry_ms),
            "slot": int(slot),
            "child": int(meta.child),
            "child_origin": meta.child_origin,
            "tp_r": float(meta.tp_r),
            "entry_price": float(meta.entry_price),
            "stop_price": float(meta.stop_price),
            "initial_units": initial_units,
            "initial_notional_usdt": float(opening.fill_notional.sum()),
            "entry_cash_including_fee": opening_cash,
            "entry_fee_usdt": opening_fees,
            "trimmed_units": trim_units,
            "terminal_units": terminal_units,
            "closed_units": trim_units + terminal_units,
            "remaining_units": residual,
            "terminal_exit_bar_ms": int(terminal.hour),
            "terminal_exit_phase": terminal.exit_phase,
            "terminal_exit_reason": terminal.exit_reason,
            "terminal_exit_price": float(terminal.actual_fill_price),
            "terminal_exit_type": _exit_type(terminal),
            "realized_net_pnl_usdt": realized_net,
            "realized_gross_pnl_usdt": realized_gross,
            "order_cash_usdt": order_cash,
            "cash_error_usdt": realized_net - order_cash,
        })

    attr = part.sort_values(["event_id", "hour", "stage", "col"], kind="mergesort").reset_index(drop=True)
    child = pd.DataFrame(child_rows)
    return attr, child


def _parent_reconciliation(funded, attr, child):
    order_cash = attr.groupby("event_id", sort=False).underlying_cash.sum().reindex(funded.index)
    realized = attr.groupby("event_id", sort=False).realized_net_pnl.sum().reindex(funded.index)
    opened = child.groupby("event_id", sort=False).initial_units.sum().reindex(funded.index)
    closed = child.groupby("event_id", sort=False).closed_units.sum().reindex(funded.index)
    residual = child.groupby("event_id", sort=False).remaining_units.sum().reindex(funded.index)
    child_notional = child.groupby("event_id", sort=False).initial_notional_usdt.sum().reindex(funded.index)
    rows = pd.DataFrame(index=funded.index)
    rows["parent_id"] = funded.parent_id if "parent_id" in funded else funded.index.to_series().str.split(":").str[0]
    rows["symbol"] = funded.symbol if "symbol" in funded else None
    rows["side"] = funded.side if "side" in funded else None
    rows["entry_ms"] = pd.to_numeric(funded.entry_ms, errors="raise").astype("int64")
    rows["funded_notional_usdt"] = pd.to_numeric(funded.notional, errors="raise")
    rows["native_child_initial_notional_usdt"] = child_notional
    rows["initial_notional_error_usdt"] = child_notional - rows["funded_notional_usdt"]
    rows["funded_cash_pnl_usdt"] = pd.to_numeric(funded.cash_pnl, errors="raise")
    rows["order_cash_usdt"] = order_cash
    rows["allocated_realized_net_pnl_usdt"] = realized
    rows["cash_error_usdt"] = rows.order_cash_usdt - rows.funded_cash_pnl_usdt
    rows["allocation_error_usdt"] = rows.allocated_realized_net_pnl_usdt - rows.funded_cash_pnl_usdt
    rows["initial_units"] = opened
    rows["closed_units"] = closed
    rows["remaining_units"] = residual
    rows.index.name = "event_id"
    out = rows.reset_index()
    out["entry_utc"] = pd.to_datetime(out.entry_ms, unit="ms", utc=True).astype(str)
    return out


def _cohort_ids(parents, top10):
    entry = pd.to_numeric(parents.entry_ms, errors="raise").astype("int64")
    ids = set(parents.index)
    result = {"all": ids, "through_2024": set(), "2025": set(), "2026": set(),
              "2025_plus": set(), "top10_cash_winners": set(top10)}
    for event_id, stamp in entry.items():
        if stamp < PERIODS["through_2024"][1]:
            result["through_2024"].add(event_id)
        elif stamp < PERIODS["2026"][0]:
            result["2025"].add(event_id)
        else:
            result["2026"].add(event_id)
    result["2025_plus"] = result["2025"] | result["2026"]
    return result


def _summaries(parents, attr, child):
    winners = (parents.reset_index().sort_values(
        ["cash_pnl", "event_id"], ascending=[False, True], kind="mergesort"
    ).set_index("event_id").head(10))
    cohorts = _cohort_ids(parents, set(winners.index))
    category_rows, origin_rows = [], []
    category_totals = {}
    for cohort, ids in cohorts.items():
        p = parents.loc[sorted(ids)]
        f = attr[attr.event_id.isin(ids) & attr.net_units.lt(0)]
        c = child[child.event_id.isin(ids)]
        initial_units_by_parent = c.groupby("event_id", sort=False).initial_units.sum().reindex(p.index)
        total_parent_notional = float(p.notional.sum())
        if set(p.index) != ids:
            raise ValueError(f"Cohort parent set changed for {cohort}")
        category_summary = {}
        for typ, group in f.groupby("exit_type", sort=True):
            category_units_by_parent = group.groupby("event_id", sort=False).fill_units.sum().reindex(p.index, fill_value=0.0)
            parent_fraction = category_units_by_parent / initial_units_by_parent
            mean_parent_fraction = float(parent_fraction.mean())
            notional_weighted_parent_fraction = (
                float((parent_fraction * p.notional).sum() / total_parent_notional)
                if total_parent_notional else 0.0
            )
            vals = {
                "cohort": cohort,
                "exit_type": typ,
                "funded_parents": int(len(ids)),
                "parents_with_exit_type": int(group.event_id.nunique()),
                "exit_fills": int(len(group)),
                "mean_parent_fraction_of_initial_units": mean_parent_fraction,
                "entry_notional_weighted_parent_fraction_of_initial_units": notional_weighted_parent_fraction,
                "exit_notional_usdt": float(group.fill_notional.sum()),
                "allocated_entry_fees_usdt": float(group.allocated_entry_fee.sum()),
                "exit_fees_usdt": float(group.fees.sum()),
                "realized_net_pnl_usdt": float(group.realized_net_pnl.sum()),
                "realized_gross_pnl_usdt": float(group.realized_gross_pnl.sum()),
            }
            category_rows.append(vals)
            category_summary[typ] = {
                "parents": vals["parents_with_exit_type"],
                "fills": vals["exit_fills"],
                "mean_parent_fraction_of_initial_units": vals["mean_parent_fraction_of_initial_units"],
                "entry_notional_weighted_parent_fraction_of_initial_units": vals[
                    "entry_notional_weighted_parent_fraction_of_initial_units"
                ],
                "realized_net_pnl_usdt": vals["realized_net_pnl_usdt"],
                "realized_gross_pnl_usdt": vals["realized_gross_pnl_usdt"],
                "allocated_entry_fees_usdt": vals["allocated_entry_fees_usdt"],
                "exit_fees_usdt": vals["exit_fees_usdt"],
            }
        category_totals[cohort] = {
            "funded_parents": len(ids),
            "frozen_parent_cash_pnl_usdt": float(p.cash_pnl.sum()),
            "allocated_realized_net_pnl_usdt": float(f.realized_net_pnl.sum()),
            "initial_notional_usdt": float(p.notional.sum()),
            "exit_types": category_summary,
        }
        for origin, group in c.groupby("child_origin", sort=True):
            trim_fraction = group.trimmed_units / group.initial_units
            terminal_fraction = group.terminal_units / group.initial_units
            total_closed_fraction = group.closed_units / group.initial_units
            origin_notional = float(group.initial_notional_usdt.sum())
            origin_rows.append({
                "cohort": cohort,
                "child_origin": origin,
                "child_positions": int(len(group)),
                "initial_notional_usdt": origin_notional,
                "mean_child_trim_fraction": float(trim_fraction.mean()),
                "mean_child_terminal_exit_fraction": float(terminal_fraction.mean()),
                "mean_child_total_closed_fraction": float(total_closed_fraction.mean()),
                "entry_notional_weighted_child_trim_fraction": float(
                    (trim_fraction * group.initial_notional_usdt).sum() / origin_notional
                ) if origin_notional else 0.0,
                "entry_notional_weighted_child_terminal_exit_fraction": float(
                    (terminal_fraction * group.initial_notional_usdt).sum() / origin_notional
                ) if origin_notional else 0.0,
                "entry_notional_weighted_child_total_closed_fraction": float(
                    (total_closed_fraction * group.initial_notional_usdt).sum() / origin_notional
                ) if origin_notional else 0.0,
                "realized_net_pnl_usdt": float(group.realized_net_pnl_usdt.sum()),
                "realized_gross_pnl_usdt": float(group.realized_gross_pnl_usdt.sum()),
            })
    return pd.DataFrame(category_rows), pd.DataFrame(origin_rows), category_totals, winners


def analyze(*, smoke=False):
    funded, orders, children, fills, hourly, all_selected = _read_inputs()
    attr, child_attr = _attribute(funded, fills)
    parent = _parent_reconciliation(funded, attr, child_attr)
    category, origin, totals, winners = _summaries(funded, attr, child_attr)

    open_rows = attr[attr.stage.eq(0)]
    trim_rows = attr[attr.stage.eq(2)]
    terminal_rows = attr[attr.stage.isin([1, 3])]
    path_children = children.copy()
    path_children["r2_price"] = path_children.entry_price + path_children.side * 2 * (
        path_children.entry_price - path_children.stop_price
    ).abs()
    path_children["double_price"] = 2 * path_children.entry_price
    path_children["absolute_double_candidate"] = path_children.child.isin([0, 2]) & path_children.side.eq(1)
    path_children["absolute_double_exit"] = (
        path_children.absolute_double_candidate
        & path_children.exit_reason.astype(str).str.startswith("take_profit")
        & np.isclose(path_children.exit_price, path_children.double_price, rtol=1e-9, atol=1e-12)
    )
    path_children["relative_2R_exit"] = (
        path_children.tp_r.eq(2)
        & path_children.exit_reason.astype(str).str.startswith("take_profit")
        & np.isclose(path_children.exit_price, path_children.r2_price, rtol=1e-9, atol=1e-12)
    )
    # All actual-path child templates, including events not funded under the cap,
    # remain available in the existing native assembly. Only funded orders are
    # attributed below.
    all_selected = all_selected.copy()
    all_selected["child"] = all_selected.slot.mod(4)
    all_selected["absolute_double_candidate"] = all_selected.child.isin([0, 2]) & all_selected.side.eq(1)
    all_selected["absolute_double_exit"] = (
        all_selected.absolute_double_candidate
        & all_selected.exit_reason.astype(str).str.startswith("take_profit")
        & np.isclose(all_selected.exit_price, 2 * all_selected.entry_price, rtol=1e-9, atol=1e-12)
    )
    double_template_rows = all_selected[all_selected.absolute_double_exit]
    template_events = set(double_template_rows.event_id)
    funded_double_rows = path_children[path_children.absolute_double_exit]

    def legacy_roster(path):
        if not path.is_file():
            return {"path": str(path.relative_to(ROOT)), "available": False}
        legacy = pd.read_csv(path, usecols=["event_id"], dtype={"event_id": str})
        events = set(legacy.event_id)
        actual = set(funded_double_rows.event_id)
        return {
            "path": str(path.relative_to(ROOT)),
            "available": True,
            "rows": int(len(legacy)),
            "unique_events": int(len(events)),
            "funded_roster_intersection": int(len(events & set(funded.index))),
            "intersection_with_actual_funded_absolute_2x_paths": int(len(events & actual)),
            "used_to_filter_attribution": False,
        }

    legacy_roster_comparison = {
        "double_directory_fills": legacy_roster(LEGACY_DIRECTORY_DOUBLINGS_PATH),
        "double_limit_saved_routes": legacy_roster(LEGACY_DOUBLE_LIMITS_PATH),
        "actual_current_native_selected_paths": {
            "all_candidate_parent_events": int(all_selected.loc[
                all_selected.child.isin([0, 2]) & all_selected.side.eq(1)
                & all_selected.exit_reason.astype(str).str.startswith("take_profit")
                & np.isclose(all_selected.exit_price, 2 * all_selected.entry_price, rtol=1e-9, atol=1e-12),
                "event_id"
            ].nunique()),
            "funded_parent_events": int(funded_double_rows.event_id.nunique()),
            "actual_funded_child_exits": int(len(funded_double_rows)),
            "selection_source": "cash-giveback account adapter selected paths joined to actual native VectorBT fills",
        },
    }

    cash_abs = float(parent.cash_error_usdt.abs().max())
    pnl_abs = float(parent.allocation_error_usdt.abs().max())
    units_abs = float(parent.remaining_units.abs().max())
    units_rel = float((parent.remaining_units.abs() / parent.initial_units).max())
    notional_abs = float(parent.initial_notional_error_usdt.abs().max())
    if cash_abs > 1e-6 or pnl_abs > 1e-6 or units_rel > 1e-8 or notional_abs > 1e-6:
        raise ValueError(
            f"Parent reconciliation failed: cash={cash_abs}, pnl={pnl_abs}, "
            f"units_abs={units_abs}, units_rel={units_rel}, notional={notional_abs}"
        )
    actual_log_final = 1000.0 + float(parent.funded_cash_pnl_usdt.sum())
    if not np.isclose(actual_log_final, float(hourly.nav.iloc[-1]), rtol=1e-9, atol=0.001):
        raise ValueError("Frozen parent cash does not reconcile to the frozen hourly account terminal NAV")
    all_category_pnl = float(category.loc[category.cohort.eq("all"), "realized_net_pnl_usdt"].sum())
    if not np.isclose(all_category_pnl, float(parent.funded_cash_pnl_usdt.sum()),
                      rtol=1e-10, atol=1e-6):
        raise ValueError("Mutually exclusive category realized PnL does not sum to parent cash")
    fraction_sums = category.groupby("cohort")[
        ["mean_parent_fraction_of_initial_units", "entry_notional_weighted_parent_fraction_of_initial_units"]
    ].sum()
    if not np.allclose(fraction_sums.to_numpy(), 1.0, rtol=1e-8, atol=1e-8):
        raise ValueError(f"Exit-category quantity fractions do not reconcile to fully closed positions: {fraction_sums}")
    if len(open_rows) != 4 * len(funded) or len(terminal_rows) != 4 * len(funded):
        raise ValueError("Expected four opening and four terminal child fills for each funded parent")
    if len(trim_rows) + len(open_rows) + len(terminal_rows) != len(orders):
        raise ValueError("Native order stages do not partition into opening, trim and terminal fills")

    stats = {
        "frozen_source": {
            "funded_ledger": str(FUNDED_PATH.relative_to(ROOT)),
            "native_order_log": str(ORDERS_PATH.relative_to(ROOT)),
            "candidate_paths": str((frozen.SOURCE.parent / "exp-cloud-close-vix-priority-20261109-v1/aligned/selected_paths.csv.gz").relative_to(ROOT)),
            "cash_giveback_source": str(frozen.__file__.replace(str(ROOT) + "/", "")),
        },
        "input_counts": {
            "funded_parents": int(len(funded)),
            "candidate_parents_after_native_assembly": int(all_selected.event_id.nunique()),
            "unfunded_candidate_parents": int(all_selected.event_id.nunique() - len(funded)),
            "funded_native_child_paths": int(len(children)),
            "native_order_rows": int(len(orders)),
            "opening_fills": int(len(open_rows)),
            "stage1_open_exit_fills": int((terminal_rows.stage == 1).sum()),
            "stage2_trim_fills": int(len(trim_rows)),
            "stage3_intrabar_or_close_exit_fills": int((terminal_rows.stage == 3).sum()),
            "terminal_child_fills": int(len(terminal_rows)),
            "absolute_2x_child_templates_all_candidates": int(len(double_template_rows)),
            "absolute_2x_candidate_parent_events_all_candidates": int(len(template_events)),
            "absolute_2x_child_exits_funded": int(len(funded_double_rows)),
            "absolute_2x_parent_events_funded": int(funded_double_rows.event_id.nunique()),
            "absolute_2x_exits_by_child_origin": funded_double_rows.child_origin.value_counts().to_dict(),
            "relative_2R_target_exits_funded": int(path_children.relative_2R_exit.sum()),
            "relative_2R_parent_events_funded": int(path_children.loc[path_children.relative_2R_exit, "event_id"].nunique()),
        },
        "double_route_roster_crosscheck": legacy_roster_comparison,
        "reconciliation": {
            "maximum_parent_native_order_cash_error_usdt": cash_abs,
            "maximum_parent_allocated_realized_cash_error_usdt": pnl_abs,
            "maximum_parent_remaining_units_abs": units_abs,
            "maximum_parent_remaining_units_fraction": units_rel,
            "maximum_parent_initial_notional_error_usdt": notional_abs,
            "orders_match_frozen_cash_pnl": bool(cash_abs <= 1e-6),
            "allocated_exit_cash_matches_frozen_cash_pnl": bool(pnl_abs <= 1e-6),
            "1000u_plus_frozen_cash_pnl_usdt": actual_log_final,
            "frozen_hourly_terminal_nav_usdt": float(hourly.nav.iloc[-1]),
            "hourly_terminal_nav_error_usdt": actual_log_final - float(hourly.nav.iloc[-1]),
            "sum_of_all_cohort_category_realized_net_pnl_usdt": all_category_pnl,
            "sum_of_frozen_parent_cash_pnl_usdt": float(parent.funded_cash_pnl_usdt.sum()),
            "exit_quantity_fraction_sums_by_cohort": fraction_sums.to_dict(orient="index"),
        },
        "periods": totals,
        "top10_cash_winners": {
            "selection": "Frozen funded cash_pnl descending; event_id ascending for ties",
            "event_ids": winners.index.tolist(),
            "frozen_cash_pnl_usdt": float(winners.cash_pnl.sum()),
            "share_of_all_parent_net_cash_pnl": float(winners.cash_pnl.sum() / parent.funded_cash_pnl_usdt.sum()),
            "attribution": totals["top10_cash_winners"],
        },
        "fee_and_exit_checks": {
            "terminal_fills_match_selected_path_price": bool(np.isclose(
                terminal_rows.actual_fill_price, terminal_rows.exit_price, rtol=1e-8, atol=1e-10).all()),
            "classified_take_profit_other": int((terminal_rows.exit_type == "unclassified_take_profit").sum()),
            "absolute_2x_vs_2R_coincident_child_exits": int((terminal_rows.exit_type == "absolute_2x_and_relative_2R_coincident").sum()),
        },
        "runtime": {"git_head": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                      capture_output=True, text=True, check=True).stdout.strip()},
        "limitations": [
            "Retrospective realized accounting attribution only; no independent wallet or hypothetical cash simulation.",
            "The 2026 entry cohort is partial year-to-date in the frozen source.",
            "No historical funding payments or additional market impact are added.",
            "Actual child exit path and fill price are read from cash-giveback's reassembled native four-child paths and the frozen native order log; separate saved doubling-fill rosters are cross-checks only, not parent eligibility filters. Their event universes differ, so their intersections do not replace current-path attribution.",
            "Entry fees are allocated to closing quantities proportionally within the same child; fees and cash are USDT.",
        ],
    }
    if smoke:
        stats["smoke"] = {
            "sample_parent_ids": funded.index[:min(10, len(funded))].tolist(),
            "sample_reconciliation_max_error_usdt": float(parent.iloc[:min(10, len(parent))].cash_error_usdt.abs().max()),
            "sample_child_rows": int(child_attr[child_attr.event_id.isin(funded.index[:10])].shape[0]),
        }
    # Fail before writing outputs if an unexpected pandas scalar or NaN leaks
    # into the frozen JSON summary.
    json.dumps(stats, ensure_ascii=False, allow_nan=False)
    return stats, attr, child_attr, parent, category, origin, winners


def _write_csv(frame, path):
    tmp = path.with_name(path.name + ".part")
    compression = "gzip" if path.name.endswith(".gz") else None
    frame.to_csv(tmp, index=False, compression=compression)
    tmp.replace(path)


def main(*, smoke=False, run=False, refresh_existing=False):
    if not CONFIG.is_file():
        raise FileNotFoundError(CONFIG)
    if sum((bool(smoke), bool(run), bool(refresh_existing))) != 1:
        raise ValueError("Choose exactly one of --smoke, --run or --refresh-existing")
    stats, attr, child, parent, category, origin, winners = analyze(smoke=smoke)
    if smoke:
        print(json.dumps(stats["smoke"], indent=2))
        return
    summary_path = EXP / "summary.json"
    if run and summary_path.exists():
        raise FileExistsError("Target-leg attribution outputs already exist")
    if refresh_existing and not summary_path.exists():
        raise FileNotFoundError("Cannot refresh target-leg attribution without existing outputs")
    EXP.mkdir(parents=True, exist_ok=True)
    _write_csv(attr, EXP / "order_attribution.csv.gz")
    _write_csv(child, EXP / "child_attribution.csv.gz")
    _write_csv(parent, EXP / "parent_reconciliation.csv.gz")
    _write_csv(category, EXP / "category_summary.csv")
    _write_csv(origin, EXP / "child_origin_summary.csv")
    _write_csv(winners.reset_index(), EXP / "top10_cash_winners.csv.gz")
    tmp = EXP / "summary.json.part"
    tmp.write_text(json.dumps(stats, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    tmp.replace(EXP / "summary.json")
    print(json.dumps({"reconciliation": stats["reconciliation"],
                      "input_counts": stats["input_counts"],
                      "periods": {k: {"funded_parents": v["funded_parents"],
                                      "frozen_parent_cash_pnl_usdt": v["frozen_parent_cash_pnl_usdt"],
                                      "exit_types": {t: x["realized_net_pnl_usdt"] for t, x in v["exit_types"].items()}}
                                  for k, v in stats["periods"].items()}}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--smoke", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--refresh-existing", action="store_true")
    args = parser.parse_args()
    main(smoke=args.smoke, run=args.run, refresh_existing=args.refresh_existing)
