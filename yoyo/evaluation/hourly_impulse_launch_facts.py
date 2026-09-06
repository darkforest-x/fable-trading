"""Saved V11 failure decomposition; no prices, feature fitting or policy search.

Post-treatment groups are descriptive only. Fixed original matching eligibility
explains denominator differences, not a new admission filter. Extreme examples
are explicitly retrospective examples, never evidence that a future winner can
be selected. All source inputs are hash-pinned by the completed V11 receipt.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_research import ROOT, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_launch_research import EXPERIMENT


def facts(mechanics, matching):
    if mechanics.event_id.isna().any() or not mechanics.event_id.is_unique or not matching.event_id.is_unique:
        raise ValueError("One row per original mother required")
    if set(mechanics.event_id) != set(matching.event_id):
        raise ValueError("Matching cannot drop unpaired original mothers")
    m = mechanics.merge(matching[["event_id", "assigned_controls"]],on="event_id",validate="one_to_one")
    m["support"] = np.where(m.assigned_controls.eq(3), "matched", "unmatched")
    if not m.assigned_controls.isin([0,3]).all():
        raise ValueError("Whole old triplets only")
    m["net_change_bp"] = (m.net_return_after-m.net_return_before)*1e4
    rows = []
    for group,part in m.groupby("support",sort=True):
        rows.append({"support":group,"n":len(part),"known":int(part.net_change_bp.notna().sum()),
            "old_net_bp":part.net_return_before.mean()*1e4,"new_net_bp":part.net_return_after.mean()*1e4,
            "delta_bp":part.net_change_bp.mean(),"event_sum_delta_bp":part.net_change_bp.sum(min_count=1)})
    transitions = []
    for group,part in m.groupby("win_loss_transition",sort=True):
        transitions.append({"transition":group,"n":len(part),"timeout_count":int(part.timeout_exit.sum()),
            "old_net_bp":part.net_return_before.mean()*1e4,"new_net_bp":part.net_return_after.mean()*1e4,
            "delta_bp":part.net_change_bp.mean(),"event_sum_delta_bp":part.net_change_bp.sum(min_count=1)})
    exits = []
    for outcome,part in m.groupby("outcome_after",sort=True):
        exits.append({"outcome":outcome,"n":len(part),"wins":int(part.net_return_after.gt(0).sum()),
            "losses":int(part.net_return_after.lt(0).sum()),
            "gross_negative_losses":int((part.net_return_after.lt(0)&part.gross_return_after.le(0)).sum()),
            "positive_gross_cost_losses":int((part.net_return_after.lt(0)&part.gross_return_after.gt(0)).sum()),
            "mean_net_bp":part.net_return_after.mean()*1e4,"old_mean_net_bp":part.net_return_before.mean()*1e4})
    cols = ["event_id","fold_before","entry_time_before","direction_before","entry_price_before","initial_stop_before",
            "exit_time_before","exit_time_after","exit_price_before","exit_price_after","hold_minutes_before","hold_minutes_after",
            "net_return_before","net_return_after","net_change_bp","win_loss_transition","outcome_after",
            "risk_pct_before","launch_max_completed_close_r","launch_completed_close_count","launch_status"]
    available = [c for c in cols if c in m]
    affected = m.loc[m.timeout_exit & m.net_change_bp.notna()]
    examples = pd.concat([affected.nsmallest(3,"net_change_bp").assign(example_selection="three_largest_sacrifices"),
                          affected.nlargest(3,"net_change_bp").assign(example_selection="three_largest_savings")])
    examples = examples[available+["example_selection"]]
    info = {"matching_strata":rows,"transitions":transitions,"exit_failure_groups":exits,
        "launch_states":m.launch_status.value_counts().to_dict(),
        "all_pairs":len(m),"unknown_pairs":int(m.net_change_bp.isna().sum()),
        "all_delta_bp":m.net_change_bp.mean(),"total_event_delta_bp":m.net_change_bp.sum(min_count=1),
        "interpretation":"Retrospective post-treatment descriptions. Matching strata retain all original mothers; do not select these groups as live filters.",
        "examples_rule":"Three largest negative and positive timeout changes, selected AFTER outcomes, never a strategy gate."}
    return info,pd.DataFrame(rows),pd.DataFrame(transitions),pd.DataFrame(exits),examples


def main():
    p=EXPERIMENT/"results"
    output=EXPERIMENT/"facts"
    if output.exists(): raise ValueError("Preserve existing facts; no overwrite")
    summary=json.loads((p/"summary.json").read_text())
    names=["paired_case_mechanics.csv.gz","baseline/matched.csv"]
    for name in names:
        if digest(p/name)!=summary["output_hashes"][name]: raise ValueError("Saved evidence changed: "+name)
    info,strata,transitions,exits,examples=facts(pd.read_csv(p/names[0]),pd.read_csv(p/names[1]))
    output.mkdir()
    for name,frame in [("matching_strata",strata),("win_loss_transitions",transitions),("exit_failure_groups",exits),("retrospective_examples",examples)]:
        write_csv(output/(name+".csv"),frame)
    write_json(output/"facts.json",{**info,"source_summary_sha256":digest(p/"summary.json"),
        "source_paths":{str((p/name).relative_to(ROOT)):digest(p/name) for name in names},
        "builder_sha256":digest(ROOT/"yoyo/evaluation/hourly_impulse_launch_facts.py"),
        "raw_prices_read":False,"new_parameters_tested":False})
    print(json.dumps(info,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
