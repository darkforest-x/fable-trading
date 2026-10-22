"""Owner TradingView1618 positions versus every weekly1618 touch (BTC, XAU).

Owner2026-10-23 asked for the regularities behind the long/short position
tools drawn on OKX BTCUSDT.P and XAUUSDT.P (layout AlGc61US, read-only export
in drawings_raw.txt). Each drawing is located in its NY Monday range: entry,
stop and target are expressed as Fib ratios ((price-L)/(H-L)), matched to the
first same-side1618 touch of that week, and resolved as drawn on15m bars
(stop-first ties). All weekly touches over the drawing period get the same
objective label as exp-monday-1618-validity (Monday edge before2.0) so drawn
and undrawn touches can be compared. Descriptive only: the drawings carry
hindsight targets and are not evidence of a causal edge.

Bars: BTC = OKX1m (exp-btc-monday-fib1618-okx) aggregated to15m, extended by
the frozen fresh OKX15m fetch after its end; XAU = frozen fresh OKX15m.
"""
from __future__ import annotations
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v

EXP = ROOT/'experiments/active/exp-monday-1618-owner-drawings-20261023-v1'
TICK = .1


def drawings():
    rows, symbol = [], None
    for line in (EXP/'drawings_raw.txt').read_text().splitlines():
        line = line.strip()
        if line.startswith('['):
            symbol = 'BTC' if 'BTC' in line else 'XAU'; continue
        if not line or line.startswith('#') or 'position' not in line:
            continue
        name, ident, p0, _p1, sl, tp = line.split(',')
        t, price = p0.split('@'); price = float(price)
        side = 1 if name.startswith('long') else -1
        rows.append(dict(symbol=symbol, id=ident, side=side, drawn_bar=pd.Timestamp(int(t), unit='s', tz='UTC'),
            entry=price, stop=round(price-side*int(sl[2:])*TICK, 1), target=round(price+side*int(tp[2:])*TICK, 1),
            rr=int(tp[2:])/int(sl[2:])))
    return pd.DataFrame(rows)


def bars(symbol):
    fresh = v.load(EXP/f'inputs/okx_{symbol}_USDT_SWAP_15m_fresh_20261022.csv')
    if symbol != 'BTC':
        return fresh
    raw = pd.read_csv(ROOT/'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1/candles.csv.gz')
    m = raw.set_index(pd.to_datetime(raw.ts, unit='ms', utc=True))
    g = m.resample('15min', closed='left', label='left')
    q = g.agg(dict(open='first', high='max', low='min', close='last'))[g.close.count().eq(15)]
    q['volume'] = np.nan  # the1m archive has no volume; only vol_ratio uses it
    return pd.concat([q, fresh[fresh.index > q.index[-1]]])


def first_hit(h, l, start, end, side, stop, target, skip_first_target=True):
    sh = (l[start:end] <= stop) if side == 1 else (h[start:end] >= stop)
    th = (h[start:end] >= target) if side == 1 else (l[start:end] <= target)
    if skip_first_target and len(th):
        th[0] = False
    s = int(np.argmax(sh)) if sh.any() else 10**9
    t = int(np.argmax(th)) if th.any() else 10**9
    return ('TP' if t < s else 'SL' if s < 10**9 else 'open'), min(s, t)


