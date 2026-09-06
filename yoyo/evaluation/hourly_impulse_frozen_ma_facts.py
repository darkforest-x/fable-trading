"""Descriptive V12 saved-ledger decomposition, never admission or tuning.

Uses paired complete outcome fields and original fixed matching assignments.
Geometry uses only the separately frozen entry-known table; its bins are not
selected from returns. Extreme examples are explicitly retrospective. No raw
price, fitted feature, new threshold or inference is calculated here.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_research import ROOT, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_frozen_ma_research import EXPERIMENT


def facts(mechanics, matching, geometry):
    """Retain every original case; unknown net outcomes are not filled with zero."""
    for frame in (mechanics, matching):
        if frame.event_id.isna().any() or not frame.event_id.is_unique:
            raise ValueError("Unique original mother identities required")
    case_geometry=geometry.loc[geometry.population.eq("case")]
    if not case_geometry.event_id.is_unique or set(case_geometry.event_id)!=set(mechanics.event_id) or set(matching.event_id)!=set(mechanics.event_id):
        raise ValueError("No geometry or matching selection allowed")
    m=mechanics.merge(matching[["event_id","assigned_controls"]],on="event_id",validate="one_to_one")
    m=m.merge(case_geometry[["event_id","geometry_bin","entry_distance_r"]],on="event_id",validate="one_to_one")
    if not m.assigned_controls.isin([0,3]).all():
        raise ValueError("Original complete control triples only")
    m["support"]=np.where(m.assigned_controls.eq(3),"matched","unmatched")
    m["net_change_bp"]=(m.net_return_after-m.net_return_before)*1e4
    paired_known=np.isfinite(m.net_return_before)&np.isfinite(m.net_return_after)
    m["paired_old"]=m.net_return_before.where(paired_known)
    m["paired_new"]=m.net_return_after.where(paired_known)
    groups={}
    for name,column in (("matching_strata","support"),("win_loss_transitions","win_loss_transition"),("geometry_outcomes","geometry_bin")):
        rows=[]
        for group,part in m.groupby(column,sort=True,dropna=False):
            rows.append({"group":group,"n":len(part),"known":int(part.net_change_bp.notna().sum()),
                "frozen_exit_count":int(part.frozen_exit.sum()),"old_net_bp":part.paired_old.mean()*1e4,
                "new_net_bp":part.paired_new.mean()*1e4,"delta_bp":part.net_change_bp.mean(),
                "event_sum_delta_bp":part.net_change_bp.sum(min_count=1)})
        groups[name]=pd.DataFrame(rows)
    exits=[]
    for outcome,part in m.groupby("outcome_after",sort=True):
        loss=part.net_return_after.lt(0)
        exits.append({"outcome":outcome,"n":len(part),"wins":int(part.net_return_after.gt(0).sum()),
            "losses":int(loss.sum()),"gross_nonpositive_losses":int((loss&part.gross_return_after.le(0)).sum()),
            "positive_gross_cost_losses":int((loss&part.gross_return_after.gt(0)).sum()),
            "paired_known":int(part.net_change_bp.notna().sum()),
            "old_net_bp":part.paired_old.mean()*1e4,"new_net_bp":part.paired_new.mean()*1e4})
    groups["exit_failure_groups"]=pd.DataFrame(exits)
    changed=m.loc[m.frozen_exit & m.net_change_bp.notna()]
    examples=pd.concat([changed.loc[changed.net_change_bp.lt(-1e-8)].nsmallest(3,"net_change_bp").assign(example_selection="up_to_three_largest_sacrifices"),
                        changed.loc[changed.net_change_bp.gt(1e-8)].nlargest(3,"net_change_bp").assign(example_selection="up_to_three_largest_savings")])
    columns=["event_id","entry_time_before","direction_before","entry_price_before","initial_stop_before","ma_before",
             "exit_time_before","exit_time_after","exit_price_before","exit_price_after","hold_minutes_before","hold_minutes_after",
             "net_return_before","net_return_after","net_change_bp","win_loss_transition","outcome_after","geometry_bin",
             "entry_distance_r","frozen_ma_trigger_close","frozen_ma_trigger_open_time","frozen_ma_trigger_available_at","example_selection"]
    groups["retrospective_examples"]=examples[columns]
    info={"all_pairs":len(m),"unknown_pairs":int(m.net_change_bp.isna().sum()),"all_delta_bp":m.net_change_bp.mean(),
          "total_event_delta_bp":m.net_change_bp.sum(min_count=1),
          "frozen_ma_states":m.frozen_ma_status.value_counts().to_dict(),
          "interpretation":"Post-treatment groups and extreme examples are descriptions, never entry filters or proof of causal selection.",
          "examples_rule":"Up to three strictly negative/positive changes beyond1e-8bp among frozen exits, selected after outcomes; no duplication or threshold fitting."}
    for name,frame in groups.items():
        if name!="retrospective_examples":info[name]=frame.to_dict("records")
    return info,groups


def main():
    p=EXPERIMENT/"results";out=EXPERIMENT/"facts"
    if out.exists():raise ValueError("Preserve prior facts; no overwrite")
    sources=committed_sources([ROOT/"yoyo/evaluation/hourly_impulse_frozen_ma_facts.py",ROOT/"tests/test_hourly_impulse_frozen_ma_facts.py"])
    summary=json.loads((p/"summary.json").read_text())
    names=["paired_case_mechanics.csv.gz","baseline/matched.csv","entry_geometry.csv"]
    for name in names:
        if digest(p/name)!=summary["output_hashes"][name]:raise ValueError("Saved evidence changed: "+name)
    info,groups=facts(*(pd.read_csv(p/name) for name in names))
    out.mkdir()
    for name,frame in groups.items():write_csv(out/(name+".csv"),frame)
    write_json(out/"facts.json",{**info,"source_summary_sha256":digest(p/"summary.json"),"sources":sources,
        "source_paths":{str((p/n).relative_to(ROOT)):digest(p/n) for n in names},
        "output_hashes":{n+".csv":digest(out/(n+".csv")) for n in groups},
        "raw_prices_read":False,"new_parameters_tested":False})
    print(json.dumps(info,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
