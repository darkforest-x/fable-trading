"""First outer-rail retest after a same-bar reclaim, using each event’s validated OKX candle source."""
import csv
import gzip
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from fetch_windows import _cache_path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WINDOWS = ROOT / 'data/research/month-first-week-okx-archive5m-v2/windows'
SOURCE = HERE.parent / 'exp-alt-month-first-week-entries-20261110-v1/events.csv.gz'
NY = ZoneInfo('America/New_York')
STEP = 300_000


def observe(event, symbol, month, side, level, near, touch_ms, source):
    """Use only the 12 bars after a completed reclaim for the retest. Measure its subsequent 12 bars separately."""
    native = source == 'okx_native_5m'
    path = _cache_path(event) if native else WINDOWS / symbol / (month + '-' + side + '.json.gz')
    with gzip.open(path, 'rt') as f:
        payload = json.load(f)
    if payload['event_id'] != event:
        raise ValueError('source event identity mismatch')
    if native:
        bars = {int(b['ts_ms']): (int(b['ts_ms']), *(float(b[k]) for k in ('open','high','low','close')))
                for b in payload['candles']}
    else:
        bars = {int(b[0]): b for b in payload['candles']}
    upper = side == 'upper'

    def crosses(bar):
        return bar[2] >= level if upper else bar[3] <= level

    def reaches_near(bar):
        return bar[3] <= near if upper else bar[2] >= near

    def window(start):
        times = [start + i * STEP for i in range(1, 13)]
        last = datetime.fromtimestamp(times[-1] / 1000, NY)
        if last.strftime('%Y-%m') != month or any(t not in bars for t in times):
            return []
        return [bars[t] for t in times]

    signal = bars.get(touch_ms)
    if not signal or not crosses(signal) or not (signal[4] < level if upper else signal[4] > level):
        return None, 'source_disagrees_on_signal'
    post = window(touch_ms)
    if not post:
        return None, 'incomplete_first_hour'
    out = dict(event_id=event, symbol=symbol, month=month, side=side,
               touch_utc=datetime.fromtimestamp(touch_ms / 1000, timezone.utc).isoformat(),
               retest='none', retest_bar=None, near_by_retest=None,
               hour_after_retest_observed=None, inward_close_from_next_open=None,
               favorable_pct=None, adverse_pct=None)
    for index, b in enumerate(post):
        if not crosses(b):
            continue
        signed = (level - b[4]) if upper else (b[4] - level)
        out.update(retest='inside' if signed > 0 else 'outside' if signed < 0 else 'equal',
                   retest_bar=index + 1, near_by_retest=any(reaches_near(x) for x in [signal] + post[:index + 1]))
        following = window(int(b[0]))
        out['hour_after_retest_observed'] = bool(following)
        if following:
            base = following[0][1]
            hi = max(x[2] for x in following) / base
            lo = min(x[3] for x in following) / base
            end = following[-1][4] / base - 1
            out.update(inward_close_from_next_open=(end < 0 if upper else end > 0),
                       favorable_pct=100 * (1 - lo if upper else hi - 1),
                       adverse_pct=100 * (hi - 1 if upper else 1 - lo))
        break
    return out, 'included'


def main():
    with gzip.open(SOURCE, 'rt') as f:
        original = {e['event_id']: e for e in csv.DictReader(f)}
    rows, counts = [], Counter()
    with gzip.open(HERE / 'event_paths.csv.gz', 'rt') as f:
        for e in csv.DictReader(f):
            if e['category'] != 'A':
                continue
            raw = original[e['event_id']]
            near = float(raw['high' if raw['side'] == 'upper' else 'low'])
            ts = int(datetime.fromisoformat(e['primary_first_touch_5m_utc'].replace('Z', '+00:00')).timestamp() * 1000)
            row, status = observe(e['event_id'], raw['symbol'], raw['month'], raw['side'],
                                  float(raw['level']), near, ts, e['primary_5m_source'])
            counts[status] += 1
            if row:
                row['primary_5m_source'] = e['primary_5m_source']
                expected_retest = e['primary_post_reclaim_1h_retouch_outer'] == 'true'
                if (row['retest'] != 'none') != expected_retest:
                    raise ValueError('first retest disagrees with event observation for ' + row['event_id'])
                rows.append(row)
    if not rows:
        raise ValueError('no eligible same-bar reclaims')
    with gzip.open(HERE / 'first_retests.csv.gz', 'wt', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    def summarize(group):
        full = [r for r in group if r['hour_after_retest_observed'] and not r['near_by_retest']]
        return dict(n=len(group), symbols=len({r['symbol'] for r in group}),
                    first_retest_by_close=dict(Counter(r['retest'] for r in group)),
                    observations_with_near_not_already_hit=len(full),
                    inward_close_next_hour=sum(r['inward_close_from_next_open'] for r in full),
                    median_favorable_next_hour_pct=median(r['favorable_pct'] for r in full) if full else None,
                    median_adverse_next_hour_pct=median(r['adverse_pct'] for r in full) if full else None)
    summary = dict(eligibility=counts, all=summarize(rows),
                   by_retest={k:summarize([r for r in rows if r['retest'] == k]) for k in ('inside','outside','equal','none')},
                   by_side={k:summarize([r for r in rows if r['side'] == k]) for k in ('upper','lower')},
                   horizon='First 12 complete same-month bars after A. Following outcomes from the next open after first retest through 12 more full bars, excluding near-boundary touches before/on retest, including the initial reclaim bar. No simulated orders or costs.')
    (HERE / 'first_retests_summary.json').write_text(json.dumps(summary,indent=2)+'\n')


if __name__ == '__main__':
    main()
