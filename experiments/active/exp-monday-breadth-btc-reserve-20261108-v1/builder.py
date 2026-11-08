"""Recombine frozen BTC/ALT event streams into independently funded sleeves.

No entry signal or transaction fill is regenerated here. The 177 ordinary BTC
events, the 53 already-reserved original BTC case events, and the original
ALT24h events are read from frozen artifacts. Only the account allocation and
portfolio aggregation are new. Run from the repository root with
``PYTHONPATH=. /Users/zhangzc/.local/bin/vectorbt-python <builder.py>``.
"""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_extension_risk as risk
from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation.monday_breadth_extension_alt import _interval_history_complete
from yoyo.evaluation.monday_breadth_extension_partial_marks import accumulated_cost


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
ADMISSION = ROOT / "experiments/active/exp-monday-breadth-btc-admission-20261108-v1/ordinary_stream.parquet"
CASE_SOURCE = ROOT / "experiments/active/exp-monday-breadth-frequency-20261108-v1/btc_target/threshold_events.parquet"
CASE_SCHEDULE = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1/dual/admission.csv"
ALT_EVENTS = ROOT / "experiments/active/exp-monday-breadth-alt-spacing-20261108-v1/events.csv"
EXT = ROOT / "experiments/active/exp-monday-breadth-extension-20261108-v1"
CASE_BJT_DATE = date(2026, 9, 16)
TICK = pd.Timedelta(minutes=5)
HOLD = pd.Timedelta(hours=144)
TAKER_PER_SIDE = 0.0005
WEIGHTS = {"BTC_ordinary": 0.25, "BTC_case": 0.25, "ALT": 0.50}
BASE_WEIGHTS = {"BTC": 0.50, "ALT": 0.50}


def _git_head() -> str:
    path = OUT.relative_to(ROOT) / "builder.py"
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", str(path)], cwd=ROOT, text=True
    ).strip()


