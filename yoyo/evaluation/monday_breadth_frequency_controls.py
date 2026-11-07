"""Causal matched controls for the locked 2026 breadth-frequency candidates.

The matcher is shared with ``monday_breadth_opt_controls``: same-asset monthly
6-hour session, prior-1h volatility/trend, observation calendar, prior-known
shock exclusion, and no overlap with the candidate's reserved holding interval.
Fixed controls use the locked six-day market exit. Dynamic controls use the
same native VectorBT policy as the locked event. Neither side includes funding.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_controls as matcher
from yoyo.evaluation import monday_breadth_opt_exits as exits
from yoyo.evaluation import monday_breadth_opt_match_exits as match_stats
from yoyo.evaluation.monday_breadth_opt_payoffs import load_full_ohlc


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
FIXED_INPUT = EXPERIMENT / "fixed"
LONG_INPUT = EXPERIMENT / "long"
CONTROL_DIR = EXPERIMENT / "controls"
DATA_DIR = ROOT / "data/research/monday_breadth_frequency_20261108"
ETH_ACTUAL_CACHE = DATA_DIR / "eth_67.parquet"
REFERENCE_POLICIES = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1/exits/policies.csv"
BAR = pd.Timedelta(minutes=5)
YEAR_START = pd.Timestamp("2026-01-01", tz="UTC")
YEAR_END = pd.Timestamp("2027-01-01", tz="UTC")
PRIMARY_DYNAMIC_POLICY_ID = 67
DYNAMIC_HOLD_HOURS = 144
ASSETS = {"BTC", "ETH", "SOL", "ALT"}


def _utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _read_locked(input_dir: Path, source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read and normalize a locked rule/event pair without inferring new rules."""
    rules_path, events_path = input_dir / "locked_rules.csv", input_dir / "locked_events.parquet"
    if not rules_path.is_file() or not events_path.is_file():
        raise FileNotFoundError(f"Expected locked pair: {rules_path} and {events_path}")
    rules, events = pd.read_csv(rules_path), pd.read_parquet(events_path)
    if "candidate_id" not in rules or "candidate_id" not in events:
        raise ValueError(f"{source} lock tables must include candidate_id")
    rules["candidate_id"] = rules.candidate_id.astype(str)
    events["candidate_id"] = events.candidate_id.astype(str)
    if rules.candidate_id.duplicated().any():
        duplicates = rules.loc[rules.candidate_id.duplicated(keep=False), "candidate_id"].unique().tolist()
        raise ValueError(f"{source} locked rules need one row per candidate_id; duplicates: {duplicates[:8]}")

    joined = events.merge(rules, on="candidate_id", how="left", suffixes=("_event", "_rule"),
                          validate="many_to_one", indicator=True)
    unmatched = joined.loc[joined._merge.ne("both"), "candidate_id"].unique().tolist()
    if unmatched:
        raise ValueError(f"{source} locked events lack a locked rule: {unmatched[:8]}")
    joined = joined.drop(columns="_merge")

    for col in ("asset", "minutes", "wait_min", "hold_hours", "role", "policy_id", "epoch", "net_pct"):
        left, right = f"{col}_event", f"{col}_rule"
        if left in joined and right in joined:
            joined[col] = joined[left].where(joined[left].notna(), joined[right])
        elif left in joined:
            joined[col] = joined[left]
        elif right in joined:
            joined[col] = joined[right]
        elif col not in joined:
            joined[col] = np.nan

    if "decision" not in joined:
        raise ValueError(f"{source} locked_events.parquet is missing decision")
    joined["decision"] = pd.to_datetime(joined.decision, utc=True)
    if "known" in joined:
        joined["known"] = pd.to_datetime(joined.known, utc=True)
    else:
        joined["known"] = pd.NaT
    for col in ("minutes", "wait_min", "hold_hours"):
        joined[col] = pd.to_numeric(joined[col], errors="coerce")
    joined["known"] = joined.known.where(
        joined.known.notna(), joined.decision - pd.to_timedelta(joined.wait_min, unit="m"),
    )
    joined["asset"] = joined.asset.map(matcher._asset)
    joined["role"] = joined.role.fillna("frequency_primary" if source == "fixed" else "dynamic_primary")
    joined["policy_id"] = joined.policy_id.where(joined.policy_id.notna(), joined.candidate_id)
    joined["source"] = "search" if source == "fixed" else "exits"
    joined["input_group"] = source
    joined["actual_trade_id"] = joined.get("trade_id", joined.candidate_id).astype(str)
    joined["epoch"] = joined.epoch.fillna(3)
    joined["epoch_rank"] = joined.epoch.map(matcher._epoch_rank)
    joined["epoch_rank"] = joined.epoch_rank.replace(0, 3)
    joined["net_pct"] = pd.to_numeric(joined.net_pct, errors="coerce")
    joined = joined.dropna(subset=["asset", "minutes", "wait_min", "hold_hours", "known", "decision"])
    joined["minutes"] = joined.minutes.astype(int)
    joined["wait_min"] = joined.wait_min.astype(int)
    joined["hold_hours"] = joined.hold_hours.astype(int)
    if not (joined.decision == joined.known + pd.to_timedelta(joined.wait_min, unit="m")).all():
        raise ValueError(f"{source} decision must equal known + wait_min")

    # The assigned study is limited to the final 2026 actual-entry sample.
    joined = joined[joined.decision.ge(YEAR_START) & joined.decision.lt(YEAR_END)].copy()
    joined["rule_event_id"] = joined.apply(
        lambda r: "|".join((source, str(r.candidate_id), str(r.policy_id), str(r.role), r.decision.isoformat())),
        axis=1,
    )
    if joined.rule_event_id.duplicated().any():
        raise ValueError(f"{source} has duplicate actual candidate event keys")

    rule_rows = rules.copy()
    rule_rows["candidate_id"] = rule_rows.candidate_id.astype(str)
    rule_rows["input_group"] = source
    if "asset" in rule_rows:
        rule_rows["asset"] = rule_rows.asset.map(matcher._asset)
    if "role" not in rule_rows:
        rule_rows["role"] = "frequency_primary" if source == "fixed" else "dynamic_primary"
    return joined.reset_index(drop=True), rule_rows.reset_index(drop=True)


