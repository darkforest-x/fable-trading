"""BTC 5m taker-pressure disagreement. Read the closed candle's open, close,
quote volume and taker buy/sell quote volume. Use only the preceding 288 fully
closed 5m candles for the 90th percentile of absolute flow imbalance and the
preceding twelve absolute returns for matching. No calendar or alarm filter.
"""
import numpy as np
import pandas as pd

from yoyo.evaluation.intraday_hour_sweep import ROOT, STEP, select, settle


def run():
    source = ROOT / "data/genuine_flow_coverage_v34/bars"
    files = sorted(source.glob("BTCUSDT-5m-*-flow.csv"))
    assert len(files) == 24
    columns = ["ts", "open", "close", "taker_buy_quote_volume", "taker_sell_quote_volume", "quote_volume"]
    bars = pd.concat([pd.read_csv(path, usecols=columns) for path in files]).set_index("ts").sort_index()
    assert len(bars) == 210528 and bars.index.to_series().diff().iloc[1:].eq(5 * STEP).all()
    flow = (bars.taker_buy_quote_volume - bars.taker_sell_quote_volume).div(bars.quote_volume.where(bars.quote_volume.gt(0)))
    # Zero-volume bars have no directional imbalance; they may warm the threshold but never trigger.
    assert flow[bars.quote_volume.gt(0)].notna().all()
    flow = flow.fillna(0)
    threshold = flow.abs().shift(1).rolling(288).quantile(.90)
    price_side = np.sign(bars.close - bars.open)
    signal = flow.abs().gt(threshold) & (price_side * flow).lt(0)
    vol = bars.close.pct_change(fill_method=None).abs().shift(1).rolling(12).mean() * 10000
    rows = pd.DataFrame(dict(symbol="BTC", side=price_side.where(signal, 0), vol=vol,
                             entry=bars.open.shift(-1), exit5=bars.open.shift(-2),
                             exit15=bars.open.shift(-4))).loc[threshold.notna()].dropna()
    rows.index = rows.index + 5 * STEP
    rows.index.name = "entry_ms"
    rows["block"] = rows.index // (240 * STEP)
    rows["vol_bin"] = np.digitize(rows.vol, [2., 5., 10., 20.])
    out = ROOT / "experiments/active/exp-intraday-taker-absorption-20261114-v1"
    settle(select(rows), out)


if __name__ == "__main__":
    run()
