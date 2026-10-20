"""Predeclared robustness and complete selected-path audit for PEPE exits.

Consumes the frozen grid without expanding it. Reports single-factor neighbors,
fixed-stop 10R/20R comparisons, overlapping exposure and winner concentration.
All results are independent trade-risk units, never account equity.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.pepe_range_data import EXP
from yoyo.evaluation.pepe_range_exits import load_context, scalar, metrics, pairs, inference


def main():
    out = EXP/"results"
    cfg = json.loads((EXP/"config.json").read_text())
    choice = json.loads((out/"selection.json").read_text())
    summary = pd.read_csv(out/"summary.csv")
    chosen = list(dict.fromkeys([cfg["baseline"], choice["selected_on_development"],
                                choice["full_history_descriptive_best"],
                                "w5_b0.05_tp5_cap0", "w5_b0.05_tp10_cap0", "w5_b0.05_tp20_cap0"]))
    paths = [x.rsplit("_cap", 1)[0] for x in chosen]
    keep = []
    for chunk in pd.read_csv(out/"paths.csv.gz", chunksize=100000, float_precision="round_trip"):
        keep.append(chunk[chunk.path.isin(paths)])
    ledger = pd.concat(keep, ignore_index=True)
    ctx = load_context()
    count, detail, audits, tables = 0, [], [], []
    for name in chosen:
        path, cap = name.rsplit("_cap", 1)
        cap = float(cap)
        rows = ledger[ledger.path == path].copy()
        if cap:
            rows = rows[rows.risk_percent <= cap+1e-10]
        policy = path.split("_", 2)[2]
        if policy.startswith("tp"):
            target, arm, mult = float(policy[2:]), 0., 0.
        else:
            a, m = policy.split("_atr")
            target, arm, mult = 0., float(a[5:]), float(m)
        for row in rows.itertuples():
            event = [row.entry_i, row.side, row.entry, row.stop, row.end_i]
            expected = scalar(ctx, event, target, arm, mult, cfg["tick"])
            actual = np.array([row.exit_i, row.exit_price, row.reason, row.peak_r,
                               row.arm_i, row.protection, row.ambiguous, row.steps])
            np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-9)
            assert abs(row.net_r - (row.side*(row.exit_price-row.entry)-cfg["round_trip_cost"]*row.entry)/row.risk) < 1e-8
            count += 1
        audits.append(dict(arm=name, signal_and_random_paths=len(rows), mismatches=0))
        signal = rows[rows.population == "signal"]
        for seg in (-1, 0, 1, 2):
            ss = signal if seg == -1 else signal[signal.segment == seg]
            rr = rows[rows.population == "random"]
            if seg != -1:
                rr = rr[rr.segment == seg]
            paired = pairs(ss, rr)
            week = ss.groupby("week").net_r.sum().sort_values(ascending=False)
            timeline = []
            for r in ss.itertuples():
                begin, finish = pd.Timestamp(r.entry_time).value, pd.Timestamp(r.exit_time).value
                timeline.extend([(begin, 1, 1), (finish, 0 if finish > begin else 2, -1)])
            timeline.sort()
            max_active = int(np.max(np.cumsum([delta for _, _, delta in timeline]))) if timeline else 0
            hold = (pd.to_datetime(ss.exit_time)-pd.to_datetime(ss.entry_time)).dt.total_seconds()/3600
            detail.append(dict(arm=name, segment=seg, **metrics(ss),
                top5_week_net_r=float(week.head(5).sum()),
                net_r_without_top5_weeks=float(ss.net_r.sum()-week.head(5).sum()),
                positive_weeks=int((week > 0).sum()), signal_weeks=len(week),
                maximum_simultaneous_references=max_active,
                holding_hours_median=float(hold.median()) if len(hold) else None,
                holding_hours_p95=float(hold.quantile(.95)) if len(hold) else None,
                holding_hours_max=float(hold.max()) if len(hold) else None,
                gross_positive_but_net_negative=int(((ss.gross_r > 0) & (ss.net_r < 0)).sum()),
                median_cost_r=float(ss.fee_r.median()) if len(ss) else None,
                paired_n=len(paired), paired_signal_net_r=float(paired.net_r.sum()),
                paired_random_net_r=float(paired.net_r_random.sum()),
                **inference(paired, cfg["seed"])))
        export = signal.copy()
        export["entry_shanghai"] = pd.to_datetime(export.entry_time).dt.tz_convert("Asia/Shanghai")
        export["exit_shanghai"] = pd.to_datetime(export.exit_time).dt.tz_convert("Asia/Shanghai")
        export["exit_reason"] = export.reason.map({0:"period_boundary",1:"initial_stop",2:"trailing_stop",3:"opposite_H1",4:"fixed_target"})
        export.to_csv(out/f"signal_{name}.csv", index=False)
        ss = summary[(summary.arm == name) & (summary.segment == -1)].iloc[0]
        # Keep each other factor at the reference setting.
        for factor in ("window", "buffer", "policy", "cap"):
            neighbors = summary.copy()
            for frozen in {"window", "buffer", "policy", "cap"}-{factor}:
                neighbors = neighbors[neighbors[frozen] == ss[frozen]]
            neighbors = neighbors.copy()
            neighbors["reference"], neighbors["varied_factor"] = name, factor
            tables.append(neighbors)
    pd.DataFrame(detail).to_json(out/"diagnostics.json", orient="records", indent=2)
    pd.concat(tables, ignore_index=True).to_csv(out/"single_factor_slices.csv", index=False)
    # Actual realized 10R/20R outcomes, not maximum floating excursion.
    subset = ledger[(ledger.population == "signal") & ledger.path.isin(
        ["w5_b0.05_trail5_atr4", "w5_b0.05_tp10", "w5_b0.05_tp20"])]
    wide = subset.pivot(index="pair_id", columns="path", values="gross_r")
    tails = []
    for threshold in (10, 20):
        fixed = wide[f"w5_b0.05_tp{threshold}"] >= threshold-1e-8
        baseline = wide["w5_b0.05_trail5_atr4"]
        tails.append(dict(target_r=threshold, fixed_target_winners=int(fixed.sum()),
            baseline_winners_same_threshold=int((baseline >= threshold-1e-8).sum()),
            fixed_winners_baseline_still_at_threshold=int((fixed & (baseline >= threshold-1e-8)).sum()),
            fixed_winners_baseline_positive=int((fixed & (baseline > 0)).sum())))
    (out/"tail_comparison.json").write_text(json.dumps(tails, indent=2)+"\n")
    receipt = dict(selected_path_audits=audits, total_scalar_paths=count, cost_identities=count,
                   builder_commit=subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip(),
                   statistical_scope="Weekly block nominal inference; full-history winner and development p-values are selection-biased. No account drawdown.",
                   inapplicable="AUC and model top-decile metrics do not apply: this study changes execution parameters, not a prediction model.")
    (out/"diagnostic_audit.json").write_text(json.dumps(receipt, indent=2)+"\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
