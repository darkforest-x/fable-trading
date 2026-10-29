"""Frozen six-major market-stress alarm versus BTC, owner authorized 2026-10-29.

This evaluates warning times, not orders or profits. Input columns are timestamp,
OHLCV from the six registered Binance 1m histories. Signals use completed close
returns and a strictly prior 1440-minute volatility window. The outcome is the
current minute's low versus the close five bars earlier, never a rolling low
attributed to a later minute. Recovery uses future closes as descriptive labels.
No fitted classifier, new market download, trading engine, or live setting.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from yoyo.evaluation.market_crash_alarm_stats import (
    match_one_to_one, matched_time_null, paired_week_bootstrap,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'experiments/active/exp-market-crash-alarm-20261029-v1'
MINUTE = 60_000


def save(path, value):
    """Write finite JSON with NumPy/scalar and timestamp conversion."""
    def clean(x):
        if isinstance(x, dict):
            return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple, np.ndarray)):
            return [clean(v) for v in x]
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.bool_,)):
            return bool(x)
        if isinstance(x, (float, np.floating)):
            return float(x) if np.isfinite(x) else None
        if isinstance(x, (pd.Timestamp, Path)):
            return str(x)
        return x
    Path(path).write_text(json.dumps(clean(value), indent=2, allow_nan=False) + '\n')


def load_panel(cfg):
    """Read each source once; gaps stay NaN on a common actual-minute grid."""
    paths = json.loads((ROOT / cfg['source_config']).read_text())['series']
    receipts = []
    for symbol in cfg['symbols']:
        p = ROOT / paths[symbol]
        st = p.stat()
        receipts.append(dict(symbol=symbol, path=str(p.relative_to(ROOT)), bytes=st.st_size, mtime_ns=st.st_mtime_ns))
    cached = OUT / 'panel.npz'
    receipt_path = OUT / 'input_receipt.json'
    if cached.exists() and receipt_path.exists():
        previous = json.loads(receipt_path.read_text())
        if previous['sources'] != receipts:
            raise ValueError('Existing panel sources changed; do not overwrite historical inputs')
        z = np.load(cached)
        return z['times'], z['close'], z['low'], previous
    frames, checks = [], []
    for r in receipts:
        f = pd.read_csv(ROOT / r['path'], usecols=['ts', 'open', 'high', 'low', 'close', 'volume'], float_precision='round_trip')
        ts = f.ts.to_numpy(np.int64)
        if len(ts) < 2 or np.any(np.diff(ts) <= 0) or np.any(ts % MINUTE):
            raise ValueError(f'Nonunique, unsorted, or nonminute timestamps: {r["symbol"]}')
        vals = f[['open', 'high', 'low', 'close', 'volume']].to_numpy()
        if not np.isfinite(vals).all() or (vals[:, :4] <= 0).any() or (vals[:, 4] < 0).any():
            raise ValueError(f'Invalid OHLCV: {r["symbol"]}')
        if (f.high < f[['open', 'close', 'low']].max(axis=1)).any() or (f.low > f[['open', 'close', 'high']].min(axis=1)).any():
            raise ValueError(f'Invalid candle geometry: {r["symbol"]}')
        frames.append(f[['ts', 'close', 'low']].set_index('ts'))
        checks.append(dict(symbol=r['symbol'], rows=len(f), first=int(ts[0]), last=int(ts[-1]), zero_volume=int((f.volume == 0).sum())))
        print(f'Read {r["symbol"]}: {len(f)} minutes', flush=True)
    start = max(int(f.index[0]) for f in frames)
    end = min(int(f.index[-1]) for f in frames)
    times = np.arange(start, end + MINUTE, MINUTE, dtype=np.int64)
    close = np.column_stack([f.close.reindex(times).to_numpy() for f in frames])
    low = np.column_stack([f.low.reindex(times).to_numpy() for f in frames])
    receipt = dict(sources=receipts, checks=checks, common_rows=len(times), first=start, last=end,
                   missing_close_by_symbol=np.isnan(close).sum(axis=0).tolist(), content_hashes=False)
    np.savez_compressed(cached, times=times, close=close, low=low)
    save(receipt_path, receipt)
    return times, close, low, receipt


def make_scores(close, cfg):
    """Use current/previous closes and only earlier1440 returns for volatility."""
    s = cfg['score']
    returns = np.log(pd.DataFrame(close)).diff()
    sigma = returns.rolling(s['volatility_window'], min_periods=s['volatility_window']).std(ddof=0).shift(1)
    sigma = sigma.clip(lower=s['minimum_volatility'])
    downside = (-returns / sigma).clip(0, s['component_cap'])
    acceleration = ((returns.shift(1) - returns) / sigma).clip(0, s['component_cap'])
    component = (downside + acceleration).to_numpy() / 2
    r = returns.to_numpy()
    valid = np.isfinite(component).all(axis=1)
    six = np.median(component, axis=1) * (r < 0).mean(axis=1)
    btc = component[:, 0] * (r[:, 0] < 0)
    six[~valid] = np.nan
    btc[~valid] = np.nan
    return dict(btc=btc, six=six), sigma.iloc[:, 0].to_numpy(), valid


def event_flags(close, low, cfg):
    """Current low, five-bar-old close. All intervening observations required."""
    e = cfg['event']
    reference = pd.DataFrame(close).shift(e['reference_bars']).to_numpy()
    drop = low / reference - 1
    complete = np.isfinite(close).all(axis=1) & np.isfinite(low).all(axis=1)
    valid = pd.Series(complete.astype(int)).rolling(e['reference_bars'] + 1, min_periods=e['reference_bars'] + 1).min().eq(1).to_numpy()
    median = np.median(drop, axis=1)
    flag = valid & (median <= e['median_drop']) & ((drop <= e['coin_drop']).sum(axis=1) >= e['minimum_coins'])
    return flag, median, valid


def group_events(flag, median_drop, close, times, cfg):
    """Group threshold flags, retaining the first actual severe minute."""
    points = np.flatnonzero(flag)
    groups = np.split(points, np.flatnonzero(np.diff(points) > cfg['event']['merge_gap_minutes']) + 1)
    rows = []
    for group in groups:
        if not len(group):
            continue
        first, last = int(group[0]), int(group[-1])
        recovery = dict(recovered_half_at60=None, basket_trough30_pct=None, basket_close60_pct=None)
        if first >= 5 and first + 60 < len(close) and np.isfinite(close[first-5:first+61]).all():
            basket = np.median(close[first:first+61] / close[first-5], axis=1)
            trough = float(basket[:31].min())
            recovery = dict(recovered_half_at60=bool(basket[60] >= (1 + trough) / 2),
                            basket_trough30_pct=100 * (trough - 1), basket_close60_pct=100 * (basket[60] - 1))
        rows.append(dict(event=len(rows), position=first, last_position=last, flag_minutes=len(group),
                         first_open=pd.to_datetime(times[first], unit='ms', utc=True),
                         known_at=pd.to_datetime(times[first] + MINUTE, unit='ms', utc=True),
                         worst_median_drop_pct=100 * float(np.min(median_drop[group])), **recovery))
    return pd.DataFrame(rows)


def alarms(score, threshold, eligible, cooldown):
    """Chronological exceedances with a causal cooldown; never consult outcomes."""
    points = np.flatnonzero(eligible & (score >= threshold))
    kept, after = [], -cooldown
    for i in points:
        if i - after >= cooldown:
            kept.append(i)
            after = i
    return np.array(kept, dtype=np.int64)


def calibrate(score, development, cfg):
    """Match alert burden using development scores only, not outcome labels."""
    days = development.sum() / 1440
    target = int(round(days * cfg['alarm']['development_target_per_exposure_day']))
    lo, hi = 0., float(np.nanmax(score[development])) + 1
    trials = []
    for _ in range(32):
        threshold = (lo + hi) / 2
        count = len(alarms(score, threshold, development, cfg['alarm']['cooldown_minutes']))
        trials.append(dict(threshold=threshold, count=count))
        if count > target:
            lo = threshold
        else:
            hi = threshold
    best = min(trials, key=lambda x: (abs(x['count'] - target), -x['threshold']))
    return dict(**best, target_count=target, exposure_days=days, achieved_per_day=best['count'] / days, trials=trials)


def classify(alarm_pos, events, cfg, latency):
    """One-to-one timely warnings; preserve late/inside and unmatched warnings."""
    a = cfg['alarm']
    event_pos = events.position.to_numpy(int)
    matched = match_one_to_one(alarm_pos, event_pos, latency_seconds=latency,
                               min_lead=a['minimum_lead_seconds'], max_lead=a['maximum_lead_seconds'])
    arrival = (alarm_pos + 1) * 60 + latency
    first = event_pos * 60
    last = (events.last_position.to_numpy(int) + 1) * 60
    previous = np.searchsorted(first, arrival, side='right') - 1
    inside = (previous >= 0) & (arrival <= last[np.maximum(previous, 0)]) if len(events) else np.zeros(len(alarm_pos), bool)
    nxt = np.searchsorted(first, arrival, side='left')
    too_late = np.zeros(len(alarm_pos), bool)
    exists = nxt < len(first)
    too_late[exists] = first[nxt[exists]] - arrival[exists] < a['minimum_lead_seconds']
    good = matched['event_for_alarm'] >= 0
    return matched, np.where(good, 'timely', np.where(inside | too_late, 'late_or_inside', 'ordinary_false'))


def time_cells(times, volatility, development):
    """Fixed development quintiles of strictly lagged BTC volatility."""
    bounds = np.quantile(volatility[development], [.2, .4, .6, .8])
    decision = times + MINUTE
    days = decision // (1440 * MINUTE)
    week = (days + 3) // 7
    hour = decision // (60 * MINUTE) % 24
    bucket = np.searchsorted(bounds, volatility)
    return (week * 24 + hour) * 5 + bucket, week, bounds


def period_mask(times, name, exclude_design):
    decision = times + MINUTE
    start = pd.Timestamp('2025-01-01', tz='UTC') if name == '2025_2026' else pd.Timestamp(name + '-01-01', tz='UTC')
    end = pd.Timestamp('2027-01-01', tz='UTC') if name in ('2026', '2025_2026') else pd.Timestamp('2026-01-01', tz='UTC')
    mask = (decision >= start.value // 10**6) & (decision < end.value // 10**6)
    if exclude_design:
        day = pd.Timestamp('2026-08-22', tz='UTC').value // 10**6
        mask &= ~((decision >= day) & (decision < day + 1440 * MINUTE))
    return mask


def eligible_events(events, mask, keep):
    """Event membership uses its open; require the whole preceding warning window."""
    positions = events.position.to_numpy(int)
    inside = np.array([e >= 1 and bool(mask[e - 1]) for e in positions])
    complete = np.array([e >= 6 and bool(keep[e - 6:e - 1].all()) for e in positions])
    return events[inside & complete].copy().reset_index(drop=True), int((inside & ~complete).sum())


def evaluate_period(name, mask, times, eligible, scores, alarm_map, events, cells, weeks, cfg, do_null=True):
    """Full-time alarm counts and event-level recall in one chronological segment."""
    keep = mask & eligible
    exposure = keep.sum() / 1440
    # Same period/availability restriction for both detectors; guard full warning window.
    ev, excluded_events = eligible_events(events, mask, keep)
    event_pos = ev.position.to_numpy(int)
    day_ids, day_counts = np.unique((times[keep] + MINUTE) // (1440 * MINUTE), return_counts=True)
    period = dict(exposure_days=exposure, valid_minutes=int(keep.sum()), events=len(ev),
                  excluded_events_incomplete_warning_window=excluded_events,
                  utc_days_with_exposure=len(day_ids), full_utc_days=int((day_counts == 1440).sum()),
                  partial_utc_days=int((day_counts < 1440).sum()), methods={}, sensitivity={})
    records = pd.DataFrame(index=np.unique(weeks[keep]), data={'event_n': 0, 'btc_hits': 0, 'six_hits': 0,
                                                            'btc_false': 0, 'six_false': 0, 'exposure_days': 0.})
    records.index.name = 'week'
    for w, count in zip(*np.unique(weeks[keep], return_counts=True)):
        records.loc[w, 'exposure_days'] = count / 1440
    for w, count in zip(*np.unique(weeks[event_pos - 1], return_counts=True)):
        records.loc[w, 'event_n'] = count
    for method in ('btc', 'six'):
        pos = alarm_map[method][keep[alarm_map[method]]]
        matched, status = classify(pos, ev, cfg, cfg['alarm']['latency_seconds'])
        hit = matched['alarm_for_event'] >= 0
        good = status == 'timely'
        for w, count in zip(*np.unique(weeks[event_pos[hit] - 1], return_counts=True)):
            records.loc[w, method + '_hits'] = count
        for w, count in zip(*np.unique(weeks[pos[~good]], return_counts=True)):
            records.loc[w, method + '_false'] = count
        # Diagnostic minute-ranking label. No independent-minute p-values are computed.
        all_pos = np.flatnonzero(keep)
        arrival = (all_pos + 1) * 60 + cfg['alarm']['latency_seconds']
        candidate_event = np.searchsorted(event_pos * 60, arrival + cfg['alarm']['minimum_lead_seconds'])
        label = np.zeros(len(all_pos), bool)
        exists = candidate_event < len(event_pos)
        label[exists] = event_pos[candidate_event[exists]] * 60 <= arrival[exists] + cfg['alarm']['maximum_lead_seconds']
        precision = int(good.sum()) / len(pos) if len(pos) else None
        details = dict(alarms=len(pos), timely_events=int(hit.sum()), recall=float(hit.mean()) if len(hit) else None,
                       precision=precision, non_timely_per_day=int((~good).sum()) / exposure,
                       late_or_inside=int((status == 'late_or_inside').sum()), ordinary_false=int((status == 'ordinary_false').sum()),
                       median_lead_seconds=float(np.nanmedian(matched['lead_for_event'])) if hit.any() else None,
                       minute_label_prevalence=float(label.mean()),
                       minute_roc_auc=float(roc_auc_score(label, scores[method][all_pos])) if label.any() and not label.all() else None,
                       minute_average_precision=float(average_precision_score(label, scores[method][all_pos])) if label.any() else None)
        event_out = ev.copy()
        event_out['timely'] = hit
        event_out['lead_seconds'] = matched['lead_for_event']
        event_out.to_csv(OUT / f'{name}_{method}_events.csv', index=False)
        pd.DataFrame(dict(position=pos, available_at=pd.to_datetime(times[pos] + MINUTE + cfg['alarm']['latency_seconds'] * 1000, unit='ms', utc=True),
                          score=scores[method][pos], status=status, matched_event=matched['event_for_alarm'])).to_csv(OUT / f'{name}_{method}_alarms.csv', index=False)
        if do_null:
            print(f'Conditional null {name}/{method}: {len(pos)} alarms, {len(ev)} events', flush=True)
            details['conditional_null'] = matched_time_null(
                all_pos, cells[all_pos], pos, event_pos, block_ids=weeks[all_pos], latency_seconds=cfg['alarm']['latency_seconds'],
                min_lead=cfg['alarm']['minimum_lead_seconds'], max_lead=cfg['alarm']['maximum_lead_seconds'],
                cooldown_minutes=cfg['alarm']['cooldown_minutes'], repeats=cfg['statistics']['null_repeats'],
                seed=cfg['statistics']['seed'] + (method == 'six'), max_restarts=cfg['statistics']['null_max_restarts'])
        period['methods'][method] = details
        for delay in cfg['alarm']['sensitivity_latency_seconds']:
            m, _ = classify(pos, ev, cfg, delay)
            period['sensitivity'].setdefault(str(delay), {})[method] = dict(timely_events=int((m['alarm_for_event'] >= 0).sum()),
                median_lead_seconds=float(np.nanmedian(m['lead_for_event'])) if np.isfinite(m['lead_for_event']).any() else None)
    records.reset_index().to_csv(OUT / f'{name}_weekly.csv', index=False)
    period['paired_weeks'] = paired_week_bootstrap(records.reset_index(), repeats=cfg['statistics']['bootstrap_repeats'], seed=cfg['statistics']['seed'])
    return period


def main():
    start_clock = time.monotonic()
    cfg = json.loads((OUT / 'config.json').read_text())
    times, close, low, receipt = load_panel(cfg)
    scores, vol, feature_valid = make_scores(close, cfg)
    flag, median_drop, target_valid = event_flags(close, low, cfg)
    future_covered = pd.Series(target_valid.astype(int)).rolling(6, min_periods=6).min().shift(-6).eq(1).to_numpy()
    eligible = feature_valid & future_covered
    events = group_events(flag, median_drop, close, times, cfg)
    events.to_csv(OUT / 'all_events.csv', index=False)
    end = pd.Timestamp(cfg['development_end']).value // 10**6
    development = eligible & (times + MINUTE < end)
    calibration = {m: calibrate(score, development, cfg) for m, score in scores.items()}
    save(OUT / 'calibration.json', calibration)
    alarm_map = {m: alarms(score, calibration[m]['threshold'], eligible, cfg['alarm']['cooldown_minutes']) for m, score in scores.items()}
    cells, weeks, vol_bounds = time_cells(times, vol, development)
    np.savez_compressed(OUT / 'score_cache.npz', times=times, btc=scores['btc'], six=scores['six'], eligible=eligible,
                        volatility=vol, cells=cells, weeks=weeks, btc_alarms=alarm_map['btc'], six_alarms=alarm_map['six'])
    # Monthly burden is diagnostic; never reselect a threshold on it.
    monthly = []
    months = pd.to_datetime(times + MINUTE, unit='ms', utc=True).strftime('%Y-%m')
    for month in np.unique(months[eligible]):
        mask = (months == month) & eligible
        row = dict(month=month, exposure_days=mask.sum() / 1440)
        for method, pos in alarm_map.items():
            row[method + '_alarms'] = int(mask[pos].sum())
        monthly.append(row)
    pd.DataFrame(monthly).to_csv(OUT / 'monthly_burden.csv', index=False)
    summary = dict(experiment_id=cfg['experiment_id'], builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   source_rows=len(times), valid_minutes=int(eligible.sum()), total_threshold_events=len(events),
                   source_start=pd.to_datetime(times[0], unit='ms', utc=True), source_end=pd.to_datetime(times[-1] + MINUTE, unit='ms', utc=True),
                   calibration=calibration, volatility_quintile_bounds=vol_bounds, results={},
                   limitations=cfg['statistics']['interpretation'], training_eligible=False, production_eligible=False)
    for name in cfg['evaluation_periods']:
        summary['results'][name] = evaluate_period(name, period_mask(times, name, True), times, eligible, scores, alarm_map, events, cells, weeks, cfg)
        save(OUT / 'summary.json', summary)
    day = pd.Timestamp(cfg['design_day_excluded_from_primary'], tz='UTC').value // 10**6
    design_mask = ((times + MINUTE >= day) & (times + MINUTE < day + 1440 * MINUTE))
    summary['design_day'] = evaluate_period('design_day', design_mask, times, eligible, scores, alarm_map, events, cells, weeks, cfg, do_null=False)
    summary['elapsed_seconds'] = time.monotonic() - start_clock
    save(OUT / 'summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k not in ('calibration', 'results')}, default=str, indent=2), flush=True)
    print('Saved summary.json', flush=True)


if __name__ == '__main__':
    main()
