"""Condition the frozen 12h 2R order expiry on the earlier same-side FVG tag.

All arms use the same known V1.7 event universe. The primary map cancels an
unfilled 2R partial after 12h for `outside` labels and keeps it live through
the native runner exit for `overlap` labels. The reciprocal map is preserved
as a non-selecting diagnostic. Positions, hourly 20% marked-name cap, fees,
and cash sizing are funded by the existing VectorBT adapter.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_fvg_exclusion as fvg
from yoyo.evaluation import cloud_close_hourly_trim as hourly
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_target_expiry as expiry
from yoyo.evaluation import cloud_close_thirds as thirds


EXP = hourly.EXP.parent / "exp-cloud-close-fvg-expiry-20261109-v1"
CONFIG_PATH = EXP / "config.json"
DECISIONS_PATH = fvg.EXP / "decisions.csv.gz"
KNOWN_TAGS = {"outside", "overlap"}
POLICIES = ("always_expire_12h", "always_persist_2r",
            "outside_expire_overlap_persist", "outside_persist_overlap_expire")
POLICY_LABEL = {
    "always_expire_12h": "Always expire unfilled 2R order at 12h",
    "always_persist_2r": "Keep original 2R order through native runner exit",
    "outside_expire_overlap_persist": "Outside expires; overlap persists",
    "outside_persist_overlap_expire": "Reciprocal non-selecting map",
}


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_tagged_paths() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Join frozen FVG decisions by the original 1-based source row identity."""
    paths = thirds.okx_paths()
    expiry.trend.repair.check_parent_sets(paths)
    decisions = pd.read_csv(DECISIONS_PATH, dtype={"symbol": str}, keep_default_na=False)
    required = {"source_row_number", "symbol", "week", "tag"}
    if not required.issubset(decisions.columns):
        raise ValueError(f"FVG decisions lack {required - set(decisions.columns)}")
    decisions["source_row_number"] = decisions.source_row_number.astype("int64")
    if decisions.source_row_number.duplicated().any() or decisions.tag.eq("").any():
        raise ValueError("Frozen FVG decisions have duplicate keys or empty labels")

    source_path = fvg.wallet.SOURCE / "signals.csv"
    source = pd.read_csv(source_path, usecols=["symbol", "week"], dtype={"symbol": str},
                         keep_default_na=False)
    source["source_row_number"] = np.arange(1, len(source) + 1, dtype=np.int64)
    if len(source) != len(decisions) or set(decisions.source_row_number) != set(source.source_row_number):
        raise ValueError("FVG decisions do not cover the exact frozen source row set")
    checked = decisions.merge(source, on="source_row_number", suffixes=("_decision", "_source"),
                              validate="one_to_one")
    if (checked.symbol_decision != checked.symbol_source).any() or (checked.week_decision != checked.week_source).any():
        raise ValueError("FVG decision symbol/week differs from its frozen source row")

    tag_by_source = decisions.set_index("source_row_number").tag
    symbol_by_source = decisions.set_index("source_row_number").symbol
    week_by_source = decisions.set_index("source_row_number").week
    paths = paths.copy()
    paths["source_row_number"] = paths.source_row_number.astype("int64")
    paths["fvg_tag"] = paths.source_row_number.map(tag_by_source)
    paths["fvg_source_symbol"] = paths.source_row_number.map(symbol_by_source)
    paths["week"] = paths.source_row_number.map(week_by_source)
    if paths[["fvg_tag", "week"]].isna().any().any():
        raise ValueError("A real or matched-control path lacks a frozen FVG label")
    if (paths.symbol.astype(str) != paths.fvg_source_symbol.astype(str)).any():
        raise ValueError("Path symbol differs from the FVG source row")
    paths["fvg_known"] = paths.fvg_tag.isin(KNOWN_TAGS)
    source_counts = decisions.tag.value_counts().to_dict()
    actual = paths[(~paths.is_control) & paths.tp_r.eq(0)]
    known_parent_ids = set(actual.loc[actual.fvg_known, "parent_id"].astype(str))
    known = paths[paths.fvg_known & paths.parent_id.astype(str).isin(known_parent_ids)].copy()
    if known.empty or not known.tp_r.isin([0, 2]).all():
        raise ValueError("The shared known FVG opportunity set is empty or has extra target paths")
    if known.duplicated(["event_id", "tp_r"]).any() or not known.groupby("event_id").size().eq(2).all():
        raise ValueError("Known opportunities must retain one runner and one native 2R leg")
    known_actual = known[(~known.is_control) & known.tp_r.eq(0)]
    known_controls = known[known.is_control & known.tp_r.eq(0)]
    known_parents = set(known_actual.parent_id.astype(str))
    known_controls = known_controls[known_controls.parent_id.astype(str).isin(known_parents)]
    known_ids = set(known_actual.parent_id.astype(str)) | set(known_controls.parent_id.astype(str))
    known = known[known.parent_id.astype(str).isin(known_ids)].copy()
    tag_counts = known_actual.groupby("fvg_tag").event_id.nunique().astype(int).to_dict()
    meta = {
        "source_signal_rows": len(source),
        "fvg_source_tag_counts": {str(k): int(v) for k, v in source_counts.items()},
        "known_actual_events_by_tag": {str(k): int(v) for k, v in tag_counts.items()},
        "known_actual_events": int(known_actual.event_id.nunique()),
        "known_matched_control_events": int(known_controls.event_id.nunique()),
        "known_parent_pairs": int(known_controls.parent_id.nunique()),
        "excluded_unknown_actual_events": int((~actual.fvg_known).sum()),
    }
    return paths, known, meta