def _attach_dynamic_actuals(events: pd.DataFrame) -> pd.DataFrame:
    """Use the supplied native ETH policy-67 fills; never write that cache."""
    result = events.copy()
    result["locked_net_pct"] = result.net_pct
    result["actual_source"] = np.where(result.net_pct.notna(), "locked_events.parquet", "missing")
    if not ETH_ACTUAL_CACHE.is_file():
        return result
    actual = pd.read_parquet(ETH_ACTUAL_CACHE)
    required = {"entry_time", "policy_id", "net_pct"}
    if not required.issubset(actual.columns):
        raise ValueError(f"{ETH_ACTUAL_CACHE} must include {sorted(required)}")
    actual = actual[pd.to_numeric(actual.policy_id, errors="coerce").eq(PRIMARY_DYNAMIC_POLICY_ID)].copy()
    actual["entry_time"] = pd.to_datetime(actual.entry_time, utc=True)
    actual["net_pct"] = pd.to_numeric(actual.net_pct, errors="coerce")
    if actual.duplicated("entry_time").any():
        raise ValueError("Supplied ETH policy-67 cache has duplicate entry_time rows")
    actual = actual.rename(columns={"entry_time": "cache_entry_time", "net_pct": "native_cache_net_pct"})
    actual = actual[["cache_entry_time", "native_cache_net_pct"]]
    result = result.merge(actual, left_on="decision", right_on="cache_entry_time", how="left", validate="one_to_one")
    result = result.drop(columns="cache_entry_time")
    both = result.locked_net_pct.notna() & result.native_cache_net_pct.notna()
    if both.any() and not np.allclose(result.loc[both, "locked_net_pct"],
                                      result.loc[both, "native_cache_net_pct"], atol=1e-8, rtol=0):
        worst = (result.loc[both, "locked_net_pct"] - result.loc[both, "native_cache_net_pct"]).abs().max()
        raise ValueError(f"Locked ETH policy-67 outcomes disagree with supplied native cache by {worst:.9g} pp")
    use_cache = result.native_cache_net_pct.notna()
    result.loc[use_cache, "net_pct"] = result.loc[use_cache, "native_cache_net_pct"]
    result.loc[use_cache, "actual_source"] = str(ETH_ACTUAL_CACHE.relative_to(ROOT))
    return result


