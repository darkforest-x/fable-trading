"""Post-run attribution of H1 fills by the archived touching 12H candle.

The existing replay card marks a long wick when the full body is inside the
rail and the rail-side wick is at least half the 12H range. Both inputs end at
the touching 12H close. Apply the signal's label to its matched random time
only as a historical paired control; never use a future outcome as a label.
"""
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/"exp-month-firstweek-h4-strategy-20261112-v1"))
from compare_fills import DEST as BASE, bootstrap, intervals


def attribution():
    fills = pd.read_csv(BASE.parent/"h1_launch/fills.csv")
    labels = pd.read_csv(BASE.parent/"group_first_watch.csv.gz",usecols=["id","first_close_state"])
    fills = fills.merge(labels.rename(columns={"id":"pair_id"}),on="pair_id",validate="many_to_one")
    assert set(fills.first_close_state)=={"实体在线内且长影","收回"}
    closed = fills[fills.exit_reason.ne("cutoff_mark")]
    result = []
    for period, data in intervals(closed):
        for (side, shape), group in data.groupby(["side","first_close_state"]):
            signal = group[group["sample"].eq("signal")]
            control = group[group["sample"].eq("random")]
            pairs = signal.merge(control,on="pair_id",suffixes=("_s","_r"),validate="one_to_one")
            net = signal.net_pct
            beijing_day = pd.to_datetime(signal.entry_ms,unit="ms",utc=True).dt.tz_convert("Asia/Shanghai").dt.date
            daily = net.groupby(beijing_day).mean()
            strongest_month = net.groupby(signal.source_month).sum().idxmax()
            boot_low, boot_high = bootstrap(net,signal.source_month)
            difference = pairs.net_pct_s-pairs.net_pct_r
            lower, upper = bootstrap(difference,pairs.source_month_s)
            result.append(dict(period=period, side=side, shape=shape, settled=len(net),
                               mean_net_pct=net.mean(),win_pct=100*net.gt(0).mean(),
                               entry_days=len(daily),equal_entry_day_mean_pct=daily.mean(),
                               top_total_month=strongest_month,
                               mean_ex_top_month_pct=net[signal.source_month.ne(strongest_month)].mean(),
                               pf=net[net>0].sum()/-net[net<0].sum(),
                               mean_month_ci_low=boot_low, mean_month_ci_high=boot_high,
                               pairs=len(pairs),common_signal_pct=pairs.net_pct_s.mean(),
                               common_random_pct=pairs.net_pct_r.mean(),delta_pp=difference.mean(),
                               delta_month_ci_low=lower,delta_month_ci_high=upper))
    return pd.DataFrame(result)


if __name__=="__main__":
    table = attribution()
    table.to_csv(HERE/"wick_attribution.csv",index=False)
    print(table[table.period=="全期"].round(3).to_string(index=False))
