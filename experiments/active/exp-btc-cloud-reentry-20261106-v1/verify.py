"""Check the eligible entry set against an independent UTC/NY join and audit native fills."""

import json
import numpy as np
import pandas as pd
from run import HERE, CONFIG, NY, eval_weeks, load_bars, save_json

bars, _ = load_bars()
five = pd.Timedelta(minutes=5)
delta = (
    bars.close.ewm(span=20, adjust=False).mean()
    - bars.close.ewm(span=50, adjust=False).mean()
)
cross = delta.gt(0) & delta.shift().le(0)
twelve = bars.close.resample("12h").agg(["last", "count"]).query("count == 144")["last"]
twelve.index += pd.Timedelta(hours=12)
bull = (
    twelve.ewm(span=20, adjust=False).mean() > twelve.ewm(span=50, adjust=False).mean()
).reindex(bars.index, method="ffill")
candidate = pd.read_csv(
    HERE / "candidates.csv.gz",
    parse_dates=[
        "entry_time",
        "h1_close_time",
        "bias_12h_close_time",
        "signal_bar_open",
        "first_reclaim_close_time",
    ],
)
base = candidate[candidate.setup_armed]
expected = []
for wk in eval_weeks():
    m = pd.Timestamp(wk, tz=NY)
    tue = m + pd.DateOffset(days=1)
    end = m + pd.DateOffset(days=7)
    mon = bars.loc[m : tue - five]
    assert len(mon) == 288
    level = mon.low.min() - 0.618 * (mon.high.max() - mon.low.min())
    own = bars.loc[tue : end - five]
    reclaim = own.index[(own.low < level) & (own.close > level)]
    if len(reclaim):
        source = own.index[(own.index > reclaim[0]) & (own.index + five < end)]
        expected.extend(
            source[(cross.loc[source] & bull.loc[source]).to_numpy()] + five
        )
assert set(expected) == set(base.entry_time) and len(expected) == len(base)
assert (base.h1_close_time <= base.signal_bar_open).all() and (
    base.bias_12h_close_time <= base.signal_bar_open
).all()
assert (base.first_reclaim_close_time <= base.signal_bar_open).all()

n = pd.read_csv(
    HERE / "native_fills.csv.gz",
    parse_dates=["entry_time", "scheduled_exit_time", "exit_bar_open_time"],
)
entrybars = bars.loc[n.entry_time]
np.testing.assert_allclose(n.entry_price, entrybars.open, atol=1e-7, rtol=0)
exitbars = bars.loc[n.exit_bar_open_time]
atopen = n.exit_time_resolution.eq("5m_open").to_numpy()
stop = n.exit_reason.eq("hard_stop").to_numpy()
np.testing.assert_allclose(
    n.exit_price[atopen], exitbars.open.to_numpy()[atopen], atol=1e-7, rtol=0
)
np.testing.assert_allclose(
    n.exit_price[stop & ~atopen], n.initial_stop[stop & ~atopen], atol=1e-7, rtol=0
)
assert (exitbars.low.to_numpy()[stop] <= n.initial_stop[stop] + 1e-7).all()
assert (n.exit_bar_open_time >= n.entry_time).all() and (
    n.exit_bar_open_time <= n.scheduled_exit_time
).all()
assert (n.exit_bar_open_time[~stop] == n.scheduled_exit_time[~stop]).all()
np.testing.assert_allclose(
    n.entry_fee, n.qty * n.entry_price * CONFIG["fees"]["taker"], atol=1e-7, rtol=0
)
np.testing.assert_allclose(
    n.exit_fee, n.qty * n.exit_price * CONFIG["fees"]["taker"], atol=1e-7, rtol=0
)
ledger = pd.read_csv(HERE / "trades.csv.gz")
arms = json.loads((HERE / "summary.json").read_text())["arms"]
for policy, mask in [
    ("all_flips", pd.Series(True, index=ledger.index)),
    ("bottom", ledger.bottom_pass),
    ("trend", ledger.trend_pass),
    ("trend_hold", ledger.trend_hold_pass),
]:
    part = ledger[mask]
    assert arms[policy]["trades"] == len(part)
    np.testing.assert_allclose(arms[policy]["net_usdt"], part.net_usdt.sum(), atol=1e-7)
    assert arms[policy]["missed_large_waves"] == int(
        (~mask & ledger.wave_r.ge(5)).sum()
    )
    assert arms[policy]["avoided_stopouts"] == int(
        (~mask & ledger.exit_reason.eq("hard_stop")).sum()
    )
save_json(
    "validation.json",
    {
        "eligible_long_opens_independently_reconstructed": len(expected),
        "native_round_trips_checked": len(n),
        "fee_fills_checked": 2 * len(n),
        "intrabar_stops_in_matching_ohlc": int((stop & ~atopen).sum()),
        "opening_stops_checked": int((stop & atopen).sum()),
        "scheduled_open_exits_checked": int((~stop).sum()),
        "ineligible_illustration_weeks": CONFIG["exclude_ny_weeks"],
        "primary_arm_net_stops_missed_waves_reconciled": True,
        "metric_scope": "Discrete rules, no fitted prediction scores; model AUC and deciles do not apply.",
    },
)
print(
    "Independent entry set, native prices, both fees and missed-wave reconciliation passed."
)