def _target_choices(paths: pd.DataFrame, policy: str, hours: int = 12) -> pd.DataFrame:
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy {policy}")
    if hours != 12:
        raise ValueError("This preregistered branch fixes expiry at 12 hours")
    target = paths.loc[paths.tp_r.eq(2)].copy()
    if policy == "always_expire_12h":
        _, frozen = expiry.time_in_force(paths, hours=hours)
        return frozen[["event_id", "choice"]].copy()
    target["choice"] = "partial_2_3"
    is_limit = target.exit_reason.astype(str).str.startswith("take_profit")
    if not np.array_equal(is_limit, target.exit_fee_rate.lt(0.0005)):
        raise ValueError("Native 2R fill reason and maker/taker fee class disagree")
    if (target.entry_ms.astype("int64") % (12 * hourly.tested.w.HOUR)).any():
        raise ValueError("Native entry is not aligned to the confirmed 12H boundary")
    expiry_ms = target.entry_ms.astype("int64") + hours * hourly.tested.w.HOUR
    late_or_unfilled = target.exit_bar_ms.astype("int64").ge(expiry_ms)
    if policy == "always_persist_2r":
        return target[["event_id", "choice"]]
    cancel_tag = "outside" if policy == "outside_expire_overlap_persist" else "overlap"
    cancel = target.fvg_tag.eq(cancel_tag) & is_limit & late_or_unfilled
    target.loc[cancel, "choice"] = "full_runner"
    return target[["event_id", "choice"]]


