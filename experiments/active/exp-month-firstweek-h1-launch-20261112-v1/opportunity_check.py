"""Count a skipped entry as zero on its own matched clock, including failed launches.

A surviving terminal position has no realized outcome; exclude the entire pair
when either side is marked at cutoff. Contrasts with trade_summary's paired
comparison conditional on both sides actually entering.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/"exp-month-firstweek-h4-strategy-20261112-v1"))
from compare_fills import DEST as BASE, bootstrap

DATA = BASE.parent/"h1_launch"


def check():
    opportunities = pd.read_csv(DATA/"opportunities.csv")
    opportunities = opportunities[opportunities["sample"].isin(["signal","random"])].copy()
    fills = pd.read_csv(DATA/"fills.csv")
    assert not opportunities.duplicated(["pair_id","sample"]).any()
    opportunities = opportunities.merge(fills[["trade_id","net_pct","exit_reason"]],on="trade_id",
                                       how="left",validate="one_to_one")
    assert opportunities.net_pct.notna().eq(opportunities.eligible).all()
    for sample, group in fills.groupby("sample"):
        sign = np.where(group.side.eq("long"),1,-1)
        gross = sign * (group.exit_px/group.entry_px - 1)
        np.testing.assert_allclose(group.net_pct, 100*(gross-group.fee_u/(group.qty*group.entry_px)),
                                   rtol=0,atol=1e-8)
        fee = np.where(group.exit_reason.eq("target"),.0002,.0005)
        np.testing.assert_allclose(group.exit_fee_u,group.qty*group.exit_px*fee,rtol=1e-10)
    exclude = set(opportunities.loc[opportunities.exit_reason.eq("cutoff_mark"),"pair_id"])
    closed = opportunities[~opportunities.pair_id.isin(exclude)].copy()
    closed["return_pct"] = closed.net_pct.fillna(0)
    pairs = closed[closed["sample"].eq("signal")].merge(closed[closed["sample"].eq("random")],
                on="pair_id",suffixes=("_s","_r"),validate="one_to_one")
    rows = []
    for side, records in [("all",pairs),*pairs.groupby("side_s")]:
        delta = records.return_pct_s - records.return_pct_r
        lo, hi = bootstrap(delta,records.source_month_s)
        rows.append(dict(side=side,pairs=len(records),signal_mean_pct=records.return_pct_s.mean(),
                         random_mean_pct=records.return_pct_r.mean(),delta_pp=delta.mean(),
                         month_ci_low=lo,month_ci_high=hi))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    results = check()
    results.to_csv(HERE/"opportunity_comparison.csv",index=False)
    print(results.round(3).to_string(index=False))
