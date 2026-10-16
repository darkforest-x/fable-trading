"""First-touch response study of the existing BTC context chart, without orders.

Formula source: yoyo/monitor/market_context.py as observed 2026-10-16. Freeze
the small profile calculation here because that UI module is an uncommitted
shared-workspace file. Tests compare the formulas; no runtime UI import.
Official reference: https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/

Formation uses only completed NY calendar sessions of complete OKX 15m OHLCV.
POC is the largest of 80 bins of volume uniformly allocated over candle range;
the contiguous value area expands to 70%. Monday/Fib levels activate Tuesday;
prior-week levels activate Monday. Untouched POCs expire after 14 calendar days.
First contact uses 1m low/high; approach uses the previous close. Measurement
starts next minute: the contact candle's intraminute path is never invented.
Volatility and momentum use only completed 15m closes before contact, windows
96 bars and previous 30 days (minimum 7 days) respectively. Future OHLC is used
only for response labels. No training, fitted targets, stops or account actions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
MINUTE = 60000
STEP = 15 * MINUTE
COLS = ["open", "high", "low", "close", "volume"]
DEFAULT = ROOT / "experiments/active/exp-btc-key-level-reactions-20261016-v1/config.json"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stamp(value):
    return int(pd.Timestamp(value).value // 10**6)


def midnight(day, zone):
    return int(datetime.combine(day, datetime.min.time(), ZoneInfo(zone)).timestamp() * 1000)


def volume_profile(frame, bins=80, area=.70):
    """UI-equivalent estimated POC/VA from a completed session's OHLCV only."""
    low, high = float(frame.low.min()), float(frame.high.max())
    if not len(frame) or high <= low:
        return None
    width = (high - low) / bins
    amounts = [0.0] * bins
    for row in frame.itertuples():
        if row.high == row.low:
            amounts[min(bins - 1, max(0, int((row.close - low) / width)))] += row.volume
            continue
        first = max(0, int((row.low - low) / width))
        last = min(bins - 1, int((row.high - low) / width))
        for i in range(first, last + 1):
            overlap = max(0, min(row.high, low + (i + 1) * width) - max(row.low, low + i * width))
            amounts[i] += row.volume * overlap / (row.high - row.low)
    total = sum(amounts)
    if total <= 0:
        return None
    point = max(range(bins), key=lambda i: amounts[i])
    a = b = point
    covered = amounts[point]
    while covered < total * area and (a > 0 or b < bins - 1):
        left, right = amounts[a - 1] if a > 0 else -1, amounts[b + 1] if b < bins - 1 else -1
        if right > left:
            b += 1
            covered += amounts[b]
        else:
            a -= 1
            covered += amounts[a]
    return {"poc": low + (point + .5) * width, "vah": low + (b + 1) * width,
            "val": low + a * width}


