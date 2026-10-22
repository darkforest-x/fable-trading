"""Post-touch states: does early reaction reveal whether a Monday1618 holds?

Stage2 of the owner Notion task 区间1.618测试, after exp-monday-1618-validity
showed pre-touch features do not generalize (later AUC0.52). Reads the frozen
touch ledger and the same15m bars; no new touches or features at touch time.

At checkpoint k completed15m bars after the touch bar (k=4/8/16 = 1h/2h/4h),
only still-unresolved touches (no2.0 extension, no Monday edge yet) qualify.
State uses bars touch..checkpoint only:
  inside   checkpoint close back inside the1618 (short below, long above)
  clean    no15m close beyond the1618 since the touch
  shallow  max excursion beyond the1618 < 0.15R (1618..1.768)
Entry at the checkpoint close; SL2.0 extension, TP Monday edge, both fixed in
price, so reward/risk varies by entry. Later bars only; same bar both = stop.
Null: 3 random bar closes per entry, same symbol/NY week/side and identical
price distances to stop and target, same conservative ordering. Selection of
one cell (12 = 3 checkpoints x 4 states) on the earlier period by excess mean
R with n>=200; the later period judges only that frozen cell.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_1618_validity as v

EXP = ROOT/'experiments/active/exp-monday-1618-post-touch-20261023-v1'
STATES = ('unresolved', 'inside', 'inside_clean', 'inside_shallow')


def resolve(h, l, c, start, end, side, stop, edge):
    """First of stop/edge from bar start; ties count as stop. Returns (valid, resolved, exit)."""
    sh = (l[start:end] <= stop) if side == 1 else (h[start:end] >= stop)
    eh = (h[start:end] >= edge) if side == 1 else (l[start:end] <= edge)
    s = int(np.argmax(sh)) if sh.any() else 10**9
    e = int(np.argmax(eh)) if eh.any() else 10**9
    if e < s:
        return True, True, edge
    if s < 10**9:
        return False, True, stop
    return False, False, c[end-1]


def states(f, rows, cfg, rng):
    h, l, c = (f[k].to_numpy() for k in ('high','low','close'))
    local = f.index.tz_convert(v.TZ)
    week = np.array([str(w.date()) for w in (local.normalize()-pd.to_timedelta(local.dayofweek, unit='D')).tz_localize(None)])
    eligible = np.asarray(local.dayofweek >= 1)
    horizon = cfg['horizon_days']*96
    out, controls = [], []
    for r in rows.itertuples():
        t, side = int(r.touch_i), int(r.side)
        for k in cfg['checkpoints']:
            j = t+k
            if j+1 >= len(f):
                continue
            seg = slice(t, j+1)
            stopped = (l[seg] <= r.stop).any() if side == 1 else (h[seg] >= r.stop).any()
            edged = (h[t+1:j+1] >= r.edge).any() if side == 1 else (l[t+1:j+1] <= r.edge).any()
            if stopped or edged:
                continue
            extreme = l[seg].min() if side == 1 else h[seg].max()
            exc = side*(r.level-extreme)/r.R
            inside = side*(c[j]-r.level) > 0
            clean = not (side*(c[seg]-r.level) < 0).any()
            entry = c[j]
            valid, resolved, exit_ = resolve(h, l, c, j+1, min(len(f), j+1+horizon), side, r.stop, r.edge)
            risk = side*(entry-r.stop); reward = side*(r.edge-entry)
            row = dict(symbol=r.symbol, week=r.week, side=side, segment=r.segment, k=k, entry_i=j,
                exc=exc, inside=inside, clean=clean, shallow=exc < cfg['shallow_R'],
                valid=valid, resolved=resolved, r=side*(exit_-entry)/risk, rr=reward/risk,
                driftless=risk/(risk+reward), touch_valid=r.valid)
            out.append(row)
            pool = np.flatnonzero(eligible & (week == r.week))
            pool = pool[(pool+1 < len(f)) & (np.abs(pool-j) > 4)]
            for q in rng.choice(pool, min(cfg['controls_per_entry'], len(pool)), replace=False):
                e0 = c[q]; stop = e0-side*risk; edge = e0+side*reward
                ok, res, ex = resolve(h, l, c, q+1, min(len(f), q+1+horizon), side, stop, edge)
                controls.append(dict(symbol=r.symbol, week=r.week, side=side, segment=r.segment, k=k,
                    entry_i=j, valid=ok, resolved=res, r=side*(ex-e0)/risk, inside=inside, clean=clean, shallow=exc < cfg['shallow_R']))
    return out, controls


def mask(frame, state):
    if state == 'unresolved':
        return np.ones(len(frame), bool)
    m = frame.inside.to_numpy(bool)
    if state == 'inside_clean':
        m &= frame.clean.to_numpy(bool)
    if state == 'inside_shallow':
        m &= frame.shallow.to_numpy(bool)
    return m


def cluster_p(paired, seed, draws=5000):
    """One-sided week-cluster sign flip on per-week summed excess R."""
    rng = np.random.default_rng(seed)
    w = paired.groupby('week').excess.sum().to_numpy()
    obs = w.sum()
    flips = rng.choice([-1, 1], size=(draws, len(w)))
    return float(((flips*w).sum(1) >= obs).mean()*draws/(draws+1)+1/(draws+1))


def cell(sig, ctl, k, state, seed):
    s = sig[(sig.k == k) & mask(sig, state)]
    c = ctl[(ctl.k == k) & mask(ctl, state)]
    base = c.groupby(['symbol','entry_i','k']).r.mean().rename('random_r')
    paired = s.join(base, on=['symbol','entry_i','k'])
    paired['excess'] = paired.r-paired.random_r
    n = len(s)
    return dict(k=k, state=state, n=n, valid_pct=float(100*s.valid.mean()) if n else None,
        driftless_pct=float(100*s.driftless.mean()) if n else None, mean_r=float(s.r.mean()) if n else None,
        random_mean_r=float(c.r.mean()) if len(c) else None,
        excess_r=float(paired.excess.mean()) if n else None, mean_rr=float(s.rr.mean()) if n else None,
        p_one_sided=cluster_p(paired.dropna(subset=['excess']), seed) if n else None, unresolved=int((~s.resolved).sum()))


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_post_touch.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    source = ROOT/cfg['touch_ledger']
    touches = pd.read_csv(source/'touches.csv.gz')
    vcfg = json.loads((source/'config.json').read_text())
    files = v.universe(vcfg); rng = np.random.default_rng(cfg['seed'])
    sig, ctl = [], []
    for symbol, rows in touches.groupby('symbol'):
        f = v.load(files[symbol]); f = f[f.index >= pd.Timestamp(vcfg['start'])]
        a, b = states(f, rows, cfg, rng); sig += a; ctl += b
    sig, ctl = pd.DataFrame(sig), pd.DataFrame(ctl)
    sig.to_csv(EXP/'entries.csv.gz', index=False); ctl.to_csv(EXP/'controls.csv.gz', index=False)
    grid = {seg: [cell(sig[sig.segment == seg], ctl[ctl.segment == seg], k, st, cfg['seed'])
                  for k in cfg['checkpoints'] for st in STATES] for seg in (0, 1)}
    eligible = [x for x in grid[0] if x['n'] >= cfg['min_n']]
    best = max(eligible, key=lambda x: x['excess_r'])
    later = next(x for x in grid[1] if x['k'] == best['k'] and x['state'] == best['state'])
    focus = {}
    for symbol in cfg['focus']:
        s = sig[sig.symbol == symbol]
        focus[symbol] = cell(s, ctl[ctl.symbol == symbol], best['k'], best['state'], cfg['seed'])
    passed = bool(later['mean_r'] > 0 and later['excess_r'] > 0 and later['p_one_sided'] < .01)
    summary = dict(config=cfg, entries=len(sig), controls=len(ctl), earlier=grid[0], later=grid[1],
        selected=dict(earlier=best, later=later, passed=passed), focus=focus,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(selected=summary['selected'], focus=focus), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
