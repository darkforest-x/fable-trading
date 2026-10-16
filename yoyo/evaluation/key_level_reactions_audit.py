"""Independent ledger arithmetic and UI formula audit for the BTC level study.

Reads frozen source and result CSVs only. Does not call the study's feature,
contact, aggregation or response functions. UI reference is the currently
observed module, whose bytes are hashed in the audit receipt. It starts no
service and never opens a live database. Run after committing this verifier.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-btc-key-level-reactions-20261016-v1"


def main():
    rel = str(Path(__file__).resolve().relative_to(ROOT))
    assert subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) == Path(__file__).read_bytes()
    cfg = json.loads((EXP / "config.json").read_text())
    dest = EXP / "results_v1" / "verification.json"
    assert not dest.exists()
    raw = pd.concat([pd.read_csv(ROOT / item["path"], usecols=["ts", "open", "high", "low", "close", "volume"])
                     for item in cfg["inputs"]]).sort_values("ts").reset_index(drop=True)
    m = pd.read_csv(EXP / "results_v1/measurements.csv.gz")
    ctr = pd.read_csv(EXP / "results_v1/controls.csv.gz")
    levels = pd.read_csv(EXP / "results_v1/levels.csv.gz")
    op, hi, lo, cl, ts = [raw[k].to_numpy() for k in ("open", "high", "low", "close", "ts")]
    assert len(np.unique(ts)) == len(ts)
    errors = dict(gross=0., net=0., mfe=0., mae=0., ideal=0., matched_control=0., frontend_level=0.)
    samples = m.drop_duplicates("level_id")
    assert len(samples) == len(m) / len(cfg["horizons_minutes"])
    for e in samples.itertuples():
        k = int(np.searchsorted(ts, e.touch_ms))
        j = int(np.searchsorted(ts, e.entry_ms))
        assert j == k + 1 and ts[j] - ts[k] == 60000
        assert lo[k] <= e.price <= hi[k]
        assert (cl[k - 1] - e.price) * e.side > 0
        a = int(np.searchsorted(ts, e.active_ms))
        assert ts[a] == e.active_ms and e.active_ms <= ts[k] < e.expires_ms
        assert np.all(np.diff(ts[a:k + 1]) == 60000)
        assert not np.any((lo[a:k] <= e.price) & (hi[a:k] >= e.price))
    split = pd.Timestamp(cfg["split"]).value // 10**6
    cost = cfg["round_trip_cost"] * 10000
    for e in m.itertuples():
        j, h = int(e.entry_index), int(e.horizon)
        assert ts[j + h - 1] - ts[j] == (h - 1) * 60000
        assert e.entry_price == op[j]
        assert (e.period == "post") == (e.touch_ms >= split)
        if e.period == "pre":
            assert ts[j + h - 1] + 60000 <= split
        g = (cl[j + h - 1] - op[j]) / op[j] * e.side * 10000
        ideal = (cl[j + h - 1] - e.price) / e.price * e.side * 10000 - cost
        best = hi[j:j + h].max() if e.side == 1 else lo[j:j + h].min()
        worst = lo[j:j + h].min() if e.side == 1 else hi[j:j + h].max()
        mfe = max(0, (best - op[j]) / op[j] * e.side * 10000)
        mae = max(0, (op[j] - worst) / op[j] * e.side * 10000)
        for key, value in (("gross", g - e.gross_bp), ("net", g - cost - e.net_bp),
                           ("mfe", mfe - e.mfe_bp), ("mae", mae - e.mae_bp),
                           ("ideal", ideal - e.ideal_level_net_bp)):
            errors[key] = max(errors[key], abs(float(value)))
    ci = np.searchsorted(ts, ctr.control_entry_ms.to_numpy())
    ei = np.searchsorted(ts, ctr.event_entry_ms.to_numpy())
    assert np.all(np.abs(ts[ci] - ts[ei]) > max(cfg["horizons_minutes"]) * 60000)
    assert not ctr.duplicated(["level_id", "control_entry_ms"]).any()
    local_c = pd.to_datetime(ts[ci] - 60000, unit="ms", utc=True).tz_convert(cfg["timezone"])
    local_e = pd.to_datetime(ts[ei] - 60000, unit="ms", utc=True).tz_convert(cfg["timezone"])
    assert np.array_equal(local_c.hour // 6, local_e.hour // 6)
    assert all(c.date() - timedelta(days=c.dayofweek) == e.date() - timedelta(days=e.dayofweek)
               for c, e in zip(local_c, local_e))
    assert np.array_equal(ts[ci] - 60000 >= split, ts[ei] - 60000 >= split)
    for h in cfg["horizons_minutes"]:
        assert np.all(ts[ci + h - 1] - ts[ci] == (h - 1) * 60000)
        values = (cl[ci + h - 1] - op[ci]) / op[ci] * ctr.side.to_numpy() * 10000 - cost
        reference = pd.Series(values).groupby(ctr.level_id).mean()
        actual = m.loc[m.horizon.eq(h) & m.controls.gt(0)].set_index("level_id")
        assert set(reference.index) == set(actual.index)
        errors["matched_control"] = max(errors["matched_control"], float(np.abs(reference - actual.control_net_bp).max()))
    indexed = raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))
    bars = indexed.resample("15min").agg(dict(open="first", high="max", low="min", close="last", volume="sum", ts="count"))
    bars = bars.loc[bars.ts.eq(15)].drop(columns="ts")
    local = bars.index.tz_convert(cfg["timezone"])
    sessions = {str(day): g for day, g in bars.groupby(local.date)}
    from yoyo.monitor.market_context import profile
    for origin, g in levels.groupby("origin"):
        sub = sessions[origin].copy()
        sub.volume *= .01
        reference = profile(sub.to_dict("records"))
        for row in g.itertuples():
            if row.family in ("poc", "yesterday_vah", "yesterday_val"):
                key = row.family.split("_")[-1]
                expected = reference[key]
            elif row.family.startswith("prior_week_"):
                d = pd.Timestamp(origin).date()
                week = [sessions[str(d + timedelta(days=i))] for i in range(7)]
                expected = max(x.high.max() for x in week) if row.family.endswith("high") else min(x.low.min() for x in week)
            else:
                high, low = sub.high.max(), sub.low.min()
                fractions = {"monday_high": 1., "monday_low": 0., "monday_eq": .5,
                    "fib_0.618_up": .618, "fib_0.618_down": .382,
                    "fib_0.705_up": .705, "fib_0.705_down": .295,
                    "extension_up": 1.13, "extension_down": -.13}
                expected = low + (high - low) * fractions[row.family]
            errors["frontend_level"] = max(errors["frontend_level"], abs(row.price - expected))
    assert max(errors.values()) < 1e-7, errors
    result = dict(verified=True, events=len(samples), measurements=len(m), control_pairs=len(ctr),
        levels=len(levels), maximum_absolute_errors=errors,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        verifier_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        ui_reference_sha256=hashlib.sha256((ROOT / "yoyo/monitor/market_context.py").read_bytes()).hexdigest(),
        checks=["dated levels match frontend profile / independent range formulas", "first contact only / approach direction / continuous prior path", "entry is next minute / no touch-minute extremes", "every response recomputed from raw OHLC", "every control gross and cost recomputed", "same week/daypart/side/split; no within-event control duplicates", "no label crosses split"],
        limitation="Control volatility/momentum construction has causal unit tests, but is not independently reimplemented here. No execution or profitability validation implied.")
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
