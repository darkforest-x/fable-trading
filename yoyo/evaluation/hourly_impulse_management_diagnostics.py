"""Post-run V8 paired failure analysis; consumes saved ledgers, never prices.

Win/loss transitions, excursions and holding times are retrospective outcomes,
not admission features or tuning gates. Every original case/control/source is
retained. Derivative artifacts live outside immutable research results.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_context_research import committed_sources
from yoyo.evaluation.hourly_impulse_management_research import EXPERIMENT, assert_saved_parity, paired_effects
from yoyo.evaluation.hourly_impulse_research import ROOT, digest, write_csv, write_json
from yoyo.evaluation.hourly_impulse_transition_research import read_frame


def mechanism_tables(before, after):
    """Exact entry pairing; all four observed win/loss transitions kept."""
    invariant = ["event_id", "entry_time", "direction", "initial_stop", "entry_price", "risk_pct", "risk_atr"]
    assert_saved_parity(before[invariant], after[invariant])
    a, b = (x.set_index("event_id").sort_index() for x in (before, after))
    columns = ["entry_time", "fold", "direction"]
    joined = a[columns].copy()
    for suffix, data in (("5m", a), ("15m", b)):
        for col in ("net_return", "gross_return", "outcome", "hold_minutes", "max_favourable_r", "mg_entry_state"):
            joined[col+"_"+suffix] = data[col]
    joined["difference"] = b.net_return-a.net_return
    joined["old_win"] = a.net_return.gt(0)
    joined["new_win"] = b.net_return.gt(0)
    rows = []
    for old in (False, True):
        for new in (False, True):
            part = joined.loc[joined.old_win.eq(old) & joined.new_win.eq(new)]
            rows.append({"old_win": old, "new_win": new, "n": len(part),
                "old_mean_bp": part.net_return_5m.mean()*1e4,
                "new_mean_bp": part.net_return_15m.mean()*1e4,
                "delta_mean_bp": part.difference.mean()*1e4,
                "delta_event_sum_bp": part.difference.sum()*1e4})
    outcome = joined.groupby(["outcome_5m", "outcome_15m"]).agg(
        n=("difference", "size"), delta_mean=("difference", "mean")).reset_index()
    outcome["delta_mean_bp"] = outcome.pop("delta_mean")*1e4
    return joined.reset_index(), pd.DataFrame(rows), outcome


def run():
    sources = committed_sources([ROOT/"yoyo/evaluation/hourly_impulse_management_diagnostics.py"])
    result, output = EXPERIMENT/"results", EXPERIMENT/"diagnostics"
    if output.exists():
        raise RuntimeError("Preserve prior diagnostic build")
    summary = json.loads((result/"summary.json").read_text())
    data, states, hashes = {}, {}, {"results/summary.json": digest(result/"summary.json")}
    for minutes in (5, 15):
        arm = f"{minutes}m_native40"
        data[minutes] = {}
        for label in ("case", "control"):
            for suffix in ("trades.csv.gz", "request_outcomes.csv.gz"):
                name = label+"_"+suffix
                data[minutes][name] = read_frame(result/arm/name)
                hashes["results/"+arm+"/"+name] = digest(result/arm/name)
            state = pd.read_csv(result/arm/f"{label}_management_states.csv")
            states[f"{arm}_{label}"] = state.to_dict("records")
            hashes[f"results/{arm}/{label}_management_states.csv"] = digest(result/arm/f"{label}_management_states.csv")
        for name in ("matched_request_outcomes.csv", "single_pending_zone_ledger.csv.gz"):
            data[minutes][name] = read_frame(result/arm/name)
            hashes["results/"+arm+"/"+name] = digest(result/arm/name)
    a,b = data[5],data[15]
    frames, effects = paired_effects(a["case_request_outcomes.csv.gz"],b["case_request_outcomes.csv.gz"],
        a["matched_request_outcomes.csv"],b["matched_request_outcomes.csv"],
        a["single_pending_zone_ledger.csv.gz"],b["single_pending_zone_ledger.csv.gz"])
    for name, frame in frames.items():
        assert_saved_parity(read_frame(result/(name+".csv")), frame)
        np.testing.assert_allclose(effects[name]["mean_bp"], summary["effects"][name]["mean_bp"], rtol=1e-12, atol=1e-12)
    joined, transitions, exits = mechanism_tables(a["case_trades.csv.gz"], b["case_trades.csv.gz"])
    serial_rows = []
    for minutes in (5, 15):
        serial = data[minutes]["single_pending_zone_ledger.csv.gz"]
        selected = serial.loc[serial.portfolio_selected]
        skipped = serial.loc[~serial.portfolio_selected]
        missed_trades = skipped.loc[skipped.completed_trade]
        net = serial.episode_net_return.where(serial.portfolio_selected, 0.)
        serial_rows.append({"minutes": minutes, "zones": len(serial), "selected_zones": len(selected),
            "skipped_zones": len(skipped), "skipped_emitted_requests": len(missed_trades),
            "skipped_winners": int(missed_trades.episode_net_return.gt(0).sum()),
            "skipped_losers": int(missed_trades.episode_net_return.lt(0).sum()),
            "mean_net_bp_per_original_zone": net.mean()*1e4,
            "net_event_sum_bp": net.sum()*1e4})
    distribution = {}
    for label, values in {"case_delta": joined.difference, "case5": joined.net_return_5m, "case15": joined.net_return_15m}.items():
        x=values.dropna()*1e4
        distribution[label] = {"quantiles_bp": {str(k): float(v) for k,v in x.quantile([0,.05,.25,.5,.75,.95,1]).items()},
            "sd_bp": x.std(ddof=1), "outliers_removed": 0}
        try:
            from scipy.stats import shapiro
            w,p=shapiro(x)
            distribution[label].update(shapiro_w=w, shapiro_p=p)
        except ImportError:
            distribution[label]["shapiro_unavailable"] = True
    output.mkdir()
    write_csv(output/"paired_case_mechanics.csv.gz", joined)
    write_csv(output/"win_loss_transitions.csv", transitions)
    write_csv(output/"exit_transitions.csv", exits)
    write_csv(output/"serial_intentions.csv", pd.DataFrame(serial_rows))
    facts = {**summary, "paired_win_loss": transitions.to_dict("records"), "paired_exit_types": exits.to_dict("records"),
        "management_states": states, "serial_intentions": serial_rows, "distribution_checks": distribution,
        "diagnostic_sources": sources, "diagnostic_input_sha256": hashes,
        "diagnostic_generated_at": pd.Timestamp.now(tz="UTC"),
        "diagnostic_limit": "Retrospective associations; no entry tuning, no price reads, no future-MFE selection."}
    write_json(output/"report_facts.json", facts)
    print(json.dumps({"report_facts": str(output/"report_facts.json"), "paired_cases": len(joined)}))


if __name__ == "__main__":
    run()
