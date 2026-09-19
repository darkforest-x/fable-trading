"""Audit and summarize the frozen three-arm V11.2 execution experiment.

Outcomes are reporting labels, never signal inputs. Calendar-month blocks keep
cross-asset dependence together. Native R, original-entry-risk R and equal-
notional returns are reported separately because changing a stop changes the
denominator. The fixed-entry ledger isolates exits from serial occupancy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v112_execution_study import ARMS, EXP, SOURCE, TABLES, validate_receipt
from yoyo.evaluation.spike_v112_support_report import dates, strict_booleans, validate_control_keys
from yoyo.evaluation.spike_v8_six_filters import _committed
from yoyo.evaluation.spike_v9_full_report import block_statistics

SAMPLE = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1/statistics/trade_book/sample_keys.json")
SEED, REPS = 91509, 2000
CONTROL_FIELDS = ("matched", "reason", "control_signal_i", "control_signal_bar_open",
                  "control_net_r", "control_net_return", "control_exit_time", "month", "vol_bin", "fold")


def control_contract(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """An equal return does not imply the same control exit timestamp."""
    joined = old.merge(new, on="trade_key", suffixes=("_old", "_new"), how="outer",
                       validate="one_to_one", indicator=True)
    assert joined._merge.eq("both").all(), "control identities differ"
    differences = {}
    for field in CONTROL_FIELDS:
        a, b = joined[field + "_old"], joined[field + "_new"]
        if field in ("control_signal_bar_open", "control_exit_time"):
            a, b = pd.to_datetime(a, utc=True, format="mixed"), pd.to_datetime(b, utc=True, format="mixed")
            equal = a.eq(b) | (a.isna() & b.isna())
        elif field in ("control_signal_i", "control_net_r", "control_net_return", "vol_bin"):
            equal = np.isclose(a.to_numpy(float), b.to_numpy(float), atol=1e-10, rtol=1e-10, equal_nan=True)
        else:
            equal = a.fillna("<NA>").eq(b.fillna("<NA>"))
        differences[field] = int((~equal).sum())
    assert not any(differences.values()), differences
    return {"rows": len(joined), "differences": differences, "passed": True}


def load(run: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads((run / "manifest.json").read_text())
    identity = json.loads((run / "identity.json").read_text())
    assert manifest["complete"] and manifest["completed"] == manifest["symbols"] == 638 and not manifest["errors"]
    ih = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    assert ih == manifest["run_identity"]
    assert {p.name for p in (run / "streams").iterdir()} == set(identity["inputs"])
    parts, controls, receipts = {name: [] for name in TABLES}, [], []
    for symbol, sha in sorted(identity["inputs"].items()):
        folder = run / "streams" / symbol
        receipt = validate_receipt(folder, ih, sha)
        assert receipt["baseline_parity"]["passed"] and receipt["control_parity"]["passed"]
        assert receipt["candidate_status_parity"]
        assert study.digest(SOURCE / "streams" / symbol / "completion.json") == identity["source_receipts"][symbol]
        receipts.append(study.digest(folder / "completion.json"))
        for name in TABLES:
            table = pd.read_csv(folder / f"{name}.csv.gz")
            if len(table):
                parts[name].append(table)
        old = pd.read_csv(SOURCE / "streams" / symbol / "controls.csv.gz")
        if len(old):
            controls.append(old.loc[old.arm.eq("box_any")])
    tables = {name: pd.concat(frames, ignore_index=True) for name, frames in parts.items()}
    t, c = tables["trades"], tables["controls"]
    validate_control_keys(t, c)
    assert len(t.loc[t.arm.eq("baseline")]) == 9287
    parity = control_contract(pd.concat(controls, ignore_index=True), c.loc[c.arm.eq("baseline")])
    for name in ("trades", "statuses", "fixed"):
        tables[name] = dates(tables[name])
        assert not tables[name].duplicated(["arm", "trade_key"]).any()
        for column in ("entry_time", "exit_time"):
            if column in tables[name]:
                tables[name][column] = pd.to_datetime(tables[name][column], utc=True, format="mixed")
    t, c = tables["trades"], tables["controls"]
    t["censored"] = strict_booleans(t.censored)
    c["matched"] = strict_booleans(c.matched)
    c["control_exit_time"] = pd.to_datetime(c.control_exit_time, utc=True, format="mixed")
    tables["trades"] = t.merge(c.drop(columns="month"), on=["arm", "trade_key"], validate="one_to_one")
    for name in ("trades", "fixed"):
        tables[name]["net_bp"] = tables[name].net_return * 1e4
        tables[name]["gross_bp"] = tables[name].gross_return * 1e4
    return tables, manifest, {"baseline_control_contract": parity, "receipt_sha256": receipts}


def period_rows(t: pd.DataFrame, period: str) -> pd.DataFrame:
    """Early trades crossing the split are not early evaluation outcomes."""
    closed = t.loc[t.status.eq("closed")]
    if period == "earlier":
        return closed.loc[closed.signal_close.lt(study.SPLIT) & closed.exit_time.lt(study.SPLIT)]
    if period == "later":
        return closed.loc[closed.signal_close.ge(study.SPLIT)]
    assert period == "full"
    return closed


def metrics(t: pd.DataFrame, period: str) -> dict:
    p = period_rows(t, period)
    matched = p.matched.copy()
    if period == "earlier":
        matched &= p.control_exit_time.lt(study.SPLIT)
    q = p.loc[matched]
    delta_r, delta_bp = q.net_r - q.control_net_r, (q.net_return - q.control_net_return) * 1e4
    delta_original_r = q.net_r_on_baseline_risk - q.control_net_r_on_baseline_risk
    rstat, bpstat = block_statistics(delta_r, q.month), block_statistics(delta_bp, q.month)
    winners, losers = p.net_r.clip(lower=0).sum(), -p.net_r.clip(upper=0).sum()
    return {"closed": len(p), "wins": int(p.net_r.gt(0).sum()), "win_rate": p.net_r.gt(0).mean(),
            "mean_net_r": p.net_r.mean(), "mean_gross_r": p.gross_r.mean(),
            "mean_net_original_r": p.net_r_on_baseline_risk.mean(), "mean_net_bp": p.net_bp.mean(),
            "mean_gross_bp": p.gross_bp.mean(), "sum_net_r": p.net_r.sum(), "sum_net_bp": p.net_bp.sum(),
            "profit_factor_r": winners / losers if losers else np.nan,
            "median_initial_risk_pct": p.initial_risk_frac.median() * 100,
            "mean_hold_hours": ((p.exit_time - p.entry_time).dt.total_seconds() / 3600).mean(),
            "cross_split_closed": int((t.status.eq("closed") & t.signal_close.lt(study.SPLIT) & t.exit_time.ge(study.SPLIT)).sum()),
            "censored_total": int(t.censored.sum()), "matched": len(q), "coverage": len(q) / len(p) if len(p) else 0.,
            "paired_actual_r": q.net_r.mean(), "random_r": q.control_net_r.mean(),
            "paired_actual_bp": q.net_bp.mean(), "random_bp": q.control_net_return.mean() * 1e4,
            "excess_r": delta_r.mean(), "excess_bp": delta_bp.mean(),
            "random_original_r": q.control_net_r_on_baseline_risk.mean(), "excess_original_r": delta_original_r.mean(),
            **{f"r_{k}": v for k, v in rstat.items()}, **{f"bp_{k}": v for k, v in bpstat.items()}}


def mean_difference(a: pd.DataFrame, b: pd.DataFrame, column: str) -> dict:
    """Candidate minus baseline; bootstrap identical UTC months in both arms."""
    aa = a.groupby("month")[column].agg(["sum", "count"])
    bb = b.groupby("month")[column].agg(["sum", "count"])
    months = sorted(set(aa.index) | set(bb.index))
    aa, bb = aa.reindex(months, fill_value=0), bb.reindex(months, fill_value=0)
    draws = np.random.default_rng(SEED).integers(0, len(months), (REPS, len(months)))
    an, bn = aa["count"].to_numpy()[draws].sum(axis=1), bb["count"].to_numpy()[draws].sum(axis=1)
    valid = (an > 0) & (bn > 0)
    boot = bb["sum"].to_numpy()[draws].sum(axis=1)[valid] / bn[valid] - aa["sum"].to_numpy()[draws].sum(axis=1)[valid] / an[valid]
    low, high = np.quantile(boot, [.025, .975])
    return {"metric": column, "difference": b[column].mean() - a[column].mean(), "ci_low": low,
            "ci_high": high, "months": len(months), "valid_reps": int(valid.sum())}


def attribution(t: pd.DataFrame, fixed: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows, tails = [], []
    for tf in ("15m", "1h"):
        a = period_rows(t.loc[t.timeframe.eq(tf) & t.arm.eq("baseline")], "full")
        for arm in ARMS[1:]:
            b = period_rows(t.loc[t.timeframe.eq(tf) & t.arm.eq(arm)], "full")
            j = a.merge(b, on="trade_key", how="outer", suffixes=("_a", "_b"), indicator=True)
            both, lost, new = j._merge.eq("both"), j._merge.eq("left_only"), j._merge.eq("right_only")
            delta = (j.loc[both, "net_bp_b"] - j.loc[both, "net_bp_a"]).sum() - j.loc[lost, "net_bp_a"].sum() + j.loc[new, "net_bp_b"].sum()
            assert np.isclose(delta, b.net_bp.sum() - a.net_bp.sum())
            rows.append({"timeframe": tf, "arm": arm, "scope": "independent_serial", "baseline_n": len(a), "candidate_n": len(b),
                         "common": int(both.sum()), "removed": int(lost.sum()), "added": int(new.sum()),
                         "common_delta_sum_bp": (j.loc[both, "net_bp_b"] - j.loc[both, "net_bp_a"]).sum(),
                         "removed_baseline_sum_bp": j.loc[lost, "net_bp_a"].sum(), "added_sum_bp": j.loc[new, "net_bp_b"].sum(),
                         "rescued_loss_to_win": int((both & j.net_r_a.lt(0) & j.net_r_b.gt(0)).sum()),
                         "win_to_loss": int((both & j.net_r_a.gt(0) & j.net_r_b.lt(0)).sum()),
                         "winners_with_less_bp": int((both & j.net_r_a.gt(0) & j.net_bp_b.lt(j.net_bp_a - 1e-8)).sum()),
                         "total_delta_sum_bp": delta})
            f = fixed.loc[fixed.timeframe.eq(tf) & fixed.arm.eq(arm)].copy()
            no_trade = f.status.isin(["rejected_age6", "parent_stop_invalid", "parent_index_invalid"])
            f.loc[no_trade, ["net_bp", "net_r_on_baseline_risk"]] = 0.
            good = f.status.eq("closed") | no_trade
            j = a.merge(f.loc[good], on="trade_key", suffixes=("_a", "_b"), validate="one_to_one")
            rows.append({"timeframe": tf, "arm": arm, "scope": "fixed_original_entries", "baseline_n": len(a),
                         "comparable": len(j), "incomparable": len(a) - len(j), "no_trade": int(j.status_b.ne("closed").sum()),
                         "mean_delta_bp": (j.net_bp_b - j.net_bp_a).mean(),
                         "mean_delta_original_r": (j.net_r_on_baseline_risk_b - j.net_r_a).mean(),
                         "rescued_loss_to_win": int((j.net_r_a.lt(0) & j.net_bp_b.gt(0)).sum()),
                         "win_to_loss": int((j.net_r_a.gt(0) & j.net_bp_b.lt(0)).sum()),
                         "winners_with_less_bp": int((j.net_r_a.gt(0) & j.net_bp_b.lt(j.net_bp_a - 1e-8)).sum())})
            big = a.loc[a.net_r.ge(10)].merge(b, on="trade_key", suffixes=("_a", "_b"), how="left", validate="one_to_one")
            tails.append({"timeframe": tf, "arm": arm, "baseline_ge10r": len(big), "retained_closed_entries": int(big.net_r_b.notna().sum()),
                          "still_ge10_native_r": int(big.net_r_b.ge(10).sum()),
                          "still_ge10_original_r": int(big.net_r_on_baseline_risk_b.ge(10).sum()),
                          "unchanged_or_better_price_return": int(big.net_bp_b.ge(big.net_bp_a - 1e-8).sum()),
                          "baseline_sum_bp": big.net_bp_a.sum(), "candidate_sum_bp_missing_as_zero": big.net_bp_b.fillna(0).sum()})
    return pd.DataFrame(rows), pd.DataFrame(tails)


def main(run: Path, out: Path) -> None:
    assert _committed((Path(__file__), Path("tests/evaluation/test_spike_v112_execution_report.py")))
    config = json.loads((EXP / "config.json").read_text())
    assert config["bootstrap_seed"] == SEED and config["bootstrap_reps"] == REPS
    tables, manifest, audit = load(run)
    t, s, f = tables["trades"], tables["statuses"], tables["fixed"]
    rows, differences, gates = [], [], []
    for tf in ("15m", "1h"):
        for arm in ARMS:
            for period in ("full", "earlier", "later"):
                part = t.loc[t.timeframe.eq(tf) & t.arm.eq(arm)]
                rows.append({"timeframe": tf, "arm": arm, "period": period, **metrics(part, period)})
                if arm != "baseline":
                    a = period_rows(t.loc[t.timeframe.eq(tf) & t.arm.eq("baseline")], period)
                    b = period_rows(part, period)
                    for column in ("net_r", "net_r_on_baseline_risk", "net_bp"):
                        differences.append({"timeframe": tf, "arm": arm, "period": period, **mean_difference(a, b, column)})
    summary = pd.DataFrame(rows)
    for tf in ("15m", "1h"):
        indexed = summary.loc[summary.timeframe.eq(tf)].set_index(["arm", "period"])
        for arm in ARMS[1:]:
            full, early, late = (indexed.loc[(arm, period)] for period in ("full", "earlier", "later"))
            checks = {"full_profitable_r_and_bp": full.mean_net_r > 0 and full.mean_net_bp > 0,
                      "later_profitable_r_and_bp": late.mean_net_r > 0 and late.mean_net_bp > 0,
                      "earlier_better_bp": early.mean_net_bp > indexed.loc[("baseline", "earlier"), "mean_net_bp"],
                      "later_better_bp": late.mean_net_bp > indexed.loc[("baseline", "later"), "mean_net_bp"],
                      "positive_matched_excess_r_and_bp": full.excess_r > 0 and full.excess_bp > 0,
                      "multiplicity_p_pass": full.r_p_month_signflip * 6 < .01, "coverage_pass": full.coverage >= .9}
            gates.append({"timeframe": tf, "arm": arm, **checks, "adjusted_p": min(1., full.r_p_month_signflip * 6), "passed": all(checks.values())})
    effects, tails = attribution(t, f)
    keys = json.loads(SAMPLE.read_text())["sample"]
    sample = f.loc[f.trade_key.isin(keys)].copy()
    sample["figure"] = sample.trade_key.map(keys)
    assert len(sample) == 200 and sample.groupby("arm").size().eq(50).all()
    actual = t[["trade_key", "arm", "status"]].rename(columns={"status": "serial_status"})
    sample = sample.merge(actual, on=["trade_key", "arm"], how="left", validate="one_to_one")
    sample["serial_taken"] = sample.serial_status.notna()
    out.mkdir(parents=True, exist_ok=True)
    outputs = {"summary": summary, "mean_differences": pd.DataFrame(differences), "gates": pd.DataFrame(gates),
               "attribution": effects, "tail_retention": tails, "sample50": sample.sort_values(["figure", "arm"]),
               "status_counts": s.groupby(["timeframe", "arm", "status"]).size().rename("n").reset_index(),
               "control_reasons": t.groupby(["timeframe", "arm", "matched", "reason"]).size().rename("n").reset_index()}
    for name, table in outputs.items():
        table.to_csv(out / f"{name}.csv", index=False)
    for name in ("trades", "fixed"):
        tables[name].to_csv(out / f"{name}.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    audit.update({"run_manifest": manifest, "report_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  "report_builder_sha256": study.digest(Path(__file__)), "sample_keys_sha256": study.digest(SAMPLE),
                  "files": {p.name: study.digest(p) for p in sorted(out.iterdir()) if p.is_file() and p.name != "audit.json"}})
    (out / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(summary[["timeframe", "arm", "period", "closed", "mean_net_r", "mean_net_original_r", "mean_net_bp", "excess_r", "r_p_month_signflip", "coverage"]].to_string(index=False))
    print(pd.DataFrame(gates).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=EXP / "results/run_v2")
    parser.add_argument("--output", type=Path, default=EXP / "statistics/run_v2")
    args = parser.parse_args()
    main(args.run, args.output)