def _assert_dynamic_policy(rules: pd.DataFrame) -> dict[str, Any]:
    """Fail closed unless runtime policy 67 matches the recorded native policy."""
    expected = exits.policies(False)[PRIMARY_DYNAMIC_POLICY_ID]
    reference = pd.read_csv(REFERENCE_POLICIES)
    stored = reference.loc[pd.to_numeric(reference.policy_id, errors="coerce").eq(PRIMARY_DYNAMIC_POLICY_ID)]
    if len(stored) != 1:
        raise ValueError("Optimization policy table does not have exactly one policy_id=67")
    row = stored.iloc[0]
    fields = ("kind", "hours", "stop_pct", "activate_pct", "width", "take_pct", "take_fraction")
    for field in fields:
        actual_value = getattr(expected, field)
        recorded = row[field]
        if isinstance(actual_value, str):
            equal = str(recorded) == actual_value
        else:
            equal = bool(np.isclose(float(recorded), float(actual_value), rtol=0, atol=1e-12))
        if not equal:
            raise ValueError(f"Runtime policy_id=67 differs from locked reference at {field}: {actual_value} vs {recorded}")
    scoped = rules[rules.role.astype(str).str.lower().eq("dynamic_primary")].copy()
    ids = pd.to_numeric(scoped.policy_id, errors="coerce") if "policy_id" in scoped else pd.Series(dtype=float)
    if len(scoped) and not ids.eq(PRIMARY_DYNAMIC_POLICY_ID).all():
        raise ValueError("dynamic_primary rules must all use native policy_id=67")
    if len(scoped) and not set(scoped.asset.map(matcher._asset)).issubset({"ETH"}):
        raise ValueError("This locked policy-67 cache covers the assigned ETH dynamic candidate only")
    return {
        "policy_id": PRIMARY_DYNAMIC_POLICY_ID,
        **{field: getattr(expected, field) for field in fields},
        "runtime_policy_key": expected.key,
        "reference_file": str(REFERENCE_POLICIES.relative_to(ROOT)),
        "market_entry_fee_pct": exits.TAKER * 100,
        "market_exit_fee_pct": exits.TAKER * 100,
        "funding_in_actual_or_random": False,
    }


def _make_tasks(events: pd.DataFrame, rules: pd.DataFrame, source: str):
    selected = events.copy()
    if source == "long":
        selected = selected[selected.role.astype(str).str.lower().eq("dynamic_primary")].copy()
    else:
        selected = selected[selected.asset.isin(ASSETS)].copy()
    selected["scope_selected"] = True
    candidates = selected.copy()
    signals = matcher._signal_lookup()
    alt_keys = {
        (int(row.minutes), _utc(row.known)) for row in selected.itertuples(index=False)
        if row.asset == "ALT"
    }
    members = matcher._member_lookup(alt_keys)
    tasks, event_meta = matcher._build_task_rows(candidates, selected, signals, members)
    if not event_meta.empty:
        event_meta["input_group"] = source
    if not tasks.empty:
        tasks["input_group"] = source
    return tasks, event_meta


