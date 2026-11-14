"""Single-alt breakout on complete 5m bars, confirmed by the next close.

Read current BTC 5m body plus the preceding 288 absolute BTC bodies; read
the alt's preceding 12 highs/lows/volumes and 288 absolute body returns.
The first post-breakout bar must retest and close beyond the old range edge.
All entries are at the next bar open after the retest has finished.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation.intraday_hour_sweep import ROOT, STEP, select, settle

SOURCE = ROOT / "data/research/ma6_knot_1m_20261011/series"


def bars(symbol):
    path = SOURCE / f"binance_um_{symbol}USDT_1m_2059200.csv"
    frame = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"]).set_index("ts")
    assert frame.index.is_unique and frame.index.to_series().diff().iloc[1:].eq(STEP).all()
    out = frame.groupby(frame.index // (5 * STEP)).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    assert len(out) * 5 == len(frame)
    out.index *= 5 * STEP
    return out


def opportunities(symbol, calm):
    b = bars(symbol)
    high, low = b.high.shift(1).rolling(12).max(), b.low.shift(1).rolling(12).min()
    move = (b.close / b.open - 1).abs()
    usual_move = move.shift(1).rolling(288).median()
    usual_volume = b.volume.shift(1).rolling(12).median()
    active = calm & move.gt(usual_move) & b.volume.ge(2 * usual_volume)
    long_break = active & b.open.le(high) & b.close.gt(high)
    short_break = active & b.open.ge(low) & b.close.lt(low)
    long = long_break.shift(1, fill_value=False) & b.low.le(high.shift(1)) & b.close.gt(high.shift(1))
    short = short_break.shift(1, fill_value=False) & b.high.ge(low.shift(1)) & b.close.lt(low.shift(1))
    side = long.astype(int) - short.astype(int)
    vol = b.close.pct_change(fill_method=None).abs().shift(1).rolling(12).mean() * 10000
    rows = pd.DataFrame(dict(symbol=symbol, side=side, vol=vol, entry=b.open.shift(-1),
                             exit5=b.open.shift(-2), exit15=b.open.shift(-4))).loc[usual_move.notna()].dropna()
    rows.index = rows.index + 5 * STEP
    rows.index.name = "entry_ms"
    rows["block"] = rows.index // (240 * STEP)
    rows["vol_bin"] = np.digitize(rows.vol, [2., 5., 10., 20.])
    return rows.loc[rows.index >= pd.Timestamp("2023-01-01", tz="Asia/Shanghai").value // 10**6]


def run():
    btc = bars("BTC")
    move = (btc.close / btc.open - 1).abs()
    calm = move.le(move.shift(1).rolling(288).median())
    rows = pd.concat([opportunities(symbol, calm) for symbol in ("BNB", "DOGE", "SOL", "XRP")])
    settle(select(rows), ROOT / "experiments/active/exp-intraday-relative-break-20261114-v1")


if __name__ == "__main__":
    run()
