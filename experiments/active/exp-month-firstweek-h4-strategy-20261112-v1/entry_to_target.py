"""Explain the archived 7d exits using the original rail. No orders are replayed."""
from pathlib import Path

import pandas as pd

from compare_fills import DEST, HERE, MAIN


def attribution():
    fills = pd.read_csv(DEST / "fills.csv", float_precision="round_trip")
    actual = fills[fills["sample"].eq("signal") & fills.group.isin(MAIN)].copy()
    levels = pd.read_csv(DEST.parent / "h4_forward_excursions/excursions.csv",
                         float_precision="round_trip")
    levels = levels.loc[levels.horizon_hours.eq(168), ["id", "rail"]]
    bars = pd.read_csv(DEST.parent / "first_reclaim_hours/landmarks.csv.gz")
    historic_winners = bars.loc[bars.hour.eq(4) & bars.status.eq("可观察")
                                & bars.outcome.eq("先有利20"), "id"]
    actual = actual.merge(levels, left_on="pair_id", right_on="id", validate="one_to_one")
    direction = actual.side.map({"short": -1, "long": 1})
    ratio = actual.entry_px / actual.rail
    distance = 100 * direction * (ratio - 1)
    rail_best = distance + ratio * actual.mfe_pct
    actual["entry_distance_pct"] = distance
    actual["rail_move_for_entry_target_pct"] = distance + 20 * ratio
    actual["from_old_winners"] = actual.pair_id.isin(historic_winners)
    actual["rail20_ahead_at_entry_and_reached_before_exit"] = (distance < 20) & (rail_best >= 20)
    result = []
    for group, rows in [("主策略", actual), *actual.groupby("group")]:
        for reason, exits in [("全部", rows), *rows.groupby("exit_reason")]:
            result.append(dict(group=group, exit_reason=reason, count=len(exits),
                               entry_distance_median_pct=exits.entry_distance_pct.median(),
                               rail_move_for_entry_target_median_pct=exits.rail_move_for_entry_target_pct.median(),
                               old_winner_count=int(exits.from_old_winners.sum()),
                               rail20_ahead_and_hit=int(exits.rail20_ahead_at_entry_and_reached_before_exit.sum())))
    return pd.DataFrame(result)


if __name__ == "__main__":
    attribution().to_csv(HERE / "entry_to_target.csv", index=False)
