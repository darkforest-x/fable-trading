"""BTC/ETH gross price moves after frozen 25-coin Monday first-touch alarms.

Anchor = close of the triggering 5m candle. Future closes and subsequent lows/highs
are labels only. Context uses closes up to the anchor: hourly realized volatility
is 100 * sqrt(sum of the last 12 squared five-minute log returns).
"""
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_15m_volume_rejection import load
from yoyo.evaluation.range_framework import ROOT


SOURCE = ROOT / "experiments/active/exp-monday-limit-alarm-entry-20261029-v1"
OUT = ROOT / "experiments/active/exp-alarm-btc-eth-price-response-20261107-v1"
MARKET = ROOT / "data/crypto/market"
STEP = pd.Timedelta(minutes=5)
HORIZONS = (5, 15, 30, 60, 120, 360, 1440)
SEED = 20261107


def frozen_alarms():
    alarms = pd.read_csv(SOURCE / "alarms.csv.gz").sort_values("ms")
    windows = pd.read_csv(SOURCE / "alarm_windows.csv.gz")
    later = windows.query("threshold == 25 and window == 30")[["alarm_ms", "side"]].drop_duplicates()
    alarms = alarms.merge(later.rename(columns={"alarm_ms": "ms"}).assign(later_touch=True),
                          how="left", on=["ms", "side"], validate="one_to_one")
    alarms["later_touch"] = alarms.later_touch.notna()
    if len(alarms) != 129 or alarms.later_touch.sum() != 96 or not alarms.threshold.eq(25).all():
        raise ValueError("Unexpected frozen alarm population")
    alarms["known_utc"] = pd.to_datetime(alarms.ms, unit="ms", utc=True) + STEP
    return alarms


def event_rows(asset, alarms):
    symbol = asset + "USDT"
    frame = load([MARKET / f"kline_preholdout_binance_um5m/series/binance_um_{symbol}_5m_665856.csv",
                  MARKET / f"kline_binance_um5m_forward_20260922/series/{symbol}.csv"])
    # Label every row by the moment its close is known, then preserve gaps on the clock.
    frame.index += STEP
    frame = frame.asfreq("5min")
    close = frame.close
    vol = np.log(close).diff().pow(2).rolling(12).sum().pow(0.5) * 100
    bucket = np.digitize(vol, [0.25, 0.5, 1, 2, 4])
    month = frame.index.year * 12 + frame.index.month
    session = frame.index.hour // 6
    complete = close.notna().iloc[::-1].rolling(289).sum().iloc[::-1].eq(289)
    eligible = complete.to_numpy() & vol.notna().to_numpy()
    locations = frame.index.get_indexer(alarms.known_utc)
    if np.any(locations < 12) or not complete.iloc[locations].all():
        raise ValueError(f"Missing full alarm path for {asset}")
    for loc in locations:
        eligible[max(0, loc - 288):loc + 289] = False
    prices = close.to_numpy()
    lows, highs = frame.low.to_numpy(), frame.high.to_numpy()
    rows = []
    for loc, alarm in zip(locations, alarms.itertuples()):
        # Same instrument, UTC month, six-hour time-of-day band and realized-vol bucket.
        pool = np.flatnonzero(eligible & (month == month[loc]) & (session == session[loc]) &
                              (bucket == bucket[loc]))
        rng = np.random.default_rng([SEED, int(alarm.ms // 300000), int(asset == "ETH")])
        controls = rng.choice(pool, size=min(24, len(pool)), replace=False)
        anchor = prices[loc]
        row = dict(asset=asset, known_utc=alarm.known_utc, side=int(alarm.side),
                   later_touch=alarm.later_touch, reference_close=anchor, controls=len(controls),
                   pre_5_pct=(anchor / prices[loc-1] - 1) * 100,
                   pre_60_pct=(anchor / prices[loc-12] - 1) * 100,
                   low_60_pct=(lows[loc+1:loc+13].min() / anchor - 1) * 100,
                   high_60_pct=(highs[loc+1:loc+13].max() / anchor - 1) * 100)
        for minutes in HORIZONS:
            ahead = minutes // 5
            row[f"move_{minutes}"] = (prices[loc + ahead] / anchor - 1) * 100
            row[f"ctrl_{minutes}"] = (np.mean(prices[controls + ahead] / prices[controls] - 1) * 100
                                     if len(controls) else np.nan)
        rows.append(row)
    return rows


def summaries(events):
    records = []
    for asset, data in events.groupby("asset"):
        for scope, cohort in (("all", data), ("later_30m", data[data.later_touch]),
                              ("no_later_30m", data[~data.later_touch])):
            for side, group in (("both", cohort), ("lower", cohort[cohort.side == 1]),
                                ("upper", cohort[cohort.side == -1])):
                if group.empty:
                    continue
                for m in HORIZONS:
                    change = group[f"move_{m}"]
                    ctrl = group[f"ctrl_{m}"]
                    paired = change[ctrl.notna()]
                    records.append(dict(asset=asset, scope=scope, side=side, minutes=m, n=len(change),
                                        mean_pct=change.mean(), median_pct=change.median(),
                                        up_pct=100 * change.gt(0).mean(), down_pct=100 * change.lt(0).mean(),
                                        p25_pct=change.quantile(.25), p75_pct=change.quantile(.75),
                                        paired_n=len(paired), control_mean_pct=ctrl.mean(),
                                        paired_delta_pp=(paired - ctrl).mean()))
    return pd.DataFrame(records)


def main():
    alarms = frozen_alarms()
    events = pd.DataFrame([row for asset in ("BTC", "ETH") for row in event_rows(asset, alarms)])
    OUT.mkdir(exist_ok=True)
    events.to_csv(OUT / "events.csv", index=False)
    summaries(events).to_csv(OUT / "summary.csv", index=False)


if __name__ == "__main__":
    main()
