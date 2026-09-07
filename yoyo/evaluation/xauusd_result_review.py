"""Independently reconcile the frozen XAUUSD result artifacts, without quotes.

Reads existing nomination/source receipts, account ledgers, event controls and
daily equity CSVs. Recomputes the declared ordering, fees, compounding, summary
statistics, clock bounds and Holm adjustment; it does not generate new policy
returns, rerank confirmation, read minute quotes, or run a carry diagnostic.
Minute-close drawdown is NOT reconstructed here: daily equity supplies only a
necessary lower bound. The dedicated minute_mdd_audit.json owns that evidence.

The CLI requires this reviewer itself to be committed and clean before writing
results/result_review.json. Input hashes bind the review to the bytes consumed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS = ROOT / "experiments/active/exp-xauusd-system-search-20260908-v1/results"
REVIEWER_PATH = Path(__file__).resolve().relative_to(ROOT).as_posix()


class Review:
    """Collect bounded failure summaries while auditing exact input bytes."""

    def __init__(self, results: Path):
        self.results = results.resolve()
        self.inputs: dict[str, dict[str, Any]] = {}
        self.failures: Counter[str] = Counter()
        self.checks = 0
        self.ledger_files = 0
        self.trade_rows = 0
        self.bankruptcy_rows = 0
        self.control_files = 0
        self.control_pair_rows = 0
        self.matched_actual_trades = 0
        self.max_equity_math_abs_error = 0.0
        self.daily_drawdown_bounds: list[dict[str, Any]] = []

    def check(self, condition: Any, message: str) -> None:
        self.checks += 1
        if not bool(condition):
            self.failures[message] += 1

    def close(self, actual: Any, expected: Any, message: str, atol: float = 1e-7) -> None:
        self.check(np.allclose(np.asarray(actual, dtype=float), np.asarray(expected, dtype=float),
                               rtol=1e-8, atol=atol, equal_nan=True), message)

    def read_bytes(self, path: Path) -> bytes:
        payload = path.read_bytes()
        try:
            key = path.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            key = str(path.resolve())
        evidence = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
        if key in self.inputs:
            self.check(self.inputs[key] == evidence, f"input_changed_during_review:{key}")
        self.inputs[key] = evidence
        return payload

    def read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(self.read_bytes(path))

    def read_csv(self, path: Path, **kwargs: Any) -> pd.DataFrame:
        return pd.read_csv(io.BytesIO(self.read_bytes(path)),
                           compression="gzip" if path.suffix == ".gz" else None, **kwargs)

    def audit_ledger(
        self, stage: str, row: Any, path: Path, curve: pd.Series, config: dict[str, Any],
    ) -> None:
        """Rebuild capital from prices and side, not reported equity transitions."""
        table = self.read_csv(path)
        self.ledger_files += 1
        self.trade_rows += len(table)
        prefix = path.name
        self.check(len(table) == int(row.trades), prefix + ":trade_count")
        start, end = [pd.Timestamp(x, tz="UTC") for x in config[stage]]
        equity = 1.0
        changes, gross_returns, net_returns = [], [], []
        prior_exit = None
        exposure = 0.0
        for trade in table.itertuples(index=False):
            decision = pd.Timestamp(trade.decision_time)
            entry, exit_time = pd.Timestamp(trade.entry_time), pd.Timestamp(trade.exit_time)
            self.check(start <= decision < end and decision <= entry < end
                       and entry <= exit_time <= end, prefix + ":period_and_decision_bounds")
            self.check(pd.Timestamp(trade.decision_bar_open) < decision, prefix + ":bar_close_after_open")
            self.check(prior_exit is None or entry >= prior_exit, prefix + ":single_position")
            self.check(int(trade.entry_index) <= int(trade.exit_index), prefix + ":minute_index_order")
            self.check(trade.side in (-1, 1), prefix + ":side")
            self.check(np.isfinite([trade.entry_price, trade.exit_price]).all()
                       and trade.entry_price > 0 and trade.exit_price > 0, prefix + ":finite_positive_prices")
            if trade.exit_reason == "signal":
                self.check(pd.notna(trade.exit_decision_time)
                           and decision <= pd.Timestamp(trade.exit_decision_time) <= exit_time,
                           prefix + ":signal_exit_clock")
            self.check(equity > 0, prefix + ":no_trade_after_bankruptcy")
            notional = equity / 1.001
            fee = notional * 0.001
            gross = trade.side * notional * (trade.exit_price / trade.entry_price - 1)
            uncapped = equity + gross - 2 * fee
            after = max(0.0, uncapped)
            self.close(
                [trade.equity_before, trade.notional, trade.entry_fee, trade.exit_fee,
                 trade.cash_after_entry_fee, trade.gross_pnl, trade.net_pnl,
                 trade.equity_after, trade.uncapped_equity_after, trade.bankruptcy_floor_adjustment],
                [equity, notional, fee, fee, equity - fee, gross, after - equity,
                 after, uncapped, after - uncapped], prefix + ":capital_and_fees",
            )
            self.close(trade.units, trade.side * notional / trade.entry_price,
                       prefix + ":signed_units", atol=1e-12)
            gross_bp, net_bp = gross / notional * 10000, (after - equity) / notional * 10000
            self.close([trade.gross_bp, trade.net_bp, trade.raw_net_bp],
                       [gross_bp, net_bp, gross_bp - 20], prefix + ":trade_basis_points")
            duration = (exit_time - entry).total_seconds()
            self.close(trade.holding_seconds, duration, prefix + ":holding_duration")
            exposure += duration
            changes.append(after - equity)
            gross_returns.append(gross_bp)
            net_returns.append(net_bp)
            self.max_equity_math_abs_error = max(self.max_equity_math_abs_error,
                                                 abs(trade.equity_after - after))
            equity, prior_exit = after, exit_time
            self.bankruptcy_rows += int(after <= 0)
        self.close(row.return_pct, (equity - 1) * 100, prefix + ":account_return")
        self.close(row.final_equity, equity, prefix + ":final_equity")
        self.close(row.exposure_time, exposure, prefix + ":exposure_seconds")
        self.close(row.exposure_fraction, exposure / (end - start).total_seconds(),
                   prefix + ":exposure_fraction")
        self.check(bool(row.bankrupt) == (equity <= 0), prefix + ":bankruptcy_flag")
        self.close(row.cost_bp, 20, prefix + ":declared_cost")
        if changes:
            values = np.asarray(changes)
            winrate = np.mean(values > 0)
            gains, losses = values[values > 0].sum(), -values[values < 0].sum()
            factor = gains / losses if losses > 0 else np.inf if gains > 0 else np.nan
            self.close([row.winrate, row.winrate_pct, row.PF,
                        row.gross_trade_mean_bp, row.net_trade_mean_bp],
                       [winrate, winrate * 100, factor, np.mean(gross_returns), np.mean(net_returns)],
                       prefix + ":aggregate_statistics")
        else:
            self.check(pd.isna(row.winrate) and pd.isna(row.PF), prefix + ":empty_metrics")
        curve = curve.dropna()
        expected_dates = pd.date_range(start.normalize(), (end - pd.Timedelta(nanoseconds=1)).normalize(), freq="D")
        self.check(curve.index.equals(expected_dates), prefix + ":daily_calendar")
        self.close(curve.iloc[-1], equity, prefix + ":daily_terminal_equity")
        marks = np.r_[1.0, curve.to_numpy(float)]
        self.check(np.isfinite(marks).all() and (marks >= 0).all(), prefix + ":daily_marks")
        daily_dd = float(np.max(1 - marks / np.maximum.accumulate(marks)) * 100)
        self.check(row.max_drawdown_pct + 1e-7 >= daily_dd
                   and row.max_drawdown_pct <= 100 + 1e-7, prefix + ":daily_drawdown_lower_bound")
        self.daily_drawdown_bounds.append({"ledger": prefix, "daily_drawdown_pct": daily_dd,
                                          "reported_minute_drawdown_pct": float(row.max_drawdown_pct)})

    def audit_controls(self, stage: str, row: Any, config: dict[str, Any]) -> None:
        """Check stored pair identity, price arithmetic and aggregation only."""
        key = f"{row.candidate}_{int(row.timeframe)}m"
        suffix = ".csv.gz" if stage == "selection" else ".csv"
        pairs = self.read_csv(self.results / f"{stage}_{key}_controls{suffix}")
        trades = self.read_csv(self.results / f"{stage}_{key}_trades{suffix}")
        self.control_files += 1
        self.control_pair_rows += len(pairs)
        self.check(len(pairs) == int(row.controls_n), key + ":controls_count")
        self.check(int(row.matched_n) + int(row.unmatched_n) == len(trades), key + ":matched_accounting")
        if pairs.empty:
            self.check(row.matched_n == 0, key + ":empty_matching")
            return
        matched = pairs.actual_trade_id.nunique()
        self.matched_actual_trades += matched
        self.check(matched == row.matched_n and pairs.groupby("actual_trade_id").size().eq(3).all(),
                   key + ":three_controls_per_actual")
        self.check(not pairs[["control_decision_index", "side"]].duplicated().any(), key + ":no_bar_side_reuse")
        actual = trades.set_index("trade_id").loc[pairs.actual_trade_id]
        for pair_column, trade_column in [("actual_gross_bp", "gross_bp"), ("actual_net_bp", "net_bp"),
                                          ("side", "side"), ("actual_decision_index", "decision_index")]:
            self.close(pairs[pair_column].to_numpy(), actual[trade_column].to_numpy(), key + ":" + pair_column)
        actual_time = pd.to_datetime(pairs.actual_decision_time, utc=True)
        control_time = pd.to_datetime(pairs.control_decision_time, utc=True)
        entry_time = pd.to_datetime(pairs.control_entry_time, utc=True)
        exit_time = pd.to_datetime(pairs.control_exit_time, utc=True)
        start, end = [pd.Timestamp(x, tz="UTC") for x in config[stage]]
        self.check((actual_time.dt.strftime("%Y-%m") == control_time.dt.strftime("%Y-%m")).all(),
                   key + ":same_utc_decision_month")
        self.check((actual_time.dt.strftime("%Y-%m") == pairs.month).all(), key + ":recorded_month")
        self.check(((control_time >= start) & (control_time < end) & (entry_time >= control_time)
                    & (entry_time < end) & (exit_time >= entry_time) & (exit_time <= end)).all(),
                   key + ":control_clocks_and_window")
        raw = pairs.side * (pairs.control_exit_price / pairs.control_entry_price - 1) * 10000
        self.close(pairs.control_gross_bp, raw, key + ":control_gross_price_arithmetic", atol=1e-6)
        self.close(pairs.control_net_bp, raw - 20, key + ":control_fee_arithmetic", atol=1e-6)
        self.close(pairs.excess_bp, pairs.actual_net_bp - pairs.control_net_bp, key + ":net_excess", atol=1e-6)
        self.close(pairs.gross_excess_bp, pairs.actual_gross_bp - pairs.control_gross_bp,
                   key + ":gross_excess", atol=1e-6)
        self.close(row.control_mean_net_bp, pairs.control_net_bp.mean(), key + ":control_mean", atol=1e-6)
        self.close(row.excess_bp, pairs.excess_bp.mean(), key + ":trade_weighted_excess", atol=1e-6)
        cases = pairs.groupby("actual_trade_id", sort=False).agg(month=("month", "first"), excess=("excess_bp", "mean"))
        monthly = cases.groupby("month").excess.mean()
        self.close(row.monthly_mean_excess_bp, monthly.mean(), key + ":equal_month_excess", atol=1e-6)
        self.check(len(monthly) == int(row.months), key + ":inference_month_count")

    def run(self) -> dict[str, Any]:
        """Audit the original frozen folds; no new strategy or quote evaluation."""
        out = self.results
        freeze = self.read_json(out / "source_freeze.json")
        nomination = self.read_json(out / "nomination.json")
        completion = self.read_json(out / "completion.json")
        config = self.read_json(out.parent / "preregistration.json")
        source_checks = {}
        for name, expected in freeze["files"].items():
            committed = subprocess.check_output(["git", "show", freeze["commit"] + ":" + name], cwd=ROOT)
            committed_hash = hashlib.sha256(committed).hexdigest()
            current_hash = hashlib.sha256(self.read_bytes(ROOT / name)).hexdigest()
            source_checks[name] = {"commit_match": committed_hash == expected, "current_match": current_hash == expected}
            self.check(committed_hash == expected and current_hash == expected, "source_hash:" + name)
        self.check(len(source_checks) == 7, "seven_frozen_sources")
        self.check(nomination["source_freeze"] == freeze and completion["source"] == freeze, "embedded_freeze_receipts")
        selection = self.read_csv(out / "selection_all.csv")
        final = self.read_csv(out / "final_summary.csv")
        self.check(hashlib.sha256(self.read_bytes(out / "selection_all.csv")).hexdigest()
                   == nomination["selection_table_sha256"], "selection_nomination_hash")
        self.check(hashlib.sha256(self.read_bytes(out / "nomination.json")).hexdigest()
                   == completion["nomination_sha256"], "completion_nomination_hash")
        expected_family = {(name, int(tf)) for name in config["candidates"] for tf in config["timeframes"]}
        self.check(len(selection) == 189 and not selection[["candidate", "timeframe"]].duplicated().any()
                   and set(zip(selection.candidate, selection.timeframe)) == expected_family, "complete_21_by_9_family")
        columns = ["return_pct", "max_drawdown_pct", "trades", "candidate", "timeframe"]
        ordered = selection.sort_values(columns, ascending=[False, True, True, True, True]).reset_index(drop=True)
        self.check(selection[["candidate", "timeframe"]].reset_index(drop=True).equals(ordered[["candidate", "timeframe"]]),
                   "predeclared_selection_order")
        winner = selection.iloc[0]
        self.check((winner.candidate, int(winner.timeframe)) == (nomination["candidate"], nomination["timeframe"]), "active_nominee")
        family_winner = selection.loc[~selection.candidate.str.startswith(("T", "R"))].iloc[0]
        self.check((family_winner.candidate, int(family_winner.timeframe))
                   == (nomination["best_imacd"]["candidate"], nomination["best_imacd"]["timeframe"]), "imacd_nominee")
        expected_confirmation = {(nomination["candidate"], nomination["timeframe"]),
                                 (nomination["best_imacd"]["candidate"], nomination["best_imacd"]["timeframe"]), ("C02", 240)}
        active_confirmation = final[(final.stage == "confirmation") & ~final.candidate.isin(["BUY_HOLD", "CASH"])]
        self.check(set(zip(active_confirmation.candidate, active_confirmation.timeframe.astype(int))) == expected_confirmation,
                   "confirmation_contains_only_frozen_candidates")
        self.check(set(map(tuple, completion["confirmation_configurations"])) == expected_confirmation, "completion_candidate_list")
        expected_final = {("confirmation", name, tf) for name, tf in expected_confirmation}
        expected_final.add(("development", nomination["candidate"], nomination["timeframe"]))
        expected_final.update((stage, name, 0) for stage in ("selection", "confirmation", "development") for name in ("BUY_HOLD", "CASH"))
        self.check(not final[["stage", "candidate", "timeframe"]].duplicated().any()
                   and set(zip(final.stage, final.candidate, final.timeframe.astype(int))) == expected_final, "original_final_scope")
        passive = final[(final.stage == "selection") & final.candidate.isin(["BUY_HOLD", "CASH"])]
        overall = pd.concat([selection.iloc[:1], passive], ignore_index=True).sort_values(columns, ascending=[False, True, True, True, True]).iloc[0]
        self.check((overall.candidate, int(overall.timeframe))
                   == (nomination["best_including_passive"]["candidate"], nomination["best_including_passive"]["timeframe"]),
                   "overall_nominee_including_passive")
        # Reimplement Holm independently, including unavailable tests in N=189.
        pvalues = selection.paired_p.to_numpy(float)
        finite = np.flatnonzero(np.isfinite(pvalues))
        self.check(not np.isinf(pvalues).any() and ((pvalues[finite] >= 0) & (pvalues[finite] <= 1)).all(), "pvalue_range")
        order = finite[np.argsort(pvalues[finite], kind="stable")]
        adjusted = np.full(len(pvalues), np.nan)
        running = 0.0
        for rank, idx in enumerate(order):
            running = max(running, (189 - rank) * pvalues[idx])
            adjusted[idx] = min(1.0, running)
        self.close(adjusted, selection.holm_p, "holm_full_family")
        selection_daily = self.read_csv(out / "selection_daily_equity.csv.gz", index_col=0, parse_dates=True)
        final_daily = self.read_csv(out / "final_daily_equity.csv.gz", index_col=0, parse_dates=True)
        self.check(len(selection_daily.columns) == 189, "selection_daily_column_count")
        for row in selection.itertuples(index=False):
            key = f"{row.candidate}_{int(row.timeframe)}m"
            self.audit_ledger("selection", row, out / f"selection_{key}_trades.csv.gz", selection_daily[key], config)
            self.audit_controls("selection", row, config)
        for row in final.itertuples(index=False):
            if row.candidate == "CASH":
                self.close([row.return_pct, row.max_drawdown_pct, row.trades], [0, 0, 0], "cash_comparator")
                continue
            if row.candidate == "BUY_HOLD":
                key, path = f"{row.stage}_BUY_HOLD", out / f"{row.stage}_buy_hold.csv"
            else:
                key = f"{row.stage}_{row.candidate}_{int(row.timeframe)}m"
                path = out / f"{key}_trades.csv"
                self.audit_controls(row.stage, row, config)
            self.audit_ledger(row.stage, row, path, final_daily[key], config)
        confirm_paths = [out / f"confirmation_{name}_{tf}m_trades.csv" for name, tf in expected_confirmation]
        timeline = {"selection_mtime_ns": (out / "selection_all.csv").stat().st_mtime_ns,
                    "nomination_mtime_ns": (out / "nomination.json").stat().st_mtime_ns,
                    "first_confirmation_mtime_ns": min(path.stat().st_mtime_ns for path in confirm_paths),
                    "completion_mtime_ns": (out / "completion.json").stat().st_mtime_ns,
                    "nomination_created_at": nomination["created_at"], "completed_at": completion["completed_at"]}
        self.check(timeline["selection_mtime_ns"] <= timeline["nomination_mtime_ns"] <= timeline["first_confirmation_mtime_ns"]
                   <= timeline["completion_mtime_ns"], "artifact_chronology")
        self.check(pd.Timestamp(nomination["created_at"]) < pd.Timestamp(completion["completed_at"]), "recorded_nomination_before_completion")
        self.check(nomination["confirmation_economic_runs_before_nomination"] == 0, "confirmation_counter")
        self.check(self.ledger_files == len(selection) + len(final[final.candidate != "CASH"]), "ledger_file_count")
        self.check(self.control_files == len(selection) + len(final[~final.candidate.isin(["BUY_HOLD", "CASH"])]), "control_file_count")
        ties = selection.loc[np.isclose(selection.return_pct, selection.return_pct.max(), rtol=0, atol=1e-10),
                             ["candidate", "timeframe", "trades"]]
        result = {
            "status": "passed" if not self.failures else "failed", "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": freeze["commit"], "source_checks": source_checks, "checks_executed": self.checks,
            "failures": dict(self.failures), "input_artifacts": self.inputs, "selection_count": len(selection),
            "ledger_files": self.ledger_files, "trade_rows": self.trade_rows, "bankruptcy_rows": self.bankruptcy_rows,
            "max_equity_math_abs_error": self.max_equity_math_abs_error,
            "reported_runner_ledger_checks": int(selection.ledger_checks.sum() + final.ledger_checks.fillna(0).sum()),
            "control_files": self.control_files, "control_pair_rows": self.control_pair_rows,
            "matched_actual_trades": self.matched_actual_trades, "holm_finite_tests": len(finite),
            "holm_p_below_001": int((selection.holm_p < 0.01).sum()), "chronology": timeline,
            "periods": {stage: config[stage] for stage in ("selection", "confirmation", "development")},
            "selection_return_ties": ties.to_dict("records"), "daily_drawdown_bounds": self.daily_drawdown_bounds,
            "quote_files_read": 0, "minute_drawdown_recomputed": False, "new_policy_evaluations": 0,
            "carry_diagnostic_included": False, "training_eligible": False, "production_eligible": False,
            "limitations": ["Quote prices are reconciled within stored ledgers; source minute quotes are not reread.",
                            "Daily drawdown is a lower bound, not an independent minute-close MDD reconstruction.",
                            "Stored volbin identity is not recalculated from raw ATR; this audit checks month, side and pair accounting.",
                            "Artifact chronology and frozen code support ordering; this is not proof that no prior visual exposure occurred.",
                            "A zero-trade reset confirmation window says nothing about continuing an earlier open position."],
        }
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args()
    # Freeze reviewer code before producing a formal receipt, like the engine.
    subprocess.run(["git", "ls-files", "--error-unmatch", REVIEWER_PATH], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", REVIEWER_PATH], cwd=ROOT, check=True)
    reviewer_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    review = Review(args.results)
    try:
        result = review.run()
    except Exception as exc:
        result = {"status": "failed", "generated_at": datetime.now(timezone.utc).isoformat(),
                  "fatal_error": {"type": type(exc).__name__, "message": str(exc)},
                  "failures": dict(review.failures), "input_artifacts": review.inputs}
    result["reviewer"] = {"path": REVIEWER_PATH, "commit": reviewer_commit,
                          "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    target = args.results / "result_review.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "output": str(target),
                      "failures": result.get("failures"), "fatal_error": result.get("fatal_error"),
                      "ledger_files": result.get("ledger_files"), "trade_rows": result.get("trade_rows")}, ensure_ascii=False))
    if result["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