def rotate(paths: pd.DataFrame, policy: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    choices = _target_choices(paths, policy)
    if choices.event_id.duplicated().any() or set(choices.event_id.astype(str)) != set(paths.event_id.astype(str)):
        raise ValueError(f"{policy}: expiry choice roster differs from native events")
    rotated = expiry.trend.rotation._rotated_paths(paths, choices)
    return rotated, choices


def _policy_decision_table(paths: pd.DataFrame) -> pd.DataFrame:
    target = paths.loc[paths.tp_r.eq(2)].copy()
    base_cols = ["event_id", "parent_id", "source_row_number", "symbol", "week", "side",
                 "is_control", "fvg_tag", "entry_ms", "exit_bar_ms", "exit_reason"]
    table = target[base_cols].copy()
    table["expiry_ms"] = table.entry_ms.astype("int64") + 12 * hourly.tested.w.HOUR
    table["target_is_fill"] = table.exit_reason.astype(str).str.startswith("take_profit")
    table["target_filled_before_expiry"] = table.target_is_fill & table.exit_bar_ms.lt(table.expiry_ms)
    for policy in POLICIES:
        choices = _target_choices(paths, policy)
        table[policy] = table.event_id.map(choices.set_index("event_id").choice)
    return table


def _selected_roster(data: dict) -> pd.DataFrame:
    selected = data["selected"]
    runner = selected.loc[selected.tp_r.eq(0), ["event_id", "slot", "entry_ms", "exit_bar_ms"]].copy()
    runner = runner.sort_values(["entry_ms", "event_id"]).reset_index(drop=True)
    if runner.event_id.duplicated().any():
        raise ValueError("VectorBT admission selected a parent more than once")
    return runner


def _decorate_positions(positions: pd.DataFrame, paths: pd.DataFrame) -> pd.DataFrame:
    metadata = paths.loc[(paths.tp_r.eq(0)),
        ["event_id", "source_row_number", "is_control", "fvg_tag", "entry_order"]].copy()
    if metadata.event_id.duplicated().any():
        raise ValueError("Position metadata is not unique by event")
    metadata = metadata.set_index("event_id")
    result = positions.copy()
    for column in ("source_row_number", "is_control", "fvg_tag", "entry_order"):
        result[column] = result.index.map(metadata[column])
    if result[["source_row_number", "is_control", "fvg_tag"]].isna().any().any():
        raise ValueError("Funded position lacks FVG decision provenance")
    return result


def _target_path_counts(targets: pd.DataFrame, choices: pd.DataFrame,
                        funded_actual_ids: set[str]) -> dict[str, int]:
    indexed = targets.set_index("event_id")
    choice = choices.set_index("event_id").choice.reindex(indexed.index)
    if choice.isna().any():
        raise ValueError("Target path count input lacks a policy decision")
    choice = choice.astype(str)
    full_runner = choice.eq("full_runner")
    actual = ~indexed.is_control.astype(bool)
    funded_actual = indexed.index.astype(str).isin(funded_actual_ids) & actual
    late_fill = (indexed.exit_reason.astype(str).str.startswith("take_profit")
                 & indexed.exit_bar_ms.ge(indexed.entry_ms + 12 * hourly.tested.w.HOUR))
    cancelled_late_fill = full_runner & late_fill
    return {
        "known_target_paths_as_full_runner": int(full_runner.sum()),
        "actual_target_paths_as_full_runner": int((full_runner & actual).sum()),
        "control_target_paths_as_full_runner": int((full_runner & ~actual).sum()),
        "funded_actual_target_paths_as_full_runner": int((full_runner & funded_actual).sum()),
        "known_late_target_fills_cancelled": int(cancelled_late_fill.sum()),
        "actual_late_target_fills_cancelled": int((cancelled_late_fill & actual).sum()),
        "funded_actual_late_target_fills_cancelled": int((cancelled_late_fill & funded_actual).sum()),
    }


def _account(paths: pd.DataFrame, *, slip: float = 0.0, use_vectorized_manager: bool = True,
             include_controls: bool = False) -> tuple[dict, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    funded_pool = paths if include_controls else paths.loc[~paths.is_control]
    data = split.pair_data(funded_pool.loc[funded_pool.tp_r.eq(0)],
                           funded_pool.loc[funded_pool.tp_r.eq(2)], 9)
    roster = _selected_roster(data)
    if not len(roster):
        raise ValueError("Invalid 1000U/9-slot funded input")
    if slip == 0.0 and use_vectorized_manager:
        metrics, positions, equity, trims = hourly.tested.trim_account(
            data, hourly.tested.w.quotes, 1.0, 0.20, review_hours=1)
    else:
        metrics, positions, equity, trims = _trim_account_with_slippage(data, hourly.tested.w.quotes,
            exposure=1.0, limit=0.20, review_hours=1, market_slip=slip)
    if set(positions.index.astype(str)) != set(roster.event_id.astype(str)):
        raise ValueError("Hourly-cap account changed its funded roster")
    if not np.isclose(1000.0 + positions.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=0.005):
        raise ValueError("Funded cash does not reconcile to the VectorBT hourly NAV")
    positions = _decorate_positions(positions, paths)
    return metrics, positions, equity, trims, roster


@njit(cache=True)
def _order_with_slippage(c, nav, actions, prices, fees, exposure, weights,
                         reserve, slot_weights, fraction, market_slip):
    if c.i % 4 == 2:
        ratio = fraction[c.col // len(weights)]
        if c.position_now == 0.0 or ratio <= 1.e-10:
            return NoOrder
        return nb.order_nb(size=-c.position_now * ratio, price=c.close[c.i, c.col],
                           fees=hourly.tested.w.TAKER, slippage=market_slip, lock_cash=True,
                           allow_partial=False, raise_reject=True)
    return hourly.tested.w.account_order(c, nav, actions, prices, fees, market_slip,
        exposure, weights, reserve, 0, slot_weights)


def _trim_account_with_slippage(data: dict, quote_loader, exposure: float, limit: float,
                                *, review_hours: int, market_slip: float):
    """VectorBT copy of the frozen hourly-cap adapter with a slippage hook.

    The established adapter intentionally fixes slippage at zero. This local
    wrapper reuses its pre-segment cap logic and wallet order-sizing callback,
    adding the preregistered +5bp to every taker fill, including cap trims.
    """
    base = data["actions"]
    hours, columns = base.shape[0] // 3, base.shape[1]
    leg_weights = (2 / 3, 1 / 3)
    nlegs = len(leg_weights)
    if review_hours != 1 or columns % nlegs:
        raise ValueError("Hourly stress adapter expects two children and H1 review")
    eligible = np.zeros((hours, columns // nlegs), dtype=bool)
    runners = data["selected"].loc[data["selected"].slot.mod(nlegs).eq(nlegs - 1)]
    if not runners.tp_r.eq(0).all() or runners.event_id.duplicated().any():
        raise ValueError("Last child must be the original cloud runner")
    for row in runners.itertuples():
        clock = quote_loader(row.symbol)[0]
        i, j = clock.searchsorted([row.entry_ms, row.exit_bar_ms + hourly.tested.w.HOUR])
        times = clock[i:j]
        next_bars = times[review_hours:]
        span = review_hours * hourly.tested.w.HOUR
        present = next_bars[(next_bars - times[:-review_hours] == span) & (next_bars % span == 0)]
        eligible[(present - data["start"]) // hourly.tested.w.HOUR, row.slot // nlegs] = True

    def expand(field):
        return data[field].reshape(hours, 3, columns)[:, [0, 1, 1, 2], :].reshape(-1, columns).copy()

    marks, prices, fees = [expand(field) for field in ("marks", "prices", "fees")]
    actions = expand("actions")
    actions[2::4] = 0
    fraction = np.zeros(columns // nlegs)
    target = np.full(columns // nlegs, -1.0)
    pf = hourly.tested.w.vbt.Portfolio.from_order_func(
        marks, _order_with_slippage, actions, prices, fees, exposure,
        np.asarray(leg_weights), np.zeros(1), np.ones((1, 1)), fraction, market_slip,
        pre_segment_func_nb=hourly.tested.segment,
        pre_segment_args=(eligible, limit, target, fraction, nlegs),
        cash_sharing=True, group_by=True, init_cash=1000., fill_pos_record=False,
        max_orders=int(np.count_nonzero(actions) + nlegs * eligible.sum() + 1),
    )
    fills = pf.orders.records.copy()
    fills["action"] = np.where(fills.idx.mod(4).eq(2), 3, actions[fills.idx, fills.col])
    fills["hour"] = data["start"] + fills.idx.floordiv(4) * hourly.tested.w.HOUR
    for slot, children in data["selected"].groupby("slot"):
        children = children.sort_values("entry_ms")
        ids = fills.index[fills.col.eq(slot)]
        stamps = fills.loc[ids, "hour"].to_numpy()
        ix = children.entry_ms.searchsorted(stamps, side="right") - 1
        if (ix < 0).any() or (stamps > children.exit_bar_ms.to_numpy()[ix]).any():
            raise ValueError("Slippage fill lies outside the native child interval")
        fills.loc[ids, "event_id"] = children.event_id.to_numpy()[ix]
    opening = fills.action.abs().eq(1)
    value = fills["size"] * fills.price
    fills["notional"] = value.where(opening, 0.)
    fills["cash_pnl"] = np.where(fills.side.eq(0), -value, value) - fills.fees
    fills["net_qty"] = fills["size"].where(fills.side.eq(0), -fills["size"])
    child = fills.groupby(["event_id", "col"]).agg(residual=("net_qty", "sum"), activity=("size", "sum"))
    if len(child) != len(data["selected"]) or (child.residual.abs() > child.activity * 1e-9 + 1e-12).any():
        raise ValueError("Slippage child account failed to open and close every funded quantity")
    runners = runners.set_index("event_id")[["parent_id", "symbol", "side", "entry_ms", "exit_bar_ms"]]
    parents = runners.join(fills.groupby("event_id")[["cash_pnl", "notional"]].sum(), validate="one_to_one")
    parents["net_return"] = parents.cash_pnl / parents.notional
    nav = np.asarray(pf.value()).reshape(-1, 4)
    cash_from_orders = 1000. + float((np.where(fills.side.eq(0), -1., 1.) * fills["size"] * fills.price - fills.fees).sum())
    if not np.isclose(nav[-1, -1], cash_from_orders, rtol=1e-10, atol=0.005):
        raise ValueError("Slippage cash does not reconcile to final VectorBT NAV")
    quantity = np.asarray(pf.assets())[2::4]
    lower = np.minimum(nav.min(axis=1), nav[:, 2] + (quantity * np.where(quantity >= 0, data["low"], data["high"])).sum(axis=1))
    upper = np.maximum(nav.max(axis=1), nav[:, 2] + (quantity * np.where(quantity >= 0, data["high"], data["low"])).sum(axis=1))
    index = pd.to_datetime(data["start"] + (1 + np.arange(hours)) * hourly.tested.w.HOUR, unit="ms", utc=True)
    equity = pd.DataFrame(dict(nav=nav[:, 3], open_nav=nav[:, 0], open_exit_nav=nav[:, 1],
                               post_trim_nav=nav[:, 2], lower_nav=lower, upper_nav=upper), index=index)
    metrics = hourly.tested.summarize(parents, equity)
    return metrics, parents, equity, fills[fills.action.eq(3)]


def _risk_payoff_pass(metrics: dict) -> bool:
    return bool(expiry.trend.second.meets(metrics) and
                (metrics.get("payoff_usdt") or 0.) >= 2.5 and metrics.get("trades", 0) >= 100)


def _repo_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=EXP.parents[2], text=True).strip()
    except Exception:
        return "unknown"


def _write_frame(path: Path, frame: pd.DataFrame, *, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    frame.to_csv(tmp, index=index, compression="gzip" if path.suffix == ".gz" else None)
    os.replace(tmp, path)


def _run_smoke() -> dict:
    paths, known, meta = load_tagged_paths()
    policy_paths = {policy: rotate(known, policy)[0] for policy in POLICIES}
    canonical = lambda frame: frame.sort_values(["event_id", "tp_r"]).reset_index(drop=True)
    # The two baseline transforms are exact path selectors, and both conditional
    # maps must preserve every native leg and every known event identity.
    if not canonical(policy_paths["always_persist_2r"]).equals(canonical(known)):
        raise ValueError("Persistent-target arm changed native paths")
    expired, _ = expiry.time_in_force(known, hours=12)
    if not canonical(policy_paths["always_expire_12h"]).equals(canonical(expired)):
        raise ValueError("Always-expire arm differs from the frozen 12h adapter")
    ids = set(known.event_id.astype(str))
    for policy, frame in policy_paths.items():
        if set(frame.event_id.astype(str)) != ids or frame.duplicated(["event_id", "tp_r"]).any():
            raise ValueError(f"{policy} changed the native path roster")
    choices = {policy: _target_choices(known, policy).set_index("event_id").choice for policy in POLICIES}
    target = known.loc[known.tp_r.eq(2)].set_index("event_id")
    for event_id, row in target.iterrows():
        if row.fvg_tag == "outside" and row.exit_reason.startswith("take_profit") and row.exit_bar_ms >= row.entry_ms + 12 * hourly.tested.w.HOUR:
            if choices["outside_expire_overlap_persist"].loc[event_id] != "full_runner":
                raise ValueError("Outside late target should expire in the primary policy")
            if choices["outside_persist_overlap_expire"].loc[event_id] != "partial_2_3":
                raise ValueError("Outside late target should persist in the reciprocal policy")
        if row.fvg_tag == "overlap" and row.exit_reason.startswith("take_profit") and row.exit_bar_ms >= row.entry_ms + 12 * hourly.tested.w.HOUR:
            if choices["outside_expire_overlap_persist"].loc[event_id] != "partial_2_3":
                raise ValueError("Overlap late target should persist in the primary policy")
            if choices["outside_persist_overlap_expire"].loc[event_id] != "full_runner":
                raise ValueError("Overlap late target should expire in the reciprocal policy")

    # Exercise VectorBT on a short, bounded known-event slice and compare the
    # frozen zero-slippage adapter with the local adverse-fill callback.
    actual = known[(~known.is_control) & known.tp_r.eq(0)].copy()
    actual["entry_year"] = pd.to_datetime(actual.entry_ms, unit="ms", utc=True).dt.year
    sample_ids: set[str] = set()
    for year_mask in (actual.entry_year.lt(2025), actual.entry_year.eq(2025), actual.entry_year.ge(2026)):
        cohort = actual.loc[year_mask].sort_values("entry_ms")
        if len(cohort) < 4:
            raise ValueError("Smoke needs at least four frozen actual events in dev, 2025, and 2026")
        sample_ids.update(cohort.head(4).event_id.astype(str))
    sample = known[known.event_id.astype(str).isin(sample_ids)].copy()
    runner = sample[sample.tp_r.eq(0)]
    start = int(runner.entry_ms.min())
    end = int(runner.exit_bar_ms.max() + hourly.tested.w.HOUR)
    data = split.pair_data(runner, sample[sample.tp_r.eq(2)], 9,
                           start=start, end=end, quote_loader=hourly.tested.w.quotes)
    normal = hourly.tested.trim_account(data, hourly.tested.w.quotes, 1.0, 0.20, review_hours=1)
    local = _trim_account_with_slippage(data, hourly.tested.w.quotes, 1.0, 0.20,
                                       review_hours=1, market_slip=0.0)
    if set(normal[1].index.astype(str)) != set(local[1].index.astype(str)) or not np.allclose(normal[1].cash_pnl, local[1].cash_pnl, rtol=1e-9, atol=1e-7) or not np.allclose(normal[2].nav, local[2].nav, rtol=1e-9, atol=1e-7):
        raise ValueError("Local VectorBT adapter differs from frozen hourly-cap manager at zero slippage")
    stressed = _trim_account_with_slippage(data, hourly.tested.w.quotes, 1.0, 0.20,
                                           review_hours=1, market_slip=0.0005)
    if set(stressed[1].index.astype(str)) != set(normal[1].index.astype(str)):
        raise ValueError("5bp sensitivity altered the smoke admission roster")
    sample_parents = set(runner.parent_id.astype(str))
    control_sample = known[known.is_control & known.parent_id.astype(str).isin(sample_parents)].copy()
    control_metrics, control_positions, control_equity, _, _ = _account(control_sample, include_controls=True)
    if not control_positions.is_control.all() or not np.isclose(
        1000.0 + control_positions.cash_pnl.sum(), control_equity.nav.iloc[-1], rtol=1e-8, atol=0.005
    ):
        raise ValueError("Control-only smoke account lost control provenance or cash reconciliation")
    return {
        "candidate_paths": len(paths),
        "known_paths": len(known),
        "known_opportunity_counts": meta,
        "smoke_positions": len(normal[1]),
        "smoke_zero_slip_final_usdt": float(normal[2].nav.iloc[-1]),
        "smoke_5bp_final_usdt": float(stressed[2].nav.iloc[-1]),
        "smoke_control_positions": len(control_positions),
        "smoke_control_final_usdt": float(control_equity.nav.iloc[-1]),
        "zero_slip_adapter_parity": True,
        "policy_roster_parity": True,
    }


def run() -> dict:
    if (EXP / "summary.json").exists():
        raise FileExistsError("FVG-conditioned expiry results already exist")
    EXP.mkdir(parents=True, exist_ok=True)
    paths, known, meta = load_tagged_paths()
    ref = json.loads((hourly.EXP / "summary.json").read_text())
    baseline_ref = ref["OKX"]["metrics"]
    decisions = _policy_decision_table(known)
    _write_frame(EXP / "policy_decisions.csv.gz", decisions, index=False)

    policy_results: dict[str, dict] = {}
    metric_rows: list[dict] = []
    common_roster = None
    for policy in POLICIES:
        changed, choices = rotate(known, policy)
        metrics, positions, equity, trims, roster = _account(changed)
        if common_roster is None:
            common_roster = roster
        elif not roster.equals(common_roster):
            raise ValueError("FVG target policy changed a known event's 9-slot admission or holding roster")
        for period, values in metrics.items():
            metric_rows.append(dict(policy=policy, scenario="actual", period=period, **values))
        positions.to_csv(EXP / f"funded_OKX_{policy}.csv.gz")
        equity.to_csv(EXP / f"hourly_OKX_{policy}.csv.gz", index_label="hour_end_utc")
        target_paths = known.loc[known.tp_r.eq(2)]
        path_counts = _target_path_counts(target_paths, choices, set(positions.index.astype(str)))
        policy_results[policy] = {
            "label": POLICY_LABEL[policy],
            "funded": len(positions),
            **path_counts,
            "metrics": metrics,
            "cash_reconciles": bool(np.isclose(1000 + positions.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=.005)),
        }
        del changed, positions, equity, trims
        gc.collect()

    primary = policy_results["outside_expire_overlap_persist"]["metrics"]
    baseline_expiry = policy_results["always_expire_12h"]["metrics"]
    baseline_persist = policy_results["always_persist_2r"]["metrics"]
    risk_payoff_gate = _risk_payoff_pass(primary["dev"]) and _risk_payoff_pass(primary["all"])
    beats_both = all(
        primary[period]["final_usdt"] > baseline[period]["final_usdt"]
        for period in ("dev", "all") for baseline in (baseline_expiry, baseline_persist)
    )
    sensitivity_gate = risk_payoff_gate and beats_both
    sensitivity: dict[str, dict] = {}
    if sensitivity_gate:
        transformed, _ = rotate(known, "outside_expire_overlap_persist")
        actual_paths = transformed[~transformed.is_control]
        control_paths = transformed[transformed.is_control & transformed.parent_id.isin(actual_paths.parent_id)]
        paired_parents = set(actual_paths.loc[actual_paths.tp_r.eq(0), "parent_id"].astype(str)) & set(
            control_paths.loc[control_paths.tp_r.eq(0), "parent_id"].astype(str))
        paired_paths = transformed[transformed.parent_id.astype(str).isin(paired_parents)]
        for name, pool, slip, include_controls in (
            ("paired_actual", paired_paths[~paired_paths.is_control], 0.0, False),
            ("matched_later_control", paired_paths[paired_paths.is_control], 0.0, True),
            ("market_slippage_5bp", actual_paths, 0.0005, False),
        ):
            metrics, positions, equity, trims, roster = _account(
                pool, slip=slip, include_controls=include_controls)
            for period, values in metrics.items():
                metric_rows.append(dict(policy="outside_expire_overlap_persist", scenario=name,
                                        period=period, **values))
            positions.to_csv(EXP / f"funded_OKX_{name}.csv.gz")
            equity.to_csv(EXP / f"hourly_OKX_{name}.csv.gz", index_label="hour_end_utc")
            sensitivity[name] = {"funded": len(positions), "metrics": metrics,
                                 "cash_reconciles": bool(np.isclose(1000 + positions.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=.005))}
            del positions, equity, trims, roster
            gc.collect()

    summary = {
        "schema": 1,
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "source_commit": _repo_commit(),
        "vectorbt_version": hourly.tested.w.vbt.__version__,
        "tag_source": str(DECISIONS_PATH.relative_to(fvg.wallet.ROOT)),
        "frozen_full_account_reference": baseline_ref,
        "frozen_fvg_coverage": meta,
        "known_coverage_used_by_all_policies": {
            "tags": sorted(KNOWN_TAGS),
            "actual_events": meta["known_actual_events"],
            "matched_control_events": meta["known_matched_control_events"],
            "same_event_slot_roster_across_policies": True,
        },
        "policy_results": policy_results,
        "primary_risk_payoff_gate_dev_and_all": risk_payoff_gate,
        "primary_beats_both_fixed_controls_dev_and_all": beats_both,
        "run_paired_and_5bp_sensitivity": sensitivity_gate,
        "sensitivity": sensitivity,
        "control_semantics": "Later matched controls inherit the frozen parent source_row_number and its FVG tag, as in the registered FVG exclusion study.",
        "limitations": ["No funding payments, market impact, or exchange minimum quantity/tick rounding.",
                        "The FVG tag is a historical frozen event label; this is a retrospective strategy comparison, not forward validation."],
    }
    pd.DataFrame(metric_rows).to_csv(EXP / "account_metrics.csv", index=False)
    (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"known": meta, "policies": {k: {"dev": v["metrics"]["dev"], "all": v["metrics"]["all"]}
                                                   for k, v in policy_results.items()},
                      "sensitivity_gate": sensitivity_gate}, indent=2), flush=True)
    return summary


def resume_existing(account_run_commit: str) -> dict:
    """Rebuild summary/metrics from a completed account run's cached ledgers.

    This is for a run that wrote all policy ledgers but stopped during final
    metadata assembly. It never reruns VectorBT accounts.
    """
    required = [EXP / "policy_decisions.csv.gz"] + [
        EXP / f"{prefix}_OKX_{policy}.csv.gz"
        for prefix in ("funded", "hourly") for policy in POLICIES
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Cannot rebuild from incomplete saved run; missing {missing}")
    paths, known, meta = load_tagged_paths()
    decisions = pd.read_csv(EXP / "policy_decisions.csv.gz", dtype={"event_id": str})
    expected = set(known.loc[known.tp_r.eq(2), "event_id"].astype(str))
    if decisions.event_id.duplicated().any() or set(decisions.event_id.astype(str)) != expected:
        raise ValueError("Saved policy decisions do not cover the frozen known target events")
    is_native_tp = decisions.exit_reason.astype(str).str.startswith("take_profit")
    if not is_native_tp.equals(decisions.target_is_fill.astype(bool)):
        raise ValueError("Saved target-fill decisions disagree with native exit reasons")

    ref = json.loads((hourly.EXP / "summary.json").read_text())
    policy_results: dict[str, dict] = {}
    metric_rows: list[dict] = []
    funded_rosters: dict[str, set[str]] = {}
    for policy in POLICIES:
        positions = pd.read_csv(EXP / f"funded_OKX_{policy}.csv.gz", index_col=0)
        positions.index = positions.index.astype(str)
        positions.index.name = "event_id"
        equity = pd.read_csv(EXP / f"hourly_OKX_{policy}.csv.gz", parse_dates=["hour_end_utc"])
        equity = equity.set_index("hour_end_utc")
        equity.index = pd.to_datetime(equity.index, utc=True)
        if positions.index.duplicated().any() or not positions.is_control.eq(False).all():
            raise ValueError(f"{policy}: saved account ledger has duplicate or control positions")
        if not equity.index.is_unique or not equity.index.is_monotonic_increasing:
            raise ValueError(f"{policy}: saved hourly NAV index is not ordered and unique")
        reconciles = bool(np.isclose(1000.0 + positions.cash_pnl.sum(), equity.nav.iloc[-1],
                                     rtol=1e-8, atol=0.005))
        if not reconciles:
            raise ValueError(f"{policy}: saved cash ledger does not reconcile to terminal NAV")
        metrics = hourly.tested.summarize(positions, equity)
        funded_rosters[policy] = set(positions.index)
        for period, values in metrics.items():
            metric_rows.append(dict(policy=policy, scenario="actual", period=period, **values))
        choices = decisions[["event_id", policy]].rename(columns={policy: "choice"})
        path_counts = _target_path_counts(decisions, choices, set(positions.index))
        policy_results[policy] = {
            "label": POLICY_LABEL[policy],
            "funded": int(len(positions)),
            **path_counts,
            "metrics": metrics,
            "cash_reconciles": reconciles,
        }
    if len({frozenset(ids) for ids in funded_rosters.values()}) != 1:
        raise ValueError("Saved policy accounts do not have the same funded event roster")

    primary = policy_results["outside_expire_overlap_persist"]["metrics"]
    baseline_expiry = policy_results["always_expire_12h"]["metrics"]
    baseline_persist = policy_results["always_persist_2r"]["metrics"]
    risk_payoff_gate = _risk_payoff_pass(primary["dev"]) and _risk_payoff_pass(primary["all"])
    beats_both = all(primary[period]["final_usdt"] > baseline[period]["final_usdt"]
                     for period in ("dev", "all")
                     for baseline in (baseline_expiry, baseline_persist))
    sensitivity_gate = risk_payoff_gate and beats_both
    if sensitivity_gate:
        raise ValueError("Saved run passed the sensitivity gate but contains no paired/stress ledgers; rerun --run")

    baseline_ref = ref["OKX"]["metrics"]
    summary = {
        "schema": 1,
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "source_commit": account_run_commit,
        "summary_rebuilt_from_cached_ledgers": True,
        "summary_rebuild_commit": _repo_commit(),
        "vectorbt_version": hourly.tested.w.vbt.__version__,
        "tag_source": str(DECISIONS_PATH.relative_to(fvg.wallet.ROOT)),
        "frozen_full_account_reference": baseline_ref,
        "frozen_fvg_coverage": meta,
        "known_coverage_used_by_all_policies": {
            "tags": sorted(KNOWN_TAGS),
            "actual_events": meta["known_actual_events"],
            "matched_control_events": meta["known_matched_control_events"],
            "same_event_slot_roster_across_policies": True,
        },
        "policy_results": policy_results,
        "primary_risk_payoff_gate_dev_and_all": risk_payoff_gate,
        "primary_beats_both_fixed_controls_dev_and_all": beats_both,
        "run_paired_and_5bp_sensitivity": sensitivity_gate,
        "sensitivity": {},
        "control_semantics": "Later matched controls inherit the frozen parent source_row_number and its FVG tag, as in the registered FVG exclusion study.",
        "limitations": ["No funding payments, market impact, or exchange minimum quantity/tick rounding.",
                        "The FVG tag is a historical frozen event label; this is a retrospective strategy comparison, not forward validation."],
    }
    pd.DataFrame(metric_rows).to_csv(EXP / "account_metrics.csv", index=False)
    (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"known": meta,
                      "policies": {k: {"dev": v["metrics"]["dev"], "all": v["metrics"]["all"]}
                                   for k, v in policy_results.items()},
                      "sensitivity_gate": sensitivity_gate}, indent=2), flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="validate tag joins, path rotation, and bounded VectorBT parity")
    mode.add_argument("--run", action="store_true", help="run the frozen known-coverage OKX account sweep")
    mode.add_argument("--resume-existing", action="store_true", help="rebuild summary from completed funded/hourly ledgers without rerunning VectorBT")
    parser.add_argument("--account-run-commit", help="source commit used for the existing account ledgers")
    args = parser.parse_args()
    if args.resume_existing:
        if not args.account_run_commit:
            parser.error("--resume-existing requires --account-run-commit")
        result = resume_existing(args.account_run_commit)
    else:
        result = _run_smoke() if args.smoke else run()
    print(json.dumps(result, indent=2, default=str), flush=True)


if __name__ == "__main__":
    main()