def _utc(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column in result:
            result[column] = pd.to_datetime(result[column], utc=True)
    return result


def load_events() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    if not ADMISSION.is_file():
        raise FileNotFoundError(f"Wait for the frozen ordinary stream: {ADMISSION}")

    ordinary = _utc(pd.read_parquet(ADMISSION), ("decision", "known", "exit_time"))
    required = {"decision", "epoch", "entry_open", "exit_vwap", "net_pct", "raw_index"}
    missing = required.difference(ordinary.columns)
    if missing:
        raise ValueError(f"Ordinary frozen stream lacks required columns: {sorted(missing)}")
    if len(ordinary) != 177 or ordinary.decision.duplicated().any():
        raise ValueError(f"Expected 177 unique frozen ordinary admissions, got {len(ordinary)}")
    ordinary["exit_open"] = ordinary.exit_vwap
    if "exit_time" not in ordinary:
        ordinary["exit_time"] = ordinary.decision + HOLD
    if not ordinary.exit_time.eq(ordinary.decision + HOLD).all():
        raise ValueError("Ordinary source is not the frozen 144h VectorBT stream")
    ordinary = ordinary.sort_values("decision", kind="stable").reset_index(drop=True)
    ordinary["stream"] = "BTC_ordinary"
    ordinary["exit_legs"] = 1
    ordinary["net_points"] = ordinary.get("net_points", ordinary.entry_open * ordinary.net_pct / 100)
    ordinary["funded_pct"] = np.nan
    ordinary["funded_points"] = np.nan

    case_source = pd.read_parquet(CASE_SOURCE)
    case_source = case_source.loc[case_source.threshold_pct.eq(5.0)].copy()
    case_source = _utc(case_source, ("decision", "known", "exit_time"))
    if len(case_source) != 53 or case_source.decision.duplicated().any():
        raise ValueError(f"The original 5% case source changed from its frozen 53 events: {len(case_source)}")

    # The target builder already applied the six-day reservation separately in
    # each original epoch before the ordinary/case capital merge. Verify its
    # source timestamps and spacing; do not apply another admission gate here.
    schedule = _utc(pd.read_csv(CASE_SCHEDULE), ("decision",))
    scheduled_case = schedule.loc[schedule.source.eq("case")].sort_values("decision")
    source_keys = case_source[["decision", "epoch"]].sort_values("decision").reset_index(drop=True)
    schedule_keys = scheduled_case[["decision", "epoch"]].sort_values("decision").reset_index(drop=True)
    source_keys["decision"] = pd.DatetimeIndex(source_keys.decision).as_unit("ns")
    schedule_keys["decision"] = pd.DatetimeIndex(schedule_keys.decision).as_unit("ns")
    if not source_keys.equals(schedule_keys):
        raise AssertionError("The dual schedule's case reminders differ from the pre-reserved original 53")
    if not scheduled_case.admitted.sum() == 32:
        raise AssertionError("The old shared-BTC admission count changed; expected 32 of 53 case reminders")
    for _, group in source_keys.groupby("epoch", sort=False):
        gap = group.decision.sort_values().diff().dropna()
        if not gap.ge(HOLD).all():
            raise AssertionError("Original case source no longer has its per-epoch six-day reservation")

    native = pd.read_parquet(funding.FUNDED_CACHE, filters=[("policy_id", "=", 4)])
    native = _utc(native, ("entry_time", "exit_time", "first_exit_time"))
    if native.entry_time.duplicated().any():
        raise ValueError("Frozen policy 4 fill cache has duplicate entry times")
    case = case_source[["decision", "known", "epoch", "initial", "new_coins"]].merge(
        native,
        left_on="decision",
        right_on="entry_time",
        how="left",
        validate="one_to_one",
        suffixes=("_source", ""),
    )
    if case.entry_time.isna().any() or len(case) != 53:
        raise ValueError("Policy 4 native half-exit fills do not cover all 53 original case entries")
    case["stream"] = "BTC_case"
    case["raw_index"] = np.nan
    case["net_pct"] = pd.to_numeric(case.net_pct, errors="raise")
    case["net_points"] = pd.to_numeric(case.net_points, errors="raise")
    case["funded_pct"] = pd.to_numeric(case.funded_pct, errors="raise")
    case["funded_points"] = pd.to_numeric(case.funded_points, errors="raise")

    alt = pd.read_csv(ALT_EVENTS, parse_dates=["decision", "known"])
    alt = alt.loc[alt.selected_24h.astype(bool)].copy()
    alt = _utc(alt, ("decision", "known"))
    if len(alt) != 239 or alt.decision.duplicated().any():
        raise ValueError(f"Original ALT24h event stream changed from 239 entries: {len(alt)}")
    alt["exit_time"] = alt.decision + pd.Timedelta(hours=6)
    alt["stream"] = "ALT"
    alt["exit_legs"] = 1
    alt["net_points"] = np.nan
    alt["funded_points"] = np.nan
    alt["funded_pct"] = np.nan

    audit = pd.read_parquet(risk.PARENT / "audit/fixed/all_rule_and_account_events.parquet")
    original_alt = audit.loc[audit.candidate_id.eq("freq36_ALT")].copy()
    original_alt = _utc(original_alt, ("decision", "exit_time"))
    if set(original_alt.decision) != set(alt.decision):
        raise AssertionError("ALT24h clocks do not match the original frozen freq36_ALT stream")
    funded = original_alt.set_index("decision").funded_pct
    alt["funded_pct"] = alt.decision.map(funded)
    if alt.loc[alt.year.eq(2026), "funded_pct"].isna().any():
        raise ValueError("The original ALT 2026 source has incomplete funded event outcomes")

    baseline = pd.read_parquet(EXT / "risk/integrated_btc_events.parquet")
    baseline = _utc(baseline, ("decision", "exit_time", "first_exit_time"))
    metadata = {
        "case_source_events": len(case_source),
        "case_source_events_2026": int(case_source.epoch.eq(3).sum()),
        "case_source_original_admitted_in_shared_pool": int(scheduled_case.admitted.sum()),
        "ordinary_source_events": len(ordinary),
        "ordinary_source_events_2026": int(ordinary.epoch.eq(3).sum()),
        "alt_source_events": len(alt),
        "alt_source_events_2026": int(alt.year.eq(2026).sum()),
        "baseline_btc_events": len(baseline),
        "baseline_btc_events_2026": int(baseline.epoch.eq(3).sum()),
        "case_source_spacing_check": "exactly 53 source events, unchanged from the independently six-day-reserved source, verified per original epoch; no second admission pass",
    }
    return ordinary, case, alt, {"metadata": metadata, "baseline_btc": baseline, "baseline_alt": original_alt}


def _records(ordinary: pd.DataFrame, case: pd.DataFrame, alt: pd.DataFrame,
             baseline_btc: pd.DataFrame, baseline_alt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    def project(frame: pd.DataFrame, stream: str, base_stream: str, base_times: set[pd.Timestamp]) -> pd.DataFrame:
        def value(column: str, default):
            return frame[column] if column in frame else pd.Series(default, index=frame.index)

        result = pd.DataFrame({
            "stream": stream,
            "source_stream": value("source", base_stream),
            "decision": frame.decision,
            "exit_time": frame.exit_time,
            "epoch": frame.epoch,
            "net_pct": frame.net_pct,
            "funded_pct": value("funded_pct", np.nan),
            "net_points": value("net_points", np.nan),
            "funded_points": value("funded_points", np.nan),
            "exit_legs": value("exit_legs", 1),
            "entry_open": frame.get("entry_open", pd.Series(np.nan, index=frame.index)),
        })
        result["added_vs_baseline"] = ~result.decision.isin(base_times)
        return result

    baseline_groups = {
        "BTC_ordinary": set(baseline_btc.loc[baseline_btc.source.eq("medium"), "decision"]),
        "BTC_case": set(baseline_btc.loc[baseline_btc.source.eq("case"), "decision"]),
        "ALT": set(baseline_alt.decision),
    }
    active = pd.concat([
        project(ordinary, "BTC_ordinary", "medium", baseline_groups["BTC_ordinary"]),
        project(case, "BTC_case", "case", baseline_groups["BTC_case"]),
        project(alt, "ALT", "ALT", baseline_groups["ALT"]),
    ], ignore_index=True)
    baseline = pd.concat([
        project(baseline_btc.loc[baseline_btc.source.eq("medium")], "BTC", "medium", set()),
        project(baseline_btc.loc[baseline_btc.source.eq("case")], "BTC", "case", set()),
        project(baseline_alt, "ALT", "ALT", set()),
    ], ignore_index=True)
    # The comparison's BTC branch is a single 50% capital sleeve, so preserve
    # its original source rows while assigning a common summary stream key.
    baseline["added_vs_baseline"] = False
    return active, baseline


def _annotate_overlaps(events: pd.DataFrame) -> pd.DataFrame:
    result = events.copy().sort_values(["decision", "stream"], kind="stable").reset_index(drop=True)
    result["same_time_streams"] = ""
    result["overlapping_other_stream_events"] = 0
    result["baseline_added_overlap"] = False
    streams = result.stream.unique().tolist()
    for i, left_stream in enumerate(streams):
        left_idx = result.index[result.stream.eq(left_stream)].to_numpy()
        for right_stream in streams[i + 1:]:
            right_idx = result.index[result.stream.eq(right_stream)].to_numpy()
            for li in left_idx:
                a = result.loc[li]
                b = result.loc[right_idx]
                overlap = b.decision.lt(a.exit_time) & b.exit_time.gt(a.decision)
                hits = right_idx[overlap.to_numpy()]
                if not len(hits):
                    continue
                result.loc[li, "overlapping_other_stream_events"] += len(hits)
                result.loc[hits, "overlapping_other_stream_events"] += 1
                if bool(a.added_vs_baseline):
                    result.loc[hits, "baseline_added_overlap"] = True
                for ri in hits:
                    result.loc[ri, "baseline_added_overlap"] |= bool(a.added_vs_baseline)
                for ri in hits:
                    if result.at[li, "decision"] == result.at[ri, "decision"]:
                        left_text = result.at[li, "same_time_streams"]
                        right_text = result.at[ri, "same_time_streams"]
                        result.at[li, "same_time_streams"] = ",".join(filter(None, [left_text, right_stream]))
                        result.at[ri, "same_time_streams"] = ",".join(filter(None, [right_text, left_stream]))
    return result


def overlap_metrics(events: pd.DataFrame) -> dict:
    counts = events.groupby("decision").size()
    pair_total = 0
    same_time_pairs = 0
    btc_pairs = 0
    alt_btc_pairs = 0
    new_pairs = 0
    streams = events.stream.unique().tolist()
    for i, left_stream in enumerate(streams):
        left = events.loc[events.stream.eq(left_stream)]
        for right_stream in streams[i + 1:]:
            right = events.loc[events.stream.eq(right_stream)]
            for a in left.itertuples():
                hits = right.loc[right.decision.lt(a.exit_time) & right.exit_time.gt(a.decision)]
                pair_total += len(hits)
                same = int(hits.decision.eq(a.decision).sum())
                same_time_pairs += same
                if {left_stream, right_stream} == {"BTC_ordinary", "BTC_case"}:
                    btc_pairs += len(hits)
                elif "ALT" in {left_stream, right_stream}:
                    alt_btc_pairs += len(hits)
                if bool(a.added_vs_baseline):
                    new_pairs += len(hits)
                else:
                    new_pairs += int(hits.added_vs_baseline.sum())
    repeated = counts[counts.gt(1)]
    repeated_times = set(repeated.index)
    new_repeat_times = int(events.loc[
        events.added_vs_baseline & events.decision.isin(repeated_times), "decision"
    ].nunique())
    return {
        "distinct_entries": int(events.decision.nunique()),
        "repeat_entry_times": int(len(repeated)),
        "repeat_entry_legs": int(repeated.sum()),
        "holding_overlap_pairs": int(pair_total),
        "same_time_pair_count": int(same_time_pairs),
        "non_same_time_overlap_pairs": int(pair_total - same_time_pairs),
        "btc_cross_sleeve_overlap_pairs": int(btc_pairs),
        "alt_btc_overlap_pairs": int(alt_btc_pairs),
        "new_repeated_entry_times": new_repeat_times,
        "new_holding_overlap_pairs": int(new_pairs),
    }


def settlement_metrics(events: pd.DataFrame, weights: dict[str, float], return_col: str) -> tuple[float, float]:
    if events.empty or events[return_col].isna().any():
        return np.nan, np.nan
    nav = {stream: 1.0 for stream in weights}
    if events.stream.isin(weights).sum() != len(events):
        raise ValueError("Unknown sleeve in portfolio event stream")
    for stream in weights:
        rows = events.loc[events.stream.eq(stream)].sort_values("decision")
        if len(rows) > 1 and rows.decision.iloc[1:].reset_index(drop=True).lt(
                rows.exit_time.iloc[:-1].reset_index(drop=True)).any():
            raise ValueError(f"Overlapping positions within independent sleeve {stream}")
    exits = events.groupby("exit_time", sort=True)
    curve = [1.0]
    for _, rows in exits:
        for row in rows.itertuples():
            nav[row.stream] *= 1 + float(getattr(row, return_col)) / 100
        curve.append(sum(weights[key] * nav[key] for key in weights))
    curve = pd.Series(curve, dtype=float)
    dd = (1 - curve / curve.cummax().clip(lower=1)).max()
    final = sum(weights[key] * nav[key] for key in weights)
    return 100 * (final - 1), 100 * dd


def build_btc_five_minute_paths(ordinary: pd.DataFrame, case: pd.DataFrame,
                                baseline_btc: pd.DataFrame) -> tuple[dict, dict, dict, dict]:
    start = min(ordinary.decision.min(), case.decision.min(), baseline_btc.decision.min())
    last_exit = max(ordinary.exit_time.max(), case.exit_time.max(), baseline_btc.exit_time.max())
    quotes, _ = prices.load_full_ohlc("BTCUSDT", start.floor("D"), last_exit, repair_missing=False)
    native = pd.read_parquet(funding.FUNDED_CACHE, filters=[("policy_id", "=", 4)])
    partials = native.set_index("entry_time")
    fee_ordinary = risk.btc_marks(ordinary, quotes, partials)
    fee_case = risk.btc_marks(case, quotes, partials)
    fee_baseline = risk.btc_marks(baseline_btc, quotes, partials)

    history = funding.rates("BTCUSDT", history=True, local_only=True)
    settlements = history.realized_rate * history.mark_price.fillna(quotes.open.reindex(history.index))
    missing: list[str] = []
    funded_paths: dict[str, dict[pd.Timestamp, np.ndarray]] = {
        "BTC_ordinary": {}, "BTC_case": {}, "BTC_baseline": {}
    }
    rows_by_stream = {"BTC_ordinary": ordinary.loc[ordinary.epoch.eq(3)],
                      "BTC_case": case.loc[case.epoch.eq(3)],
                      "BTC_baseline": baseline_btc.loc[baseline_btc.epoch.eq(3)]}
    fee_paths = {"BTC_ordinary": fee_ordinary, "BTC_case": fee_case,
                 "BTC_baseline": fee_baseline}
    funding_rows: list[dict] = []
    for stream, frame in rows_by_stream.items():
        for row in frame.loc[frame.epoch.eq(3)].itertuples():
            if not _interval_history_complete(history, row.decision, row.exit_time):
                missing.append(f"{stream}:{row.decision.isoformat()}")
                funding_rows.append({"stream": stream, "decision": row.decision,
                                     "funding_status": "missing_or_incomplete_local_history",
                                     "funding_points": np.nan, "funded_pct": np.nan})
                continue
            times = pd.date_range(row.decision, row.exit_time, freq=TICK)
            costs = accumulated_cost(settlements, row.decision, row.exit_time, times)
            if row.exit_legs == 2:
                costs = 0.5 * (costs + accumulated_cost(settlements, row.decision,
                                                       row.first_exit_time, times))
            funding_points = float(costs[-1])
            if pd.notna(getattr(row, "funding_points", np.nan)):
                np.testing.assert_allclose(funding_points, row.funding_points, atol=1e-6)
            path = fee_paths[stream][row.decision] - costs / row.entry_open
            funded_pct = 100 * (path[-1] - 1)
            if pd.notna(getattr(row, "funded_pct", np.nan)):
                np.testing.assert_allclose(funded_pct, row.funded_pct, atol=1e-7)
            funded_paths[stream][row.decision] = path
            funding_rows.append({"stream": stream, "decision": row.decision,
                                 "funding_status": "complete_local_actual_settlements",
                                 "funding_points": funding_points, "funded_pct": funded_pct})
    return {"BTC_ordinary": fee_ordinary, "BTC_case": fee_case, "BTC_baseline": fee_baseline}, {
        "missing": missing,
        "rows": pd.DataFrame(funding_rows),
        "quotes": quotes,
        "funded_paths": funded_paths,
    }


def _sep16_metrics(case_events: pd.DataFrame, ordinary_events: pd.DataFrame) -> dict:
    local_day = case_events.decision.dt.tz_convert("Asia/Shanghai").dt.date
    sep = case_events.loc[local_day.eq(CASE_BJT_DATE)].copy()
    if len(sep) != 1:
        raise AssertionError(f"Expected exactly one original September 16 BTC case event, got {len(sep)}")
    row = sep.iloc[0]
    prior_all = case_events.loc[case_events.decision.lt(row.decision)].sort_values("decision")
    all_returns = prior_all.net_pct.to_numpy(dtype=float)
    if not np.isfinite(all_returns).all() or pd.isna(row.funded_pct):
        raise ValueError("Cannot value September 16 from complete fee-only history and funded 2026 outcome")
    full_equity = 250 * np.prod(1 + all_returns / 100)
    prior_2026 = prior_all.loc[prior_all.decision.dt.year.eq(2026), "funded_pct"].to_numpy(dtype=float)
    equity_2026 = 250 * np.prod(1 + prior_2026 / 100)
    pnl_2026 = equity_2026 * float(row.funded_pct) / 100
    overlaps = ordinary_events.loc[
        ordinary_events.decision.lt(row.decision) & ordinary_events.exit_time.gt(row.decision), "decision"
    ].sort_values()
    return {
        "sep16_admitted": True,
        "sep16_decision": row.decision,
        "sep16_case_net_points": float(row.net_points),
        "sep16_case_funded_points": float(row.funded_points),
        "sep16_case_fee_net_pct": float(row.net_pct),
        "sep16_case_funded_pct": float(row.funded_pct),
        "sep16_case_equity_pre_usd_all_history_seed1000": float(full_equity),
        "sep16_case_contribution_usd_all_history_seed1000": float(full_equity * row.funded_pct / 100),
        "sep16_case_equity_pre_usd_2026_reset": float(equity_2026),
        "sep16_case_contribution_usd_2026_reset": float(pnl_2026),
        "sep16_case_exit_legs": int(row.exit_legs),
        "sep16_overlapping_ordinary_count": int(len(overlaps)),
        "sep16_overlapping_ordinary_decisions": " | ".join(t.isoformat() for t in overlaps),
        "sep16_all_history_basis": "$250 initial case sleeve compounded fee-only before entry; Sep 16 PnL uses the actual funded return",
        "sep16_2026_basis": "2026-only reset; fee and actual funding included",
    }


def _baseline_sep16_metrics(btc_events: pd.DataFrame) -> dict:
    local_day = btc_events.decision.dt.tz_convert("Asia/Shanghai").dt.date
    sep = btc_events.loc[local_day.eq(CASE_BJT_DATE)].copy()
    if len(sep) != 1 or sep.source.iloc[0] != "case":
        raise AssertionError("The frozen below-plus-case BTC baseline must include the September 16 case")
    row = sep.iloc[0]
    before = btc_events.loc[btc_events.decision.lt(row.decision)].sort_values("decision")
    all_history_equity = 500 * np.prod(1 + before.net_pct.to_numpy(dtype=float) / 100)
    prior_2026 = before.loc[before.epoch.eq(3), "funded_pct"].to_numpy(dtype=float)
    if not np.isfinite(prior_2026).all():
        raise ValueError("Baseline September 16 balance lacks a funded 2026 prior-event outcome")
    reset_equity = 500 * np.prod(1 + prior_2026 / 100)
    return {
        "sep16_admitted": True,
        "sep16_decision": row.decision,
        "sep16_case_net_points": float(row.net_points),
        "sep16_case_funded_points": float(row.funded_points),
        "sep16_case_fee_net_pct": float(row.net_pct),
        "sep16_case_funded_pct": float(row.funded_pct),
        "sep16_case_equity_pre_usd_all_history_seed1000": float(all_history_equity),
        "sep16_case_contribution_usd_all_history_seed1000": float(all_history_equity * row.net_pct / 100),
        "sep16_case_equity_pre_usd_2026_reset": float(reset_equity),
        "sep16_case_contribution_usd_2026_reset": float(reset_equity * row.funded_pct / 100),
        "sep16_case_exit_legs": int(row.exit_legs),
        "sep16_overlapping_ordinary_count": 0,
        "sep16_overlapping_ordinary_decisions": "",
        "sep16_all_history_basis": "$500 initial BTC sleeve compounded fee-only before entry; event contribution uses fee-only return",
        "sep16_2026_basis": "$500 initial BTC sleeve reset in 2026; event contribution uses actual fees and funding",
    }


def _summary_row(variant: str, scope: str, events: pd.DataFrame, return_col: str,
                 weights: dict[str, float], marked_dd: float | None,
                 funding_status: str, sep16: dict) -> dict:
    growth, settlement_dd = settlement_metrics(events, weights, return_col)
    overlap = overlap_metrics(events)
    ordinary = int(events.stream.eq("BTC_ordinary").sum()) if "BTC_ordinary" in events.stream.unique() else int(events.source_stream.eq("medium").sum())
    case = int(events.stream.eq("BTC_case").sum()) if "BTC_case" in events.stream.unique() else int(events.source_stream.eq("case").sum())
    btc = ordinary + case if variant == "reserved_25_25_50" else int(events.stream.eq("BTC").sum())
    alt = int(events.stream.eq("ALT").sum())
    exit_legs = int(events.exit_legs.sum())
    new_rows = int(events.added_vs_baseline.sum())
    row = {
        "variant": variant,
        "scope": scope,
        "btc_count": btc,
        "btc_ordinary_count": ordinary,
        "btc_below_count": ordinary,
        "btc_case_count": case,
        "alt_count": alt,
        "total_legs": len(events),
        "native_exit_legs": exit_legs,
        "growth_pct": growth,
        "settlement_dd_pct": settlement_dd,
        "marked_dd_pct": marked_dd,
        "new_vs_baseline_event_rows": new_rows,
        "funding_coverage_count": int(events.funded_pct.notna().sum()) if scope == "2026" else 0,
        "funding_missing_count": int(events.funded_pct.isna().sum()) if scope == "2026" else 0,
        "funding_status": funding_status,
        "risk_basis": ("2026 fees and funding; synchronized 5m marks for marked_dd_pct; settlement_dd_pct samples exits only"
                       if scope == "2026" else "fee-only outcomes and synchronized 5m price marks; settlement_dd_pct samples exits only"),
        **overlap,
        **sep16,
    }
    return row


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ordinary, case, alt, loaded = load_events()
    active, baseline = _records(ordinary, case, alt, loaded["baseline_btc"], loaded["baseline_alt"])
    active = _annotate_overlaps(active)
    baseline = _annotate_overlaps(baseline)
    sep16 = _sep16_metrics(case, ordinary)
    baseline_sep16 = _baseline_sep16_metrics(loaded["baseline_btc"])

    # Full history deliberately uses the common fee-only basis. Funding is only
    # applied to the explicitly scoped 2026 comparison below.
    active_all = active.copy()
    base_all = baseline.copy()
    active_all["funded_pct"] = active_all["net_pct"]
    base_all["funded_pct"] = base_all["net_pct"]
    active_all = active_all.sort_values("decision")
    base_all = base_all.sort_values("decision")
    full_weights = WEIGHTS
    baseline_weights = BASE_WEIGHTS

    curves = pd.read_parquet(EXT / "sleeves/equity_5m.parquet")
    grid_2026 = curves.index
    grid_all = pd.date_range(min(active.decision.min(), baseline.decision.min()).floor("D"),
                             max(active.exit_time.max(), baseline.exit_time.max()), freq=TICK)
    ordinary26 = ordinary.loc[ordinary.epoch.eq(3)].copy()
    case26 = case.loc[case.epoch.eq(3)].copy()
    alt26 = alt.loc[alt.year.eq(2026)].copy()
    fee_paths, marks = build_btc_five_minute_paths(ordinary, case, loaded["baseline_btc"])
    btc_ordinary_funded = marks["rows"].loc[marks["rows"].stream.eq("BTC_ordinary")].set_index("decision")
    btc_case_funded = marks["rows"].loc[marks["rows"].stream.eq("BTC_case")].set_index("decision")
    btc_baseline_funded = marks["rows"].loc[marks["rows"].stream.eq("BTC_baseline")].set_index("decision")
    ordinary26["funded_pct"] = ordinary26.decision.map(btc_ordinary_funded.funded_pct)
    ordinary26["funding_points"] = ordinary26.decision.map(btc_ordinary_funded.funding_points)
    case26["funded_pct"] = case26.decision.map(btc_case_funded.funded_pct)
    if len(marks["missing"]):
        funding_status = "incomplete local coverage: " + " | ".join(marks["missing"])
    else:
        funding_status = "complete local actual BTC settlements; ALT reuses verified funded 5m marks"
    if ordinary26.funded_pct.isna().any() or case26.funded_pct.isna().any():
        raise ValueError("2026 BTC funding is incomplete; no missing interval is treated as zero")

    # Reuse the original ALT branch's frozen actual 2026 5m equity marks.
    alt_funded_paths = risk.funded_relative(alt26, curves.ALT)
    with np.load(EXT / "risk/alt_6h_marks.npz") as alt_fee_cache:
        alt_fee_times = pd.to_datetime(alt_fee_cache["timestamps_ns"], utc=True)
        alt_fee_paths = dict(zip(alt_fee_times, alt_fee_cache["unit_fee_values"], strict=True))
    if set(alt.decision) != set(alt_fee_paths):
        raise AssertionError("Frozen ALT fee mark cache no longer matches all 239 ALT24h events")
    for row in alt.itertuples():
        np.testing.assert_allclose(alt_fee_paths[row.decision][-1], 1 + row.net_pct / 100, atol=5e-7)

    # Add actual 2026 funded paths. Historical fee paths remain those of the frozen fills.
    funded_paths = marks["funded_paths"]
    ord_paths = funded_paths["BTC_ordinary"]
    case_paths = funded_paths["BTC_case"]
    base_paths = funded_paths["BTC_baseline"]

    if len(ord_paths) != len(ordinary26) or len(case_paths) != len(case26):
        raise ValueError("Missing 2026 BTC marked event paths")
    for row in alt26.itertuples():
        if row.decision not in alt_funded_paths:
            raise ValueError(f"Missing synchronized ALT funded marks at {row.decision}")

    # Reuse the risk adapter's independent sleeve compounding and mark-to-market drawdown.
    curves_new = {
        "BTC_ordinary": risk.sleeve(ordinary26, ord_paths, grid_2026, 1.0, 0),
        "BTC_case": risk.sleeve(case26, case_paths, grid_2026, 1.0, 0),
        "ALT": risk.sleeve(alt26, alt_funded_paths, grid_2026, 1.0, 0),
    }
    total_new = 0.25 * curves_new["BTC_ordinary"] + 0.25 * curves_new["BTC_case"] + 0.50 * curves_new["ALT"]
    marked_new = float(risk.stats(total_new)["max_dd_pct"])

    alt_fee_ledger = active.loc[active.stream.eq("ALT")].copy()
    new_fee_curves = {
        "BTC_ordinary": risk.sleeve(ordinary, fee_paths["BTC_ordinary"], grid_all, 1.0, 0),
        "BTC_case": risk.sleeve(case, fee_paths["BTC_case"], grid_all, 1.0, 0),
        "ALT": risk.sleeve(alt_fee_ledger, alt_fee_paths, grid_all, 1.0, 0),
    }
    total_new_fee = (0.25 * new_fee_curves["BTC_ordinary"] + 0.25 * new_fee_curves["BTC_case"]
                     + 0.50 * new_fee_curves["ALT"])
    marked_new_all_history = float(risk.stats(total_new_fee)["max_dd_pct"])

    base_all_btc = loaded["baseline_btc"]
    base_all_alt = loaded["baseline_alt"]
    baseline_fee_curves = {
        "BTC": risk.sleeve(base_all_btc, fee_paths["BTC_baseline"], grid_all, 1.0, 0),
        "ALT": risk.sleeve(base_all_alt, alt_fee_paths, grid_all, 1.0, 0),
    }
    total_baseline_fee = 0.50 * baseline_fee_curves["BTC"] + 0.50 * baseline_fee_curves["ALT"]
    baseline_all_marked = float(risk.stats(total_baseline_fee)["max_dd_pct"])

    # The frozen baseline's 2026 marked DD was measured on the same synchronized 5m/funding basis.
    old_mark = pd.read_csv(OUT.parent / "exp-monday-breadth-alt-spacing-20261108-v1/marks_2026.csv")
    old_mark = old_mark.loc[old_mark.minimum_gap_hours.eq(24)].iloc[0]
    baseline26_marked = float(old_mark.funded_marked_dd_pct)
    baseline26_growth = float(old_mark.funded_growth_pct)
    baseline26_paths = risk.sleeve(
        loaded["baseline_btc"].loc[loaded["baseline_btc"].epoch.eq(3)],
        {t: base_paths[t] for t in loaded["baseline_btc"].loc[loaded["baseline_btc"].epoch.eq(3), "decision"]},
        grid_2026, 1.0, 0,
    )
    total_baseline_2026 = 0.50 * baseline26_paths + 0.50 * risk.sleeve(alt26, alt_funded_paths, grid_2026, 1.0, 0)
    baseline26_marked_rebuilt = float(risk.stats(total_baseline_2026)["max_dd_pct"])
    np.testing.assert_allclose(baseline26_marked_rebuilt, baseline26_marked, atol=1e-6)

    active26 = active.loc[active.decision.dt.year.eq(2026)].copy()
    active26.loc[active26.stream.eq("BTC_ordinary"), "funded_pct"] = active26.loc[
        active26.stream.eq("BTC_ordinary"), "decision"].map(ordinary26.set_index("decision").funded_pct)
    active26.loc[active26.stream.eq("BTC_case"), "funded_pct"] = active26.loc[
        active26.stream.eq("BTC_case"), "decision"].map(case26.set_index("decision").funded_pct)
    base26 = baseline.loc[baseline.decision.dt.year.eq(2026)].copy()
    base26.loc[base26.stream.eq("BTC"), "funded_pct"] = base26.loc[
        base26.stream.eq("BTC"), "decision"].map(loaded["baseline_btc"].set_index("decision").funded_pct)
    base26.loc[base26.stream.eq("ALT"), "funded_pct"] = base26.loc[
        base26.stream.eq("ALT"), "decision"].map(loaded["baseline_alt"].set_index("decision").funded_pct)
    if active26.funded_pct.isna().any() or base26.funded_pct.isna().any():
        raise ValueError("2026 portfolio contains a leg without an explicit funded return")

    # Baseline is a single 50% BTC sleeve plus 50% ALT; merge BTC source labels.
    base26["stream"] = base26.stream.replace({"BTC": "BTC"})
    summary = [
        _summary_row("reserved_25_25_50", "all_history", active_all, "net_pct", full_weights, marked_new_all_history,
                     "not applied outside 2026", sep16),
        _summary_row("reserved_25_25_50", "2026", active26, "funded_pct", full_weights, marked_new,
                     funding_status, sep16),
        _summary_row("frozen_below_plus_case_50_50", "all_history", base_all, "net_pct", baseline_weights,
                     baseline_all_marked, "fee-only", baseline_sep16),
        _summary_row("frozen_below_plus_case_50_50", "2026", base26, "funded_pct", baseline_weights,
                     baseline26_marked, "complete existing funded marks", baseline_sep16),
    ]
    summary_df = pd.DataFrame(summary)
    if not np.isclose(summary_df.loc[(summary_df.variant == "frozen_below_plus_case_50_50") &
                                     summary_df.scope.eq("2026"), "growth_pct"].iloc[0],
                      baseline26_growth, atol=1e-6):
        raise AssertionError("Frozen 2026 baseline growth no longer matches its synchronized funding output")

    # Include event-level provenance, overlaps, and actual allocated case-sleeve dollars.
    ledger = active.copy()
    ledger["allocation_pct"] = ledger.stream.map({key: 100 * value for key, value in WEIGHTS.items()})
    ledger["funding_points"] = np.nan
    ledger["funded_pct_2026"] = np.nan
    ledger.loc[ledger.stream.eq("BTC_ordinary"), "funding_points"] = ledger.loc[
        ledger.stream.eq("BTC_ordinary"), "decision"].map(ordinary26.set_index("decision").funding_points)
    ledger.loc[ledger.stream.eq("BTC_ordinary"), "funded_pct_2026"] = ledger.loc[
        ledger.stream.eq("BTC_ordinary"), "decision"].map(ordinary26.set_index("decision").funded_pct)
    ledger.loc[ledger.stream.eq("BTC_case"), "funding_points"] = ledger.loc[
        ledger.stream.eq("BTC_case"), "decision"].map(case.funding_points.set_axis(case.decision))
    case26_funded = case.loc[case.epoch.eq(3)].set_index("decision").funded_pct
    ledger.loc[ledger.stream.eq("BTC_case"), "funded_pct_2026"] = ledger.loc[
        ledger.stream.eq("BTC_case"), "decision"].map(case26_funded)
    ledger.loc[ledger.stream.eq("ALT"), "funded_pct_2026"] = ledger.loc[
        ledger.stream.eq("ALT"), "decision"].map(alt.set_index("decision").funded_pct)
    ledger["case_equity_pre_usd_all_history_seed1000"] = np.nan
    ledger["case_equity_pre_usd_2026_reset"] = np.nan
    ledger["case_account_pnl_usd_all_history_seed1000"] = np.nan
    ledger["case_account_pnl_usd_2026_reset"] = np.nan
    sep_mask = ledger.stream.eq("BTC_case") & ledger.decision.eq(sep16["sep16_decision"])
    for key, value in {
        "case_equity_pre_usd_all_history_seed1000": sep16["sep16_case_equity_pre_usd_all_history_seed1000"],
        "case_equity_pre_usd_2026_reset": sep16["sep16_case_equity_pre_usd_2026_reset"],
        "case_account_pnl_usd_all_history_seed1000": sep16["sep16_case_contribution_usd_all_history_seed1000"],
        "case_account_pnl_usd_2026_reset": sep16["sep16_case_contribution_usd_2026_reset"],
    }.items():
        ledger.loc[sep_mask, key] = value

    summary_df.to_csv(OUT / "partition_summary.csv", index=False)
    ledger.sort_values(["decision", "stream"], kind="stable").to_csv(OUT / "event_ledger.csv", index=False)
    marks["rows"].to_csv(OUT / "btc_2026_funding_coverage.csv", index=False)
    manifest = {
        "experiment_id": "exp-monday-breadth-btc-reserve-20261108-v1",
        "builder_commit": _git_head(),
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "inputs": {
            "ordinary_stream": str(ADMISSION.relative_to(ROOT)),
            "original_case_stream": str(CASE_SOURCE.relative_to(ROOT)),
            "case_native_fills": str(funding.FUNDED_CACHE.relative_to(ROOT)),
            "alt24h_events": str(ALT_EVENTS.relative_to(ROOT)),
            "baseline_btc": str((EXT / "risk/integrated_btc_events.parquet").relative_to(ROOT)),
            "risk_adapter": "yoyo.evaluation.monday_breadth_extension_risk",
        },
        "source_counts": loaded["metadata"],
        "case_policy_id": 4,
        "case_source_retained_without_second_admission_pass": True,
        "2026_btc_funding_missing_intervals": marks["missing"],
        "2026_funding_status": funding_status,
        "september_16": {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v)
                          for k, v in sep16.items()},
        "portfolio": "unlevered 25% ordinary BTC, 25% original case BTC, 50% ALT24h; independent compounding, no transfers",
        "scope_notes": {
            "all_history": "fee-only returns; both five-minute price-marked DD and exit-settlement DD are reported; no funding is applied",
            "2026": "funding-adjusted returns through 2026-10-27 and synchronized five-minute marked DD; missing funding is an error, never zero",
            "counting": "total_legs counts allocated entry events per source stream; distinct_entries de-duplicates exact timestamps; native_exit_legs separately counts partial exit legs",
            "post_hoc": "The 24h 50/50 baseline to 25/25/50 split was formed after inspecting the ordinary EMA prefilter and the blocked 2026-09-16 case event.",
        },
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str) + "\n")
    print(summary_df.to_string(index=False))
    print("BTC funding coverage:", funding_status)
    print("Wrote", OUT)


if __name__ == "__main__":
    run()
