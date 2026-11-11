"""Compare visibility of frozen observations before the original +20/-5 descriptive outcome.

The old cohort has one Binance first touch per contract, NY month and side. A
stage is on time if its 12h close is available by the OPEN of the first decisive
hour. Historical barrier labels are used only to evaluate already dated stages.
Counts on the positive subset measure retention, not trade win rates.
"""
from pathlib import Path

import numpy as np
import pandas as pd

from replay import OUT, HERE, HOUR, KEY, ROUNDTRIP
from census12 import OUT as OLDER


def utc_ms(local):
    timestamps = pd.to_datetime(local).dt.tz_localize("Asia/Shanghai")
    return timestamps.map(lambda t: int(t.value // 1_000_000) if pd.notna(t) else np.nan)


def match():
    archive = pd.read_csv(OLDER / "首次触价池_45天内20pct先于5pct止损.csv", **ROUNDTRIP)
    archive = archive.rename(columns={"币安合约": "symbol", "纽约区间月": "month"})
    archive["side"] = archive["触价轨道（下轨做多／上轨做空）"].map({"上轨": "short", "下轨": "long"})
    archive["result_hour"] = utc_ms(archive["确认结果所在小时（北京）"])
    archive["touch_ms"] = utc_ms(archive["触价小时（北京）"])
    first = pd.read_csv(OUT / "group_first_watch.csv.gz", **ROUNDTRIP)
    second = pd.read_csv(OUT / "two_reclaims_followup.csv.gz", **ROUNDTRIP)
    cases = archive.merge(first, on=KEY + ["touch_ms"], validate="one_to_one")
    cases = cases.merge(second, on=KEY, how="left", validate="one_to_one")
    assert len(cases) == len(archive) == 11776
    assert cases["判定"].eq("确认TP先于SL").equals(cases["首触2033"])
    return cases


def count_stages(cases, period):
    first_inside = cases.first_close_state.isin(["收回", "实体在线内且长影"])
    confirming = cases["当月后续"].eq("推进")
    confirmation_time = cases.time_ms + cases["二试后首个结果小时数"] * HOUR
    stages = {
        "首碰12H收回": cases.scheduled_close_ms.where(first_inside),
        "首碰12H实体在线内且长影": cases.scheduled_close_ms.where(cases.first_close_state.eq("实体在线内且长影")),
        "二试12H收回": cases.time_ms,
        "二试后12H突破": confirmation_time.where(confirming),
    }
    favorable = cases["判定"].eq("确认TP先于SL")
    adverse = cases["判定"].eq("确认SL先于TP")
    out = []
    for name, when in stages.items():
        timely = when.le(cases.result_hour)
        captured = int((timely & favorable).sum())
        out.append(dict(时期=period, 观察条件=name, 首触池=len(cases), 目标样本=int(favorable.sum()),
                        出现该条件=int(when.notna().sum()), 达标前可见=captured,
                        达标前覆盖率=captured / favorable.sum(),
                        达标后才可见=int((favorable & when.gt(cases.result_hour)).sum()),
                        目标样本中未出现=int((favorable & when.isna()).sum()),
                        观察后先不利5=int((timely & adverse).sum())))
    return out


if __name__ == "__main__":
    cases = match()
    out = count_stages(cases, "全部")
    for mask, label in [(cases.month < "2025-01", "2025年以前"), (cases.month >= "2025-01", "2025年起")]:
        out.extend(count_stages(cases[mask], label))
    pd.DataFrame(out).to_csv(HERE / "direction_coverage.csv", index=False, encoding="utf-8-sig")
    print(pd.DataFrame(out).round(3).to_string(index=False))
