"""Screen frozen 90d V1.7 entries by executable opposite-extension room in R.

The single variable is the minimum directional distance from the actual
confirmation-entry price to the original opposite Monday 1.618 reference,
divided by the original absolute entry-to-stop distance. Entries, original
stops, the 75% fixed 2R resting partial, the 25% cloud runner, nine-slot
low-prior-ATR cash sharing, fees, and the frozen 90d arm are reused unchanged.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_slots as capacity


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-room-entry-20261109-v1"
CANDIDATES = (
    ("no_room_filter", None),
    ("room_ge_2", 2.0),
    ("room_ge_3", 3.0),
    ("room_ge_5", 5.0),
)
ROOM_GEOMETRY_FRACTION = 0.5  # geometry fields are invariant to this target fraction
METRIC_FIELDS = (
    "final_usdt", "multiple", "dd_mark_pct", "dd_bound_pct", "trades",
    "win_pct", "payoff", "payoff_usdt", "median_net_pct",
)
ROOM_FIELDS = (
    "event_id", "parent_id", "symbol", "side", "is_control", "entry_ms",
    "entry_price", "stop_price", "risk_distance", "target_fraction",
    "target_reference_price", "target_reference_remaining_distance",
    "target_relative_favorable_pct", "target_parent_reference_price",
    "target_parent_entry_price",
)


def _load_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _close(actual: float, expected: float, label: str) -> None:
    if not np.isclose(float(actual), float(expected), rtol=1e-10, atol=1e-7):
        raise ValueError(f"Frozen parent metric changed for {label}: {actual} != {expected}")


def _baseline_contract(config: dict) -> tuple[pd.DataFrame, dict]:
    frozen = config["frozen_inputs"]
    week_config = _load_json(ROOT / frozen["week_target_config"])
    week_summary = _load_json(ROOT / frozen["week_target_summary"])
    scaleout = _load_json(ROOT / frozen["scaleout_experiment"] / "summary.json")
    slots = _load_json(ROOT / frozen["slots_experiment"] / "summary.json")
    priority = _load_json(ROOT / frozen["priority_experiment"] / "summary.json")
    selected = _load_json(ROOT / frozen["leadership_experiment"] / "summary.json")

    account = config["account"]
    if week_config["experiment_id"] != "exp-cloud-close-week-target-20261109-v1":
        raise ValueError("Room screen must consume the frozen week-target geometry builder")
    if ROOM_GEOMETRY_FRACTION not in week_config["target_fractions_of_remaining_extension"]:
        raise ValueError("Week-target geometry fraction used by this screen is unavailable")
    if (week_config["baseline_candidate"] != "fixed_2r"
            or float(week_config["account"]["partial_fraction"]) != float(account["partial_fraction"])
            or int(week_config["account"]["slots"]) != int(account["slots"])
            or week_config["account"]["priority_policy"] != account["priority_policy"]):
        raise ValueError("Week-target fixed-2R baseline account contract changed")
    for field in ("maker_per_fill", "taker_per_fill", "slippage_stress_per_taker_fill"):
        if float(week_config["costs"][field]) != float(config["costs"][field]):
            raise ValueError(f"Frozen parent cost changed for {field}")
    if int(week_summary["path_audit"]["target_path_rows"]) != int(
            frozen["expected_week_target_path_rows"]):
        raise ValueError("Frozen week-target path row count changed")
    if float(scaleout["pick"]) != float(account["partial_fraction"]):
        raise ValueError("Frozen parent partial fraction changed")
    if int(slots["pick"]) != int(account["slots"]):
        raise ValueError("Frozen parent slot count changed")
    if (priority["pick"] != account["priority_policy"]
            or int(priority["slots"]) != int(account["slots"])):
        raise ValueError("Frozen parent priority selection changed")
    if (float(selected["pick"]) != float(account["leadership_fraction"])
            or float(selected["best_dev_growth"]) != float(account["leadership_fraction"])):
        raise ValueError("Frozen 90d leadership pick changed")

    metrics_path = ROOT / frozen["leadership_experiment"] / "account_metrics.csv"
    metrics = pd.read_csv(metrics_path)
    baseline = metrics[metrics.top_fraction.eq(float(account["leadership_fraction"]))].copy()
    expected_periods = {"all", "dev", "2025", "2026"}
    if set(baseline.period) != expected_periods:
        raise ValueError("Frozen 90d baseline is missing a required account period")
    for key, expected in frozen["baseline_expected"].items():
        period, field = key.split(".", 1)
        row = baseline[baseline.period.eq(period)]
        if len(row) != 1:
            raise ValueError(f"Frozen 90d baseline has no unique {period} row")
        _close(row.iloc[0][field], expected, key)

    funded_path = ROOT / frozen["leadership_funded"]
    hourly_path = ROOT / frozen["leadership_hourly"]
    funded = pd.read_csv(funded_path, usecols=["event_id", "cash_pnl"])
    hourly = pd.read_csv(hourly_path, usecols=["nav"])
    all_row = baseline[baseline.period.eq("all")].iloc[0]
    cash_final = float(account["starting_equity_usdt"] + funded.cash_pnl.sum())
    hourly_final = float(hourly.nav.iloc[-1])
    _close(cash_final, float(all_row.final_usdt), "baseline funded ledger cash final")
    _close(hourly_final, float(all_row.final_usdt), "baseline hourly NAV final")
    if int(len(funded)) != int(all_row.trades) or funded.event_id.duplicated().any():
        raise ValueError("Frozen 90d baseline funded ledger does not match its metrics")
    if not selected.get("checks", {}).get(str(float(account["leadership_fraction"]))):
        raise ValueError("Frozen 90d baseline controls and stress checks are unavailable")

    return baseline, {
        "week_target_path_audit": week_summary["path_audit"],
        "frozen_parent_picks": {
            "partial_fraction": float(scaleout["pick"]),
            "slots": int(slots["pick"]),
            "priority_policy": priority["pick"],
            "leadership_fraction": float(selected["pick"]),
        },
        "cash_reconciliation": {
            "funded_rows": int(len(funded)),
            "ledger_cash_final_usdt": cash_final,
            "hourly_final_nav_usdt": hourly_final,
            "account_metrics_final_usdt": float(all_row.final_usdt),
            "ledger_minus_metrics_usdt": float(cash_final - all_row.final_usdt),
            "hourly_minus_metrics_usdt": float(hourly_final - all_row.final_usdt),
            "reused_without_replaying": True,
        },
        "checks": selected["checks"][str(float(account["leadership_fraction"]))],
    }


def _room_features(config: dict, runner: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    path = ROOT / config["frozen_inputs"]["week_target_paths"]
    geometry_all = pd.read_csv(path, usecols=list(ROOM_FIELDS), low_memory=False,
                               float_precision="round_trip", dtype={"symbol": str})
    geometry = geometry_all[
        geometry_all.target_fraction.eq(ROOM_GEOMETRY_FRACTION)
    ].copy()
    del geometry_all
    if geometry.event_id.duplicated().any():
        raise ValueError("Week-target geometry must provide one row per event at the selected fraction")
    if len(geometry) != int(config["frozen_inputs"]["expected_runner_events"]):
        raise ValueError("Week-target event geometry population changed")
    if set(geometry.event_id.astype(str)) != set(runner.event_id.astype(str)):
        raise ValueError("Room geometry event identities differ from the frozen 12H runner")
    by_event = geometry.set_index("event_id").loc[runner.event_id.astype(str)]
    if not np.array_equal(by_event.entry_ms.to_numpy(dtype=np.int64),
                          runner.entry_ms.to_numpy(dtype=np.int64)):
        raise ValueError("Week-target geometry entry times differ from the frozen runner")
    if not np.array_equal(by_event.symbol.astype(str).to_numpy(), runner.symbol.astype(str).to_numpy()):
        raise ValueError("Week-target geometry symbols differ from the frozen runner")
    if not np.array_equal(by_event.side.to_numpy(dtype=np.int64),
                          runner.side.to_numpy(dtype=np.int64)):
        raise ValueError("Week-target geometry directions differ from the frozen runner")
    if not np.allclose(by_event.entry_price, runner.entry_price, rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target geometry entry prices differ from the frozen runner")
    if not np.allclose(by_event.stop_price, runner.stop_price, rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target geometry original stops differ from the frozen runner")
    if not geometry.side.isin([-1, 1]).all():
        raise ValueError("Room geometry contains an invalid direction")
    numeric = [
        "entry_price", "stop_price", "risk_distance", "target_reference_price",
        "target_reference_remaining_distance", "target_relative_favorable_pct",
        "target_parent_reference_price", "target_parent_entry_price",
    ]
    if not np.isfinite(geometry[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Room geometry contains a missing or non-finite price field")
    if (geometry.entry_price.le(0) | geometry.stop_price.le(0)
            | geometry.target_reference_price.le(0) | geometry.risk_distance.le(0)).any():
        raise ValueError("Room geometry requires positive entry, stop, reference and risk")

    # These fields come from the week-target's tested directional mapping:
    # numerator and denominator are both quote-price distances; their ratio is R.
    remaining = geometry.side.to_numpy(dtype=float) * (
        geometry.target_reference_price.to_numpy(dtype=float)
        - geometry.entry_price.to_numpy(dtype=float)
    )
    risk = np.abs(geometry.entry_price.to_numpy(dtype=float)
                  - geometry.stop_price.to_numpy(dtype=float))
    if not np.allclose(remaining, geometry.target_reference_remaining_distance,
                       rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target directional remaining-distance field changed")
    if not np.allclose(risk, geometry.risk_distance, rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target risk_distance is not the absolute entry-to-stop distance")
    if not np.allclose(remaining / geometry.entry_price,
                       geometry.target_relative_favorable_pct, rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target relative favorable percentage field changed")
    geometry["room_r"] = remaining / risk

    signals = geometry[~geometry.is_control].set_index("parent_id", drop=False)
    controls = geometry[geometry.is_control]
    if not signals.index.is_unique or controls.parent_id.duplicated().any():
        raise ValueError("Frozen 12H signal/control pairing must be unique by parent")
    if not controls.parent_id.isin(signals.index).all():
        raise ValueError("A frozen control has no corresponding actual parent signal")
    if not np.allclose(signals.target_parent_entry_price, signals.entry_price,
                       rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target signal reference is not anchored to its actual entry")
    if not np.allclose(signals.target_parent_reference_price, signals.target_reference_price,
                       rtol=1e-9, atol=1e-10):
        raise ValueError("Week-target signal reference differs from the original weekly level")
    if len(controls):
        matched = controls.join(
            signals[["entry_price", "target_reference_price"]].rename(columns={
                "entry_price": "actual_parent_entry_price",
                "target_reference_price": "actual_parent_reference_price",
            }), on="parent_id", rsuffix="_parent", validate="many_to_one")
        if not np.allclose(matched.target_parent_entry_price,
                           matched.actual_parent_entry_price, rtol=1e-9, atol=1e-10):
            raise ValueError("Week-target controls no longer inherit the matched parent's entry")
        if not np.allclose(matched.target_parent_reference_price,
                           matched.actual_parent_reference_price, rtol=1e-9, atol=1e-10):
            raise ValueError("Week-target controls no longer inherit the parent's reference")
        if not np.allclose(
                matched.target_reference_price / matched.entry_price,
                matched.target_parent_reference_price / matched.target_parent_entry_price,
                rtol=1e-9, atol=1e-10):
            raise ValueError("Week-target control reference transfer changed")

    return geometry, {
        "geometry_source": config["frozen_inputs"]["week_target_paths"],
        "selected_target_fraction_row": ROOM_GEOMETRY_FRACTION,
        "field_units": {
            "target_reference_remaining_distance": "quote-price units; side-adjusted favorable direction",
            "risk_distance": "quote-price units; abs(actual entry - original stop)",
            "room_r": "dimensionless R = directional remaining distance / original risk distance",
        },
        "field_checks": {
            "remaining_distance_equals_side_times_reference_minus_entry": True,
            "risk_equals_abs_entry_minus_original_stop": True,
            "relative_percent_matches_remaining_over_entry": True,
            "control_reference_transfer_matches_parent_entry_relative_ratio": True,
        },
        "events": int(len(geometry)),
        "signal_events": int((~geometry.is_control).sum()),
        "control_events": int(geometry.is_control.sum()),
        "signal_room_ratio_min": float(signals.room_r.min()),
        "signal_room_ratio_max": float(signals.room_r.max()),
    }


def _entry_period_counts(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"all": 0, "dev": 0, "2025": 0, "2026": 0}
    events = frame.drop_duplicates("parent_id")
    t = events.entry_ms.to_numpy(dtype=np.int64)
    split = int(wallet.SPLIT)
    year_end = pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000
    end = int(wallet.END)
    return {
        "all": int(len(events)),
        "dev": int(((t >= int(wallet.START)) & (t < split)).sum()),
        "2025": int(((t >= split) & (t < year_end)).sum()),
        "2026": int(((t >= year_end) & (t < end)).sum()),
    }


def _candidate_counts(candidate: str, threshold: float | None,
                      signal_parents: pd.DataFrame, control_parents: pd.DataFrame) -> dict:
    if threshold is None:
        selected = signal_parents.copy()
    else:
        selected = signal_parents[signal_parents.room_r.ge(float(threshold))].copy()
    selected_ids = set(selected.parent_id.astype(str))
    available_controls = control_parents[control_parents.parent_id.astype(str).isin(selected_ids)].copy()
    matched_ids = set(available_controls.parent_id.astype(str))
    paired = selected[selected.parent_id.astype(str).isin(matched_ids)]
    room = selected.room_r.astype(float)
    return {
        "candidate": candidate,
        "room_threshold_r": threshold,
        "screened_signal_entries": _entry_period_counts(selected),
        "matched_control_entries": _entry_period_counts(available_controls),
        "paired_signal_entries": _entry_period_counts(paired),
        "unmatched_eligible_signal_entries": _entry_period_counts(
            selected[~selected.parent_id.astype(str).isin(matched_ids)]),
        "room_ratio_summary_r": {
            "min": float(room.min()) if len(room) else None,
            "median": float(room.median()) if len(room) else None,
            "p95": float(room.quantile(0.95)) if len(room) else None,
            "max": float(room.max()) if len(room) else None,
        },
    }


def _period_rows(candidate: str, threshold: float | None,
                 metrics: dict, slots: int, partial: float, policy: str) -> list[dict]:
    return [
        {
            "candidate": candidate,
            "room_threshold_r": threshold,
            "partial_fraction": partial,
            "runner_fraction": 1.0 - partial,
            "slots": slots,
            "priority_policy": policy,
            "period": period,
            **values,
        }
        for period, values in metrics.items()
    ]


def _baseline_rows(baseline: pd.DataFrame, slots: int,
                   partial: float, policy: str) -> list[dict]:
    rows = []
    for record in baseline.to_dict("records"):
        rows.append({
            "candidate": "no_room_filter",
            "room_threshold_r": np.nan,
            "partial_fraction": partial,
            "runner_fraction": 1.0 - partial,
            "slots": slots,
            "priority_policy": policy,
            "period": record["period"],
            **{key: record[key] for key in METRIC_FIELDS},
        })
    return rows


def _rank_low_prior_atr(paths: pd.DataFrame, prior_atr: pd.Series) -> pd.DataFrame:
    ranked = paths.copy()
    ranked["entry_order"] = ranked.source_row_number.map(prior_atr).fillna(np.inf)
    return ranked


def _qualifies(row: pd.Series, constraints: dict) -> bool:
    return bool(
        pd.notna(row.win_pct) and float(row.win_pct) >= float(constraints["win_pct_min"])
        and pd.notna(row.payoff) and float(row.payoff) >= float(constraints["payoff_pct_min"])
        and pd.notna(row.payoff_usdt) and float(row.payoff_usdt) >= float(constraints["payoff_usdt_min"])
        and pd.notna(row.dd_bound_pct) and float(row.dd_bound_pct) <= float(constraints["dd_bound_pct_max"])
        and int(row.trades) >= int(constraints["trades_min"])
    )


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    args = parser.parse_args(argv)
    config_path = args.config if args.config.is_absolute() else ROOT / args.config
    config = _load_json(config_path)
    if [(str(k), v) for k, v in CANDIDATES] != [
            (str(x["id"]), x["minimum_room_r"]) for x in config["candidates"]]:
        raise ValueError("Registered room-screen candidates differ from this frozen builder")

    outputs = {key: ROOT / value for key, value in config["outputs"].items()}
    existing = [str(path) for path in outputs.values() if path.exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite frozen outputs: {existing}")

    baseline, baseline_audit = _baseline_contract(config)
    frozen_paths = wallet.read_paths()
    paths = frozen_paths[frozen_paths.tp_r.isin([0.0, 2.0])].copy()
    runner = paths[paths.tp_r.eq(0.0)].copy()
    if len(runner) != int(config["frozen_inputs"]["expected_runner_events"]):
        raise ValueError("Frozen runner opportunity population changed")
    if runner.event_id.duplicated().any():
        raise ValueError("Frozen runner must contain one row per signal or control event")

    scores = leadership.leadership()
    rankable_index = scores.dropna().index
    event_index = pd.MultiIndex.from_arrays(
        [runner.entry_ms.to_numpy(dtype=np.int64), runner.symbol.astype(str).to_numpy()],
        names=["entry_ms", "symbol"],
    )
    runner["leadership_eligible"] = event_index.isin(rankable_index)
    rankable_by_event = runner.set_index("event_id").leadership_eligible
    paths["leadership_eligible"] = paths.event_id.map(rankable_by_event).fillna(False).astype(bool)
    del scores, rankable_index, rankable_by_event, event_index
    gc.collect()

    geometry, geometry_audit = _room_features(config, runner)
    room_by_event = geometry.set_index("event_id").room_r
    runner["room_r"] = runner.event_id.map(room_by_event)
    if runner.room_r.isna().any():
        raise ValueError("A frozen runner event has no tested entry-room geometry")
    paths["room_r"] = paths.event_id.map(room_by_event)
    if paths.room_r.isna().any():
        raise ValueError("A frozen 2R/runner path has no tested entry-room geometry")
    del room_by_event, geometry
    gc.collect()

    baseline_real_runner = runner[runner.leadership_eligible & ~runner.is_control].copy()
    baseline_control_runner = runner[runner.leadership_eligible & runner.is_control].copy()
    baseline_parent_ids = set(baseline_real_runner.parent_id.astype(str))
    baseline_control_runner = baseline_control_runner[
        baseline_control_runner.parent_id.astype(str).isin(baseline_parent_ids)
    ].copy()
    signal_parents = baseline_real_runner[
        ["parent_id", "event_id", "entry_ms", "symbol", "room_r"]
    ].copy()
    control_parents = baseline_control_runner[
        ["parent_id", "event_id", "entry_ms", "symbol"]
    ].copy()
    if signal_parents.parent_id.duplicated().any() or control_parents.parent_id.duplicated().any():
        raise ValueError("Frozen 90d arm requires unique signal/control parent identities")
    if not control_parents.parent_id.isin(signal_parents.parent_id).all():
        raise ValueError("A rankable 90d control has no rankable parent signal")
    parent_entry = signal_parents.set_index("parent_id").entry_ms
    if (control_parents.entry_ms.to_numpy(dtype=np.int64)
            <= control_parents.parent_id.map(parent_entry).to_numpy(dtype=np.int64)).any():
        raise ValueError("A matched control must open after its parent signal")

    expected_counts = config["frozen_inputs"]["expected_90d_arm_counts"]
    if len(signal_parents) != int(expected_counts["signal_events"]):
        raise ValueError("Frozen 90d rankable signal count changed")
    if len(control_parents) != int(expected_counts["matched_control_events"]):
        raise ValueError("Frozen 90d rankable matched-control count changed")

    source = pd.read_csv(
        ROOT / config["frozen_inputs"]["signals_csv"], usecols=["prior_atr_pct"],
        low_memory=False,
    )
    if len(source) != int(config["frozen_inputs"]["expected_signal_rows"]):
        raise ValueError("Frozen signal source row count changed")
    source.index = pd.RangeIndex(1, len(source) + 1)
    prior_atr = source.prior_atr_pct
    paths = _rank_low_prior_atr(paths, prior_atr)

    partial = float(config["account"]["partial_fraction"])
    slots = int(config["account"]["slots"])
    policy = str(config["account"]["priority_policy"])
    rows = _baseline_rows(baseline, slots, partial, policy)
    counts_by_candidate: dict[str, dict] = {}
    candidate_pools: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    curves: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}

    for candidate, threshold in CANDIDATES:
        counts = _candidate_counts(candidate, threshold, signal_parents, control_parents)
        counts_by_candidate[candidate] = counts
        if threshold is None:
            candidate_real_parent_ids = baseline_parent_ids
        else:
            candidate_real_parent_ids = set(
                signal_parents.loc[signal_parents.room_r.ge(float(threshold)), "parent_id"].astype(str)
            )
        if len(candidate_real_parent_ids) != counts["screened_signal_entries"]["all"]:
            raise ValueError(f"{candidate} screen count does not match the account event set")
        real = paths[
            ~paths.is_control & paths.leadership_eligible
            & paths.parent_id.astype(str).isin(candidate_real_parent_ids)
        ].copy()
        controls = paths[
            paths.is_control & paths.leadership_eligible
            & paths.parent_id.astype(str).isin(candidate_real_parent_ids)
            & paths.parent_id.astype(str).isin(set(control_parents.parent_id.astype(str)))
        ].copy()
        if controls.parent_id.astype(str).nunique() != counts["matched_control_entries"]["all"]:
            raise ValueError(f"{candidate} matched-control count does not match its parent screen")
        if candidate == "no_room_filter":
            candidate_pools[candidate] = (real, controls)
            continue  # Reuse frozen 90d metrics and path outputs; do not replay baseline.
        if not real.empty and real.parent_id.nunique() != len(candidate_real_parent_ids):
            raise ValueError(f"{candidate} actual path arms do not cover each eligible parent")
        metrics, funded, hourly = capacity.run(real, slots, partial)
        rows.extend(_period_rows(candidate, threshold, metrics, slots, partial, policy))
        candidate_pools[candidate] = (real, controls)
        curves[candidate] = (funded, hourly)
        print(f"Accounted {candidate}: {len(candidate_real_parent_ids)} eligible signals", flush=True)
        del metrics, funded, hourly, real, controls
        gc.collect()

    frame = pd.DataFrame(rows)
    dev = frame[frame.period.eq("dev")].copy()
    order = {name: i for i, (name, _) in enumerate(CANDIDATES)}
    dev["_order"] = dev.candidate.map(order)
    dev = dev.sort_values(["multiple", "_order"], ascending=[False, True], kind="stable")
    constraints = config["selection"]["constraints"]
    eligible = dev[dev.apply(lambda row: _qualifies(row, constraints), axis=1)]
    pick = str(eligible.candidate.iloc[0]) if len(eligible) else "no_room_filter"
    best = str(dev.candidate.iloc[0])

    checks: dict[str, dict] = {}
    for candidate in sorted({pick, best}, key=lambda name: order[name]):
        if candidate == "no_room_filter":
            checks[candidate] = baseline_audit["checks"]
            continue
        real, controls = candidate_pools[candidate]
        matched_ids = set(controls.parent_id.astype(str))
        paired = real[real.parent_id.astype(str).isin(matched_ids)]
        checks[candidate] = {
            "control": capacity.run(controls, slots, partial)[0] if len(controls) else None,
            "paired_signals": capacity.run(paired, slots, partial)[0] if len(paired) else None,
            "slippage_5bp": capacity.run(real, slots, partial, 0.0005)[0] if len(real) else None,
            "matched_signal_entries": int(paired.parent_id.nunique()),
            "matched_control_entries": int(controls.parent_id.nunique()),
            "paired_definition": (
                "Frozen control parent_id is retained only when the parent actual signal passed this room threshold; "
                "control must also be rankable in the frozen 90d top_fraction=1 arm at its own entry. "
                "No room threshold is applied to the control itself."
            ),
        }
        gc.collect()

    outputs["account_metrics_csv"].parent.mkdir(parents=True, exist_ok=True)
    for candidate, (funded, hourly) in curves.items():
        funded.to_csv(EXP / f"funded_{candidate}.csv.gz", index=False, compression="gzip")
        hourly.to_csv(EXP / f"hourly_{candidate}.csv.gz", index_label="hour_end_utc")
    frame.to_csv(outputs["account_metrics_csv"], index=False)

    summary = {
        "experiment_id": config["experiment_id"],
        "question": config["question"],
        "selection": {
            "development_period": "entry and exit before 2025-01-01 UTC; same frozen account split",
            "objective": "maximum development ending account capital among candidates satisfying every registered gate",
            "constraints": constraints,
            "eligible": eligible.candidate.tolist(),
            "pick": pick,
            "best_dev_growth": best,
            "fallback": "reuse the original 90d no-room-filter baseline if no candidate qualifies",
        },
        "room_screen": {
            "formula": "side * (original opposite Monday 1.618 reference - actual confirmation-entry price) / abs(actual entry - original stop)",
            "thresholds_r": [None, 2.0, 3.0, 5.0],
            "screen_timing": "evaluated at the actual next-1H-open entry after the completed UTC 12H confirmation; reference and original stop are frozen signal geometry",
            "count_period_basis": "signal/control counts are grouped by entry_ms; account metric trade counts require both entry and exit inside the period",
            "baseline_scope": "no_room_filter still requires rankability in the frozen 90-day top_fraction=1.0 arm",
            "candidate_counts": counts_by_candidate,
            "geometry_audit": geometry_audit,
            "matched_controls": (
                "Controls are from the previously eligible parent signal set; each frozen control must also be rankable "
                "in the 90-day arm at its own entry, and its parent must pass the candidate room gate. The control's "
                "own room ratio is not screened. wallet.read_paths verifies control entry is strictly after the parent entry."
            ),
        },
        "frozen_baseline": {
            "candidate": "no_room_filter",
            "source_account_metrics": config["frozen_inputs"]["leadership_metrics"],
            "source_funded_ledger": config["frozen_inputs"]["leadership_funded"],
            "source_hourly_ledger": config["frozen_inputs"]["leadership_hourly"],
            "account_metrics": baseline.to_dict("records"),
            **baseline_audit["cash_reconciliation"],
            "replayed": False,
        },
        "parent_picks": baseline_audit["frozen_parent_picks"],
        "checks": checks,
        "costs_and_limits": {
            "starting_equity_usdt": float(config["account"]["starting_equity_usdt"]),
            "taker_fee_per_fill": float(config["costs"]["taker_per_fill"]),
            "resting_maker_fee_per_fill": float(config["costs"]["maker_per_fill"]),
            "slippage_stress": "additional 5bp per taker fill; resting 2R target fills do not receive market slippage",
            "funding": "not modeled",
            "capacity": "one shared 1000U account, nine concurrent slots; no exchange depth or market-impact model",
            "fills": "reuses frozen V1.7 VectorBT OHLC path semantics and the existing cash-sharing account; resting 2R limit is assumed filled at the frozen target",
            "liquidation": "exchange-specific maintenance-margin tiers and liquidation are not modeled",
        },
        "outputs": {
            "account_metrics_csv": str(outputs["account_metrics_csv"].relative_to(ROOT)),
            "summary_json": str(outputs["summary_json"].relative_to(ROOT)),
            "funded_and_hourly": "funded_<candidate>.csv.gz and hourly_<candidate>.csv for the three filtered candidates; no_room_filter reuses its frozen parent files",
        },
    }
    outputs["summary_json"].write_text(
        json.dumps(_json_safe(summary), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(frame[frame.period.eq("all")].to_string(index=False), flush=True)
    print(json.dumps({"pick": pick, "best_dev_growth": best,
                      "eligible": eligible.candidate.tolist(),
                      "counts": counts_by_candidate}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
