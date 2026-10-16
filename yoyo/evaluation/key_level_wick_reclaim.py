"""Causal 5m wick-reclaim filter on the frozen BTC first-contact cohort.

Owner request (2026-10-16): use five-minute candles, only wick through a key
level and close back. Keep UI level definitions and first-contact identities
from the audited v1 study. For a long, 5m open and close must exceed the level
and low must be strictly below it; short is symmetric. No wick/body ratio is
fitted. Entry is the following 5m open, never the reclaimed level price.

Signal columns: complete 5m OHLC containing the first contact. Matching uses
15m closes completed BEFORE that 5m candle, 96-return volatility and prior
30-day tertiles (minimum 7 days). Labels use only the subsequent 15/60/240m.
The all-contact comparator waits for the same 5m close to isolate the filter.
No targets, stops, fills, portfolio sizing, training or production changes.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import key_level_reactions as base

ROOT, MINUTE, COLS = base.ROOT, base.MINUTE, base.COLS
FIVE = 5 * MINUTE
DEFAULT = ROOT / "experiments/active/exp-btc-key-level-wick-reclaim-20261016-v2/config.json"


def signal_candles(raw, contacts):
    """Attach the complete first-touch 5m bar; strict crossing, no future input."""
    grouped = raw.groupby(raw.ts // FIVE * FIVE, sort=True)
    bars = grouped.agg(open=("open", "first"), high=("high", "max"),
        low=("low", "min"), close=("close", "last"), count=("ts", "size"))
    bars = bars.loc[bars["count"].eq(5)]
    ts = raw.ts.to_numpy()
    events, audit = [], Counter()
    for event in contacts.to_dict("records"):
        start = int(event["touch_ms"] // FIVE * FIVE)
        finish = start + FIVE
        if start not in bars.index:
            audit["incomplete_signal_bar"] += 1
            continue
        if start < event["active_ms"] or finish > event["expires_ms"]:
            audit["signal_outside_level_lifetime"] += 1
            continue
        j = int(np.searchsorted(ts, finish))
        if j >= len(ts) or ts[j] != finish:
            audit["missing_next_open"] += 1
            continue
        candle = bars.loc[start]
        side, price = event["side"], event["price"]
        original_side = (candle.open - price) * side > 0
        reclaimed = (candle.close - price) * side > 0
        pierced = candle.low < price if side == 1 else candle.high > price
        wick = bool(original_side and reclaimed and pierced)
        event.update(signal_ms=start, confirmed_ms=finish, entry_index=j,
            signal_open=float(candle.open), signal_high=float(candle.high),
            signal_low=float(candle.low), signal_close=float(candle.close),
            wick_reclaim=wick, opened_on_approach_side=bool(original_side),
            strict_pierce=bool(pierced), closed_on_approach_side=bool(reclaimed))
        events.append(event)
        audit["wick_reclaim" if wick else "not_reclaim"] += 1
    return pd.DataFrame(events), dict(audit)


def measure(raw, bars15, events, cfg):
    """Same-clock comparator and random 5m entries with pre-signal matching."""
    ts, op = raw.ts.to_numpy(), raw.open.to_numpy()
    hs = cfg["horizons_minutes"]
    paths = base.forward_paths(raw, hs)
    max_h = max(hs)
    start, split, end = map(base.stamp, (cfg["start"], cfg["split"], cfg["end"]))
    signal_ts = ts - FIVE
    vol, mom, vol_value = base.causal_features(bars15, signal_ts)
    local = pd.to_datetime(signal_ts, unit="ms", utc=True).tz_convert(cfg["timezone"])
    week = (pd.to_datetime(pd.Series(local.date)) - pd.to_timedelta(local.dayofweek, unit="D")).dt.strftime("%Y-%m-%d").to_numpy()
    part, post = local.hour.to_numpy() // 6, signal_ts >= split
    complete_signal = np.zeros(len(ts), dtype=bool)
    complete_signal[5:] = ts[5:] - ts[:-5] == FIVE
    valid = (ts % FIVE == 0) & complete_signal & paths[max_h]["valid"]
    valid &= np.isfinite(vol) & np.isfinite(mom) & (signal_ts >= start)
    valid &= paths[max_h]["end"] <= end
    valid &= post | (paths[max_h]["end"] <= split)
    pools = defaultdict(list)
    for j in np.flatnonzero(valid):
        pools[(week[j], int(part[j]), int(vol[j]), int(mom[j]), bool(post[j]))].append(j)
    pools = {key: np.asarray(value, dtype=int) for key, value in pools.items()}
    cost = cfg["round_trip_cost"] * 10000
    records, matches, audit = [], [], Counter()
    for event in events.to_dict("records"):
        j, side = event["entry_index"], event["side"]
        if not valid[j]:
            audit["excluded_path_features_or_split"] += 1
            continue
        assert ts[j] == event["confirmed_ms"] and signal_ts[j] == event["signal_ms"]
        key = (week[j], int(part[j]), int(vol[j]), int(mom[j]), bool(post[j]))
        pool = pools[key]
        pool = pool[np.abs(ts[pool] - ts[j]) > max_h * MINUTE]
        digest = int(hashlib.sha256(event["level_id"].encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(cfg["controls"]["seed"] + digest)
        n = min(cfg["controls"]["count"], len(pool))
        chosen = rng.choice(pool, n, replace=False) if n >= cfg["controls"]["minimum"] else np.array([], dtype=int)
        audit["matched" if len(chosen) else "unmatched"] += 1
        for ci in chosen:
            matches.append(dict(level_id=event["level_id"], event_entry_ms=int(ts[j]),
                control_entry_ms=int(ts[ci]), week=week[j], daypart=int(part[j]),
                vol_bucket=int(vol[j]), momentum_sign=int(mom[j]), side=side))
        for h in hs:
            gross, mfe, mae = base.response(paths[h], j, side, op[j])
            if len(chosen):
                cg, cf, ca = base.response(paths[h], chosen, side, op[chosen])
                ctrl, ctrl_mfe, ctrl_mae = float(cg.mean() - cost), float(cf.mean()), float(ca.mean())
            else:
                ctrl = ctrl_mfe = ctrl_mae = np.nan
            records.append(dict(**event, horizon=h, period="post" if post[j] else "pre",
                entry_ms=int(ts[j]), entry_price=float(op[j]), volatility=float(vol_value[j]),
                vol_bucket=int(vol[j]), momentum_sign=int(mom[j]),
                gross_bp=float(gross), net_bp=float(gross - cost), mfe_bp=float(mfe), mae_bp=float(mae),
                controls=len(chosen), control_net_bp=ctrl, control_mfe_bp=ctrl_mfe,
                control_mae_bp=ctrl_mae, excess_bp=float(gross - cost - ctrl)))
    return pd.DataFrame(records), pd.DataFrame(matches), dict(audit)


def summaries(measured, cfg):
    """Primary tests are within reclaim; baseline cohorts are descriptive."""
    frames = []
    for name, sub in (("all_contacts_5m_clock", measured),
                      ("wick_reclaim", measured.loc[measured.wick_reclaim]),
                      ("not_reclaim", measured.loc[~measured.wick_reclaim])):
        # Legacy summarizer expects this reference; do not expose an impossible fill.
        result = base.summarize(sub.assign(ideal_level_net_bp=np.nan), cfg)
        result = result.drop(columns="ideal_level_net_bp")
        result.insert(0, "cohort", name)
        frames.append(result)
    return pd.concat(frames, ignore_index=True)


def independent_audit(raw, measured, controls, events, cfg):
    """Replay 5m signals/entries and all returns using direct minute slices.

