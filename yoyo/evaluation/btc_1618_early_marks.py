"""Count BTC 15m V2.42 glyphs in the first five candles of an excursion.

Owner 2026-10-31: all existing OKX BTC-USDT-SWAP history, 1--5 candles.
Reuse closed OHLC and the existing Pine-equivalent Stoch(5,3,3) function.
Monday levels use only the completed New York Monday. A wick excursion is
high > upper or low < lower, including candles wholly beyond the level;
one candle wholly inside/equal rearms. Week changes also reset. A signal
uses its own closed candle and prior observations only. Later excursion
length is descriptive metadata, never part of the first-five signal filter.
This is a pattern census, with no orders, return labels or profitability claim.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_fanshen_exit import compute_signals
from yoyo.evaluation.pepe_range_exits import signals as rounded_signals

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-btc-1618-early-marks-20261031-v1'
OLD = ROOT / 'experiments/active/exp-range-v231-fiveyear-20261021-v1/BTC/candles.csv.gz'
EARLY = ROOT / 'data/research/spike_v1_okx_30m_5y_20261013/okx_BTC_USDT_SWAP_1m_2672637.csv'
CACHE = Path.home() / 'Library/Application Support/Fable/ImpulseMonitor/shared-candles/cache.sqlite3'
STEP = pd.Timedelta(minutes=15)
COLS = ['open', 'high', 'low', 'close']


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def aggregate(raw, count):
    raw = raw.copy()
    raw.index = pd.to_datetime(raw.ts.astype('int64'), unit='ms', utc=True)
    assert raw.index.is_unique and raw.index.is_monotonic_increasing
    g = raw.resample('15min')
    bars = g.agg(dict(open='first', high='max', low='min', close='last'))
    return bars[g.close.count().eq(count)]


def load():
    """Reuse repaired 5m cache, older 1m prefix and a frozen recent 15m tail."""
    snapshot = EXP / 'bars.csv.gz'
    if snapshot.exists():
        bars = pd.read_csv(snapshot, index_col=0, parse_dates=True)
        return bars, json.loads((EXP / 'input_receipt.json').read_text())
    assert EARLY.exists() and OLD.exists(), 'Registered historical source is offline'
    old = aggregate(pd.read_csv(OLD, float_precision='round_trip'), 3)
    cutoff = int((old.index[0] + pd.Timedelta(days=1)).timestamp() * 1000)
    chunks = []
    for chunk in pd.read_csv(EARLY, usecols=['ts'] + COLS,
                             float_precision='round_trip', chunksize=100_000):
        chunks.append(chunk[chunk.ts < cutoff])
        if chunk.ts.max() >= cutoff:
            break
    early = aggregate(pd.concat(chunks, ignore_index=True), 15)
    with sqlite3.connect(CACHE.as_uri() + '?mode=ro', uri=True) as db:
        rows = [json.loads(r[0]) for r in db.execute(
            'SELECT raw FROM candles WHERE key=? ORDER BY t',
            ('https://www.okx.com|BTC-USDT-SWAP|15m',))]
    recent = pd.DataFrame(rows, columns=['ts'] + COLS + ['contracts', 'base', 'quote', 'confirm'])
    recent = recent[recent['confirm'].astype(str).eq('1')].copy()
    recent.index = pd.to_datetime(recent.ts.astype('int64'), unit='ms', utc=True)
    recent = recent[COLS].astype(float)
    overlaps = []
    for a, b in [(early, old), (old, recent)]:
        overlap = a.index.intersection(b.index)
        assert len(overlap), 'Sources must have observed overlap'
        assert np.allclose(a.loc[overlap], b.loc[overlap], atol=1e-8, rtol=0)
        overlaps.append(len(overlap))
    bars = pd.concat([early[early.index < old.index[0]], old,
                      recent[recent.index > old.index[-1]]])
    assert bars.index.is_unique and bars.index.is_monotonic_increasing
    assert (bars.low <= bars[COLS].min(axis=1)).all()
    assert (bars.high >= bars[COLS].max(axis=1)).all()
    gaps = bars.index[1:][np.diff(bars.index.asi8) != STEP.value]
    receipt = dict(start=str(bars.index[0]), last_open=str(bars.index[-1]),
                   closed_through=str(bars.index[-1] + STEP), bars=len(bars),
                   overlap_bars_equal=overlaps, gaps_after=[str(x) for x in gaps],
                   source_files=[dict(path=str(p), bytes=p.stat().st_size,
                                      mtime_ns=p.stat().st_mtime_ns) for p in [EARLY, OLD]],
                   recent_source=str(CACHE), recent_confirmed_bars=len(recent),
                   source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                        cwd=ROOT, text=True).strip())
    bars.to_csv(snapshot, index_label='open_time')
    dump(EXP / 'input_receipt.json', receipt)
    return bars, receipt


def census(bars):
    """Signal bar ordinal reads only same-week consecutive outside candles."""
    sig = compute_signals(bars)
    local = bars.index.tz_convert('America/New_York').tz_localize(None)
    weeks = local.normalize() - pd.to_timedelta(local.dayofweek, unit='D')
    groups = pd.Series(np.arange(len(bars)), index=weeks).groupby(level=0)
    rows, episodes, skipped = [], [], []
    for week, values in groups:
        pos = values.to_numpy()
        mon = pos[local[pos].dayofweek == 0]
        rest = pos[local[pos].dayofweek > 0]
        if len(mon) != 96 or (len(pos) > 1 and
                              not (np.diff(bars.index[pos].asi8) == STEP.value).all()):
            skipped.append(str(week.date()))
            continue
        if not len(rest):
            continue
        hi, lo = float(bars.high.iloc[mon].max()), float(bars.low.iloc[mon].min())
        if hi <= lo:
            continue
        for side, level, col in [('upper', lo + 1.618*(hi-lo), 'arrow_short'),
                                 ('lower', hi - 1.618*(hi-lo), 'arrow_long')]:
            for basis in ['wick', 'close']:
                extreme = bars.close if basis == 'close' else (bars.high if side == 'upper' else bars.low)
                outside = (extreme.iloc[rest] > level if side == 'upper' else extreme.iloc[rest] < level).to_numpy()
                hits = np.flatnonzero(outside)
                if not len(hits):
                    continue
                starts = hits[np.r_[True, np.diff(hits) > 1]]
                ends = hits[np.r_[np.diff(hits) > 1, True]]
                for a, b in zip(starts, ends):
                    first, last = int(rest[a]), int(rest[b])
                    eid = f'{week.date()}-{side}-{basis}-{bars.index[first].isoformat()}'
                    # A later inside candle proves duration; the week/data boundary censors it.
                    duration_known = b + 1 < len(rest)
                    episode = dict(episode_id=eid, week=str(week.date()), side=side, basis=basis,
                                   start=str(bars.index[first]), last=str(bars.index[last]),
                                   episode_bars=last-first+1, duration_known=duration_known)
                    episodes.append(episode)
                    for i in range(first, last + 1):
                        if not sig[col].iloc[i]:
                            continue
                        row = dict(episode, signal_open=str(bars.index[i].tz_convert('Asia/Shanghai')),
                                   signal_close=str((bars.index[i]+STEP).tz_convert('Asia/Shanghai')),
                                   ordinal=i-first+1, weekday=int(local[i].dayofweek),
                                   visible_v242=bool(local[i].dayofweek < 5),
                                   level=level, monday_high=hi, monday_low=lo,
                                   k=float(sig.k.iloc[i]), d=float(sig.d.iloc[i]),
                                   **{c: float(bars[c].iloc[i]) for c in COLS})
                        row['close_outside'] = bool(row['close'] > level if side == 'upper' else row['close'] < level)
                        rows.append(row)
    return pd.DataFrame(rows), pd.DataFrame(episodes), skipped


def counts(frame):
    return dict(signals=len(frame), episodes=int(frame.episode_id.nunique()),
                short=int(frame.side.eq('upper').sum()), long=int(frame.side.eq('lower').sum()))


def run():
    EXP.mkdir(parents=True, exist_ok=True)
    bars, receipt = load()
    marks, episodes, skipped = census(bars)
    primary = marks[marks.basis.eq('wick') & marks.visible_v242 & marks.ordinal.le(5)].copy()
    primary.sort_values(['signal_open', 'side']).to_csv(EXP/'matches.csv', index=False)
    primary[primary.episode_bars.le(5) & primary.duration_known].sort_values(
        ['signal_open', 'side']).to_csv(EXP/'short_excursion_matches.csv', index=False)
    marks.to_csv(EXP/'all_outside_marks.csv', index=False)
    episodes.to_csv(EXP/'episodes.csv', index=False)
    # Existing independent normalized implementation should agree away from warmup.
    literal = compute_signals(bars)
    rounded = rounded_signals(bars)
    difference = (literal.arrow_long.astype(int)-literal.arrow_short.astype(int)).iloc[20:] != rounded.iloc[20:]
    # Prefix check prevents later excursion duration from influencing admission.
    prefix_checks = []
    for n in [len(bars)//2, len(bars)-101]:
        prefix, _, _ = census(bars.iloc[:n])
        got = prefix[prefix.basis.eq('wick') & prefix.visible_v242 & prefix.ordinal.le(5)]
        expected = primary[pd.to_datetime(primary.signal_open, utc=True) < bars.index[n]]
        columns = ['episode_id', 'signal_open', 'ordinal', 'k', 'd', 'level']
        pd.testing.assert_frame_equal(got[columns].reset_index(drop=True),
                                      expected[columns].reset_index(drop=True))
        prefix_checks.append(dict(bars=n, matches=len(got)))
    summary = dict(symbol='OKX:BTCUSDT.P', interval='15m', source=receipt,
                   rule='V2.42 default Stoch5/3/3; closed candle; NY Tue-Fri; strict directional wick outside completed Monday1.618; first1-5 consecutive outside candles',
                   primary=counts(primary), by_ordinal={}, by_year={},
                   close_also_outside=counts(primary[primary.close_outside]),
                   whole_excursion_at_most_five=counts(primary[primary.episode_bars.le(5) & primary.duration_known]),
                   close_based_first_five=counts(marks[marks.basis.eq('close') & marks.visible_v242 & marks.ordinal.le(5)]),
                   including_weekends=counts(marks[marks.basis.eq('wick') & marks.ordinal.le(5)]),
                   skipped_incomplete_weeks=skipped, prefix_checks=prefix_checks,
                   literal_vs_rounded_signal_mismatches=int(difference.sum()),
                   training_eligible=False, production_eligible=False,
                   reproduction='.venv/bin/python -m yoyo.evaluation.btc_1618_early_marks',
                   scope='Pattern census only; no fitted score, orders, win rate, return or random-entry performance comparison.')
    for n in range(1, 6):
        summary['by_ordinal'][n] = counts(primary[primary.ordinal.eq(n)])
    for year, group in primary.groupby(pd.to_datetime(primary.signal_open, utc=True).dt.year):
        summary['by_year'][int(year)] = counts(group)
    dump(EXP/'summary.json', summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(primary.tail(12)[['signal_open', 'side', 'ordinal', 'episode_bars', 'level', 'close_outside']].to_string(index=False))


if __name__ == '__main__':
    run()
