"""Is the Monday NY1618 special? Levels and anchors under one geometry (method17).

Owner2026-10-23 chose this. Every level is tested with the same barrier
proportions as the1618 trade: a level at distance d=x*R beyond the anchor's
near edge (x=0.618 is the1618), target = back to that edge (distance d),
stop = d*0.618 further out. Driftless odds are therefore38.2percent for
every row, so validity is directly comparable.
Levels x: 0.272, 0.414, 0.618, 1.0 (1.272/1.414/1.618/2.0) and one uniform
random x in [0.2, 1.2] per anchor-side (null level).
Anchors: monday_ny (NY Monday, observe Tue..next Mon NY), monday_utc (UTC
Monday, observe Tue..next Mon UTC), prev_week (previous NY week high/low,
observe the whole current NY week). First touch per anchor-side-level;
15m bars; touch bar can only fail, later same-bar both = fail; 14-day mark.
Datasets: Binance alts2020-01..2025-05 (alt-hours inputs) and OKX swaps
2025-06..2026-08 (validity universe), time-disjoint.
Pass (frozen): monday_ny x=0.618 validity minus the pooled other levels and
random of the same anchor >0 with week-cluster bootstrap95 lower bound >0
in BOTH datasets.
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
from yoyo.evaluation.monday_1618_flow import klines

EXP = ROOT/'experiments/active/exp-monday-1618-levels-20261023-v1'
LEVELS = [0.272, 0.414, 0.618, 1.0]
K = .382/.618


def anchors(f):
    """Yield (anchor, week_key, anchor bar positions, observation positions)."""
    ny = f.index.tz_convert(v.TZ); utc = f.index
    pos = np.arange(len(f))
    wk_ny = (ny.normalize()-pd.to_timedelta(ny.dayofweek, unit='D')).tz_localize(None)
    wk_utc = (utc.normalize()-pd.to_timedelta(utc.dayofweek, unit='D')).tz_localize(None)
    s_ny = pd.Series(pos, index=wk_ny); s_utc = pd.Series(pos, index=wk_utc)
    weeks = sorted(set(wk_ny))
    for k, w in enumerate(weeks):
        p = s_ny.loc[[w]].to_numpy()
        mon = p[ny[p].dayofweek == 0]
        if len(mon) == 96 and mon[-1]-mon[0] == 95:
            yield 'monday_ny', str(w.date()), mon, p[p > mon[-1]]
        if k and weeks[k-1] == w-pd.Timedelta(days=7):
            prev = s_ny.loc[[weeks[k-1]]].to_numpy()
            if len(prev) >= 600:
                yield 'prev_week', str(w.date()), prev, p
    for w in sorted(set(wk_utc)):
        p = s_utc.loc[[w]].to_numpy()
        mon = p[utc[p].dayofweek == 0]
        if len(mon) == 96 and mon[-1]-mon[0] == 95:
            yield 'monday_utc', str(w.date()), mon, p[p > mon[-1]]


def resolve(h, l, t, end, side, stop, edge):
    sh = (l[t:end] <= stop) if side == 1 else (h[t:end] >= stop)
    eh = (h[t+1:end] >= edge) if side == 1 else (l[t+1:end] <= edge)
    s = int(np.argmax(sh)) if sh.any() else 10**9
    e = int(np.argmax(eh))+1 if eh.any() else 10**9
    return e < s


def events(f, symbol, rng, horizon=14*96):
    h, l = f.high.to_numpy(), f.low.to_numpy()
    out = []
    for anchor, week, a, obs in anchors(f):
        if not len(obs) or obs[0] < 1000:
            continue
        H, L = h[a].max(), l[a].min(); R = H-L
        if R <= 0:
            continue
        for side in (1, -1):
            edge = L if side == 1 else H
            xs = [(str(x), x) for x in LEVELS]+[('random', float(rng.uniform(.2, 1.2)))]
            for name, x in xs:
                d = x*R; level = edge-side*d; stop = level-side*K*d
                hit = (l[obs] <= level) if side == 1 else (h[obs] >= level)
                if not hit.any():
                    continue
                t = int(obs[np.argmax(hit)])
                out.append(dict(symbol=symbol, anchor=anchor, week=week, side=side, level=name,
                                touch_time=str(f.index[t]), valid=resolve(h, l, t, min(len(f), t+horizon), side, stop, edge)))
    return out


def boot_diff(d, rng, draws=1000):
    """Week-cluster bootstrap of validity(1618) minus validity(other levels)."""
    g = {w: x for w, x in d.groupby('week')}; weeks = list(g)
    vals = []
    for _ in range(draws):
        s = pd.concat([g[w] for w in rng.choice(weeks, len(weeks))])
        a = s[s.level == '0.618'].valid.mean(); b = s[s.level != '0.618'].valid.mean()
        vals.append(100*(a-b))
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_levels.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    rng = np.random.default_rng(cfg['seed'])
    alt_root = ROOT/cfg['binance_alt_inputs']
    sets = {}
    rows = []
    for s in sorted(p.name for p in (alt_root/'klines15m').iterdir()):
        f = klines(alt_root, s)
        f = f[(f.index >= pd.Timestamp(cfg['binance_start'])) & (f.index < pd.Timestamp(cfg['binance_end'])+pd.Timedelta(days=14))]
        if len(f) < 2000:
            continue
        rows += [r for r in events(f, s[:-4], rng) if pd.Timestamp(r['touch_time']) < pd.Timestamp(cfg['binance_end'])]
    sets['binance_alts'] = pd.DataFrame(rows)
    vcfg = json.loads((ROOT/cfg['okx_universe_config']).read_text())
    rows = []
    for s, path in v.universe(vcfg).items():
        f = v.load(path); f = f[f.index >= pd.Timestamp(vcfg['start'])]
        rows += events(f, s, rng)
    sets['okx'] = pd.DataFrame(rows)
    result = {}
    for name, d in sets.items():
        d.to_csv(EXP/f'events_{name}.csv.gz', index=False)
        tab = d.groupby(['anchor', 'level']).valid.agg(['size', 'mean']).reset_index()
        cell = {f'{r.anchor}|{r.level}': dict(n=int(r.size), valid_pct=float(100*r.mean)) for r in tab.itertuples()}
        diff = {a: dict(diff_pp=float(100*(g[g.level == '0.618'].valid.mean()-g[g.level != '0.618'].valid.mean())),
                        boot95=boot_diff(g, rng)) for a, g in d.groupby('anchor')}
        result[name] = dict(events=len(d), cells=cell, monday_1618_vs_rest=diff)
    passed = bool(all(result[n]['monday_1618_vs_rest']['monday_ny']['boot95'][0] > 0 for n in result))
    summary = dict(config=cfg, driftless_pct=100*.382, results=result, passed=passed,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    dump(EXP/'summary.json', summary)
    print(json.dumps(dict(results=result, passed=passed), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--config', type=Path, default=EXP/'config.json')
    args = parser.parse_args(); EXP = args.config.resolve().parent; run()
