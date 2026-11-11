"""Observe a single price reference in the frozen non-advancing H1 cohort.

The reference is the close of the original 12H reclaim. At hours 2, 3 and 4
use the completed H1 close, the h1..h rail state and that checkpoint's
observability. Record the first favorable cross while continuously holding.
No candidate needs to survive to hour 4 to count as an earlier signal.
Separately compare holds with and without a cross on the same H4 risk set.
"""
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "exp-month-firstweek-all-replay-20261111-v1"))
from replay import OUT, ROUNDTRIP

HELD = "收盘守住"
GOOD = "先有利20"
BAD = "先不利5"


def load_checkpoints():
    paths = pd.read_csv(OUT / "stalled_rail_paths/paths.csv.gz", **ROUNDTRIP)
    marks = pd.read_csv(OUT / "first_reclaim_hours/landmarks.csv.gz", **ROUNDTRIP)
    fields = ["id", "hour", "when", "start_close", "close", "status", "result_hour",
              "farther", "distance_pct", "move_pct"]
    joined = paths.merge(marks[fields], on=["id", "hour", "when"], validate="one_to_one")
    assert len(joined) == len(paths) == 2514 * 3
    assert joined.groupby("id").hour.agg(list).map(lambda h: h == [2, 3, 4]).all()
    return joined


def first_crosses(checkpoints):
    """All decisions use the prefix of the path at the current completed H1."""
    observable = checkpoints.status.eq("可观察")
    upper = checkpoints.side.eq("上轨")
    crossed = (upper & checkpoints.close.lt(checkpoints.start_close)) | (
        ~upper & checkpoints.close.gt(checkpoints.start_close))
    assert crossed[observable].equals(checkpoints.loc[observable, "farther"].astype(bool))
    eligible = checkpoints.path.eq(HELD)
    assert (observable[eligible] & checkpoints.loc[eligible, "when"].le(
        checkpoints.loc[eligible, "result_hour"])).all()
    confirmed = checkpoints.loc[eligible & crossed].sort_values("hour")
    return confirmed.drop_duplicates("id", keep="first")


def main():
    checkpoints = load_checkpoints()
    first = first_crosses(checkpoints)
    details = first[["id", "hour", "when", "close", "start_close", "distance_pct", "move_pct"]]
    details = details.rename(columns={k: "confirmed_" + k for k in details if k != "id"})
    final = checkpoints.loc[checkpoints.hour.eq(4), ["id", "side", "outcome", "month", "path", "h1_state"]]
    cases = final.merge(details, how="left", on="id", validate="one_to_one")
    assert cases.outcome.value_counts().to_dict() == {GOOD: 660, BAD: 1854}
    result = []
    for (side, outcome), group in cases.groupby(["side", "outcome"]):
        matched = group.loc[group.confirmed_hour.notna()]
        held_at_four = group.loc[group.path.eq(HELD)]
        result.append({
            "轨道": side, "历史结局": outcome, "第一小时未推进": len(group),
            **{f"第{h}根首次越过12H收盘": int(matched.confirmed_hour.eq(h).sum()) for h in (2, 3, 4)},
            "截至第四根累计出现": len(matched), "原组未出现": len(group) - len(matched),
            "确认时距轨中位数_pct": matched.confirmed_distance_pct.median(),
            "确认时较12H收盘位移中位数_pct": matched.confirmed_move_pct.median(),
            "截至第四根仍守轨": len(held_at_four),
            "截至第四根仍守轨且曾越过": int(held_at_four.confirmed_hour.notna().sum()),
        })
    results = pd.DataFrame(result)
    destination = OUT / "stalled_restart"
    destination.mkdir(parents=True, exist_ok=True)
    cases.to_csv(destination / "cases.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    results.to_csv(HERE / "counts.csv", index=False, encoding="utf-8-sig")
    print(results.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
