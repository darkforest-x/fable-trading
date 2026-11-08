"""Tag a frozen subset of original cloud signals with ChartPrime's pre-touch 12H FVG.

Only OHLC preceding the first weekly touch and the middle candle's volume feed
the descriptors. Original 12H confirmation and path labels are read separately.
Run with python -m yoyo.evaluation.cloud_fvg_pilot.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pandas as pd

from yoyo.evaluation.cloud_allmarket_screen import load_signals
from yoyo.evaluation.cloud_peak10_inputs import HOUR, ROOT, dump
from yoyo.monitor.monday_fib import week_window

EXP = ROOT / "experiments/active/exp-cloud-fvg-pilot-20261108-v1"
CONFIG = json.loads((EXP / "config.json").read_text())
SOURCE = EXP.parent / CONFIG["source"]
STEP = 12 * HOUR


@dataclass(frozen=True)
class Zone:
    side: int
    low: float
    high: float
    middle: int

    def survived(self, low, high):
        return low >= self.low if self.side == 1 else high <= self.high


def snapshots(bars):
    """At every 12H open, snapshot boxes using only earlier 12H high/low."""
    assert bars.index.to_series().diff().dropna().eq(STEP).all()
    low, high = bars.low, bars.high
    bull_size, bear_size = low - high.shift(2), low.shift(2) - high
    bull_sd = bull_size.rolling(200).std(ddof=0)
    bear_sd = bear_size.rolling(200).std(ddof=0)
    bull = (high.shift() > high.shift(2)) & (bull_sd > 0) & (bull_size > 0.5 * bull_sd)
    bear = (low.shift() < low.shift(2)) & (bear_sd > 0) & (bear_size > 0.5 * bear_sd)
    active, previous = [], {}
    for i, (stamp, candle) in enumerate(bars.iterrows()):
        previous[stamp] = tuple(active) if i >= 202 else None
        if bull.iat[i]:
            active.append(Zone(1, high.iat[i-2], candle.low, stamp - STEP))
        if bear.iat[i]:
            active.append(Zone(-1, candle.high, low.iat[i-2], stamp - STEP))
        active = [z for z in active[-10:] if z.survived(candle.low, candle.high)]
    return previous


def first_touch(row, hourly, states):
    """From the NY Monday close, use 1H H/L for first contact and only pre-contact extrema for survival."""
    key = int(pd.Timestamp(row.key_open_bjt).timestamp() * 1000)
    window = week_window(key)
    assert window["week"] == row.week and window["end"] <= key
    hours = hourly.loc[window["end"]:key + STEP - HOUR]
    assert len(hours) == (key + STEP - window["end"]) // HOUR
    line, side = row.reference_entry, row.side
    reached = hours.low.le(line) if side == 1 else hours.high.ge(line)
    if not reached.any():
        return {"tag": "no_hourly_touch"}
    stamp = reached[reached].index[0]
    bar = hours.loc[stamp]
    start = stamp // STEP * STEP
    result = {"touch_hour_utc": pd.to_datetime(stamp, unit="ms", utc=True).isoformat(),
              "touch_in_key_bar": bool(start == key)}
    if (side == 1 and bar.open < line) or (side == -1 and bar.open > line):
        return dict(result, tag="opened_beyond_line")
    if states[start] is None:
        return dict(result, tag="warmup")
    prior = hourly.loc[start:stamp-HOUR]
    low = min(bar.open, prior.low.min()) if len(prior) else bar.open
    high = max(bar.open, prior.high.max()) if len(prior) else bar.open
    zones = [z for z in states[start] if z.side == side and z.low <= line <= z.high
             and z.survived(low, high)]
    if not zones:
        return dict(result, tag="outside")
    zone = zones[-1]
    risk = abs(line - row.reference_stop)
    assert zone.middle + 2 * STEP <= start and risk > 0
    return dict(result, tag="overlap", zones=len(zones), middle_ms=int(zone.middle),
                zone_low=zone.low, zone_high=zone.high, risk=risk,
                midpoint_r=abs(line - (zone.low + zone.high) / 2) / risk)


def metrics(frame):
    out = {"signals": len(frame)}
    for name, column in [("early", "early_fixed_stop_2bars"), ("chart", "status"),
                         ("close", "confirmation_basis_status")]:
        val = frame[column]
        known = val.notna() if name == "early" else val.isin(["hit", "miss"])
        yes = val.eq(True if name == "early" else "hit")
        out[name] = {"yes": int(yes.sum()), "known": int(known.sum())}
    return out


def comparison(frame, group_col, selected):
    keep = frame[group_col].eq(selected)
    result = {name: metrics(g) for name, g in (
        ("baseline", frame), ("kept", frame[keep]), ("other", frame[~keep]))}
    expected = {name: 0.0 for name in ["early", "chart", "close"]}
    for _, cell in frame.groupby(["symbol", "side", "year"]):
        all_counts = metrics(cell)
        kept_counts = metrics(cell[cell[group_col].eq(selected)])
        for name in expected:
            if all_counts[name]["known"]:
                expected[name] += (kept_counts[name]["known"] * all_counts[name]["yes"] /
                                   all_counts[name]["known"])
    result["matched_expected_events"] = expected
    return result


def main():
    signals, warnings = load_signals(SOURCE / "signals.csv")
    assert not warnings, warnings
    cohort = signals[signals.primary_non_top20_alt & signals.year.isin([2025, 2026])]
    counts = cohort.groupby("symbol").size().sort_index().sort_values(ascending=False, kind="stable")
    assert counts.head(6).index.tolist() == CONFIG["symbols"]
    features = []
    for symbol in CONFIG["symbols"]:
        folder = SOURCE / "inputs" / symbol
        read = lambda name: pd.read_csv(folder / name, index_col="time", float_precision="round_trip")
        states = snapshots(read("bars12h.csv.gz"))
        hourly = read("bars1h.csv.gz")
        assert hourly.index.to_series().diff().dropna().eq(HOUR).all()
        for row in cohort[cohort.symbol.eq(symbol)].itertuples():
            features.append(dict(symbol=symbol, week=row.week, **first_touch(row, hourly, states)))
    ledger = cohort.merge(pd.DataFrame(features), on=["symbol", "week"], validate="one_to_one")
    assert len(ledger) == counts.head(6).sum()
    overlap = ledger[ledger.tag.eq("overlap")]
    from yoyo.evaluation.cloud_fvg_poc_inputs import profile
    def zone_key(r):
        return r.symbol, int(r.middle_ms), r.zone_low, r.zone_high
    unique_zones = dict.fromkeys(zone_key(r) for r in overlap.itertuples())
    with ThreadPoolExecutor(max_workers=4) as pool:
        profiles = dict(zip(unique_zones, pool.map(lambda k: profile(*k, EXP / "profiles"), unique_zones)))
    for r in overlap.itertuples():
        data = profiles[zone_key(r)]
        for field in ("poc", "profile_source", "profile_status"):
            ledger.at[r.Index, field] = data[field]
    if not overlap.empty:
        ledger["poc_r"] = (ledger.reference_entry - ledger.poc).abs() / ledger.risk
        ledger["poc_nearer"] = ledger.poc_r < ledger.midpoint_r - 1e-10
    eligible = ledger[ledger.tag.isin(["overlap", "outside"])]
    common = lambda f: comparison(f, "tag", "overlap")
    result = {"counts": ledger.tag.value_counts().to_dict(), "all": metrics(ledger),
              "earlier_weekly_touches": int(ledger.touch_in_key_bar.eq(False).sum()),
              "overall": common(eligible),
              "year": {str(y): common(g) for y, g in eligible.groupby("year")},
              "symbol": {s: common(g) for s, g in eligible.groupby("symbol")}}
    if not overlap.empty:
        complete = ledger[ledger.profile_status.eq("auto_72m") & ledger.poc.notna()]
        result["profile_status"] = ledger.loc[overlap.index, "profile_status"].value_counts().to_dict()
        result["profile"] = comparison(complete, "poc_nearer", True)
    columns = ["symbol","week","side","key_open_bjt","confirmed_bjt","year","reference_entry",
               "reference_stop","confirmation_close","early_fixed_stop_2bars","status",
               "confirmation_basis_status","tag","touch_hour_utc","touch_in_key_bar","zones",
               "middle_ms","zone_low","zone_high","risk","midpoint_r","poc","profile_source",
               "profile_status","poc_r","poc_nearer"]
    ledger.reindex(columns=columns).to_csv(EXP / "signals.csv", index=False)
    dump(EXP / "summary.json", result)


if __name__ == "__main__":
    main()
