#!/usr/bin/env python3
"""Build the normalized, deterministic strategy-booklet dataset for breadth-ema20."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1"
FREQUENCY = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1"
OPTIMIZATION = ROOT / "experiments/active/exp-monday-breadth-optimization-20261108-v1"
SPACING = ROOT / "experiments/active/exp-monday-breadth-alt-spacing-20261108-v1"
BTC_ADMISSION = ROOT / "experiments/active/exp-monday-breadth-btc-admission-20261108-v1"
BTC_RESERVE = ROOT / "experiments/active/exp-monday-breadth-btc-reserve-20261108-v1"
OUTPUT = ROOT / "yoyo/monitor/static/strategy-booklet/breadth-ema20-data.json"
BTC_PARTIAL_FILLS = ROOT / "data/research/monday_breadth_optimization_20261108/btc_funded_exit_fills.parquet"

PERIOD_BY_EPOCH = {
    0: "pre2023",
    1: "2023_2024",
    2: "2025",
    3: "2026_to_ledger_end",
}


def iso_utc(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def number(value: Any) -> int | float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    out = float(value)
    return out if np.isfinite(out) else None


def flag(value: Any) -> bool | None:
    if value is None or pd.isna(value):
        return None
    return bool(value)


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return iso_utc(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return number(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def event_id(asset: str, decision: Any) -> str:
    return f"{asset}|{iso_utc(decision)}"


def assert_close(actual: Any, expected: Any, label: str, atol: float = 1e-6) -> None:
    if pd.isna(actual) or pd.isna(expected) or not np.isclose(float(actual), float(expected), rtol=0, atol=atol):
        raise ValueError(f"{label} mismatch: actual={actual!r}, expected={expected!r}")


def btc_legs(fill: pd.Series, *, source: str) -> tuple[list[dict[str, Any]], float | None]:
    exit_vwap = float(fill.exit_vwap)
    leg_count = int(fill.exit_legs)
    if leg_count == 2:
        first_price = float(fill.first_exit_price)
        last_price = 2 * exit_vwap - first_price
        assert_close((first_price + last_price) / 2, exit_vwap, "BTC 50/50 partial exit VWAP")
        return [
            {
                "time": iso_utc(fill.first_exit_time),
                "price": number(first_price),
                "size_fraction": 0.5,
                "fill_type": "limit_maker",
            },
            {
                "time": iso_utc(fill.exit_time),
                "price": number(last_price),
                "size_fraction": 0.5,
                "fill_type": "market_taker",
            },
        ], last_price
    fee = number(getattr(fill, "exit_fee_rate", 0.0005))
    fill_type = "limit_maker" if fee is not None and np.isclose(fee, 0.0002) else "market_taker"
    return [
        {
            "time": iso_utc(fill.exit_time),
            "price": number(exit_vwap),
            "size_fraction": 1.0,
            "fill_type": fill_type,
        }
    ], exit_vwap


def build_btc_events() -> list[dict[str, Any]]:
    integrated = pd.read_parquet(EXPERIMENT / "risk/integrated_btc_events.parquet").copy()
    integrated["decision"] = pd.to_datetime(integrated.decision, utc=True)

    dual = pd.read_parquet(EXPERIMENT / "dual/events.parquet")
    medium_fills = dual[(dual.strategy == "dual_source_specific") & (dual.source == "medium")].copy()
    medium_fills["decision"] = pd.to_datetime(medium_fills.decision, utc=True)
    medium_by_time = medium_fills.set_index("decision")

    partial_fills = pd.read_parquet(BTC_PARTIAL_FILLS)
    partial_fills = partial_fills[partial_fills.policy_id.eq(4)].copy()
    partial_fills["entry_time"] = pd.to_datetime(partial_fills.entry_time, utc=True)
    if partial_fills.entry_time.duplicated().any():
        raise ValueError("BTC policy-4 partial fills contain duplicate entry times")
    partial_by_time = partial_fills.set_index("entry_time")

    opportunities = pd.read_csv(EXPERIMENT / "btc/below/opportunities.csv", parse_dates=["decision", "known"])
    medium_opportunities = opportunities[opportunities.candidate_id.eq("freq36_BTC")].copy()
    medium_opportunities["decision"] = pd.to_datetime(medium_opportunities.decision, utc=True)
    opportunity_by_time = medium_opportunities.set_index("decision")

    case_path = FREQUENCY / "btc_target/threshold_events.parquet"
    case_events = pd.read_parquet(case_path)
    case_events = case_events[(case_events.threshold_pct == 5.0) & case_events.candidate_id.eq("case_target_funded_BTC")].copy()
    case_events["decision"] = pd.to_datetime(case_events.decision, utc=True)
    case_by_time = case_events.set_index("decision")

    output: list[dict[str, Any]] = []
    for row in integrated.sort_values("decision").itertuples(index=False):
        decision = pd.Timestamp(row.decision).tz_convert("UTC")
        source = str(row.source)
        epoch = int(row.epoch)
        if source == "medium":
            if decision not in medium_by_time.index or decision not in opportunity_by_time.index:
                raise ValueError(f"Missing medium BTC source row at {decision}")
            fill = medium_by_time.loc[decision]
            signal = opportunity_by_time.loc[decision]
            if isinstance(fill, pd.DataFrame) or isinstance(signal, pd.DataFrame):
                raise ValueError(f"Duplicate medium BTC source row at {decision}")
            assert_close(fill.entry_open, row.entry_open, f"BTC medium entry at {decision}")
            assert_close(fill.net_pct, row.net_pct, f"BTC medium fee-only return at {decision}")
            if not bool(signal.accept_below_4h_ema20):
                raise ValueError(f"Medium BTC event did not pass the recorded 4h EMA20 gate: {decision}")
            legs, final_price = btc_legs(fill, source=source)
            gate = {
                "timeframe": "4h",
                "bar_open_time": iso_utc(signal["4h_bar_open_time"]),
                "bar_close_time": iso_utc(signal["4h_bar_close_time"]),
                "close": number(signal["4h_close"]),
                "ema20": number(signal["4h_ema20"]),
                "accepted": True,
            }
            signal_snapshot = {
                "known_time": iso_utc(signal.known),
                "initial_touch_count": number(signal.initial),
                "new_coins": number(signal.new_coins),
                "ema_filter": gate,
            }
            exit_policy = "fixed_6d_market"
            reason = number(fill.get("reason"))
            exit_fee_rate = 0.0005
        elif source == "case":
            if decision not in partial_by_time.index or decision not in case_by_time.index:
                raise ValueError(f"Missing funded case BTC source row at {decision}")
            fill = partial_by_time.loc[decision]
            signal = case_by_time.loc[decision]
            if isinstance(fill, pd.DataFrame) or isinstance(signal, pd.DataFrame):
                raise ValueError(f"Duplicate case BTC source row at {decision}")
            for field in ("entry_open", "exit_time", "exit_legs", "first_exit_time", "net_pct", "funded_pct"):
                expected = getattr(row, field)
                actual = fill[field]
                if field.endswith("_time"):
                    if pd.Timestamp(actual).tz_convert("UTC") != pd.Timestamp(expected).tz_convert("UTC"):
                        raise ValueError(f"BTC case {field} mismatch at {decision}")
                else:
                    assert_close(actual, expected, f"BTC case {field} at {decision}")
            legs, final_price = btc_legs(fill, source=source)
            signal_snapshot = {
                "known_time": iso_utc(signal.known),
                "initial_touch_count": number(signal.initial),
                "new_coins": number(signal.new_coins),
                "market_share_28d_pct": number(signal.market_share_pct),
                "new_ratio_pct": number(signal.new_ratio_pct),
                "alt_move_pct": number(signal.alt_move),
                "reclaim_fraction": number(signal.reclaim_fraction),
                "ema_filter": {"applies": False, "reason": "case source is exempt from the medium-entry EMA20 veto"},
            }
            exit_policy = "half_then_6d"
            reason = number(fill.reason)
            exit_fee_rate = number(fill.get("exit_fee_rate", 0.0005))
        else:
            raise ValueError(f"Unexpected integrated BTC source: {source}")

        entry_price = float(row.entry_open)
        exit_vwap = float(fill.exit_vwap)
        event = {
            "event_id": event_id("BTC", decision),
            "asset": "BTC",
            "source": source,
            "signal_rule": "freq36_BTC_below_4h_ema20" if source == "medium" else "case_target_funded_BTC",
            "entry_time": iso_utc(decision),
            "entry_price": number(entry_price),
            "exit_time": iso_utc(row.exit_time),
            "exit_price": number(final_price),
            "exit_vwap": number(exit_vwap),
            "first_exit_time": iso_utc(row.first_exit_time),
            "first_exit_price": number(fill.get("first_exit_price", fill.exit_vwap)),
            "exit_legs": int(row.exit_legs),
            "exit_legs_detail": legs,
            "exit_policy": exit_policy,
            "exit_reason_code": reason,
            "exit_fee_rate": exit_fee_rate,
            "gross_pct": number((exit_vwap / entry_price - 1) * 100),
            "net_pct": number(row.net_pct),
            "funded_pct": number(row.funded_pct) if epoch == 3 else None,
            "funding_coverage_pct": 100.0 if epoch == 3 else None,
            "funding_scope": "2026_actual_funding" if epoch == 3 else None,
            "epoch": epoch,
            "period": PERIOD_BY_EPOCH[epoch],
            "signal_snapshot": signal_snapshot,
            "expected_members": None,
            "observed_members": None,
            "member_coverage_pct": None,
            "members": [],
        }
        output.append(event)
    if len(output) != 159:
        raise ValueError(f"Expected 159 integrated BTC events, got {len(output)}")
    return output


def build_alt_events() -> list[dict[str, Any]]:
    event_results = pd.read_csv(EXPERIMENT / "alt/event_results.csv.gz", parse_dates=["decision"])
    event_results = event_results[event_results.strategy.eq("baseline_6h")].copy()
    event_results["decision"] = pd.to_datetime(event_results.decision, utc=True)
    member_rows = pd.read_parquet(EXPERIMENT / "alt/member_strategy_returns.parquet")
    member_rows = member_rows[member_rows.strategy.eq("baseline_6h")].copy()
    member_rows["decision"] = pd.to_datetime(member_rows.decision, utc=True)
    member_rows["selected_exit_time"] = pd.to_datetime(member_rows.selected_exit_time, utc=True)

    original_signals = pd.read_parquet(FREQUENCY / "fixed/locked_events.parquet")
    original_signals = original_signals[original_signals.candidate_id.eq("freq36_ALT")].copy()
    original_signals["decision"] = pd.to_datetime(original_signals.decision, utc=True)
    original_signals["known"] = pd.to_datetime(original_signals.known, utc=True)
    if original_signals.decision.duplicated().any():
        raise ValueError("Duplicate original ALT signal timestamps")
    original_signal_by_time = original_signals.set_index("decision")

    feature_signals = pd.read_csv(
        OPTIMIZATION / "features/signals.csv.gz",
        usecols=["minutes", "wait_min", "known", "initial", "new_coins", "share28_pct", "market_share_status"],
        parse_dates=["known"],
    )
    feature_signals = feature_signals[(feature_signals.minutes == 5) & (feature_signals.wait_min == 0)].copy()
    feature_signals["known"] = pd.to_datetime(feature_signals.known, utc=True)
    if feature_signals.known.duplicated().any():
        raise ValueError("Duplicate original ALT market-share feature timestamps")
    feature_signal_by_time = feature_signals.set_index("known")

    interval_checks = pd.read_csv(
        EXPERIMENT / "risk/alt_6h_official_interval_checks.csv",
        parse_dates=["decision", "exit_time"],
    )
    interval_checks["decision"] = pd.to_datetime(interval_checks.decision, utc=True)
    interval_checks["exit_time"] = pd.to_datetime(interval_checks.exit_time, utc=True)
    check_map = {
        (row.symbol, row.decision, row.exit_time): float(row.funding_cost_pct)
        for row in interval_checks.itertuples(index=False)
    }

    verified = pd.read_csv(EXPERIMENT / "risk/alt_policy_verified_events_2026.csv", parse_dates=["decision"])
    verified = verified[verified.strategy.eq("baseline_6h")].copy()
    verified["decision"] = pd.to_datetime(verified.decision, utc=True)
    verified_by_time = verified.set_index("decision")
    member_groups = {time: group.sort_values("symbol") for time, group in member_rows.groupby("decision", sort=False)}
    if event_results.decision.duplicated().any() or verified_by_time.index.duplicated().any():
        raise ValueError("Duplicate ALT event summary timestamps")

    output: list[dict[str, Any]] = []
    for row in event_results.sort_values("decision").itertuples(index=False):
        decision = pd.Timestamp(row.decision).tz_convert("UTC")
        epoch = int(row.epoch)
        if decision not in member_groups:
            raise ValueError(f"Missing ALT member rows at {decision}")
        source_members = member_groups[decision]
        expected = int(row.expected_members)
        observed = int(row.observed_members)
        if len(source_members) != observed or observed != expected:
            raise ValueError(
                f"ALT member coverage mismatch at {decision}: rows={len(source_members)}, "
                f"observed={observed}, expected={expected}"
            )
        if source_members.symbol.duplicated().any():
            raise ValueError(f"Duplicate ALT member symbol at {decision}")
        if decision not in original_signal_by_time.index or decision not in feature_signal_by_time.index:
            raise ValueError(f"Missing frozen ALT signal features at {decision}")
        original_signal = original_signal_by_time.loc[decision]
        feature_signal = feature_signal_by_time.loc[decision]
        if int(original_signal.initial) < 2:
            raise ValueError(f"ALT event failed the recorded initial touch-count rule at {decision}")
        if int(feature_signal.initial) != int(original_signal.initial) or int(feature_signal.new_coins) != int(original_signal.new_coins):
            raise ValueError(f"ALT frozen signal-feature parity mismatch at {decision}")
        if int(original_signal.new_coins) != 0:
            raise ValueError(f"ALT zero-wait signal unexpectedly has new coins at {decision}")
        if float(feature_signal.share28_pct) < 5.0:
            raise ValueError(f"ALT event failed the recorded 5% market-share rule at {decision}")

        members: list[dict[str, Any]] = []
        corrected_funded_returns: list[float] = []
        for member in source_members.itertuples(index=False):
            funded_pct = None
            funding_complete = None
            funding_source = None
            if epoch == 3:
                raw_funded = number(member.selected_funded_net_pct)
                audit_key = (member.symbol, decision, pd.Timestamp(member.selected_exit_time).tz_convert("UTC"))
                if audit_key in check_map:
                    raw_funded = float(member.selected_net_pct) - check_map[audit_key]
                    funding_complete = True
                    funding_source = "official_interval_reconciliation"
                elif bool(member.funding_complete) and raw_funded is not None:
                    funding_complete = True
                    funding_source = "member_funding_ledger"
                else:
                    funding_complete = False
                    funding_source = None
                funded_pct = raw_funded
                if funded_pct is not None:
                    corrected_funded_returns.append(float(funded_pct))

            member_id = f"{event_id('ALT', decision)}|{member.symbol}"
            members.append(
                {
                    "member_id": member_id,
                    "event_id": event_id("ALT", decision),
                    "symbol": str(member.symbol),
                    "entry_time": iso_utc(member.decision),
                    "entry_price": number(member.entry_open),
                    "selected_exit_time": iso_utc(member.selected_exit_time),
                    "selected_exit_open": number(member.selected_exit_open),
                    "exit_price": number(member.selected_exit_open),
                    "selected_exit_hours": number(member.selected_exit_hours),
                    "gross_pct": number(member.selected_gross_pct),
                    "net_pct": number(member.selected_net_pct),
                    "selected_net_pct": number(member.selected_net_pct),
                    "funded_pct": funded_pct,
                    "selected_funded_net_pct": funded_pct,
                    "outcome_complete": flag(member.member_outcome_complete),
                    "funding_complete": funding_complete,
                    "funding_source": funding_source,
                }
            )

        assert_close(np.mean([m["net_pct"] for m in members]), row.net_pct, f"ALT event net return at {decision}")
        assert_close(np.mean([m["gross_pct"] for m in members]), row.gross_pct, f"ALT event gross return at {decision}")
        exit_times = {m["selected_exit_time"] for m in members}
        if len(exit_times) != 1:
            raise ValueError(f"ALT baseline basket members do not share a fixed 6h exit at {decision}")

        funded_pct = None
        funding_coverage_pct = None
        if epoch == 3:
            if decision not in verified_by_time.index:
                raise ValueError(f"Missing verified ALT funding summary at {decision}")
            check = verified_by_time.loc[decision]
            if int(check["count"]) != int(check["size"]) or int(check["size"]) != expected:
                raise ValueError(f"ALT event funding is not fully verified at {decision}")
            if len(corrected_funded_returns) != expected:
                raise ValueError(f"ALT member funding is incomplete at {decision}")
            funded_pct = number(check.funded_net_pct)
            funding_coverage_pct = 100.0 * int(check["count"]) / int(check["size"])
            assert_close(np.mean(corrected_funded_returns), funded_pct, f"ALT verified funded return at {decision}")

        output.append(
            {
                "event_id": event_id("ALT", decision),
                "asset": "ALT",
                "source": "baseline_6h",
                "signal_rule": "freq36_ALT",
                "entry_time": iso_utc(decision),
                "entry_price": None,
                "exit_time": next(iter(exit_times)),
                "exit_price": None,
                "exit_vwap": None,
                "first_exit_time": None,
                "first_exit_price": None,
                "exit_legs": None,
                "exit_legs_detail": [],
                "exit_policy": "baseline_6h",
                "gross_pct": number(row.gross_pct),
                "net_pct": number(row.net_pct),
                "funded_pct": funded_pct,
                "funding_coverage_pct": funding_coverage_pct,
                "funding_scope": "2026_actual_funding" if epoch == 3 else None,
                "epoch": epoch,
                "period": PERIOD_BY_EPOCH[epoch],
                "signal_snapshot": {
                    "known_time": iso_utc(original_signal.known),
                    "initial_touch_count": number(original_signal.initial),
                    "new_coins": number(original_signal.new_coins),
                    "market_share_28d_pct": number(feature_signal.share28_pct),
                    "market_share_definition": str(feature_signal.market_share_status),
                },
                "expected_members": expected,
                "observed_members": observed,
                "member_coverage_pct": number(row.member_coverage_pct),
                "members": members,
            }
        )
    if len(output) != 239:
        raise ValueError(f"Expected 239 ALT events, got {len(output)}")
    return output


def close_curve(events: list[dict[str, Any]], *, period: str, return_field: str, start: str) -> dict[str, Any]:
    selected = [event for event in events if event["period"] == period or period == "all_history"]
    start_stamp = pd.Timestamp(start)
    if start_stamp.tzinfo is None:
        start_stamp = start_stamp.tz_localize("UTC")
    else:
        start_stamp = start_stamp.tz_convert("UTC")
    states = {"BTC": 1.0, "ALT": 1.0}
    points = [
        {
            "time": iso_utc(start_stamp),
            "btc_equity": 1.0,
            "alt_equity": 1.0,
            "combined_equity": 1.0,
        }
    ]
    by_exit: dict[str, list[dict[str, Any]]] = {}
    for event in selected:
        result = number(event.get(return_field))
        if result is None:
            raise ValueError(f"Missing {return_field} for curve event {event['event_id']}")
        by_exit.setdefault(event["exit_time"], []).append(event)
    for stamp in sorted(by_exit):
        for event in sorted(by_exit[stamp], key=lambda x: x["asset"]):
            states[event["asset"]] *= 1.0 + float(event[return_field]) / 100.0
        points.append(
            {
                "time": stamp,
                "btc_equity": states["BTC"],
                "alt_equity": states["ALT"],
                "combined_equity": (states["BTC"] + states["ALT"]) / 2.0,
            }
        )
    return {
        "unit": "initial value 1.0 per independent 50% sleeve",
        "marking": "realized exit settlements only; not intratrade 5-minute marks",
        "return_field": return_field,
        "points": points,
    }


def record(row: pd.Series) -> dict[str, Any]:
    return {str(key): clean(value) for key, value in row.items()}


def btc_admission_review(accounts: pd.DataFrame) -> list[dict[str, Any]]:
    variants = pd.read_csv(BTC_ADMISSION / "summary.csv")
    variants["scope"] = variants.scope.map({"all_fee": "all_history", "2026_funded": "2026"})
    variants["variant"] = variants.variant.map({
        "baseline_below_post_reservation": "current", "ema_before_reservation": "early_ema",
    })
    reserve = pd.read_csv(BTC_RESERVE / "partition_summary.csv")
    reserve = reserve.loc[reserve.scope.isin(["all_history", "2026"])].assign(variant="reserved_case")
    rows = pd.concat([variants, reserve], ignore_index=True)
    if rows.duplicated(["variant", "scope"]).any() or len(rows.dropna(subset=["variant", "scope"])) != 6:
        raise ValueError("Expected three BTC allocation rules in both cost scopes")
    original = accounts.query('strategy == "below_plus_case" and sleeve_deployment == 1 and extra_roundtrip_pp == 0')
    for source, scope in [("all_fee", "all_history"), ("2026_funded", "2026")]:
        check = rows[(rows.scope == scope) & rows.variant.eq("current")].iloc[0]
        reference = original[original.scope.eq(source)].iloc[0]
        np.testing.assert_allclose([check.growth_pct, check.marked_dd_pct, check.distinct_entries],
                                   [reference.growth_pct, reference.max_dd_pct, reference.distinct], atol=1e-6)
    cols = ["variant", "scope", "btc_count", "alt_count", "total_legs", "distinct_entries",
            "growth_pct", "settlement_dd_pct", "marked_dd_pct", "sep16_admitted"]
    return clean(rows[cols].to_dict("records"))


def frequency_review(accounts: pd.DataFrame) -> dict[str, Any]:
    periods = {"2026_to_2026-10-27", "all_history"}
    branch = pd.read_csv(SPACING / "spacing_summary.csv")
    portfolio = pd.read_csv(SPACING / "portfolio_summary.csv")
    keys = ["minimum_gap_hours", "period"]
    joined = portfolio.loc[portfolio.period.isin(periods), keys + [
        "alt_legs", "total_legs", "distinct_entry_times", "combo_50_50_fee_only_growth_pct",
        "combo_50_50_settlement_drawdown_pct",
    ]].merge(branch[keys + [
        "raw_rule_signals", "selected", "new_vs_24h", "replaced_24h", "new_mean_net_pct", "new_win_pct",
    ]], on=keys, validate="one_to_one")
    if set(zip(joined.period, joined.minimum_gap_hours)) != {
        (period, hours) for period in periods for hours in (6, 12, 24)
    } or not joined.alt_legs.equals(joined.selected):
        raise ValueError("Incomplete or inconsistent ALT spacing comparison")
    events = pd.read_csv(SPACING / "events.csv", parse_dates=["decision"])
    extra = events.loc[events.year.eq(2026) & ~events.selected_24h & (events.selected_6h | events.selected_12h)].copy()
    extra["entry_bjt"] = extra.decision.dt.tz_convert("Asia/Shanghai").dt.strftime("%m-%d %H:%M")
    marks = pd.read_csv(SPACING / "marks_2026.csv")
    checked = joined[joined.period.eq("2026_to_2026-10-27")].merge(marks, on="minimum_gap_hours", validate="one_to_one")
    if len(checked) != 3 or not checked.alt_legs.equals(checked.events):
        raise ValueError("2026 marked account uses different baskets")
    np.testing.assert_allclose(checked.combo_50_50_fee_only_growth_pct, checked.fee_growth_pct, atol=1e-5)
    archived = pd.read_csv(FREQUENCY / "audit/fixed/performance.csv")
    high_frequency = archived.loc[
        archived.period.astype(str).eq("2026") & archived.candidate_id.isin(["account_36", "account_72"]),
        ["candidate_id", "n", "funded_mean", "marked_close_dd_before_funding"]
    ].sort_values("candidate_id")
    if list(high_frequency.candidate_id) != ["account_36", "account_72"]:
        raise ValueError("Missing high-frequency account comparison")
    return {
        "btc_variants": btc_admission_review(accounts),
        "spacing": clean(joined.drop(columns="selected").sort_values("minimum_gap_hours", ascending=False).to_dict("records")),
        "higher_frequency_2026": clean(high_frequency.to_dict("records")),
        "extra_baskets_2026": clean(extra[["entry_bjt", "member_count", "net_pct", "selected_6h", "selected_12h"]].to_dict("records")),
        "marks_2026": clean(marks[["minimum_gap_hours", "events", "fee_marked_dd_pct", "funded_events",
                                   "funded_growth_pct", "funded_marked_dd_pct"]].to_dict("records")),
    }


def build_summaries(events: list[dict[str, Any]]) -> dict[str, Any]:
    accounts = pd.read_csv(EXPERIMENT / "risk/accounts.csv")
    integrated_years = pd.read_csv(EXPERIMENT / "risk/integrated_years.csv")
    integrated_months = pd.read_csv(EXPERIMENT / "risk/integrated_months.csv")
    sensitivity = pd.read_csv(EXPERIMENT / "risk/integrated_sensitivity.csv")
    controls = pd.read_csv(EXPERIMENT / "risk/integrated_controls.csv")

    all_fee = accounts[(accounts.strategy == "below_plus_case") & (accounts.scope == "all_fee") & (accounts.extra_roundtrip_pp == 0)].iloc[0]
    all_fee_stress = accounts[(accounts.strategy == "below_plus_case") & (accounts.scope == "all_fee") & (accounts.extra_roundtrip_pp == 0.2)].iloc[0]
    actual_2026 = accounts[(accounts.strategy == "below_plus_case") & (accounts.scope == "2026_funded") & (accounts.extra_roundtrip_pp == 0)].iloc[0]
    actual_2026_stress = accounts[(accounts.strategy == "below_plus_case") & (accounts.scope == "2026_funded") & (accounts.extra_roundtrip_pp == 0.2)].iloc[0]
    actual_sensitivity = sensitivity[sensitivity.subset.eq("all_2026")].iloc[0]
    slipped = pd.read_csv(EXPERIMENT / "partial/sep16_native_cost_sensitivity.csv")
    slipped_case = slipped.loc[slipped.rule.eq("half_then_6d") & slipped.slip_market_bp_per_side.eq(10) & slipped.slip_limit_bp.eq(0)]
    if len(slipped_case) != 1 or slipped_case.iloc[0].exit_legs != 1:
        raise ValueError("September case slippage sensitivity does not reconcile")
    original_rows = accounts[accounts.strategy.eq("half_then_6d")]

    def scope_summary(source: pd.Series, scope: str, *, funded: bool) -> dict[str, Any]:
        return {
            "scope": scope,
            "event_count": int(source.distinct),
            "btc_event_count": int(source.btc_n),
            "alt_event_count": int(source.alt_n),
            "growth_pct": number(source.growth_pct),
            "max_drawdown_pct": number(source.max_dd_pct),
            "max_drawdown_basis": "synchronized 5-minute close marks",
            "returns_by_sleeve": {
                "BTC": {"growth_pct": number(source.btc_growth_pct), "max_drawdown_pct": number(source.btc_dd_pct)},
                "ALT": {"growth_pct": number(source.alt_growth_pct), "max_drawdown_pct": number(source.alt_dd_pct)},
            },
            "funding_included": funded,
        }

    def baseline_pair(scope: str, regular: pd.Series, stress: pd.Series) -> dict[str, Any]:
        return {
            "scope": scope,
            "event_count": int(regular.distinct),
            "btc_event_count": int(regular.btc_n),
            "alt_event_count": int(regular.alt_n),
            "growth_pct": number(regular.growth_pct),
            "max_drawdown_pct": number(regular.max_dd_pct),
            "stress_0_20pp_growth_pct": number(stress.growth_pct),
            "stress_0_20pp_max_drawdown_pct": number(stress.max_dd_pct),
            "returns_by_sleeve": {
                "BTC": {"growth_pct": number(regular.btc_growth_pct), "max_drawdown_pct": number(regular.btc_dd_pct)},
                "ALT": {"growth_pct": number(regular.alt_growth_pct), "max_drawdown_pct": number(regular.alt_dd_pct)},
            },
        }

    original_all = original_rows[(original_rows.scope == "all_fee") & (original_rows.extra_roundtrip_pp == 0)].iloc[0]
    original_all_stress = original_rows[(original_rows.scope == "all_fee") & (original_rows.extra_roundtrip_pp == 0.2)].iloc[0]
    original_2026 = original_rows[(original_rows.scope == "2026_funded") & (original_rows.extra_roundtrip_pp == 0)].iloc[0]
    original_2026_stress = original_rows[(original_rows.scope == "2026_funded") & (original_rows.extra_roundtrip_pp == 0.2)].iloc[0]

    yearly = []
    for row in integrated_years.sort_values("year").itertuples(index=False):
        yearly.append(
            {
                "year": int(row.year),
                "event_count": int(row.distinct),
                "btc_event_count": int(row.BTC),
                "alt_event_count": int(row.ALT),
                "equal_sleeves_growth_pct": number(row.growth_fee_only_year_reset),
                "return_basis": "fee-only; sleeve equity resets at January 1",
            }
        )

    actual_months = [
        {
            "month": str(row.month),
            "event_count": int(row.distinct),
            "distinct_entry_times": int(row.distinct),
            "event_count": int(row.legs),
            "wins": int(row.wins),
        }
        for row in integrated_months.sort_values("month").itertuples(index=False)
    ]
    matched = record(controls.iloc[0])
    matched["source"] = "risk/integrated_controls.csv"

    return {
        "all_history_fee_only": scope_summary(all_fee, "2020_to_2026-10-27", funded=False),
        "2026_actual_funded": {
            **scope_summary(actual_2026, "2026-01-01_to_2026-10-27", funded=True),
            "wins": int(actual_sensitivity.wins),
            "win_rate_pct": number(actual_sensitivity.win_pct),
            "active_month_count": int(len(integrated_months)),
            "minimum_monthly_entries": int(integrated_months.distinct.min()),
            "maximum_monthly_entries": int(integrated_months.distinct.max()),
            "monthly": actual_months,
        },
        "calendar_year_fee_only_reset": yearly,
        "stress_round_trip_0_20pp": {
            "scope": "separate 0.20 percentage-point round-trip sensitivity",
            "all_history_fee_only": {
                "growth_pct": number(all_fee_stress.growth_pct),
                "max_drawdown_pct": number(all_fee_stress.max_dd_pct),
            },
            "2026_actual_funded": {
                "growth_pct": number(actual_2026_stress.growth_pct),
                "max_drawdown_pct": number(actual_2026_stress.max_dd_pct),
            },
        },
        "baseline_original_half_then_6d": {
            "all_history_fee_only": baseline_pair("all_fee", original_all, original_all_stress),
            "2026_actual_funded": baseline_pair("2026_funded", original_2026, original_2026_stress),
        },
        "matched_control_2026": matched,
        "september_case_review": {
            "without_case_growth_pct": number(sensitivity.loc[sensitivity.subset.eq("omit_owner_case_entry"), "growth_funded"].item()),
            "slipped_funded_points": number(slipped_case.funded_points.item()),
        },
        "frequency_review": frequency_review(accounts),
    }


def build_payload() -> dict[str, Any]:
    btc_events = build_btc_events()
    alt_events = build_alt_events()
    events = sorted([*btc_events, *alt_events], key=lambda item: (item["entry_time"], item["asset"]))
    if len(events) != 398:
        raise ValueError(f"Expected 398 total events, got {len(events)}")
    ids = [event["event_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate normalized event IDs")

    counts_2026 = [event for event in events if event["epoch"] == 3]
    if len(counts_2026) != 48 or sum(event["asset"] == "BTC" for event in counts_2026) != 23:
        raise ValueError("2026 event counts disagree with the registered account")
    if sum(event["asset"] == "ALT" for event in counts_2026) != 25:
        raise ValueError("2026 ALT event count disagrees with the registered account")

    summaries = build_summaries(events)
    equity_curves = {
        "all_history_fee_only_settlement": close_curve(
            events, period="all_history", return_field="net_pct", start="2020-01-01T00:00:00Z"
        ),
        "2026_actual_funded_settlement": close_curve(
            counts_2026, period="2026_to_ledger_end", return_field="funded_pct", start="2026-01-01T00:00:00Z"
        ),
    }
    all_fee_terminal = equity_curves["all_history_fee_only_settlement"]["points"][-1]["combined_equity"]
    actual_terminal = equity_curves["2026_actual_funded_settlement"]["points"][-1]["combined_equity"]
    assert_close((all_fee_terminal - 1) * 100, summaries["all_history_fee_only"]["growth_pct"], "all-history curve terminal")
    assert_close((actual_terminal - 1) * 100, summaries["2026_actual_funded"]["growth_pct"], "2026 curve terminal")

    case_sep16 = [
        event for event in events
        if event["event_id"] == "BTC|2026-09-15T19:20:00Z"
    ]
    if len(case_sep16) != 1:
        raise ValueError("Missing the original 2026-09-16 Beijing BTC case event")
    sep_snapshot = case_sep16[0]["signal_snapshot"]
    if sep_snapshot["initial_touch_count"] != 68 or sep_snapshot["new_coins"] != 0:
        raise ValueError("September 16 case signal no longer records 68/0")

    return {
        "schema_version": "breadth-ema20.v1",
        "strategy_id": "breadth-ema20",
        "signal_cutoff_exclusive_utc": "2026-10-28T00:00:00Z",
        "provenance": {
            "experiment_id": "exp-monday-breadth-extension-20261108-v1",
            "btc_admission_study": str(BTC_ADMISSION.relative_to(ROOT) / "summary.csv"),
            "btc_reserve_study": str(BTC_RESERVE.relative_to(ROOT) / "partition_summary.csv"),
            "spacing_study": str(SPACING.relative_to(ROOT) / "portfolio_summary.csv"),
            "event_sources": {
                "BTC": "experiments/active/exp-monday-breadth-extension-20261108-v1/risk/integrated_btc_events.parquet",
                "ALT": "experiments/active/exp-monday-breadth-extension-20261108-v1/alt/event_results.csv.gz + alt/member_strategy_returns.parquet",
            },
            "signal_features": {
                "ALT": "experiments/active/exp-monday-breadth-frequency-20261108-v1/fixed/locked_events.parquet + experiments/active/exp-monday-breadth-optimization-20261108-v1/features/signals.csv.gz",
            },
            "BTC_fill_prices": {
                "medium": "experiments/active/exp-monday-breadth-extension-20261108-v1/dual/events.parquet (dual_source_specific, source=medium)",
                "case": "data/research/monday_breadth_optimization_20261108/btc_funded_exit_fills.parquet (policy_id=4)",
            },
            "ALT_2026_funding": "experiments/active/exp-monday-breadth-extension-20261108-v1/risk/alt_policy_verified_events_2026.csv + alt_6h_official_interval_checks.csv",
            "comparison": "Retrospective through the 2026-10-27 UTC signal candle; 2026 history had been inspected and is not a blind period.",
            "historical_funding": "All-history account totals and curves use fee-only net_pct; historical funded/proxy values are omitted from event fields and excluded from primary totals.",
            "time_semantics": "Event and member entry_time values are UTC 5-minute candle open timestamps; corresponding entry prices are that candle's open fills.",
            "costs": {
                "market_fee_per_fill_pct": 0.05,
                "BTC_profit_limit_fee_per_fill_pct": 0.02,
                "funding": "Included in the separate 2026 actual-funded summary and event field only; not mixed into all-history fee-only results.",
                "slippage_sensitivity_round_trip_pp": 0.2,
            },
        },
        "rules": {
            "btc_medium": {
                "candidate_id": "freq36_BTC",
                "initial_window_min": 720,
                "post_window_wait_min": 10,
                "initial_touch_count_min": 2,
                "new_coins_max": 0,
                "confirmation": "at least 75% of initially touched tradable altcoins reclaimed the lower band",
                "reclaim_fraction_min": 0.75,
                "max_hold_hours": 144,
                "ema_filter": {
                    "timeframe": "4h",
                    "ema_period": 20,
                    "accepted_when": "completed BTC 4h close <= EMA20",
                    "applies_to": "source=medium only",
                },
            },
            "btc_case": {
                "candidate_id": "case_target_funded_BTC",
                "initial_window_min": 5,
                "post_window_wait_min": 30,
                "initial_touch_count_min": 1,
                "market_share_28d_min_pct": 5.0,
                "new_coins_to_initial_max_pct": 25.0,
                "alt_move_min_pct": 2.0,
                "max_hold_hours": 144,
                "ema_filter_applies": False,
                "exit": {
                    "first_leg_size_pct": 50,
                    "first_leg_funded_net_target_points": 10000,
                    "remaining_leg_stop_pct": 6,
                    "remaining_leg_max_hold_hours": 144,
                    "first_leg_fill": "profit limit; maker fee",
                    "remaining_leg_fill": "market stop or deadline; taker fee",
                },
            },
            "alt": {
                "candidate_id": "freq36_ALT",
                "initial_window_min": 5,
                "post_window_wait_min": 0,
                "initial_touch_count_min": 2,
                "market_share_28d_min_pct": 5.0,
                "diffusion": "any",
                "confirmation": "none",
                "member_selection": "initial lower-touch altcoin constituents; the original membership is retained per event",
                "member_weighting": "equal dollar per constituent at entry",
                "hold_hours": 6,
                "exit": "each constituent exits at its own 6h open price",
                "excluded_symbols": ["BTC", "ETH", "SOL", "USTC"],
            },
            "account": {
                "sleeves": {"BTC": 0.5, "ALT": 0.5},
                "capital_transfers_between_sleeves": False,
                "signal_cutoff_exclusive_utc": "2026-10-28T00:00:00Z",
            },
        },
        "summaries": summaries,
        "equity_curves": equity_curves,
        "events": events,
    }


def main() -> None:
    payload = build_payload()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    temporary.replace(OUTPUT)
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: {len(payload['events'])} events, "
        f"{sum(len(event['members']) for event in payload['events'])} ALT members"
    )


if __name__ == "__main__":
    main()