Uses no aggregation/response helper. Inherits the frozen v1 level provenance
and independent UI-formula audit. Control feature construction is separately
covered by causal tests; feature values are not independently reimplemented.
"""
    ts, op, hi, lo, cl = [raw[c].to_numpy() for c in ("ts", "open", "high", "low", "close")]
    cost, split = cfg["round_trip_cost"] * 10000, base.stamp(cfg["split"])
    errors = dict(gross=0., net=0., mfe=0., mae=0., control=0.)
    for e in events.itertuples():
        a, j = np.searchsorted(ts, [e.signal_ms, e.confirmed_ms])
        assert j - a == 5 and np.array_equal(ts[a:j], np.arange(e.signal_ms, e.confirmed_ms, MINUTE))
        assert e.signal_ms <= e.touch_ms < e.confirmed_ms
        assert ts[j] == e.confirmed_ms
        assert e.signal_open == op[a] and e.signal_close == cl[j - 1]
        assert e.signal_high == max(hi[a:j]) and e.signal_low == min(lo[a:j])
        wick = (op[a] > e.price and cl[j - 1] > e.price and min(lo[a:j]) < e.price) if e.side == 1 else (op[a] < e.price and cl[j - 1] < e.price and max(hi[a:j]) > e.price)
        assert bool(wick) == e.wick_reclaim
        assert j == e.entry_index
    for e in measured.itertuples():
        j, h = e.entry_index, e.horizon
        assert ts[j] == e.confirmed_ms == e.entry_ms and ts[j] % FIVE == 0
        assert ts[j + h - 1] - ts[j] == (h - 1) * MINUTE
        assert e.entry_price == op[j]
        assert (e.period == "post") == (e.signal_ms >= split)
        assert e.period == "post" or ts[j + h - 1] + MINUTE <= split
        gross = e.side * (cl[j + h - 1] - op[j]) / op[j] * 10000
        best, worst = (max(hi[j:j + h]), min(lo[j:j + h])) if e.side == 1 else (min(lo[j:j + h]), max(hi[j:j + h]))
        mfe = max(0., e.side * (best - op[j]) / op[j] * 10000)
        mae = max(0., e.side * (op[j] - worst) / op[j] * 10000)
        for key, delta in (("gross", gross - e.gross_bp), ("net", gross - cost - e.net_bp), ("mfe", mfe - e.mfe_bp), ("mae", mae - e.mae_bp)):
            errors[key] = max(errors[key], abs(float(delta)))
    ci = np.searchsorted(ts, controls.control_entry_ms.to_numpy())
    ei = np.searchsorted(ts, controls.event_entry_ms.to_numpy())
    assert np.all(ts[ci] % FIVE == 0) and np.all(ts[ci] - ts[ci - 5] == FIVE)
    assert np.all(np.abs(ts[ci] - ts[ei]) > max(cfg["horizons_minutes"]) * MINUTE)
    assert not controls.duplicated(["level_id", "control_entry_ms"]).any()
    lc = pd.to_datetime(ts[ci] - FIVE, unit="ms", utc=True).tz_convert(cfg["timezone"])
    le = pd.to_datetime(ts[ei] - FIVE, unit="ms", utc=True).tz_convert(cfg["timezone"])
    assert np.array_equal(lc.hour // 6, le.hour // 6)
    assert all(c.date() - timedelta(days=c.dayofweek) == e.date() - timedelta(days=e.dayofweek)
               for c, e in zip(lc, le))
    assert np.array_equal(ts[ci] - FIVE >= split, ts[ei] - FIVE >= split)
    for h in cfg["horizons_minutes"]:
        assert np.all(ts[ci + h - 1] - ts[ci] == (h - 1) * MINUTE)
        values = (cl[ci + h - 1] - op[ci]) / op[ci] * controls.side.to_numpy() * 10000 - cost
        reference = pd.Series(values).groupby(controls.level_id).mean()
        actual = measured.loc[measured.horizon.eq(h) & measured.controls.gt(0)].set_index("level_id")
        assert set(reference.index) == set(actual.index)
        errors["control"] = max(errors["control"], float(np.abs(reference - actual.control_net_bp).max()))
    assert max(errors.values()) < 1e-7, errors
    return dict(verified=True, signals=len(events), measurements=len(measured), control_pairs=len(controls),
        maximum_absolute_errors=errors,
        checks=["all 5m wick predicates recomputed from raw slices", "entry after confirmation at next 5m open", "no signal-candle extremes in outcomes", "every return and control return independently recomputed", "same week/daypart/side/split and no repeated control within event"],
        limitation="Frozen v1 provides audited level formation/first contacts. Matching feature arithmetic has causal unit tests, not an independent second implementation. No execution validation.")


def run(config_path):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    out = config_path.parent / "results_v2"
    if out.exists():
        raise FileExistsError(out)
    builders = {}
    for path in (Path(__file__).resolve(), Path(base.__file__).resolve(), config_path):
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) != path.read_bytes():
            raise ValueError("commit builder/config before generation: " + rel)
        builders[rel] = base.sha(path)
    print("Checking frozen inputs and baseline lineage", flush=True)
    for item in cfg["inputs"] + cfg["baseline_artifacts"]:
        if base.sha(ROOT / item["path"]) != item["sha256"]:
            raise ValueError("input changed: " + item["path"])
    raw = pd.concat([pd.read_csv(ROOT / item["path"], usecols=["ts"] + COLS) for item in cfg["inputs"]]).sort_values("ts").reset_index(drop=True)
    assert not raw.ts.duplicated().any() and (raw.ts % MINUTE == 0).all()
    contacts = pd.read_csv(ROOT / cfg["contacts_path"])
    bars15, _ = base.aggregate_15m(raw)
    events, signal_audit = signal_candles(raw, contacts)
    print(f"Signal candles: {signal_audit}; matching 5m controls", flush=True)
    measured, controls, matched_audit = measure(raw, bars15, events, cfg)
    print(f"Matching: {matched_audit}; independent replay", flush=True)
    verification = independent_audit(raw, measured, controls, events, cfg)
    summary = summaries(measured, cfg)
    out.mkdir()
    for name, frame in (("events", events), ("measurements", measured), ("controls", controls)):
        frame.to_csv(out / (name + ".csv.gz"), index=False, compression=dict(method="gzip", mtime=0))
    summary.to_csv(out / "summary.csv", index=False)
    (out / "verification.json").write_text(json.dumps(verification, indent=2) + "\n")
    receipt = dict(experiment_id=cfg["experiment_id"], generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        builders=builders, inputs=cfg["inputs"], baseline_artifacts=cfg["baseline_artifacts"],
        source_rows=len(raw), signal_audit=signal_audit, matching=matched_audit,
        measured_events=measured.level_id.nunique(), reclaim_events=measured.loc[measured.wick_reclaim].level_id.nunique(),
        unique_reclaim_candles=measured.loc[measured.wick_reclaim].signal_ms.nunique(),
        controls=len(controls), unique_control_minutes=controls.control_entry_ms.nunique(),
        versions=dict(numpy=np.__version__, pandas=pd.__version__),
        artifacts={p.name: dict(sha256=base.sha(p), size_bytes=p.stat().st_size) for p in out.iterdir()},
        training_eligible=False, production_eligible=False,
        interpretation="First-touch 5m wick-reclaim filter with confirmation-close timing. Fixed-horizon responses, not a fitted TP/SL strategy or real fills. Later retests are outside this experiment.")
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(summary.loc[summary.category.eq("ALL") & summary.side.eq(0),
        ["cohort", "period", "horizon", "n", "net_bp", "control_net_bp", "excess_bp", "p", "q_primary"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    run(parser.parse_args().config)
