"""Pre-2025/2025 stability screen and frozen follow-up for 5m candidates.

The fitting phase uses only (a) the existing pre-2025 10% summary and (b)
pre-2025 5% plus UTC-2025 account replays. It writes and freezes selected
profiles before the evaluation phase reads any 2026 outcomes. All fills come
from ``run_research.independent`` and all shared-wallet runs use
``account.run_account``; this module contains no trade/fill engine.

This is a secondary stability screen. It was requested after the first-round
four-winner summary had been viewed, so it is not described as a blind test.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
import sys

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_research as research  # noqa: E402
from account import run_account  # noqa: E402


FIT_PATH = HERE / "stability_fit.csv"
SELECTED_PATH = HERE / "stability_selected.json"
METRICS_PATH = HERE / "stability_metrics.csv"
INITIAL_CASH = 1_000.0
FRACTIONS = (0.05, 0.10)
MAX_SLOTS = 10
BASE_SLIPPAGE_BPS = 1
STRESS_SLIPPAGE_BPS = 5
YEAR_MS = 365.25 * 24 * 60 * 60 * 1000

EARLY_START_MS = int(research.START)
EARLY_END_MS = int(research.SPLIT)
YEAR_2025_START_MS = int(pd.Timestamp("2025-01-01T00:00:00Z").value // 1_000_000)
YEAR_2025_END_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000)
YEAR_2026_START_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").value // 1_000_000)
PERIOD_END_MS = int(research.END)

OBJECTIVES = {
    "net_win_rate": "net_win_rate_pct",
    "annualized_return": "annualized_return_pct",
    "trades_per_year": "trades_per_year",
    "return_over_intrabar_dd": "annualized_return_over_intrabar_dd",
}
FIT_COLUMNS = [
    "strategy", "fraction", "early_source", "early_trades_funded", "early_net_win_rate_pct",
    "early_net_return_pct", "early_final_equity", "early_max_dd_intrabar_pct",
    "early_trades_per_year", "early_annualized_return_pct", "early_annualized_return_over_intrabar_dd",
    "2025_trades_funded", "2025_net_win_rate_pct", "2025_net_return_pct", "2025_final_equity",
    "2025_max_dd_intrabar_pct", "2025_trades_per_year", "2025_annualized_return_pct",
    "2025_annualized_return_over_intrabar_dd", "worst_net_win_rate_pct",
    "worst_annualized_return_pct", "worst_trades_per_year", "worst_return_over_intrabar_dd",
    "minimum_annualized_return_pct", "early_positive", "2025_positive", "early_dd_pass",
    "2025_dd_pass", "early_frequency_pass", "2025_frequency_pass", "eligible",
    "diagnostic_eligible", "fail_reasons",
]
METRIC_COLUMNS = [
    "strategy", "fraction", "profile_roles", "mode", "market_slippage_bps", "period",
    "signal_candidates_in_period", "matched_signal_candidates", "match_coverage_pct",
    "placebo_clocks_in_period", "placebo_clock_coverage_pct", "trades_funded",
    "win_rate_pct", "net_return_pct", "final_equity", "max_dd_close_pct",
    "max_dd_intrabar_pct", "max_gross_exposure_pct", "trade_frequency_per_year",
    "annualized_return_pct", "annualized_return_over_intrabar_dd", "max_unrealized_profit_pct",
    "max_unrealized_profit_usdt", "trades_outside_period", "skipped_same_symbol",
    "skipped_capacity", "initial_cash", "period_start_ms", "period_end_ms",
]


def _as_float(value: Any, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _annualized_return(final_equity: float, start_ms: int, end_ms: int) -> float:
    years = (end_ms - start_ms) / YEAR_MS
    if years <= 0 or not math.isfinite(final_equity) or final_equity <= 0:
        return -100.0
    return float((math.pow(final_equity / INITIAL_CASH, 1.0 / years) - 1.0) * 100.0)


def _return_over_dd(annualized_pct: float, dd_pct: float) -> float:
    if not math.isfinite(annualized_pct) or not math.isfinite(dd_pct):
        return np.nan
    if dd_pct > 0:
        return float(annualized_pct / dd_pct)
    if annualized_pct > 0:
        return math.inf
    if annualized_pct < 0:
        return -math.inf
    return 0.0


def _summary_metrics(summary: dict, start_ms: int, end_ms: int) -> dict:
    final_equity = _as_float(summary.get("final_equity"), INITIAL_CASH)
    ann = _annualized_return(final_equity, start_ms, end_ms)
    dd = abs(_as_float(summary.get("max_dd_intrabar_pct"), np.nan))
    return {
        "trades_funded": int(_as_float(summary.get("trades_funded"), 0.0)),
        "net_win_rate_pct": _as_float(summary.get("win_rate_pct")),
        "net_return_pct": _as_float(summary.get("net_return_pct")),
        "final_equity": final_equity,
        "max_dd_intrabar_pct": dd,
        "trades_per_year": _as_float(summary.get("trade_frequency_per_year")),
        "annualized_return_pct": ann,
        "annualized_return_over_intrabar_dd": _return_over_dd(ann, dd),
    }


def _slice_period(
    fills: pd.DataFrame, marks: pd.DataFrame, start_ms: int, end_ms: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    in_period = (
        fills.entry_ms.ge(start_ms) & fills.entry_ms.lt(end_ms)
        & fills.exit_ms.ge(start_ms) & fills.exit_ms.lt(end_ms)
    )
    selected = fills.loc[in_period].copy()
    selected_marks = marks.loc[marks.candidate_id.isin(selected.candidate_id)].copy()
    return selected, selected_marks


def _run_account_period(
    fills: pd.DataFrame,
    marks: pd.DataFrame,
    *,
    period_name: str,
    account_period: str,
    start_ms: int,
    end_ms: int,
    fraction: float,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    period_fills, period_marks = _slice_period(fills, marks, start_ms, end_ms)
    result = run_account(
        period_fills,
        period_marks,
        period=account_period,
        period_start_ms=start_ms,
        period_end_ms=end_ms,
        initial_cash=INITIAL_CASH,
        max_slots=MAX_SLOTS,
        per_trade_fraction=fraction,
        slippage_bps=0.0,  # The 1/5 bp market slip is already in VectorBT engine fills.
    )
    summary = dict(result["summary"])
    summary["period"] = period_name
    summary.update(_summary_metrics(summary, start_ms, end_ms))
    return summary, result["funded_trades"], result["equity_5m"]


def _prior_early_10pct(cfgs: dict) -> dict[str, dict]:
    path = HERE / "selection_period_all.csv"
    if not path.is_file():
        raise FileNotFoundError(f"Required existing 10% early summary is missing: {path}")
    source = pd.read_csv(path)
    required = {"period", "strategy", "fraction", "trades_funded", "win_rate_pct",
                "net_return_pct", "final_equity", "max_dd_intrabar_pct",
                "trade_frequency_per_year"}
    missing = required.difference(source.columns)
    if missing:
        raise ValueError(f"selection_period_all.csv is missing fields {sorted(missing)}")
    early = source.loc[
        source.period.eq("early")
        & np.isclose(pd.to_numeric(source.fraction, errors="coerce"), 0.10)
        & source.strategy.isin(cfgs)
    ].copy()
    if early.strategy.duplicated().any():
        raise ValueError("Existing early 10% summaries must have one row per configuration")
    missing_keys = sorted(set(cfgs).difference(early.strategy))
    if missing_keys:
        raise ValueError(f"Existing early 10% summary is missing {len(missing_keys)} configs: {missing_keys[:5]}")
    return {row.strategy: row._asdict() for row in early.itertuples(index=False)}


def _fit_row(strategy: str, fraction: float, early: dict, year: dict, early_source: str) -> dict:
    row = {
        "strategy": strategy,
        "fraction": float(fraction),
        "early_source": early_source,
        "early_trades_funded": early["trades_funded"],
        "early_net_win_rate_pct": early["net_win_rate_pct"],
        "early_net_return_pct": early["net_return_pct"],
        "early_final_equity": early["final_equity"],
        "early_max_dd_intrabar_pct": early["max_dd_intrabar_pct"],
        "early_trades_per_year": early["trades_per_year"],
        "early_annualized_return_pct": early["annualized_return_pct"],
        "early_annualized_return_over_intrabar_dd": early["annualized_return_over_intrabar_dd"],
        "2025_trades_funded": year["trades_funded"],
        "2025_net_win_rate_pct": year["net_win_rate_pct"],
        "2025_net_return_pct": year["net_return_pct"],
        "2025_final_equity": year["final_equity"],
        "2025_max_dd_intrabar_pct": year["max_dd_intrabar_pct"],
        "2025_trades_per_year": year["trades_per_year"],
        "2025_annualized_return_pct": year["annualized_return_pct"],
        "2025_annualized_return_over_intrabar_dd": year["annualized_return_over_intrabar_dd"],
    }
    for objective, metric in OBJECTIVES.items():
        early_metric = early.get(metric, np.nan)
        year_metric = year.get(metric, np.nan)
        vals = [float(v) for v in (early_metric, year_metric) if v is not None and not pd.isna(v)]
        row[f"worst_{objective}_pct" if objective != "trades_per_year" else "worst_trades_per_year"] = min(vals) if vals else np.nan
    row["minimum_annualized_return_pct"] = min(
        _as_float(early.get("annualized_return_pct")),
        _as_float(year.get("annualized_return_pct")),
    )
    early_positive = _as_float(early.get("net_return_pct")) > 0
    year_positive = _as_float(year.get("net_return_pct")) > 0
    early_dd_pass = _as_float(early.get("max_dd_intrabar_pct"), math.inf) <= 15.0
    year_dd_pass = _as_float(year.get("max_dd_intrabar_pct"), math.inf) <= 15.0
    early_frequency_pass = int(early.get("trades_funded", 0)) >= 100
    year_frequency_pass = int(year.get("trades_funded", 0)) >= 30
    reasons = []
    for label, passed in (
        ("early_net_profit", early_positive), ("2025_net_profit", year_positive),
        ("early_intrabar_dd_le_15", early_dd_pass), ("2025_intrabar_dd_le_15", year_dd_pass),
        ("early_funded_trades_ge_100", early_frequency_pass), ("2025_funded_trades_ge_30", year_frequency_pass),
    ):
        if not passed:
            reasons.append(label)
    row.update({
        "early_positive": bool(early_positive),
        "2025_positive": bool(year_positive),
        "early_dd_pass": bool(early_dd_pass),
        "2025_dd_pass": bool(year_dd_pass),
        "early_frequency_pass": bool(early_frequency_pass),
        "2025_frequency_pass": bool(year_frequency_pass),
        "eligible": bool(early_positive and year_positive and early_dd_pass and year_dd_pass
                          and early_frequency_pass and year_frequency_pass),
        # Diagnostic candidates waive profit only; drawdown and frequency gates remain.
        "diagnostic_eligible": bool(early_dd_pass and year_dd_pass and early_frequency_pass and year_frequency_pass),
        "fail_reasons": ";".join(reasons),
    })
    return row


def _assert_base_signal_caches(cfgs: dict) -> None:
    missing = []
    for strategy in cfgs:
        folder = research.CACHE / f"{strategy}__signal__{BASE_SLIPPAGE_BPS}bp"
        if not (folder / "fills.parquet").is_file() or not (folder / "marks.parquet").is_file():
            missing.append(strategy)
    if missing:
        raise FileNotFoundError(
            f"Expected all {len(cfgs)} existing 1bp signal fill/mark caches; "
            f"missing {len(missing)} (first: {missing[:5]}). Refusing to rebuild fills in stability.py."
        )


def _objective_score_column(objective: str) -> str:
    if objective == "trades_per_year":
        return "worst_trades_per_year"
    if objective == "return_over_intrabar_dd":
        return "worst_return_over_intrabar_dd_pct"
    return f"worst_{objective}_pct"


def _rank_rows(rows: pd.DataFrame, objective: str) -> pd.DataFrame:
    metric_col = _objective_score_column(objective)
    ranked = rows.copy()
    ranked[metric_col] = pd.to_numeric(ranked[metric_col], errors="coerce").fillna(-np.inf)
    ranked["minimum_annualized_return_pct"] = pd.to_numeric(
        ranked.minimum_annualized_return_pct, errors="coerce"
    ).fillna(-np.inf)
    return ranked.sort_values(
        [metric_col, "minimum_annualized_return_pct", "strategy", "fraction"],
        ascending=[False, False, True, True],
        kind="mergesort",
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.floating, float)):
        if math.isnan(float(value)):
            return None
        if math.isinf(float(value)):
            return "Infinity" if value > 0 else "-Infinity"
        return float(value)
    if pd.isna(value):
        return None
    return value


def _profile_id(strategy: str, fraction: float) -> str:
    pct = int(round(float(fraction) * 100))
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(strategy)).strip("-")
    return f"stable_{safe}__{pct}pct"


def fit_and_freeze() -> dict:
    if FIT_PATH.exists() or SELECTED_PATH.exists():
        raise FileExistsError("Stability fit/freeze already exists; refusing to silently replace it")
    cfgs = research.configurations()
    if len(cfgs) != 118:
        raise ValueError(f"Expected the frozen 118-config matrix, found {len(cfgs)}")
    _assert_base_signal_caches(cfgs)
    prior_early = _prior_early_10pct(cfgs)
    data = research.Inputs()
    early_metrics: dict[tuple[str, float], dict] = {}
    for i, strategy in enumerate(cfgs):
        old = prior_early[strategy]
        early_metrics[(strategy, 0.10)] = _summary_metrics(
            old, EARLY_START_MS, EARLY_END_MS
        )
        fills, marks = research.independent(data, strategy, cfgs, BASE_SLIPPAGE_BPS, mode="signal")
        early_result, _, _ = _run_account_period(
            fills, marks, period_name="pre_2025", account_period="early",
            start_ms=EARLY_START_MS, end_ms=EARLY_END_MS, fraction=0.05,
        )
        early_metrics[(strategy, 0.05)] = _summary_metrics(
            early_result, EARLY_START_MS, EARLY_END_MS
        )
        print(f"Early 5% {i + 1}/{len(cfgs)} {strategy}: "
              f"{early_result['trades_funded']} funded, {early_result['net_return_pct']:.2f}% net",
              flush=True)

    # Both sizes reset to 1000 U at the exact UTC-2025 boundary. We use the
    # period-filtered fill/path subset before account.run_account for speed.
    year_metrics: dict[tuple[str, float], dict] = {}
    for i, strategy in enumerate(cfgs):
        fills, marks = research.independent(data, strategy, cfgs, BASE_SLIPPAGE_BPS, mode="signal")
        for fraction in FRACTIONS:
            year_result, _, _ = _run_account_period(
                fills, marks, period_name="2025", account_period="late",
                start_ms=YEAR_2025_START_MS, end_ms=YEAR_2025_END_MS, fraction=fraction,
            )
            year_metrics[(strategy, fraction)] = _summary_metrics(
                year_result, YEAR_2025_START_MS, YEAR_2025_END_MS
            )
            print(f"2025 {fraction:.0%} {i + 1}/{len(cfgs)} {strategy}: "
                  f"{year_result['trades_funded']} funded, {year_result['net_return_pct']:.2f}% net",
                  flush=True)

    rows = []
    for strategy in cfgs:
        for fraction in FRACTIONS:
            rows.append(_fit_row(
                strategy,
                fraction,
                early_metrics[(strategy, fraction)],
                year_metrics[(strategy, fraction)],
                "selection_period_all.csv:early:10%" if fraction == 0.10 else "account.run_account:pre_2025:5%",
            ))
    fit = pd.DataFrame(rows, columns=FIT_COLUMNS)
    fit.to_csv(FIT_PATH, index=False)

    eligible = fit.loc[fit.eligible].copy()
    selection: dict[str, Any] = {
        "schema": "stability-selection-v1",
        "frozen_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "selection_uses_2026": False,
        "selection_context": (
            "Secondary stability selection created after the first-round four-winner summary was viewed; "
            "this is not a fully blind test."
        ),
        "periods": {
            "pre_2025": {"start_utc": pd.Timestamp(EARLY_START_MS, unit="ms", tz="UTC").isoformat(),
                          "end_exclusive_utc": pd.Timestamp(EARLY_END_MS, unit="ms", tz="UTC").isoformat()},
            "2025": {"start_utc": pd.Timestamp(YEAR_2025_START_MS, unit="ms", tz="UTC").isoformat(),
                     "end_exclusive_utc": pd.Timestamp(YEAR_2025_END_MS, unit="ms", tz="UTC").isoformat()},
        },
        "account": {"initial_cash_usdt": INITIAL_CASH, "fractions": list(FRACTIONS),
                    "max_slots": MAX_SLOTS, "leverage": 1, "market_slippage_bps": BASE_SLIPPAGE_BPS},
        "eligibility": {
            "both_periods_net_profit_positive": True,
            "both_periods_intrabar_drawdown_pct_at_most": 15.0,
            "pre_2025_minimum_funded_trades": 100,
            "2025_minimum_funded_trades": 30,
        },
        "ranking": {
            "objectives": list(OBJECTIVES),
            "primary": "maximize the lower of pre-2025 and 2025 objective metric",
            "tie_break": "higher minimum annualized return across the two periods, then strategy name, then fraction",
            "annualization": "compound rate using exact period milliseconds divided by 365.25 days",
        },
        "counts": {"configurations": int(len(cfgs)), "profiles_tested": int(len(fit)),
                   "eligible_profiles": int(len(eligible))},
        "objective_winners": {},
        "distinct_alternatives": {},
        "candidate_diagnostics": [],
        "selected_profiles": [],
    }

    if len(eligible):
        winners: dict[str, dict] = {}
        winner_profile_ids: dict[str, str] = {}
        for objective in OBJECTIVES:
            winner = _rank_rows(eligible, objective).iloc[0]
            record = {
                "strategy": str(winner.strategy),
                "fraction": float(winner.fraction),
                "profile_id": _profile_id(winner.strategy, winner.fraction),
                "worst_score": _as_float(winner[_objective_score_column(objective)]),
                "minimum_annualized_return_pct": _as_float(winner.minimum_annualized_return_pct),
                "passed_both_period_gates": True,
            }
            winners[objective] = record
            winner_profile_ids[objective] = record["profile_id"]
        selection["objective_winners"] = winners

        counts: dict[str, int] = {}
        for profile_id in winner_profile_ids.values():
            counts[profile_id] = counts.get(profile_id, 0) + 1
        occupied = set(winner_profile_ids.values())
        alternatives: dict[str, dict] = {}
        for objective in OBJECTIVES:
            profile_id = winner_profile_ids[objective]
            if counts[profile_id] < 2:
                continue
            ranked = _rank_rows(eligible, objective)
            ranked = ranked.loc[
                ~ranked.apply(lambda r: _profile_id(r.strategy, r.fraction) in occupied, axis=1)
            ]
            if ranked.empty:
                alternatives[objective] = {"status": "none_available"}
                continue
            alt = ranked.iloc[0]
            alternative = {
                "strategy": str(alt.strategy),
                "fraction": float(alt.fraction),
                "profile_id": _profile_id(alt.strategy, alt.fraction),
                "worst_score": _as_float(alt[_objective_score_column(objective)]),
                "minimum_annualized_return_pct": _as_float(alt.minimum_annualized_return_pct),
                "passed_both_period_gates": True,
                "alternative_for_duplicate_winner": profile_id,
            }
            alternatives[objective] = alternative
            occupied.add(alternative["profile_id"])
        selection["distinct_alternatives"] = alternatives

        profiles: dict[str, dict] = {}
        for objective, winner in winners.items():
            profile = profiles.setdefault(winner["profile_id"], {
                "profile_id": winner["profile_id"], "strategy": winner["strategy"],
                "fraction": winner["fraction"], "roles": [], "is_alternative": False,
            })
            profile["roles"].append(objective)
        for objective, alt in alternatives.items():
            if "profile_id" not in alt:
                continue
            profiles[alt["profile_id"]] = {
                "profile_id": alt["profile_id"], "strategy": alt["strategy"],
                "fraction": alt["fraction"], "roles": [f"distinct_alternative_for_{objective}"],
                "is_alternative": True,
            }
        selection["selected_profiles"] = list(profiles.values())
        selection["status"] = "eligible_profiles_selected"
    else:
        diagnostic_pool = fit.loc[fit.diagnostic_eligible].copy()
        diagnostics = []
        for objective in OBJECTIVES:
            ranked = _rank_rows(diagnostic_pool, objective) if len(diagnostic_pool) else diagnostic_pool
            if ranked.empty:
                continue
            candidate = ranked.iloc[0]
            diagnostics.append({
                "objective": objective,
                "strategy": str(candidate.strategy),
                "fraction": float(candidate.fraction),
                "profile_id": _profile_id(candidate.strategy, candidate.fraction),
                "worst_score": _as_float(candidate[_objective_score_column(objective)]),
                "minimum_annualized_return_pct": _as_float(candidate.minimum_annualized_return_pct),
                "passed_profit_gate": bool(candidate.early_positive and candidate["2025_positive"]),
                "positive_return_gate_waived_for_diagnostics": True,
                "passed_drawdown_and_frequency_gates": True,
                "status": "candidate_diagnostic_not_selected",
            })
        selection["candidate_diagnostics"] = diagnostics
        selection["status"] = "zero_eligible_profiles" if len(diagnostics) else "zero_eligible_and_no_diagnostics"

    SELECTED_PATH.write_text(json.dumps(_json_value(selection), ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(f"Frozen fit: {len(eligible)} eligible profiles; {len(selection['selected_profiles'])} selected profiles; "
          f"{len(selection['candidate_diagnostics'])} diagnostics", flush=True)
    return selection


def _selected_profiles() -> tuple[dict, list[dict]]:
    if not SELECTED_PATH.is_file():
        raise FileNotFoundError("Run --phase fit before evaluating 2026/full-period outcomes")
    frozen = json.loads(SELECTED_PATH.read_text())
    if not frozen.get("selection_uses_2026") is False:
        raise ValueError("Frozen selection must explicitly state that 2026 was not used")
    if frozen.get("schema") != "stability-selection-v1":
        raise ValueError("Unknown stability selection schema")
    return frozen, frozen.get("selected_profiles", [])


def _write_result_files(profile_id: str, period: str, fills: pd.DataFrame, marks: pd.DataFrame,
                        funded: pd.DataFrame, equity: pd.DataFrame) -> None:
    root = HERE / f"{profile_id}__signal_1bp__{period}"
    fills.to_csv(root.with_name(root.name + "_fills.csv.gz"), index=False, compression="gzip")
    marks.to_csv(root.with_name(root.name + "_mark_path.csv.gz"), index=False, compression="gzip")
    funded.to_csv(root.with_name(root.name + "_funded_trades.csv.gz"), index=False, compression="gzip")
    equity.to_csv(root.with_name(root.name + "_equity.csv.gz"), index=True, compression="gzip")


def evaluate_frozen() -> pd.DataFrame:
    if METRICS_PATH.exists():
        raise FileExistsError("stability_metrics.csv already exists; refusing to replace frozen evaluation")
    frozen, profiles = _selected_profiles()
    if not profiles:
        pd.DataFrame(columns=METRIC_COLUMNS).to_csv(METRICS_PATH, index=False)
        print("No eligible profile was frozen; no full/2026 account replay was run.", flush=True)
        return pd.DataFrame(columns=METRIC_COLUMNS)

    cfgs = research.configurations()
    data = research.Inputs()
    roles_by_id = {p["profile_id"]: ",".join(p["roles"]) for p in profiles}
    scenarios = [
        ("signal", 1),
        ("signal", 5),
        ("matched", 1),
        ("placebo", 1),
    ]
    periods = [
        ("full", "full", EARLY_START_MS, PERIOD_END_MS),
        ("2026", "late", YEAR_2026_START_MS, PERIOD_END_MS),
    ]
    metric_rows = []

    for profile in profiles:
        strategy = profile["strategy"]
        fraction = float(profile["fraction"])
        profile_id = profile["profile_id"]
        if strategy not in cfgs:
            raise ValueError(f"Frozen strategy no longer exists in config: {strategy}")
        signal_1bp, signal_1bp_marks = research.independent(data, strategy, cfgs, 1, mode="signal")
        matched_signal, _ = research.independent(data, strategy, cfgs, 1, mode="matched")
        matched_signal_ids = set(matched_signal.candidate_id)
        for mode, bps in scenarios:
            fills, marks = research.independent(data, strategy, cfgs, bps, mode=mode)
            for period_name, account_period, left, right in periods:
                # The matched signal subset is defined by the signal's actual
                # execution clock. A placebo uses its own earlier control clock;
                # cohort and in-period coverage are reported separately.
                signal_cohort = signal_1bp.loc[
                    signal_1bp.entry_ms.ge(left) & signal_1bp.entry_ms.lt(right)
                    & signal_1bp.exit_ms.ge(left) & signal_1bp.exit_ms.lt(right)
                ]
                signal_ids = set(signal_cohort.candidate_id)
                if mode in ("matched", "placebo"):
                    fills = fills.loc[fills.candidate_id.isin(signal_ids)].copy()
                    marks = marks.loc[marks.candidate_id.isin(fills.candidate_id)].copy()
                period_fills, period_marks = _slice_period(fills, marks, left, right)
                summary, funded, equity = _run_account_period(
                    period_fills, period_marks, period_name=period_name,
                    account_period=account_period, start_ms=left, end_ms=right,
                    fraction=fraction,
                )

                matched_signal_n = int(len(signal_ids & matched_signal_ids))
                match_coverage = (100.0 * matched_signal_n / len(signal_cohort)
                                  if len(signal_cohort) else np.nan)
                if mode == "signal":
                    placebo_n = 0
                    placebo_coverage = np.nan
                    signal_n = int(len(signal_cohort))
                else:
                    signal_n = int(len(signal_cohort))
                    if mode == "placebo":
                        placebo_n = int(period_fills.candidate_id.nunique())
                        placebo_coverage = (100.0 * placebo_n / matched_signal_n
                                            if matched_signal_n else np.nan)
                    else:
                        placebo_n = 0
                        placebo_coverage = np.nan

                ann = summary["annualized_return_pct"]
                dd = summary["max_dd_intrabar_pct"]
                metric_rows.append({
                    "strategy": strategy,
                    "fraction": fraction,
                    "profile_roles": roles_by_id[profile_id],
                    "mode": mode,
                    "market_slippage_bps": bps,
                    "period": period_name,
                    "signal_candidates_in_period": signal_n,
                    "matched_signal_candidates": matched_signal_n,
                    "match_coverage_pct": match_coverage,
                    "placebo_clocks_in_period": placebo_n,
                    "placebo_clock_coverage_pct": placebo_coverage,
                    "trades_funded": summary["trades_funded"],
                    "win_rate_pct": summary["net_win_rate_pct"],
                    "net_return_pct": summary["net_return_pct"],
                    "final_equity": summary["final_equity"],
                    "max_dd_close_pct": summary.get("max_dd_close_pct"),
                    "max_dd_intrabar_pct": dd,
                    "max_gross_exposure_pct": summary.get("max_gross_exposure_pct"),
                    "trade_frequency_per_year": summary["trades_per_year"],
                    "annualized_return_pct": ann,
                    "annualized_return_over_intrabar_dd": _return_over_dd(ann, dd),
                    "max_unrealized_profit_pct": summary.get("max_unrealized_profit_pct"),
                    "max_unrealized_profit_usdt": summary.get("max_unrealized_profit_usdt"),
                    "trades_outside_period": summary.get("trades_outside_period"),
                    "skipped_same_symbol": summary.get("skipped_same_symbol"),
                    "skipped_capacity": summary.get("skipped_capacity"),
                    "initial_cash": INITIAL_CASH,
                    "period_start_ms": left,
                    "period_end_ms": right,
                })
                if mode == "signal" and bps == 1:
                    # Preserve candidate-level actual fills and per-bar OHLC/MFE
                    # paths alongside the funded ledger and shared-account equity.
                    period_signal_fills, period_signal_marks = _slice_period(
                        signal_1bp, signal_1bp_marks, left, right
                    )
                    _write_result_files(
                        profile_id, period_name, period_signal_fills, period_signal_marks,
                        funded, equity,
                    )
            print(f"Evaluated {profile_id} {mode}/{bps}bp", flush=True)

    metrics = pd.DataFrame(metric_rows, columns=METRIC_COLUMNS)
    metrics.to_csv(METRICS_PATH, index=False)
    print(f"Saved frozen-profile metrics for {len(profiles)} profiles; "
          f"selection remains unchanged ({frozen['frozen_at_utc']}).", flush=True)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("fit", "evaluate", "all"), required=True)
    args = parser.parse_args()
    if args.phase in ("fit", "all"):
        fit_and_freeze()
    if args.phase in ("evaluate", "all"):
        evaluate_frozen()


if __name__ == "__main__":
    main()