def _collect_matches(tasks: pd.DataFrame, phase: str) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Run the old past-only matcher once per symbol-month and retain rejects."""
    if tasks.empty:
        return pd.DataFrame(), pd.DataFrame(), []
    lower_touches, shocks = matcher._ledger_controls()
    tasks = tasks.copy()
    tasks["month"] = tasks.decision.dt.strftime("%Y-%m")
    matches: list[dict] = []
    coverage_rows: list[dict] = []
    receipts: list[dict] = []
    symbol_months = tasks.groupby(["symbol", "month"], sort=True)
    for i, ((symbol, month), group) in enumerate(symbol_months, 1):
        month_start = pd.Timestamp(f"{month}-01", tz="UTC")
        month_end = month_start + pd.DateOffset(months=1)
        warmup = pd.Timedelta(minutes=max(int(group.minutes.max() + group.wait_min.max()), 65) + 5)
        max_h = int(group.hold_hours.max())
        start, end = month_start - warmup, month_end + pd.Timedelta(hours=max_h)
        needed = [(start, end)]
        frame, receipt = load_full_ohlc(str(symbol), start, end,
                                        needed_intervals=needed, repair_missing=False)
        grid, prefix = matcher._monitor_prefix(start, month_end)
        rows, proofs = matcher._process_symbol_month(
            group, lower_touches, shocks, past_only=True, price_frame=frame,
            price_receipt=receipt,
        )
        matches.extend(rows)
        for proof in proofs:
            coverage_rows.extend(proof.pop("task_coverage", []))
            proof.update({"phase": phase, "source": sorted(group.source.astype(str).unique()),
                          "symbol": str(symbol), "month": month,
                          "load_start": start.isoformat(), "load_end": end.isoformat(),
                          "local_sources": receipt.get("local_sources", []),
                          "remaining_missing_full_ohlc_ranges": receipt.get("remaining_missing_full_ohlc_ranges", [])})
            receipts.append(proof)
        if i % 25 == 0 or i == len(symbol_months):
            print(f"matched {phase} symbol-months {i}/{len(symbol_months)}; controls={len(matches)}", flush=True)
        del frame
    return pd.DataFrame(matches), pd.DataFrame(coverage_rows), receipts


def _load_native_control_fills(matches: pd.DataFrame, tasks: pd.DataFrame,
                               rule_manifest: dict[str, Any]) -> tuple[pd.DataFrame, list[dict]]:
    """Price deduplicated dynamic controls once per symbol-month with native VectorBT."""
    if matches.empty or "source" not in matches:
        return matches.copy(), []
    dynamic = matches[matches.source.eq("exits")].copy()
    if dynamic.empty:
        return dynamic, []
    policy_id = PRIMARY_DYNAMIC_POLICY_ID
    policy = exits.policies(False)[policy_id]
    if rule_manifest.get("policy_id") != policy_id:
        raise ValueError("Dynamic policy assertion was not completed")
    dynamic["random_entry"] = pd.to_datetime(dynamic.random_entry, utc=True)
    dynamic["policy_id_numeric"] = pd.to_numeric(dynamic.policy_id, errors="coerce")
    if dynamic.policy_id_numeric.isna().any() or not dynamic.policy_id_numeric.eq(policy_id).all():
        raise ValueError("Dynamic match rows did not preserve the locked policy_id")
    fill_rows: list[pd.DataFrame] = []
    receipts: list[dict] = []
    dynamic["control_month"] = dynamic.random_entry.dt.strftime("%Y-%m")
    groups = dynamic.groupby(["symbol", "control_month"], sort=True)
    for i, ((symbol, month), group) in enumerate(groups, 1):
        month_start = pd.Timestamp(f"{month}-01", tz="UTC")
        month_end = month_start + pd.DateOffset(months=1)
        times = pd.DatetimeIndex(group.random_entry.drop_duplicates()).sort_values()
        if not len(times):
            continue
        start = min(month_start - pd.Timedelta(days=12), times.min() - pd.Timedelta(days=12))
        end = max(month_end + pd.Timedelta(days=10), times.max() + pd.Timedelta(days=10))
        needed = [(time - pd.Timedelta(days=12), time + pd.Timedelta(days=10)) for time in times]
        raw, receipt = load_full_ohlc(str(symbol), start, end, needed_intervals=needed,
                                      repair_missing=False)
        if raw.empty:
            fills = pd.DataFrame(columns=["entry_time", "policy_id", "net_pct", "exit_time"])
        else:
            native_frame = exits.prepare(raw)
            fills = exits.simulate(native_frame, times, [policy], batch_size=8)
            if not fills.empty:
                fills["policy_id"] = policy_id
        if fills.empty:
            mapped = group[["symbol", "random_entry"]].drop_duplicates().copy()
            mapped["control_net_pct"] = np.nan
            mapped["control_exit_time"] = pd.NaT
            mapped["native_fill_status"] = "no_complete_native_fill"
        else:
            fills = fills.rename(columns={"net_pct": "control_net_pct", "exit_time": "control_exit_time"})
            mapped = group[["symbol", "random_entry"]].drop_duplicates().merge(
                fills[["entry_time", "control_net_pct", "control_exit_time"]],
                left_on="random_entry", right_on="entry_time", how="left", validate="one_to_one",
            ).drop(columns="entry_time")
            mapped["native_fill_status"] = np.where(mapped.control_net_pct.notna(), "complete", "no_complete_native_fill")
        fill_rows.append(mapped)
        receipts.append({
            "symbol": str(symbol), "month": month, "unique_random_entries": int(len(times)),
            "native_fill_rows": int(mapped.control_net_pct.notna().sum()),
            "load_start": start.isoformat(), "load_end": end.isoformat(),
            "local_sources": receipt.get("local_sources", []),
            "remaining_missing_full_ohlc_ranges": receipt.get("remaining_missing_full_ohlc_ranges", []),
            "policy_id": policy_id, "policy_key": policy.key,
        })
        if i % 25 == 0 or i == len(groups):
            print(f"native dynamic fills {i}/{len(groups)} symbol-months; unique_controls={len(times)}", flush=True)
    if not fill_rows:
        return dynamic, receipts
    fills = pd.concat(fill_rows, ignore_index=True)
    output = dynamic.merge(fills, on=["symbol", "random_entry"], how="left", validate="many_to_one")
    return output, receipts


def _attach_dynamic_member_actuals(individual: pd.DataFrame, events: pd.DataFrame,
                                  tasks: pd.DataFrame) -> pd.DataFrame:
    if individual.empty:
        return individual
    out = individual.copy()
    out["policy_id"] = pd.to_numeric(out.policy_id, errors="raise").astype(int)
    # The supplied cache is authoritative for ETH. The lock-file value remains
    # a fallback and was checked against this cache on read when both exist.
    event_actual = events[["rule_event_id", "net_pct", "actual_source"]].rename(
        columns={"net_pct": "locked_event_actual_pct"})
    out = out.merge(event_actual, on="rule_event_id", how="left", validate="many_to_one")
    if ETH_ACTUAL_CACHE.is_file():
        actual = pd.read_parquet(ETH_ACTUAL_CACHE, columns=["entry_time", "policy_id", "net_pct"])
        actual = actual[pd.to_numeric(actual.policy_id, errors="coerce").eq(PRIMARY_DYNAMIC_POLICY_ID)].copy()
        actual["entry_time"] = pd.to_datetime(actual.entry_time, utc=True)
        actual = actual.rename(columns={"entry_time": "decision", "net_pct": "native_actual_pct"})
        actual = actual[["decision", "policy_id", "native_actual_pct"]].drop_duplicates(["decision", "policy_id"])
        out = out.merge(actual, on=["decision", "policy_id"], how="left", validate="many_to_one")
    else:
        out["native_actual_pct"] = np.nan
    # For any non-ETH member without the supplied cache, replay its actual entry
    # using the same native policy and price slice as its paired random entries.
    # The assigned dynamic candidate is ETH-only; this branch leaves unsupported
    # actual members visibly unmatched rather than manufacturing a basket return.
    use_cache = out.native_actual_pct.notna()
    out["actual_pct"] = out.locked_event_actual_pct
    # A member-level actual cannot be inferred from an event-weighted ALT net.
    # ETH has one constituent, so its locked/cache event and member value agree.
    eth_mask = out.symbol.eq("ETHUSDT")
    out.loc[eth_mask & use_cache, "actual_pct"] = out.loc[eth_mask & use_cache, "native_actual_pct"]
    out["actual_pct"] = pd.to_numeric(out.actual_pct, errors="coerce")
    return out


def _paired_fixed(matches: pd.DataFrame, event_meta: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    fixed_matches = matches[matches.source.eq("search")].copy() if not matches.empty else pd.DataFrame()
    meta = event_meta[event_meta.source.eq("search")].copy() if not event_meta.empty else pd.DataFrame()
    if meta.empty:
        return pd.DataFrame(), pd.DataFrame()
    paired_draws = matcher._paired_fixed(fixed_matches, meta)
    eligible = paired_draws[paired_draws.eligible_coverage].copy()
    if eligible.empty:
        grouped = pd.DataFrame(columns=["rule_event_id", "matched_actual_pct", "matched_random_pct",
                                        "coverage_pct", "eligible_draws", "min_member_coverage_pct",
                                        "max_member_coverage_pct"])
    else:
        grouped = eligible.groupby("rule_event_id", as_index=False).agg(
            matched_actual_pct=("candidate_member_net_pct", "mean"),
            matched_random_pct=("control_net_pct", "mean"),
            coverage_pct=("coverage_pct", "mean"),
            eligible_draws=("draw", "nunique"),
            min_member_coverage_pct=("coverage_pct", "min"),
            max_member_coverage_pct=("coverage_pct", "max"),
        )
    event_cols = ["rule_event_id", "candidate_id", "decision", "asset", "role", "policy_id",
                  "epoch", "expected_symbols"]
    events = meta[event_cols].merge(grouped, on="rule_event_id", how="left", validate="one_to_one")
    events["eligible_draws"] = events.eligible_draws.fillna(0).astype(int)
    events["coverage_pct"] = events.coverage_pct.fillna(0.0)
    events["eligible_coverage"] = events.eligible_draws.gt(0) & events.coverage_pct.ge(80.0)
    events["paired_excess_pp"] = events.matched_actual_pct - events.matched_random_pct
    events["matched_month"] = events.decision.dt.strftime("%Y-%m")
    return paired_draws, events.sort_values(["decision", "candidate_id"]).reset_index(drop=True)


def _paired_dynamic(individual: pd.DataFrame, events: pd.DataFrame,
                    tasks: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = _attach_dynamic_member_actuals(individual, events, tasks) if not individual.empty else individual.copy()
    usable = data.dropna(subset=["actual_pct", "control_net_pct"]).copy() if not data.empty else data
    if usable.empty:
        paired = pd.DataFrame(columns=["rule_event_id", "candidate_id", "decision", "matched_actual_weighted_pct",
                                       "matched_random_weighted_pct", "matched_weight", "paired_members",
                                       "min_member_draws", "max_member_draws"])
    else:
        # Average the pre-seeded, non-overlapping random draws per member, then
        # keep the event only when >=80% of its expected original members pair.
        members = usable.groupby(["rule_event_id", "candidate_id", "decision", "symbol"], as_index=False).agg(
            weight=("weight", "first"), actual_pct=("actual_pct", "first"),
            matched_random_pct=("control_net_pct", "mean"), draws=("draw", "nunique"),
        )
        members["actual_weighted_pct"] = members.actual_pct * members.weight
        members["random_weighted_pct"] = members.matched_random_pct * members.weight
        paired = members.groupby(["rule_event_id", "candidate_id", "decision"], as_index=False).agg(
            matched_actual_weighted_pct=("actual_weighted_pct", "sum"),
            matched_random_weighted_pct=("random_weighted_pct", "sum"),
            matched_weight=("weight", "sum"), paired_members=("symbol", "nunique"),
            min_member_draws=("draws", "min"), max_member_draws=("draws", "max"),
        )
    event_cols = ["rule_event_id", "candidate_id", "decision", "asset", "role", "policy_id", "epoch"]
    expected = events[event_cols].copy()
    if "expected" in events:
        expected["expected_symbols"] = pd.to_numeric(events.expected, errors="coerce")
    else:
        expected["expected_symbols"] = np.nan
    if not tasks.empty:
        task_expected = tasks.groupby(["rule_event_id", "candidate_id", "decision"], as_index=False).agg(
            task_expected_symbols=("expected_symbols", "max"),
        )
        expected = expected.merge(task_expected, on=["rule_event_id", "candidate_id", "decision"],
                                  how="left", validate="one_to_one")
        expected["expected_symbols"] = expected.task_expected_symbols.where(
            expected.task_expected_symbols.notna(), expected.expected_symbols)
        expected = expected.drop(columns="task_expected_symbols")
    paired = expected.merge(paired, on=["rule_event_id", "candidate_id", "decision"], how="left", validate="one_to_one")
    for col in ("paired_members", "matched_weight", "min_member_draws", "max_member_draws"):
        paired[col] = paired[col].fillna(0)
    paired["sampled_member_share"] = paired.paired_members / paired.expected_symbols.replace(0, np.nan)
    paired["matched_actual_pct"] = paired.matched_actual_weighted_pct / paired.matched_weight.replace(0, np.nan)
    paired["matched_random_pct"] = paired.matched_random_weighted_pct / paired.matched_weight.replace(0, np.nan)
    paired["coverage_pct"] = paired.sampled_member_share * 100
    paired["paired_excess_pp"] = paired.matched_actual_pct - paired.matched_random_pct
    paired["eligible_coverage"] = paired.sampled_member_share.ge(0.80) & paired.matched_weight.gt(0)
    paired["matched_month"] = paired.decision.dt.strftime("%Y-%m")
    return data, paired.sort_values(["decision", "candidate_id"]).reset_index(drop=True)


def _holm(values: pd.Series, family_size: int | None = None) -> pd.Series:
    n = int(family_size if family_size is not None else len(values))
    if n < len(values):
        raise ValueError("Holm family size cannot be smaller than supplied hypotheses")
    p = values.fillna(1.0).clip(0.0, 1.0).to_numpy(dtype=float)
    order = np.argsort(p, kind="stable")
    adjusted = np.empty(len(p), dtype=float)
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (n - rank) * p[index]))
        adjusted[index] = running
    return pd.Series(adjusted, index=values.index)


def _summarize_family(rules: pd.DataFrame, source: str, paired_events: pd.DataFrame,
                      actual_events: pd.DataFrame) -> pd.DataFrame:
    rule_rows = rules.copy()
    rule_rows["candidate_id"] = rule_rows.candidate_id.astype(str)
    if source == "long" and "role" in rule_rows:
        rule_rows = rule_rows[rule_rows.role.astype(str).str.lower().eq("dynamic_primary")].copy()
    rows: list[dict[str, Any]] = []
    for rule in rule_rows.to_dict("records"):
        candidate_id = str(rule["candidate_id"])
        part = paired_events[paired_events.candidate_id.eq(candidate_id)].copy() if not paired_events.empty else pd.DataFrame()
        all_rows = part.copy()
        good = part[part.eligible_coverage.eq(True)].dropna(subset=["matched_actual_pct", "matched_random_pct"]).copy() if not part.empty else part
        if not good.empty:
            diff = good.set_index("decision").paired_excess_pp
            p_month = match_stats.paired_month_p(diff)
            months = int(good.matched_month.nunique())
            n_pairs = int(good.decision.nunique())
            actual_mean = float(good.matched_actual_pct.mean())
            random_mean = float(good.matched_random_pct.mean())
            edge = float(diff.mean())
            avg_coverage = float(good.coverage_pct.mean()) if "coverage_pct" in good else float(good.sampled_member_share.mean() * 100)
        else:
            p_month, months, n_pairs = np.nan, 0, 0
            actual_mean = random_mean = edge = avg_coverage = np.nan
        if source == "fixed":
            expected_events = int(actual_events.loc[actual_events.candidate_id.eq(candidate_id), "rule_event_id"].nunique()) if len(actual_events) else 0
            coverage_pct = 100.0 * n_pairs / expected_events if expected_events else 0.0
            total_draws = int(part.eligible_draws.sum()) if not part.empty and "eligible_draws" in part else 0
            rejected = int(max(0, expected_events - n_pairs))
        else:
            expected_events = int(actual_events.loc[actual_events.candidate_id.eq(candidate_id), "rule_event_id"].nunique()) if len(actual_events) else 0
            # Include locked 2026 events that had no candidate-control task.
            coverage_pct = 100.0 * n_pairs / expected_events if expected_events else 0.0
            total_draws = int(part.min_member_draws.sum()) if not part.empty and "min_member_draws" in part else 0
            rejected = int(max(0, expected_events - n_pairs))
        record = {**rule,
                  "input_group": source,
                  "n_2026_actual_events": expected_events,
                  "n_paired_events": n_pairs,
                  "coverage_pct": coverage_pct,
                  "eligible_months": months,
                  "eligible_draw_or_member_count": total_draws,
                  "mean_matched_actual_pct": actual_mean,
                  "mean_matched_random_pct": random_mean,
                  "mean_paired_edge_pp": edge,
                  "p_month_sign": p_month,
                  "unpaired_or_ineligible_events": rejected,
                  "mean_pair_member_coverage_pct": avg_coverage,
                  "test_scope": "2026 actual events only; event-month differences summed within UTC month before sign-flip test",
                  "funding_in_either_side": False}
        rows.append(record)
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary["p_holm_family"] = _holm(summary.p_month_sign, family_size=len(summary))
        summary["pass_frequency_control"] = False
    return summary


def _write_phase(phase: str, rules: pd.DataFrame, events: pd.DataFrame,
                 rule_manifest: dict[str, Any]) -> dict[str, Any]:
    source = "fixed" if phase == "fixed" else "long"
    tasks, event_meta = _make_tasks(events, rules, source)
    if tasks.empty:
        matches, task_coverage, price_receipts = pd.DataFrame(), pd.DataFrame(), []
    else:
        matches, task_coverage, price_receipts = _collect_matches(tasks, phase)
    if not event_meta.empty:
        event_meta = event_meta.merge(
            events[["rule_event_id", "net_pct"]].rename(columns={"net_pct": "locked_net_pct"}),
            on="rule_event_id", how="left", validate="one_to_one")

    if phase == "fixed":
        paired_draws, paired_events = _paired_fixed(matches, event_meta)
        individual, native_receipts = pd.DataFrame(), []
    else:
        events = _attach_dynamic_actuals(events)
        matches, native_receipts = _load_native_control_fills(matches, tasks, rule_manifest)
        individual, paired_events = _paired_dynamic(matches, events, tasks)
        paired_draws = pd.DataFrame()

    # All lock-level events, including missing signals/pools/actual fills, stay
    # in the coverage ledger so rejected opportunities are inspectable.
    if not event_meta.empty:
        if phase == "fixed":
            coverage = event_meta.merge(
                task_coverage[[c for c in task_coverage.columns if c == "rule_event_id" or c == "symbol" or c == "pool_n" or c == "selected_draws" or c == "symbol_status" or c.endswith("bucket") or c.startswith("candidate_monitor")]]
                if not task_coverage.empty else pd.DataFrame(columns=["rule_event_id"]),
                on="rule_event_id", how="left", validate="one_to_many")
            coverage = coverage.merge(
                paired_events[["rule_event_id", "coverage_pct", "eligible_draws", "eligible_coverage"]]
                if not paired_events.empty else pd.DataFrame(columns=["rule_event_id"]),
                on="rule_event_id", how="left", validate="many_to_one")
        else:
            task_columns = ["rule_event_id", "symbol", "pool_n", "selected_draws", "symbol_status",
                            "candidate_vol_bucket", "candidate_trend_bucket", "candidate_monitor_fraction"]
            coverage = event_meta.merge(
                task_coverage[task_columns] if not task_coverage.empty else pd.DataFrame(columns=["rule_event_id"]),
                on="rule_event_id", how="left", validate="one_to_many")
            if not paired_events.empty:
                coverage = coverage.merge(
                    paired_events[["rule_event_id", "sampled_member_share", "eligible_coverage",
                                    "matched_actual_pct", "matched_random_pct"]],
                    on="rule_event_id", how="left", validate="many_to_one")
    else:
        coverage = pd.DataFrame()

    summary = _summarize_family(rules, source, paired_events, events)
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)
    matches.to_parquet(CONTROL_DIR / f"{phase}_matches.parquet", index=False)
    if not task_coverage.empty:
        task_coverage.to_csv(CONTROL_DIR / f"{phase}_task_coverage.csv", index=False)
    coverage.to_csv(CONTROL_DIR / f"{phase}_event_coverage.csv", index=False)
    paired_events.to_parquet(CONTROL_DIR / f"{phase}_paired_events.parquet", index=False)
    paired_events.to_csv(CONTROL_DIR / f"{phase}_paired_events.csv", index=False)
    if phase == "fixed":
        paired_draws.to_parquet(CONTROL_DIR / "fixed_paired_draws.parquet", index=False)
    else:
        individual.to_parquet(CONTROL_DIR / "long_individual_payoffs.parquet", index=False)
    summary.to_csv(CONTROL_DIR / f"{phase}_validity.csv", index=False)
    receipt_path = CONTROL_DIR / f"{phase}_price_source_receipts.json"
    receipt_path.write_text(json.dumps({"matcher_price_loads": price_receipts,
                                        "native_dynamic_control_fills": native_receipts},
                                       indent=2, ensure_ascii=False, default=str) + "\n")
    manifest = {
        "phase": phase,
        "input_rules": str((FIXED_INPUT if phase == "fixed" else LONG_INPUT) / "locked_rules.csv"),
        "input_events": str((FIXED_INPUT if phase == "fixed" else LONG_INPUT) / "locked_events.parquet"),
        "actual_entry_scope": "decision >= 2026-01-01T00:00:00Z and < 2027-01-01T00:00:00Z",
        "matcher": "monday_breadth_opt_controls._candidate_pool(..., past_only=True), _process_symbol_month",
        "matching_strata": ["same asset and constituent", "same UTC decision month", "same UTC 6-hour decision block",
                             "same prior-1h realized-volatility bucket", "same prior-1h trend sign before known",
                             "same New York weekday at known and decision", "same monitor fraction over initial+wait"],
        "recent_shock_rule": "Exclude a control if any same-side 25+ breadth shock was known during [control decision - 60m, control decision], inclusive at the decision boundary. Future shocks are allowed.",
        "non_overlap_rule": "For each locked event, control [entry, entry+hold_hours) may not overlap the actual [decision, decision+hold_hours) reservation; each event's own hold_hours is used.",
        "fixed_method": "One unlevered long entered at Binance USD-M next-bar open and market-exited at the locked event's own hold_hours open; VectorBT two TAKER fees of 0.05% per side.",
        "dynamic_method": "Random controls replayed using the locked native VectorBT policy_id=67; one market entry and market exit; no funding on actual or controls.",
        "costs": {"market_entry_fee_pct": 0.05, "market_exit_fee_pct": 0.05,
                  "slippage_pct": 0.0, "funding_in_either_side": False},
        "draws": {"fixed_max_per_symbol_event": matcher.MAX_FIXED_DRAWS,
                  "dynamic_max_per_symbol_event": matcher.MAX_DYNAMIC_DRAWS,
                  "sampling": "seeded without replacement within each event's exact matched pool"},
        "alt_eligibility": "Pair actual and random returns on the same member subset; include only rows with >=80% expected constituent coverage.",
        "monthly_inference": "Reuse monday_breadth_opt_match_exits.paired_month_p on the summed event differences within each UTC month; finite-sample exact sign flips through 16 months, seeded 65,535 draws above 16 months.",
        "multiplicity": "Holm correction within locked primary candidate family; combined 9-family table will be written once fixed and long phases are both present.",
        "dynamic_policy": rule_manifest,
        "rows": {"actual_2026_events": int(len(events)), "tasks": int(len(tasks)),
                 "control_matches": int(len(matches)), "paired_events": int(len(paired_events))},
    }
    (CONTROL_DIR / f"{phase}_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    return manifest


def _write_combined_holm() -> pd.DataFrame:
    summaries = []
    for phase in ("fixed", "long"):
        path = CONTROL_DIR / f"{phase}_validity.csv"
        if path.is_file():
            part = pd.read_csv(path)
            if not part.empty:
                part["family"] = phase
                summaries.append(part)
    if not summaries:
        return pd.DataFrame()
    all_rows = pd.concat(summaries, ignore_index=True, sort=False)
    all_rows["p_holm_all_primary"] = _holm(all_rows.p_month_sign, family_size=max(9, len(all_rows)))
    all_rows.to_csv(CONTROL_DIR / "combined_primary_validity.csv", index=False)
    return all_rows


def _git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unavailable"


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--phase", choices=("fixed", "long", "all"), required=True)
    args = cli.parse_args()
    if args.phase in {"fixed", "all"}:
        fixed, fixed_rules = _read_locked(FIXED_INPUT, "fixed")
        if len(fixed):
            if not fixed.asset.isin(ASSETS).all():
                raise ValueError("Fixed lock contains assets outside BTC/ETH/SOL/ALT")
        _write_phase("fixed", fixed_rules, fixed, {"locked_hold_hours": sorted(fixed.hold_hours.unique().tolist())})
    if args.phase in {"long", "all"}:
        long_events, long_rules = _read_locked(LONG_INPUT, "long")
        long_rules = long_rules[long_rules.role.astype(str).str.lower().eq("dynamic_primary")].copy()
        long_events = long_events[long_events.role.astype(str).str.lower().eq("dynamic_primary")].copy()
        if len(long_events) and not long_events.hold_hours.eq(DYNAMIC_HOLD_HOURS).all():
            raise ValueError("Long dynamic controls must preserve the original 144h reservation")
        policy_manifest = _assert_dynamic_policy(long_rules)
        _write_phase("long", long_rules, long_events, policy_manifest)
    combined = _write_combined_holm()
    run_manifest = {"builder_commit": _git_head(), "phases_completed": list(args.phase.split()),
                    "combined_hypotheses": int(len(combined)), "control_dir": str(CONTROL_DIR.relative_to(ROOT))}
    (CONTROL_DIR / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n")
    print(json.dumps(run_manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