def aggregate_15m(raw):
    """Reject incomplete buckets, rather than silently combining across gaps."""
    grouped = raw.groupby(raw.ts // STEP * STEP, sort=True)
    bars = grouped.agg(open=("open", "first"), high=("high", "max"),
                       low=("low", "min"), close=("close", "last"),
                       volume=("volume", "sum"), count=("ts", "size"))
    incomplete = int((bars["count"] != 15).sum())
    return bars.loc[bars["count"].eq(15), COLS], incomplete


def make_levels(bars, zone):
    """Create dated level identities; every activation is after formation."""
    dates = pd.to_datetime(bars.index, unit="ms", utc=True).tz_convert(zone).date
    sessions, levels, stats = {}, [], Counter()
    for day, sub in bars.groupby(dates):
        a, b = midnight(day, zone), midnight(day + timedelta(days=1), zone)
        if np.array_equal(sub.index.to_numpy(), np.arange(a, b, STEP)):
            sessions[day] = sub
        else:
            stats["incomplete_sessions"] += 1

    def add(kind, price, origin, a, b, first_day_end=None):
        levels.append(dict(level_id=f"{kind}|{origin}", family=kind, price=float(price),
                           origin=str(origin), active_ms=a, expires_ms=b,
                           first_day_end_ms=first_day_end or b))

    for day, sub in sessions.items():
        end = midnight(day + timedelta(days=1), zone)
        next_end = midnight(day + timedelta(days=2), zone)
        base_volume = sub.copy()
        base_volume["volume"] *= .01
        value = volume_profile(base_volume)
        contracts = volume_profile(sub)
        if value and contracts and any(value[k] != contracts[k] for k in value):
            stats["profile_unit_scaling_changed_sessions"] += 1
        if value:
            add("poc", value["poc"], day, end, midnight(day + timedelta(days=15), zone), next_end)
            for key in ("vah", "val"):
                add("yesterday_" + key, value[key], day, end, next_end)
        if day.weekday() == 0:
            hi, lo = float(sub.high.max()), float(sub.low.min())
            width = hi - lo
            expiry = midnight(day + timedelta(days=7), zone)
            vals = {"monday_high": hi, "monday_low": lo, "monday_eq": (hi + lo) / 2}
            for ratio in (.618, .705):
                vals[f"fib_{ratio}_up"] = lo + ratio * width
                vals[f"fib_{ratio}_down"] = hi - ratio * width
            vals.update(extension_up=hi + .13 * width, extension_down=lo - .13 * width)
            for kind, price in vals.items():
                add(kind, price, day, end, expiry)
            week_days = [day + timedelta(days=k) for k in range(7)]
            if all(d in sessions for d in week_days):
                a, b = expiry, midnight(day + timedelta(days=14), zone)
                add("prior_week_high", max(sessions[d].high.max() for d in week_days), day, a, b)
                add("prior_week_low", min(sessions[d].low.min() for d in week_days), day, a, b)
            else:
                stats["incomplete_weeks"] += 1
    return pd.DataFrame(levels), dict(stats, complete_sessions=len(sessions))


def first_contacts(raw, levels, start, end, zone):
    """One first contact per origin/level; missing prior path invalidates it."""
    ts, hi, lo, cl, op = (raw[c].to_numpy() for c in ("ts", "high", "low", "close", "open"))
    gaps = np.r_[0, np.cumsum(np.diff(ts) != MINUTE)]
    events, status = [], []
    for level in levels.to_dict("records"):
        a, b = np.searchsorted(ts, [level["active_ms"], min(level["expires_ms"], end)])
        reason, hit = "no_touch", None
        if a >= len(ts) or b <= a:
            reason = "outside_data"
        elif ts[a] != level["active_ms"]:
            reason = "missing_activation"
        else:
            touches = np.flatnonzero((lo[a:b] <= level["price"]) & (hi[a:b] >= level["price"]))
            if len(touches):
                hit = a + int(touches[0])
                if gaps[hit] != gaps[a] or hit == 0 or ts[hit] - ts[hit - 1] != MINUTE:
                    reason = "missing_before_touch"
                elif not start <= ts[hit] < end:
                    reason = "touch_outside_study"
                elif cl[hit - 1] == level["price"]:
                    reason = "approach_equal"
                else:
                    side = 1 if cl[hit - 1] > level["price"] else -1
                    local = pd.Timestamp(int(ts[hit]), unit="ms", tz="UTC").tz_convert(zone)
                    family = level["family"]
                    if family == "poc":
                        family = "yesterday_poc" if ts[hit] < level["first_day_end_ms"] else "npoc"
                    events.append(dict(**level, category=family, touch_index=hit,
                        touch_ms=int(ts[hit]), side=side, entry_index=hit + 1,
                        week=str(local.date() - timedelta(days=local.dayofweek)),
                        touch_day=str(local.date()), gap_through=bool((op[hit] - level["price"]) * side < 0)))
                    reason = "contact"
            elif gaps[b - 1] != gaps[a]:
                reason = "missing_no_touch_path"
        status.append(dict(level_id=level["level_id"], family=level["family"], status=reason,
                           touch_ms=int(ts[hit]) if hit is not None else None))
    return pd.DataFrame(events), pd.DataFrame(status)


def causal_features(bars, minute_ts):
    """Map completed 15m return/24h vol/prior-30d tertiles to contact minutes."""
    grid = bars.reindex(np.arange(bars.index.min(), bars.index.max() + STEP, STEP))
    returns = np.log(grid.close).diff()
    vol = returns.rolling(96, min_periods=96).std()
    past = vol.shift(1).rolling(96 * 30, min_periods=96 * 7)
    q1, q2 = past.quantile(1 / 3), past.quantile(2 / 3)
    bucket = np.where(vol < q1, 0, np.where(vol < q2, 1, 2)).astype(float)
    bucket[~(vol.notna() & q1.notna() & q2.notna())] = np.nan
    # At the open of a contact minute, only candles closed at/before it exist.
    lookup = np.searchsorted(grid.index.to_numpy() + STEP, minute_ts, side="right") - 1
    good = lookup >= 0
    lookup = np.maximum(lookup, 0)
    mapped = bucket[lookup]
    mapped[~good] = np.nan
    mom = np.sign(returns.to_numpy()[lookup])
    mom[~good] = np.nan
    return mapped, mom, vol.to_numpy()[lookup]


def forward_paths(raw, horizons):
    """For an entry minute j, label j..j+h-1; no touch-minute extremes."""
    ts, op, hi, lo, cl = (raw[c].to_numpy() for c in ("ts", "open", "high", "low", "close"))
    out = {}
    for h in horizons:
        highs = pd.Series(hi[::-1]).rolling(h, min_periods=h).max().to_numpy()[::-1]
        lows = pd.Series(lo[::-1]).rolling(h, min_periods=h).min().to_numpy()[::-1]
        close = np.r_[cl[h - 1:], np.full(h - 1, np.nan)]
        end = np.r_[ts[h - 1:], np.full(h - 1, np.nan)]
        valid = end - ts == (h - 1) * MINUTE
        out[h] = dict(high=highs, low=lows, close=close, valid=valid, end=end + MINUTE)
    return out


def response(paths, indices, side, entry):
    end, hi, lo = (paths[c][indices] for c in ("close", "high", "low"))
    gross = side * (end / entry - 1) * 10000
    mfe = np.maximum(0, (hi / entry - 1) if side == 1 else (1 - lo / entry)) * 10000
    mae = np.maximum(0, (1 - lo / entry) if side == 1 else (hi / entry - 1)) * 10000
    return gross, mfe, mae


def match_and_measure(raw, bars, events, cfg):
    ts, op = raw.ts.to_numpy(), raw.open.to_numpy()
    hs = cfg["horizons_minutes"]
    paths = forward_paths(raw, hs)
    max_h = max(hs)
    start, split, end = map(stamp, (cfg["start"], cfg["split"], cfg["end"]))
    contact_ts = ts - MINUTE
    vol, mom, vol_value = causal_features(bars, contact_ts)
    local = pd.to_datetime(contact_ts, unit="ms", utc=True).tz_convert(cfg["timezone"])
    day = pd.Series(local.date)
    week = (pd.to_datetime(day) - pd.to_timedelta(local.dayofweek, unit="D")).dt.strftime("%Y-%m-%d").to_numpy()
    part = local.hour.to_numpy() // 6
    post = contact_ts >= split
    valid = paths[max_h]["valid"] & np.isfinite(vol) & np.isfinite(mom)
    valid &= (contact_ts >= start) & (paths[max_h]["end"] <= end)
    valid &= post | (paths[max_h]["end"] <= split)
    valid &= np.r_[False, np.diff(ts) == MINUTE]
    pools = defaultdict(list)
    for j in np.flatnonzero(valid):
        pools[(week[j], int(part[j]), int(vol[j]), int(mom[j]), bool(post[j]))].append(j)
    pools = {k: np.asarray(v, dtype=int) for k, v in pools.items()}
    cost = cfg["round_trip_cost"] * 10000
    measures, matches, audit = [], [], Counter()
    for event in events.to_dict("records"):
        j, side = event["entry_index"], event["side"]
        if j >= len(ts) or not valid[j]:
            audit["excluded_missing_path_features_or_split"] += 1
            continue
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
            gross, mfe, mae = response(paths[h], j, side, op[j])
            ideal, ideal_mfe, ideal_mae = response(paths[h], j, side, event["price"])
            if len(chosen):
                cg, cf, ca = response(paths[h], chosen, side, op[chosen])
                ctrl, ctrl_mfe, ctrl_mae = float(cg.mean() - cost), float(cf.mean()), float(ca.mean())
            else:
                ctrl = ctrl_mfe = ctrl_mae = np.nan
            measures.append(dict(**event, horizon=h, period="post" if post[j] else "pre",
                entry_ms=int(ts[j]), entry_price=float(op[j]), volatility=float(vol_value[j]),
                vol_bucket=int(vol[j]), momentum_sign=int(mom[j]),
                gross_bp=float(gross), net_bp=float(gross - cost), mfe_bp=float(mfe), mae_bp=float(mae),
                ideal_level_net_bp=float(ideal - cost), ideal_level_mfe_bp=float(ideal_mfe),
                ideal_level_mae_bp=float(ideal_mae), entry_slippage_from_level_bp=float(side * (op[j] / event["price"] - 1) * 10000),
                controls=len(chosen), control_net_bp=ctrl, control_mfe_bp=ctrl_mfe,
                control_mae_bp=ctrl_mae, excess_bp=float(gross - cost - ctrl)))
    return pd.DataFrame(measures), pd.DataFrame(matches), dict(audit)


def cluster_stats(frame, seed, draws):
    paired = frame.dropna(subset=["excess_bp"])
    if not len(paired):
        return dict(p=np.nan, ci_low=np.nan, ci_high=np.nan, matched_weeks=0)
    cluster = paired.groupby("week").excess_bp.agg(["sum", "count"])
    sums, counts = cluster["sum"].to_numpy(), cluster["count"].to_numpy()
    n, observed = len(sums), abs(sums.sum() / counts.sum())
    if n < 2:
        return dict(p=np.nan, ci_low=np.nan, ci_high=np.nan, matched_weeks=n)
    rng = np.random.default_rng(seed)
    nulls, boots = [], []
    for first in range(0, draws, 250):
        k = min(250, draws - first)
        signs = rng.choice([-1, 1], size=(k, n))
        nulls.extend(np.abs((signs * sums).sum(axis=1) / counts.sum()))
        ids = rng.integers(0, n, size=(k, n))
        boots.extend(sums[ids].sum(axis=1) / counts[ids].sum(axis=1))
    return dict(p=(1 + np.count_nonzero(np.asarray(nulls) >= observed)) / (draws + 1),
                ci_low=float(np.quantile(boots, .025)), ci_high=float(np.quantile(boots, .975)), matched_weeks=n)


def summarize(measures, cfg):
    result = []
    cost = cfg["round_trip_cost"] * 10000
    for period in ("all", "pre", "post"):
        data = measures if period == "all" else measures.loc[measures.period.eq(period)]
        for category in ["ALL"] + sorted(data.category.unique().tolist()):
            part = data if category == "ALL" else data.loc[data.category.eq(category)]
            for side in (0, 1, -1):
                sub = part if side == 0 else part.loc[part.side.eq(side)]
                for h, g in sub.groupby("horizon"):
                    matched = g.loc[g.controls.gt(0)]
                    stats = cluster_stats(matched, cfg["statistics"]["seed"], cfg["statistics"]["resamples"])
                    result.append(dict(period=period, category=category, side=side, horizon=h, n=len(g),
                        weeks=g.week.nunique(), unique_touch_minutes=g.touch_ms.nunique(),
                        matched_n=len(matched), gross_bp=g.gross_bp.mean(), net_bp=g.net_bp.mean(),
                        net_win_rate=g.net_bp.gt(0).mean(), median_net_bp=g.net_bp.median(),
                        mfe_median_bp=g.mfe_bp.median(), mfe_p75_bp=g.mfe_bp.quantile(.75),
                        mae_median_bp=g.mae_bp.median(), mae_p90_bp=g.mae_bp.quantile(.90),
                        mfe_covers_cost=g.mfe_bp.ge(cost).mean(),
                        ideal_level_net_bp=g.ideal_level_net_bp.mean(),
                        control_net_bp=matched.control_net_bp.mean(), paired_event_net_bp=matched.net_bp.mean(),
                        excess_bp=matched.excess_bp.mean(),
                        excess_mfe_bp=(matched.mfe_bp - matched.control_mfe_bp).mean(),
                        excess_mae_bp=(matched.mae_bp - matched.control_mae_bp).mean(),
                        gap_through_rate=g.gap_through.mean(), **stats))
    out = pd.DataFrame(result)
    out["q_primary"] = np.nan
    for period in ("all", "pre", "post"):
        selected = out.loc[out.period.eq(period) & out.horizon.eq(cfg["primary_horizon_minutes"]) & out.p.notna()].sort_values("p")
        q = selected.p.to_numpy() * len(selected) / np.arange(1, len(selected) + 1)
        q = np.minimum(1, np.minimum.accumulate(q[::-1])[::-1])
        out.loc[selected.index, "q_primary"] = q
    return out


def run(config_path):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    out = config_path.parent / "results_v1"
    if out.exists():
        raise FileExistsError(out)
    builders = {}
    for path in (Path(__file__).resolve(), config_path):
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) != path.read_bytes():
            raise ValueError("commit builder/config before generating: " + rel)
        builders[rel] = sha(path)
    print("Reading and checking frozen OKX 1m sources", flush=True)
    inputs, frames = [], []
    for item in cfg["inputs"]:
        path = ROOT / item["path"]
        actual = sha(path)
        if actual != item["sha256"]:
            raise ValueError("source hash changed: " + str(path))
        d = pd.read_csv(path, usecols=["ts"] + COLS)
        frames.append(d)
        inputs.append(dict(**item, rows=len(d), first_ms=int(d.ts.min()), last_ms=int(d.ts.max())))
    raw = pd.concat(frames).sort_values("ts").reset_index(drop=True)
    if raw.ts.duplicated().any() or (raw.ts % MINUTE != 0).any():
        raise ValueError("duplicate or unaligned source timestamps")
    if (not np.isfinite(raw[COLS].to_numpy()).all() or (raw[COLS[:4]] <= 0).any().any()
        or (raw.volume < 0).any() or (raw.low > raw[["open", "close"]].min(axis=1)).any()
        or (raw.high < raw[["open", "close"]].max(axis=1)).any()):
        raise ValueError("invalid source OHLCV")
    raw = raw.loc[raw.ts.lt(stamp(cfg["end"]))].reset_index(drop=True)
    print("Building causal 15m sessions and dated levels", flush=True)
    bars, incomplete = aggregate_15m(raw)
    levels, formation = make_levels(bars, cfg["timezone"])
    events, status = first_contacts(raw, levels, stamp(cfg["start"]), stamp(cfg["end"]), cfg["timezone"])
    print(f"Levels={len(levels)} first contacts={len(events)}; matching controls", flush=True)
    measured, controls, matched_audit = match_and_measure(raw, bars, events, cfg)
    print(f"Measured={measured.level_id.nunique()} matched={matched_audit}; cluster statistics", flush=True)
    summary = summarize(measured, cfg)
    out.mkdir()
    for name, frame in (("levels", levels), ("level_status", status), ("events", events),
                        ("measurements", measured), ("controls", controls)):
        frame.to_csv(out / (name + ".csv.gz"), index=False, compression=dict(method="gzip", mtime=0))
    summary.to_csv(out / "summary.csv", index=False)
    receipt = dict(experiment_id=cfg["experiment_id"], generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        builders=builders, inputs=inputs, source_rows=len(raw),
        source_gap_intervals=int((raw.ts.diff().dropna() != MINUTE).sum()),
        source_missing_minutes=int((raw.ts.iloc[-1] - raw.ts.iloc[0]) // MINUTE + 1 - len(raw)),
        complete_15m_bars=len(bars), incomplete_15m_buckets=incomplete, formation=formation,
        level_status=status.status.value_counts().to_dict(), matching=matched_audit,
        control_rows=len(controls), unique_control_minutes=controls.control_entry_ms.nunique(),
        measured_events=measured.level_id.nunique(), unique_touch_minutes=measured.touch_ms.nunique(),
        versions=dict(numpy=np.__version__, pandas=pd.__version__),
        artifacts={p.name: dict(sha256=sha(p), size_bytes=p.stat().st_size) for p in out.iterdir()},
        training_eligible=False, production_eligible=False,
        interpretation="Descriptive next-minute reaction measurements; neither hypothetical level prices nor maximum excursions are fills or an executable strategy.")
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(summary.loc[summary.category.eq("ALL") & summary.side.eq(0),
        ["period", "horizon", "n", "net_bp", "control_net_bp", "excess_bp", "p", "q_primary"]].to_string(index=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT)
    run(parser.parse_args().config)
