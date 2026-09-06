"""Descriptive control audit for all eight already-frozen development exit arms.

No selection changes. Reads V1 2023--2024 prefix only and applies the exact
registered same-month/session/causal-volatility/known-colour control contract.
Control eligibility is causal; realised returns remain outcome labels.
"""
import json

import pandas as pd

from yoyo.evaluation.hourly_impulse_research import EXPERIMENT, ROOT, Study, clean, write_csv, write_json


def main():
    config = json.loads((EXPERIMENT/"config.json").read_text())
    output = ROOT/"analysis/output/btcusdtp_1h_impulse_ltf_exit_20260906_v1/exit_controls"
    if (output/"summary.json").exists():
        raise RuntimeError("Preserve existing diagnostic control audit")
    output.mkdir(parents=True,exist_ok=True)
    study = Study(config,"development")
    summaries = []
    for policy in config["exit_policies"]:
        name = policy["id"]
        trades = pd.read_csv(EXPERIMENT/f"results/development_exit_{name}_trades.csv.gz")
        controls,pairs,info = study.matched(trades,policy,config["baseline"])
        if "control_mean_return" in pairs:
            exact = pairs.loc[pairs["control_mean_return"].notna()]
            info["matched_event_mean_net_bp"] = exact["event_net_return"].mean()*10000
        write_csv(output/f"{name}_controls.csv.gz",controls)
        write_csv(output/f"{name}_pairs.csv",pairs)
        summaries.append({"id":name,**info})
        print(json.dumps(clean(summaries[-1])),flush=True)
    write_json(output/"summary.json",{"source":study.source_receipt,"arms":summaries,"selection_changed":False,"audit_prices_read":False})


if __name__ == "__main__":
    main()