def run():
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_owner_drawings.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    d = drawings(); out, weekly = [], []
    for symbol, group in d.groupby('symbol'):
        f = bars(symbol)
        touches = pd.DataFrame(v.touches(f, symbol, dict(horizon_days=14)))
        h, l = f.high.to_numpy(), f.low.to_numpy()
        local = f.index.tz_convert(v.TZ)
        for r in group.itertuples():
            ny = r.drawn_bar.tz_convert(v.TZ)
            week = str((ny.normalize()-pd.Timedelta(days=ny.dayofweek)).date())
            mon = f[(local.normalize() == ny.normalize()-pd.Timedelta(days=ny.dayofweek))]
            row = dict(symbol=symbol, id=r.id, side=r.side, week=week, drawn_bar=str(r.drawn_bar), entry=r.entry,
                       stop=r.stop, target=r.target, rr=r.rr, stop_pct=100*abs(r.entry-r.stop)/r.entry)
            if len(mon) != 96:
                out.append(dict(row, status='no_monday_data')); continue
            H, L = mon.high.max(), mon.low.min(); R = H-L
            row.update(entry_ratio=(r.entry-L)/R, stop_ratio=(r.stop-L)/R, target_ratio=(r.target-L)/R)
            # Entry fill: first15m bar trading the drawn price within +-12h of the drawn4h bar.
            lo = f.index.searchsorted(r.drawn_bar-pd.Timedelta(hours=12)); hi = f.index.searchsorted(r.drawn_bar+pd.Timedelta(hours=16))
            fills = np.flatnonzero((l[lo:hi] <= r.entry) & (h[lo:hi] >= r.entry))
            if len(fills):
                i = lo+int(fills[0])
                outcome, n = first_hit(h, l, i, len(f), r.side, r.stop, r.target)
                row.update(fill_time=str(f.index[i]), drawn_outcome=outcome, bars_to_outcome=n)
            t = touches[(touches.week == week) & (touches.side == r.side)]
            if len(t):
                t = t.iloc[0]
                row.update(first_touch=t.touch_time, hours_after_first_touch=(r.drawn_bar-pd.Timestamp(t.touch_time)).total_seconds()/3600,
                           level_gap_pct=100*(r.entry-t.level)/t.level, objective_valid=bool(t.valid))
            out.append(row)
        span = touches[(pd.to_datetime(touches.touch_time) >= group.drawn_bar.min()-pd.Timedelta(days=7))]
        drawn = {(x['week'], x['side']) for x in out if x['symbol'] == symbol and 'first_touch' in x}
        for t in span.itertuples():
            weekly.append(dict(symbol=symbol, week=t.week, side=t.side, touch_time=t.touch_time, valid=bool(t.valid),
                               drawn=(t.week, t.side) in drawn))
    table, touches_all = pd.DataFrame(out), pd.DataFrame(weekly)
    table.to_csv(EXP/'drawings_located.csv', index=False); touches_all.to_csv(EXP/'period_touches.csv', index=False)
    summary = {}
    for symbol, g in table.groupby('symbol'):
        located = g[g.entry_ratio.notna()]
        near = located[np.where(located.side == 1, (located.entry_ratio+.618).abs(), (located.entry_ratio-1.618).abs()) <= .03]
        w = touches_all[touches_all.symbol == symbol]
        summary[symbol] = dict(drawings=len(g), longs=int(g.side.eq(1).sum()), shorts=int(g.side.eq(-1).sum()),
            entry_within_003R_of_1618=len(near), drawn_outcomes=g.drawn_outcome.value_counts().to_dict(),
            stop_ratio_short_median=float(located[located.side == -1].stop_ratio.median()) if (located.side == -1).any() else None,
            stop_ratio_long_median=float(located[located.side == 1].stop_ratio.median()) if (located.side == 1).any() else None,
            stop_pct_median=float(g.stop_pct.median()), rr_median=float(g.rr.median()), rr_range=[float(g.rr.min()), float(g.rr.max())],
            hours_after_first_touch_median=float(g.hours_after_first_touch.median()),
            entered_after_4h_from_first_touch=int((g.hours_after_first_touch > 4).sum()),
            period_touches=len(w), drawn_touches=int(w.drawn.sum()),
            drawn_objective_valid_pct=float(100*w[w.drawn].valid.mean()) if w.drawn.any() else None,
            undrawn_objective_valid_pct=float(100*w[~w.drawn].valid.mean()) if (~w.drawn).any() else None)
    dump(EXP/'summary.json', dict(summary=summary, note='Descriptive; drawings are hindsight winners and not causal evidence.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
