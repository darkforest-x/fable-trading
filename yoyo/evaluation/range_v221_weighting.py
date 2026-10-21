"""Read-only weighting comparison on completed version/exit ledgers.

No entry or exit is replayed. Equal original-risk PnL weights each trade by
inverse initial stop distance; equal entry notional weights price returns
equally. Preserve both, including same-weight matched random controls.
"""
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT,dump,file_stamp
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.pepe_range_exits import inference,pairs

DEFAULT=ROOT/"experiments/active/exp-range-v221-compare-20261021-v1"


def weighting(rows,seed):
    """Original price/risk metadata and realized outcomes only; no new feature."""
    expected=rows.net_r*rows.risk/rows.entry*10000
    np.testing.assert_allclose(rows.net_notional_bp,expected,rtol=0,atol=1e-7)
    signal=rows[rows.population=="signal"].copy()
    random=rows[rows.population=="random"].copy()
    result=dict(n=len(signal),net_r=float(signal.net_r.sum()),random_net_r=float(random.net_r.sum()),
        wins=int((signal.net_r>1e-9).sum()),mean_net_bp=float(signal.net_notional_bp.mean()),
        random_mean_net_bp=float(random.net_notional_bp.mean()),
        median_risk_percent=float((signal.risk/signal.entry*100).median()),
        mean_cost_r=float(signal.fee_r.mean()))
    signal["net_r"]=signal.net_notional_bp
    random["net_r"]=random.net_notional_bp
    estimate=inference(pairs(signal,random),seed)
    result.update(bp_excess_sum=estimate["excess_r"],bp_excess_p=estimate["p_one_sided"],
        bp_excess_weekly_bootstrap_95ci=estimate["weekly_bootstrap_total_r_95ci"])
    return result


def main():
    source=Path(__file__).resolve()
    if subprocess.run(["git","diff","--quiet","HEAD","--",str(source)],cwd=ROOT).returncode:
        raise RuntimeError("Commit the weighting builder first")
    cfg=json.loads((DEFAULT/"config.json").read_text())
    result=[];inputs=[]
    prior=json.loads((DEFAULT/"summary.json").read_text())
    for r in prior:
        if r["group"]!="all":
            continue
        variant,symbol,arm=r["variant"],r["symbol"],r["arm"]
        if variant=="v230":
            path=(ROOT/cfg["source"]/symbol/"native/pivot/trades.csv" if arm=="pivot" else
                  ROOT/cfg["exit_reference"]/symbol/arm/"trades.csv")
        else:
            path=(DEFAULT/variant/symbol/"native/pivot/trades.csv" if arm=="pivot" else
                  DEFAULT/variant/"exits"/symbol/arm/"trades.csv")
        data=pd.read_csv(path,float_precision="round_trip")
        values=weighting(data,cfg["seed"])
        np.testing.assert_allclose(values["net_r"],r["net_r"],rtol=0,atol=1e-8)
        inputs.append(file_stamp(path))
        result.append(dict(variant=variant,symbol=symbol,arm=arm,**values))
    for symbol in (s["symbol"] for s in cfg["symbols"]):
        subset=[r for r in result if r["symbol"]==symbol]
        for r,pvalue in zip(subset,holm([r["bp_excess_p"] for r in subset])):
            r["bp_excess_holm_p"]=float(pvalue)
    dump(DEFAULT/"weighting.json",dict(rows=result,inputs=inputs,
        source_commit=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
        code=file_stamp(source),scope="Read-only aggregation; original executions unchanged"))


if __name__=="__main__":
    main()
