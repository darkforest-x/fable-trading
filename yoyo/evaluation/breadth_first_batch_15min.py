"""Read the frozen freq36_ALT entry list and compare fixed 15-minute exits.

The decision uses the original 5m lower-touch counts, lagged 28-day universe
and 24h spacing. The entry price is the next 5m open. The exit is the third
completed 5m close, known at a fixed time. OHLC prices approximate market fills.
"""
from pathlib import Path

import pandas as pd

from yoyo.evaluation.monday_breadth_opt_payoffs import (
    TAKER_FEE, _vbt_net_returns, load_full_ohlc,
)


ACTIVE = Path(__file__).resolve().parents[2] / "experiments" / "active"
SOURCE = ACTIVE / "exp-monday-breadth-extension-20261108-v1" / "alt"
OUT = ACTIVE / "exp-breadth-first-batch-15min-20261114-v1"


def run():
    members = pd.read_parquet(SOURCE / "actual_member_price_inputs.parquet")
    members = members.loc[members.epoch.eq(3)].copy()
    marks = pd.read_parquet(SOURCE / "actual_oos_path_marks.parquet",
                            filters=[("step_5m", "in", [0, 3])])
    prices = marks.pivot(index=["symbol", "entry_time"], columns="step_5m",
                         values="mark_price")
    members = members.merge(prices, left_on=["symbol", "decision"],
                            right_index=True, how="left", validate="one_to_one")
    assert members[0].eq(members.entry_open).all()
    assert members.groupby("decision").size().eq(members.groupby("decision").expected_members.first()).all()
    fills = members[["decision", "symbol", "entry_open", 3]].rename(columns={3: "exit_close"})
    fills = fills.assign(arm="initial_alts", entry_time=fills.decision)

    events = pd.DatetimeIndex(sorted(members.decision.unique()))
    assert len(events) == 25 and len(fills) == 1486
    btc, _ = load_full_ohlc("BTCUSDT", events.min() - pd.Timedelta(days=7),
                            events.max() + pd.Timedelta(minutes=15), repair_missing=False)
    arms = [fills]
    for name, days_back in (("btc", 0), ("btc_week_before", 7)):
        entry = events - pd.Timedelta(days=days_back)
        # Candles are indexed by their open. The third bar closes at t+15m.
        third_bar = entry + pd.Timedelta(minutes=10)
        arms.append(pd.DataFrame(dict(decision=events, symbol="BTCUSDT", arm=name,
                                      entry_time=entry, entry_open=btc.open.reindex(entry).to_numpy(),
                                      exit_close=btc.close.reindex(third_bar).to_numpy())))
    fills = pd.concat(arms, ignore_index=True)
    assert fills[["entry_open", "exit_close"]].gt(0).all().all()
    fills["net_pct"] = _vbt_net_returns(fills.entry_open.to_numpy(), fills.exit_close.to_numpy())
    fills["fee_per_side"] = TAKER_FEE
    event_returns = fills.pivot_table(index="decision", columns="arm", values="net_pct")
    event_returns["constituents"] = members.groupby("decision").size()
    event_returns["initial_alts_original_6h"] = members.groupby("decision").net_6h.mean()
    assert event_returns.notna().all().all()
    fills.to_csv(OUT / "fills.csv.gz", index=False)
    event_returns.to_csv(OUT / "event_returns.csv")


if __name__ == "__main__":
    run()
