"""V37 same-event exit intervention; no entry optimization or new market scope.

Inputs: hash-frozen V36 request columns at decision_time and V34 complete5m
2023-2024 source. Native SMA40 uses only closed bars; future OHLC solely labels
execution. V36 early-exit labels join AFTER replay for mechanism attribution.
Identity/unknown joins follow pandas2.3.3 validate='one_to_one':
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.merge.html
Round-trip CSV reading preserves existing numeric contract:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import BAR_COLUMNS, add_features, resample_complete
from yoyo.evaluation.owner_k1k2_genuine_flow import (
    ROOT, FOLDS, checked as checked_source, load_month, sha, clean, write_json,
    contribution, describe, month_inference, rank_diagnostics,
)
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events, single_position_ledger

REL = Path("experiments/active/exp-btcusdtp-owner-k1k2-transition-exit-20260907-v37")
HERE = ROOT / REL
BASE = Path("data/owner_k1k2_genuine_flow_v36")
INPUTS = ["case_requests", "control_requests", "assignments", "case_trades", "control_trades"]
REQUEST_COLUMNS = ["event_id", "decision_time", "fold", "direction", "initial_stop",
                   "signal_atr", "ma", "body_ratio", "gap_bars"]
PARITY_COLUMNS = ["event_id", "decision_time", "direction", "initial_stop", "signal_atr",
    "entry_time", "entry_price", "exit_time", "exit_price", "closed", "outcome",
    "gross_return", "net_return", "risk_pct", "risk_atr", "hold_minutes",
    "max_favourable_r", "max_adverse_r"]


def checked():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    config = json.loads((HERE / "config.json").read_text())
    for name in config["builder_paths"] + [str(REL / "config.json"), str(REL / "PROJECT_PLAN.md")]:
        if subprocess.check_output(["git", "show", commit + ":" + name], cwd=ROOT) != (ROOT/name).read_bytes():
            raise ValueError("Commit builder before replay: " + name)
    if config["flow_gate"] is not False: raise ValueError("Both arms must have flow gate off")
    changed = {k for k in config["baseline_policy"] if config["baseline_policy"][k] != config["candidate_policy"].get(k)}
    if changed != {"exit_mode"} or set(config["baseline_policy"]) != set(config["candidate_policy"]):
        raise ValueError("Only exit_mode may change")
    source_commit, source_config, manifest = checked_source()
    if source_commit != commit or source_config["policy"] != config["baseline_policy"]:
        raise ValueError("Different V36 baseline policy")
    if sha(ROOT/config["baseline_summary"]) != config["baseline_summary_sha256"]:
        raise ValueError("Baseline summary drift")
    baseline = json.loads((ROOT/config["baseline_summary"]).read_text())
    if baseline["config_sha256"] != sha(ROOT / "experiments/active/exp-btcusdtp-owner-k1k2-genuine-flow-20260907-v36/config.json"):
        raise ValueError("Baseline config drift")
    for name in INPUTS:
        path = str(BASE / (name + ".csv"))
        if sha(ROOT/path) != baseline["files"][path]["sha256"]: raise ValueError("Frozen V36 input drift")
    if sha(ROOT/config["clock_audit"]) != config["clock_audit_sha256"]: raise ValueError("Clock group drift")
    return commit, config, baseline, manifest


def read_input(name, columns=None):
    path = ROOT / BASE / (name + ".csv")
    clocks = pd.to_datetime(pd.read_csv(path, usecols=["decision_time"]).decision_time, utc=True)
    if clocks.isna().any() or not ((clocks >= pd.Timestamp("2023-01-01", tz="UTC")) &
                                  (clocks < pd.Timestamp("2025-01-01", tz="UTC"))).all():
        raise ValueError("Out-of-scope request clocks; do not materialize")
    f = pd.read_csv(path, usecols=columns, float_precision="round_trip")
    f.decision_time = clocks
    if f.event_id.isna().any() or f.event_id.duplicated().any(): raise ValueError("Null/duplicate identity")
    return f


def comparison(state, transition):
    """One row per original event; unknown outcomes persist across both arms."""
    if state.event_id.isna().any() or transition.event_id.isna().any(): raise ValueError("Null event identity")
    if set(state.event_id) != set(transition.event_id): raise ValueError("Changed event universe")
    cols = ["event_id", "decision_time", "fold", "direction", "initial_stop", "signal_atr", "entry_price"]
    a = state[cols].copy()
    for label, f in [("state", state), ("transition", transition)]:
        x = f[["event_id", "closed", "outcome", "exit_time", "hold_minutes"]].copy()
        x["net"] = contribution(f)
        x["gross"] = contribution(f, gross=True)
        x = x.rename(columns={k:label + "_" + k for k in x if k != "event_id"})
        a = a.merge(x, on="event_id", validate="one_to_one")
    b = transition.set_index("event_id").reindex(state.event_id)
    for col in ["decision_time", "fold", "direction", "initial_stop", "signal_atr", "entry_price"]:
        pd.testing.assert_series_equal(state[col].reset_index(drop=True), b[col].reset_index(drop=True), check_dtype=False, check_names=False)
    a["known"] = a.state_net.notna() & a.transition_net.notna()
    a["delta_net"] = a.transition_net - a.state_net
    a["delta_gross"] = a.transition_gross - a.state_gross
    a["delta_saved_cost"] = a.delta_net - a.delta_gross
    a["delta_hold_minutes"] = a.transition_hold_minutes - a.state_hold_minutes
    return a


def matched_comparison(cases, controls, assignments, control_requests):
    """Preserve all original three-control groups, including unresolved pairs."""
    if control_requests.event_id.duplicated().any(): raise ValueError("Duplicate control identity")
    if set(controls.event_id) != set(control_requests.event_id): raise ValueError("Changed control universe")
    ctrl = controls.merge(control_requests[["event_id", "parent_event_id"]], on="event_id", validate="one_to_one")
    result = []
    for event_id in assignments.loc[assignments.match_status.eq("matched"), "event_id"]:
        case = cases.loc[cases.event_id.eq(event_id)]
        group = ctrl.loc[ctrl.parent_event_id.eq(event_id)]
        if len(case) != 1 or len(group) != 3 or group.event_id.nunique() != 3: raise ValueError("Original3controls required")
        row = case.iloc[0]
        known = bool(row.known and group.known.all())
        out = dict(event_id=event_id, decision_time=row.decision_time, fold=row.fold,
                   controls=3, complete_pair=known)
        for measure in ["net", "gross"]:
            for arm in ["state", "transition"]:
                out[arm + "_excess_" + measure] = row[arm+"_"+measure] - group[arm+"_"+measure].mean() if known else np.nan
            out["delta_excess_"+measure] = out["transition_excess_"+measure] - out["state_excess_"+measure]
        out["delta_excess_saved_cost"] = out["delta_excess_net"] - out["delta_excess_gross"]
        result.append(out)
    return pd.DataFrame(result)


def baseline_parity(replayed, saved):
    if set(replayed.event_id) != set(saved.event_id): raise ValueError("Baseline universe drift")
    a, b = (x[PARITY_COLUMNS].sort_values("event_id").reset_index(drop=True).copy() for x in [replayed, saved])
    for k in ["decision_time", "entry_time", "exit_time"]:
        a[k], b[k] = pd.to_datetime(a[k], utc=True), pd.to_datetime(b[k], utc=True)
    pd.testing.assert_frame_equal(a, b, check_dtype=False, rtol=1e-12, atol=1e-12)
    return dict(rows=len(a), columns=PARITY_COLUMNS, exact_identity=True, numeric_tolerance=1e-12)


def ledger(trades):
    f = trades.copy()
    f.loc[f.outcome.isin(["entry_missing", "entry_invalid"]), "outcome"] = "execution_unknown"
    out = single_position_ledger(f)
    out.outcome = out.event_id.map(trades.set_index("event_id").outcome)
    return out


def changes(f):
    x = f.loc[f.known].copy()
    return clean(dict(events=len(f), known=len(x), unknown=len(f)-len(x),
        mean_delta_net_bp=x.delta_net.mean()*1e4, mean_delta_gross_bp=x.delta_gross.mean()*1e4,
        mean_delta_saved_cost_bp=x.delta_saved_cost.mean()*1e4,
        improved=int(x.delta_net.gt(1e-12).sum()), worsened=int(x.delta_net.lt(-1e-12).sum()),
        unchanged=int(x.delta_net.abs().le(1e-12).sum()),
        recovered_winners=int((x.state_net.le(0) & x.transition_net.gt(0)).sum()),
        lost_winners=int((x.state_net.gt(0) & x.transition_net.le(0)).sum()),
        extended_losers=int((x.transition_net.lt(0) & x.delta_hold_minutes.gt(0)).sum()),
        median_hold_delta_minutes=x.delta_hold_minutes.median()))


def run():
    commit, config, old, manifest = checked()
    output = ROOT/config["output_dir"]
    if output.exists() or (HERE/"summary.json").exists(): raise ValueError("One-shot evidence already exists")
    cases = read_input("case_requests", REQUEST_COLUMNS)
    controls = read_input("control_requests", REQUEST_COLUMNS+["parent_event_id"])
    assignments = read_input("assignments", ["event_id", "decision_time", "fold", "match_status"])
    if len(cases) != 63 or len(controls) != 108 or len(assignments) != 63: raise ValueError("Original support changed")
    for f in [cases, controls]:
        for fold, start, end in FOLDS:
            selected = f.loc[f.fold.eq(fold)]
            if not (selected.decision_time.ge(pd.Timestamp(start)) & selected.decision_time.lt(pd.Timestamp(end)-pd.Timedelta(hours=72))).all():
                raise ValueError("Frozen fold clock violation")
    source = pd.concat([load_month(ROOT/r["output_path"], r) for r in manifest["monthly"]], ignore_index=True)
    raw = resample_complete(source[BAR_COLUMNS], 5)
    featured = add_features(raw, "SMA", 40)
    output.mkdir(parents=True)
    files = {}
    def save(name, frame):
        path = output/(name+".csv")
        if path.exists(): raise ValueError("No evidence overwrite")
        frame.to_csv(path, index=False, float_format="%.17g")
        files[str(path.relative_to(ROOT))] = dict(sha256=sha(path), rows=len(frame), columns=list(frame))
    for name, f in [("case_requests",cases), ("control_requests",controls), ("assignments",assignments)]: save(name,f)
    write_json(HERE/"pre_outcome_receipt.json", dict(source_commit=commit, config_sha256=sha(HERE/"config.json"),
        generated_at=pd.Timestamp.now(tz="UTC"), files=files.copy(), original_case_ids=cases.event_id.tolist(),
        original_control_ids=controls.event_id.tolist(), outcomes_materialized=False))
    results, parity, foldrows, ledger_info = {}, {}, [], {}
    for arm, policy in [("state",config["baseline_policy"]), ("transition",config["candidate_policy"])]:
        for cohort, requests in [("case",cases), ("control",controls)]:
            t = pd.concat([simulate_events(raw, featured, requests.loc[requests.fold.eq(fold)], policy, end_exclusive=end)
                for fold,_,end in FOLDS], ignore_index=True)
            if arm == "state": parity[cohort] = baseline_parity(t,read_input(cohort+"_trades"))
            t["failure_class"] = np.select([~t.closed,t.net_return.gt(0),t.gross_return.gt(0)],
                ["unknown_or_rejected","net_winner","fee_erased_gain"], default="price_loss")
            results[(arm,cohort)] = t
            save(arm+"_"+cohort+"_trades",t)
            for fold,_,_ in FOLDS: foldrows.append(dict(arm=arm,cohort=cohort,fold=fold,**describe(t.loc[t.fold.eq(fold)])))
        l = ledger(results[(arm,"case")]); save(arm+"_case_single_position",l)
        ledger_info[arm] = dict(selected=int(l.portfolio_selected.sum()), skip_reasons=l.portfolio_skip_reason.value_counts().to_dict(),
                               metrics=describe(l.loc[l.portfolio_selected]))
    cdelta = comparison(results[("state","case")],results[("transition","case")])
    rdelta = comparison(results[("state","control")],results[("transition","control")])
    paired = matched_comparison(cdelta,rdelta,assignments,controls)
    groups = pd.read_csv(ROOT/config["clock_audit"], usecols=["event_id","color_exit_without_new_flip"])
    if groups.event_id.duplicated().any() or set(groups.event_id) != set(cases.event_id): raise ValueError("Diagnostic group identity drift")
    cdelta = cdelta.merge(groups,on="event_id",validate="one_to_one")
    rdelta = rdelta.merge(controls[["event_id","parent_event_id"]],on="event_id",validate="one_to_one")
    rdelta = rdelta.merge(groups.rename(columns={"event_id":"parent_event_id"}),on="parent_event_id",validate="many_to_one")
    for name,f in [("case_changes",cdelta),("control_changes",rdelta),("paired_contrasts",paired),("fold_metrics",pd.DataFrame(foldrows))]: save(name,f)
    group_results = []
    for cohort, delta in [("case",cdelta),("control",rdelta)]:
        for early in [True,False]:
            group_results.append(dict(cohort=cohort,early_continuation=early,**changes(delta.loc[delta.color_exit_without_new_flip.eq(early)])))
    summaries = {arm:{cohort:describe(results[(arm,cohort)]) for cohort in ["case","control"]} for arm in ["state","transition"]}
    inf = month_inference(paired,"delta_excess_net",config["draws"],config["seed"])
    trans = results[("transition","case")]
    closed = trans.loc[trans.closed]
    active = pd.to_datetime(closed.decision_time,utc=True).dt.strftime("%Y-%m")
    ownfolds = [x for x in foldrows if x["arm"]=="transition" and x["cohort"]=="case"]
    gates = dict(min80=len(closed)>=80,min12_per_fold=all(x["closed"]>=12 for x in ownfolds),
        four_positive_folds=all(x["mean_net_bp"] is not None and x["mean_net_bp"]>0 for x in ownfolds),
        positive_net=bool(closed.net_return.mean()>0),pf1_1=(describe(trans)["profit_factor"] or 0)>=1.1,
        min12_active_months=active.nunique()>=12,
        min3_months_per_fold=all(pd.to_datetime(closed.loc[closed.fold.eq(f)].decision_time,utc=True).dt.strftime("%Y-%m").nunique()>=3 for f,_,_ in FOLDS),
        matched_coverage90=len(paired)/len(cases)>=.9,all_pairs_known=bool(paired.complete_pair.all()) and len(paired)>0,
        primary_p01=inf.get("p_one_sided",1)<.01,primary_ci_positive=inf.get("ci95_bp",[-np.inf])[0]>0,
        positive_absolute_excess=bool(paired.transition_excess_net.mean()>0))
    summary = dict(status="research_pass_not_deployable" if all(gates.values()) else "research_gate_failed",
        source_commit=commit,generated_at=pd.Timestamp.now(tz="UTC"),config_sha256=sha(HERE/"config.json"),
        baseline_summary_sha256=config["baseline_summary_sha256"],source_bars=len(raw),cases=len(cases),controls=len(controls),
        matched_cases=len(paired),matched_coverage=len(paired)/len(cases),baseline_parity=parity,
        arms=summaries,fold_metrics=foldrows,case_changes=changes(cdelta),control_changes=changes(rdelta),groups=group_results,
        primary_matched_increment=inf,
        matched_state_excess=month_inference(paired,"state_excess_net",config["draws"],config["seed"]),
        matched_transition_excess=month_inference(paired,"transition_excess_net",config["draws"],config["seed"]),
        matched_gross_delta_bp=paired.delta_excess_gross.mean()*1e4,matched_cost_delta_bp=paired.delta_excess_saved_cost.mean()*1e4,
        ledger=ledger_info,gates=gates,descriptive_body_rank={a:rank_diagnostics(results[(a,"case")],"body_ratio") for a in ["state","transition"]},
        transition_initial_states=trans.transition_initial_state.value_counts().to_dict(),
        transition_never_armed_outcomes=trans.loc[trans.transition_first_armed_at.isna()].outcome.value_counts().to_dict(),
        files=files,holdout_evaluated=False,training_eligible=False,production_eligible=False,
        live_delivery_verified=False,funding_modelled=False)
    if checked() != (commit,config,old,manifest): raise ValueError("Inputs changed during replay")
    write_json(HERE/"summary.json",summary)
    print(json.dumps(clean({k:v for k,v in summary.items() if k not in {"files","fold_metrics","baseline_parity"}}),indent=2))


if __name__ == "__main__":
    run()
