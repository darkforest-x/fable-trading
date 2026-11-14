"""BTC/ETH minute sweeps of the preceding hour, using completed OHLC only.

Use the prior 60 highs/lows for the range and 60 prior absolute close returns
for control matching. The signal minute must close strictly inside both bounds.
Enter at the next open. Both 5m and 15m exits use the same accepted entries.
"""
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/active/exp-intraday-hour-sweep-20261114-v1"
SOURCES = {
    "BTC": "data/kline_deep_1m/okx_BTC_USDT_SWAP_1m_1225440.csv",
    "ETH": "data/crypto/market/kline_lowtf_20260922/okx_ETH_USDT_SWAP_1m_1402560.csv",
}
STEP = 60000
FEE = 0.0005


def minute_opportunities(symbol, source):
    bars = pd.read_csv(ROOT / source, usecols=["ts", "open", "high", "low", "close"]).set_index("ts")
    begin = pd.Timestamp("2024-01-01", tz="Asia/Shanghai").value // 10**6
    end = pd.Timestamp("2026-05-01", tz="Asia/Shanghai").value // 10**6
    bars = bars.reindex(pd.RangeIndex(begin, end, STEP))
    high = bars.high.shift(1).rolling(60).max()
    low = bars.low.shift(1).rolling(60).min()
    inside = bars.close.gt(low) & bars.close.lt(high)
    down, up = bars.low.lt(low), bars.high.gt(high)
    side = (inside & down & ~up).astype(int) - (inside & up & ~down).astype(int)
    vol = bars.close.pct_change(fill_method=None).abs().shift(1).rolling(60).mean() * 10000
    rows = pd.DataFrame(dict(symbol=symbol, side=side, vol=vol, entry=bars.open.shift(-1),
                             exit5=bars.open.shift(-6), exit15=bars.open.shift(-16)))
    # Keep only complete one-minute tapes across the fixed evaluation horizon.
    rows = rows.loc[bars.notna().all(axis=1).rolling(17).min().shift(-16).eq(1)].dropna()
    rows.index = rows.index + STEP
    rows.index.name = "entry_ms"
    rows["block"] = rows.index // (240 * STEP)
    rows["vol_bin"] = np.digitize(rows.vol, [2., 5., 10., 20.])
    return rows


def select(rows):
    signals = rows.loc[rows.side.ne(0)].reset_index().sort_values(["entry_ms", "symbol"])
    times = signals.entry_ms.to_numpy()
    keep = []
    i = 0
    while i < len(signals):
        keep.append(i)
        i = np.searchsorted(times, times[i] + 15 * STEP)
    actual = signals.iloc[keep].copy()
    actual["event"] = actual.entry_ms
    actual["arm"] = "signal"
    controls = rows.loc[rows.side.eq(0)].reset_index()
    groups = controls.groupby(["symbol", "block", "vol_bin"]).indices
    rng = np.random.default_rng(20261114)
    matched = []
    for row in actual.itertuples():
        indices = groups.get((row.symbol, row.block, row.vol_bin))
        if indices is not None:
            matched.append((rng.choice(indices), row.event, row.side))
    indices, events, sides = zip(*matched)
    control = controls.iloc[list(indices)].assign(event=events, side=sides, arm="control")
    return pd.concat([actual, control], ignore_index=True)


def run():
    minutes = pd.concat([minute_opportunities(s, f) for s, f in SOURCES.items()])
    rows = select(minutes)
    legs = pd.concat([rows.assign(hold=h, exit_price=rows[f"exit{h}"]) for h in (5, 15)], ignore_index=True)
    prices = np.stack([legs.entry, legs.exit_price])
    sizes = np.stack([legs.side, -legs.side])
    portfolio = vbt.Portfolio.from_orders(prices, price=prices, size=sizes, fees=FEE,
                                         init_cash=float(prices.max() * 3), cash_sharing=False)
    trades = portfolio.trades.records.sort_values("col")
    assert np.array_equal(trades.col, np.arange(len(legs))) and trades.status.eq(1).all()
    assert np.allclose(trades.entry_fees + trades.exit_fees, FEE * (legs.entry + legs.exit_price))
    assert np.allclose(trades.pnl, legs.side * (legs.exit_price - legs.entry) - FEE * (legs.entry + legs.exit_price))
    legs["net_pct"] = 100 * trades.pnl.to_numpy() / legs.entry
    legs["fee_pct"] = 100 * (trades.entry_fees + trades.exit_fees).to_numpy() / legs.entry
    legs["day"] = pd.to_datetime(legs.event, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d")
    legs[["day", "event", "entry_ms", "arm", "symbol", "side", "hold", "entry", "exit_price",
          "fee_pct", "net_pct"]].to_csv(OUT / "trades.csv.gz", index=False)
    summary = legs.groupby(["arm", "hold", legs.day.str[:4].rename("year")]).net_pct.agg(
        trades="size", mean_net_pct="mean", median_net_pct="median", positive_fraction=lambda a: a.gt(0).mean())
    summary.to_csv(OUT / "summary.csv")


if __name__ == "__main__":
    run()
