"""Measure same-time slot admission priorities on frozen breakout paths.

Only account admission order changes. The baseline CRC32 wallet result is
reused from the frozen 360-name run; alternate admission orders use the same
native VectorBT shared-cash account and the identical pre-priced event paths.
"""
from __future__ import annotations

import argparse
import json
import math
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_branch as branch
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine

ROOT = engine.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-breakout-admission-20261109-v1"
CONFIG = EXP / "config.json"
PERIODS = branch.PERIODS
BASE_POLICIES = ("crc32_original", "close_risk_min", "close_risk_max")


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(_json_safe(value), ensure_ascii=False,
                                    indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_inputs(config: dict) -> tuple[pd.DataFrame, dict, dict]:
    path = ROOT / config["data"]["frozen_paths_csv_gz"]
    paths = pd.read_csv(path, dtype={"symbol": str})
    events = paths[(paths.arm.eq("breakout")) & paths.tp_r.eq(0.0)
                   & paths.status.eq("executed") & ~paths.is_control].copy()
    if len(events) != int(config["data"]["expected_executable_actual_paths"]):
        raise ValueError(f"Unexpected executable breakout path count: {len(events)}")
    if events.event_id.duplicated().any() or events.parent_id.duplicated().any():
        raise ValueError("Executable actual breakout identities must be unique")
    for column in ("key_open_ms", "confirmation_ms", "entry_ms", "side"):
        events[column] = pd.to_numeric(events[column], errors="raise").astype("int64")
    for column in ("entry_price", "stop_price", "risk_fraction"):
        events[column] = pd.to_numeric(events[column], errors="raise")
    if (events.entry_ms.to_numpy() != events.confirmation_ms.to_numpy()).any():
        raise ValueError("Entry must use the exact confirming 12H close's next H1 open")
    if (events.confirmation_ms.to_numpy() != events.key_open_ms.to_numpy() + engine.STEP_12H).any():
        raise ValueError("Confirmation timestamp is not the native 12H bar close")
    if (events.risk_fraction.isna() | (events.risk_fraction <= 0)).any():
        raise ValueError("Every executable path must have positive entry-open risk")

    base_summary_path = ROOT / config["account"]["baseline"]["summary_json"]
    base_summary = json.loads(base_summary_path.read_text(encoding="utf-8"))
    base_metrics_path = ROOT / config["account"]["baseline"]["account_metrics_csv"]
    base_metrics = pd.read_csv(base_metrics_path)
    base_metrics = base_metrics[base_metrics.arm.eq("breakout")
                                & base_metrics.scenario.eq("actual")].copy()
    base_funded_path = ROOT / config["account"]["baseline"]["funded_actual_csv_gz"]
    base_funded = pd.read_csv(base_funded_path, usecols=["event_id", "symbol", "entry_ms"])
    if len(base_funded) != int(config["account"]["baseline"]["funded_positions"]):
        raise ValueError("Frozen CRC baseline funded count changed")
    if not base_metrics.period.isin(PERIODS).all() or set(base_metrics.period) != set(PERIODS):
        raise ValueError("Frozen CRC baseline metrics do not cover all periods")
    return events, {"summary": base_summary, "metrics": base_metrics,
                    "funded": base_funded}, {"paths_csv_gz": str(path.relative_to(ROOT))}


def _confirmation_closes(events: pd.DataFrame, history_config: dict) -> pd.DataFrame:
    close_by_symbol = {}
    retired = history_config["data"].get("retired_h1_sources", {})
    source_paths = dict(zip(events.symbol, events.source_path_12h))
    for symbol in sorted(events.symbol.unique()):
        source_path = source_paths[symbol]
        if symbol in retired:
            bars12, _, _, _, _, _ = branch._load_symbol(symbol, history_config)
        else:
            p12 = ROOT / str(source_path)
            if not p12.is_file():
                raise FileNotFoundError(f"Native 12H source for {symbol} is unavailable: {source_path}")
            bars12 = engine._load_bars(p12, "12h")
        close_by_symbol[symbol] = bars12.close

    confirmation_close = np.empty(len(events), dtype=np.float64)
    missed = []
    for pos, row in enumerate(events.itertuples(index=False)):
        series = close_by_symbol[row.symbol]
        key = int(row.key_open_ms)
        if key not in series.index:
            missed.append((row.symbol, key))
            continue
        confirmation_close[pos] = float(series.loc[key])
    if missed:
        raise ValueError(f"Missing native 12H confirmation closes: {missed[:5]} (count={len(missed)})")
    close_distance = events.side.to_numpy(dtype=float) * (
        confirmation_close - events.stop_price.to_numpy(dtype=float)) / confirmation_close
    if not np.isfinite(close_distance).all() or np.any(close_distance <= 0):
        bad = events.loc[(~np.isfinite(close_distance)) | (close_distance <= 0),
                         ["symbol", "event_id", "side", "key_open_ms", "stop_price"]].head(10)
        raise ValueError(f"Confirmation-close risk distance is not adverse-positive: {bad.to_dict('records')}")
    result = events.copy()
    result["confirmation_close"] = confirmation_close
    result["close_stop_distance_pct"] = close_distance
    result["entry_open_stop_distance_pct"] = result.risk_fraction
    result["crc32_priority"] = [zlib.crc32(s.encode("utf-8")) for s in result.symbol]
    return result


def _assign_priority_order(events: pd.DataFrame, mode: str) -> pd.DataFrame:
    if mode not in ("close_risk_min", "close_risk_max"):
        raise ValueError(f"Unsupported priority mode: {mode}")
    ordered = events.copy()
    ordered["entry_order"] = -1
    for index in ordered.groupby("entry_ms", sort=False).groups.values():
        block = ordered.loc[index]
        ordered_block = block.sort_values(
            ["close_stop_distance_pct", "crc32_priority", "symbol", "event_id"],
            ascending=[mode == "close_risk_min", True, True, True],
            kind="mergesort",
        )
        ordered.loc[ordered_block.index, "entry_order"] = np.arange(len(ordered_block), dtype=np.int64)
    if (ordered.entry_order < 0).any() or ordered.entry_order.isna().any():
        raise AssertionError("Every same-time opportunity must receive a deterministic entry order")
    return ordered


def _priority_audit(events: pd.DataFrame) -> dict:
    group_sizes = events.groupby("entry_ms").size()
    tied_close = events.groupby(["entry_ms", "close_stop_distance_pct"]).size()
    tied_entry = events.groupby(["entry_ms", "entry_open_stop_distance_pct"]).size()
    changed_groups = 0
    changed_events = 0
    for _, group in events.groupby("entry_ms", sort=False):
        close_order = group.sort_values(
            ["close_stop_distance_pct", "crc32_priority", "symbol", "event_id"],
            ascending=[True, True, True, True], kind="mergesort").event_id.tolist()
        open_order = group.sort_values(
            ["entry_open_stop_distance_pct", "crc32_priority", "symbol", "event_id"],
            ascending=[True, True, True, True], kind="mergesort").event_id.tolist()
        if close_order != open_order:
            changed_groups += 1
            changed_events += len(group)
    return {
        "opportunities": len(events),
        "unique_entry_timestamps": int(group_sizes.size),
        "same_time_competition_groups": int((group_sizes > 1).sum()),
        "opportunities_in_same_time_competition_groups": int(group_sizes[group_sizes > 1].sum()),
        "same_time_groups_with_equal_close_distance": int(
            (tied_close.groupby(level=0).max() > 1).sum()),
        "same_time_groups_with_equal_entry_open_distance": int(
            (tied_entry.groupby(level=0).max() > 1).sum()),
        "same_time_groups_with_different_close_vs_entry_open_order": changed_groups,
        "opportunities_in_groups_with_different_close_vs_entry_open_order": changed_events,
    }


def _metrics_for(funded: pd.DataFrame, equity: pd.DataFrame) -> dict:
    return {period: wallet.summarize(funded, equity, *bounds)
            for period, bounds in PERIODS.items()}


def _same_time_account(events: pd.DataFrame, quote_loader) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    data = wallet.prepare(events, slots=9, start=wallet.START, end=wallet.END,
                          quote_loader=quote_loader)
    funded, equity = wallet.account(data, slip=0.0, exposure=1.0)
    metrics = _metrics_for(funded, equity)
    if not np.isclose(1000.0 + funded.cash_pnl.sum(), equity.nav.iloc[-1],
                      rtol=1e-8, atol=.005):
        raise ValueError("Priority account does not reconcile cash PnL to marked final NAV")
    meta = {
        "input_executable_actual_paths": len(events),
        "admitted": len(data["selected"]),
        "funded": len(funded),
        "rejected": data["skips"],
        "unquoted_position_hours": int(data["gap_marks"]),
    }
    return metrics, funded, equity, meta


def _mini_check() -> dict:
    """Check direction, stable ties, and full-slot same-time admission only."""
    events = pd.DataFrame({
        "event_id": [f"mini-{i:02d}" for i in range(11)],
        "symbol": [f"S{i:02d}" for i in range(11)],
        "entry_ms": [123_000] * 11,
        "exit_bar_ms": [456_000] * 11,
        "close_stop_distance_pct": np.arange(1, 12, dtype=float) / 100,
    })
    events["crc32_priority"] = [zlib.crc32(s.encode("utf-8")) for s in events.symbol]
    low = _assign_priority_order(events, "close_risk_min")
    high = _assign_priority_order(events, "close_risk_max")
    selected_low, skip_low = wallet.admit(low, 9)
    selected_high, skip_high = wallet.admit(high, 9)
    expected_low = set(events.nsmallest(9, "close_stop_distance_pct").event_id)
    expected_high = set(events.nlargest(9, "close_stop_distance_pct").event_id)
    if set(selected_low.event_id) != expected_low or set(selected_high.event_id) != expected_high:
        raise AssertionError("Same-time close-risk priority did not select the expected nine events")
    if skip_low["all_slots_busy"] != 2 or skip_high["all_slots_busy"] != 2:
        raise AssertionError("Mini admission must reject exactly two same-time events")
    return {"same_time_events": len(events), "slots": 9,
            "min_priority_selected": sorted(selected_low.event_id),
            "max_priority_selected": sorted(selected_high.event_id),
            "rejected_per_policy": 2, "existing_admission_runner": "cloud_close_reward_account.admit"}


def run() -> dict:
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    if config["experiment_id"] != EXP.name:
        raise ValueError("Unexpected experiment_id")
    if (EXP / config["outputs"]["summary_json"]).exists():
        raise FileExistsError("Refusing to overwrite completed breakout admission study")
    if (int(config["account"]["start_ms"]) != wallet.START
            or int(config["account"]["end_ms"]) != wallet.END):
        raise ValueError("Admission study must use the frozen nine-slot wallet time bounds")
    history_config = json.loads((ROOT / config["data"]["history_config"]).read_text(encoding="utf-8"))
    events, baseline, source_meta = _read_inputs(config)
    events = _confirmation_closes(events, history_config)
    priority_audit = _priority_audit(events)
    quote_loader = branch._quote_loader(history_config)

    priority_rows = events[["event_id", "parent_id", "symbol", "side", "week",
                            "key_open_ms", "confirmation_ms", "entry_ms", "entry_price",
                            "stop_price", "risk_fraction", "confirmation_close",
                            "close_stop_distance_pct", "entry_open_stop_distance_pct",
                            "crc32_priority"]].copy()
    low_events = _assign_priority_order(events, "close_risk_min")
    high_events = _assign_priority_order(events, "close_risk_max")
    priority_rows["entry_order_min"] = low_events.entry_order
    priority_rows["entry_order_max"] = high_events.entry_order
    priority_rows.to_csv(EXP / config["outputs"]["priority_audit_csv_gz"], index=False, compression="gzip")

    base_selected = set(baseline["funded"].event_id)
    events_by_id = events.set_index("event_id", drop=False)
    scenarios = {}
    metrics_rows = []
    admission_changes = []
    for policy in BASE_POLICIES:
        if policy == "crc32_original":
            metric_rows = baseline["metrics"]
            acct = baseline["summary"]["accounts"]["breakout"]["actual"]
            selected = baseline["funded"]
            scenarios[policy] = {
                "metrics": {row.period: {k: getattr(row, k) for k in metric_rows.columns
                                         if k not in ("arm", "scenario", "period")}
                            for row in metric_rows.itertuples(index=False)},
                "admission": acct["admission"],
                "funded_positions": int(acct["funded_positions"]),
                "exit_reasons": acct["exit_reasons"],
                "baseline_source": config["account"]["baseline"]["funded_actual_csv_gz"],
            }
            for row in metric_rows.itertuples(index=False):
                metrics_rows.append({"policy": policy, "period": row.period,
                                     **{k: getattr(row, k) for k in metric_rows.columns
                                        if k not in ("arm", "scenario", "period")}})
        else:
            sample = low_events if policy == "close_risk_min" else high_events
            metrics, funded, equity, admission = _same_time_account(sample, quote_loader)
            scenarios[policy] = {
                "metrics": metrics,
                "admission": admission,
                "funded_positions": len(funded),
                "exit_reasons": funded.exit_reason.value_counts().to_dict(),
            }
            funded.to_csv(EXP / config["outputs"]["funded_files"][policy], index=False, compression="gzip")
            equity.to_csv(EXP / config["outputs"]["hourly_files"][policy], index_label="hour_end_utc",
                          compression="gzip")
            for period, values in metrics.items():
                metrics_rows.append({"policy": policy, "period": period, **values})
            selected = funded
        selected_ids = set(selected.event_id)
        scenarios[policy]["selected_vs_crc32"] = {
            "same_as_crc32": selected_ids == base_selected,
            "retained_from_crc32": len(selected_ids & base_selected),
            "added_vs_crc32": len(selected_ids - base_selected),
            "dropped_vs_crc32": len(base_selected - selected_ids),
        }
        if policy != "crc32_original":
            for event_id, status in (
                [(event_id, "added") for event_id in sorted(selected_ids - base_selected)]
                + [(event_id, "dropped") for event_id in sorted(base_selected - selected_ids)]
            ):
                row = events_by_id.loc[event_id]
                admission_changes.append({
                    "policy": policy, "event_id": event_id, "parent_id": row.parent_id,
                    "symbol": row.symbol, "side": int(row.side), "entry_ms": int(row.entry_ms),
                    "change_vs_crc32": status,
                    "confirmation_close": float(row.confirmation_close),
                    "stop_price": float(row.stop_price),
                    "close_stop_distance_pct": float(row.close_stop_distance_pct),
                    "entry_open_stop_distance_pct": float(row.entry_open_stop_distance_pct),
                })

    pd.DataFrame(metrics_rows).to_csv(EXP / config["outputs"]["account_metrics_csv"], index=False)
    pd.DataFrame(admission_changes).to_csv(
        EXP / config["outputs"]["admission_changes_csv_gz"], index=False, compression="gzip")
    summary = {
        "experiment_id": EXP.name,
        "source_paths": source_meta,
        "universe": {"historical_names": 360, "frozen_path_rows_reused": True,
                     "new_signals_or_path_pricing": False,
                     "actual_executable_breakout_opportunities": len(events)},
        "single_variable": config["single_variable"],
        "priority_audit": priority_audit,
        "risk_semantics": {
            "primary_priority_variable": "side*(confirmed_12h_close-stop_price)/confirmed_12h_close",
            "known_at": "The close of the signal's completed native UTC 12H candle; fixed stop already known.",
            "entry_open_risk_audit": "Path risk_fraction is retained separately as entry-open stop distance; it is not used for priority.",
            "tie_break": "CRC32(symbol), symbol, event_id in the same ascending order as the frozen wallet admission rule.",
        },
        "accounts": scenarios,
        "baseline": config["account"]["baseline"],
        "outputs": config["outputs"],
    }
    _write_json(EXP / config["outputs"]["summary_json"], summary)
    print(json.dumps(_json_safe(summary), ensure_ascii=False, indent=2), flush=True)
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mini-check", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args(argv)
    if args.mini_check:
        print(json.dumps(_mini_check(), ensure_ascii=False, indent=2), flush=True)
    else:
        run()


if __name__ == "__main__":
    main()
