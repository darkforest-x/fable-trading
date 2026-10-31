"""Resting pre-touch score orders plus post-alarm entries, as one Monday1618 account (owner 2026-10-31).

Owner: "回测这个组合". Single-bar crashes (all touches inside the 25-coin alarm bar, e.g. 2026-08-22 05:10) can only be
caught by a limit already resting at the 1.618, and the frozen pre-touch score picks the touches that rebound inside
those bars; the alarm rule (exp-monday-limit-alarm-entry-20261029-v1) catches the later touches without any score.
Legs, on the same rule per variant (near Monday edge or opposite 1.618, backstop, breakeven +0.17 percent, 60m, hold):
  rest     every first weekly touch whose causal score (frozen model on the 5m bar before the touch) is >= rest_cut;
  alarm    every same-side touch in the window after a >= 25-coin alarm bar closes, no score;
  combo    the union, each event once.
Fills: the alarm study's saved NautilusTrader fills are reused (alarm windows and its seeded control); only rest-leg
events it never filled are run here, with monday_limit_alarm_entry.process unchanged (same orders, adapter, fees).
The summary is read-only arithmetic: worse path, 10bp slippage on non-target exits, frac N20 3x replays by period,
the rest leg split by timing (inside the alarm bar / after the alarm / other), and the alarm study's seeded
no-alarm sample as the unscored control.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_alarm_entry as ae

A = ROOT/'experiments/active'


def source_events(src):
    return set(pd.read_csv(src/'alarm_windows.csv.gz').event) | set(pd.read_csv(src/'control_events.csv.gz').event)


def rest_events(cfg):
    """Rest-leg events: causal score >= the cut, among the first weekly touches the alarm study scored."""
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    t = ae.touches()
    t = t[t.event.isin(sc.index)].drop_duplicates('event')
    return set(t.event[t.event.map(sc.causal) >= cfg['rest_cut']])


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed('yoyo/evaluation/monday_limit_alarm_combo.py', 'yoyo/evaluation/monday_limit_alarm_entry.py',
                 'yoyo/layers/l3_backtest/nautilus_independent.py', str(config.resolve().relative_to(ROOT)))
    path = exp/'rest_events.csv.gz'
    if not path.exists():
        rest = rest_events(cfg)
        pd.DataFrame(dict(event=sorted(rest), filled_by_source=[e in source_events(ROOT/cfg['source']) for e in sorted(rest)])).to_csv(path, index=False)
    r = pd.read_csv(path)
    events = set(r.event[~r.filled_by_source])
    syms = sorted({e.split(':')[0] for e in events})
    root = exp/intrabar.lower()
    todo = [s for s in syms if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(ae.process, s, intrabar, str(exp), cfg, {e for e in events if e.startswith(s+':')}): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                out = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                out = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', out)
            print(json.dumps(dict(progress=k, total=len(todo), **out)), flush=True)


def load(exp, syms, slip, workers):
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run {exp.name}: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(ae.load_symbol, [(str(exp), s, slip) for s in syms], chunksize=4))
    return pd.concat([g[1] for g in got if len(g[1])], ignore_index=True), [f'{g[0]}: {i}' for g in got for i in g[2]]


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import replay
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed('yoyo/evaluation/monday_limit_alarm_combo.py')
    src = ROOT/cfg['source']
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    rest = pd.read_csv(exp/'rest_events.csv.gz')
    win = pd.read_csv(src/'alarm_windows.csv.gz')
    control = set(pd.read_csv(src/'control_events.csv.gz').event)
    a_src, i_src = load(src, sorted({e.split(':')[0] for e in source_events(src)}), cfg['slippage_bp'], workers)
    new = sorted({e.split(':')[0] for e in rest.event[~rest.filled_by_source]})
    a_new, i_new = load(exp, new, cfg['slippage_bp'], workers)
    if set(a_src.event) & set(a_new.event):
        raise RuntimeError('An event was filled twice')
    t = pd.concat([a_src, a_new], ignore_index=True)
    t = t[(t.act == 'hold') & ~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    t['pre'] = t.event.map(sc.causal)
    w = win[(win.threshold == cfg['threshold']) & (win.window == cfg['window_min'])].drop_duplicates('event').set_index('event').alarm_ms
    al = pd.read_csv(src/'alarms.csv.gz')
    al = al[al.threshold == cfg['threshold']]
    # Timing of each touch relative to the alarms of its side: inside the alarm bar, after it (alarm leg), or other.
    bar = t.entry_t.astype('int64')//10**6-5*60_000
    inbar = pd.Series(False, index=t.index)
    for side, g in al.groupby('side'):
        inbar |= (t.side == side) & bar.isin(set(g.ms))
    t['timing'] = np.where(t.event.isin(w.index), 'after_alarm', np.where(inbar, 'alarm_bar', 'other'))
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    rest_set = set(rest.event)
    jobs, desc = [], {}
    for v in cfg['variants']:
        g = t[t.kind == v]
        legs = {'alarm': g[g.event.isin(w.index)], 'rest': g[g.event.isin(rest_set)],
                'combo': g[g.event.isin(w.index) | g.event.isin(rest_set)], 'unscored_control': g[g.event.isin(control)]}
        for name, x in legs.items():
            jobs.append((f'{v}|{name}', 'frac', x.reset_index(drop=True), window, cfg, ro, sh, py))
        r = legs['rest']
        desc[v] = {k: dict(n=int(len(y)), mean_bp=float(y.net.mean()) if len(y) else None, sum_pct=float(y.net.sum()/100),
                           bars=int((y.entry_t.astype('int64')//10**6).nunique()))
                   for k, y in r.groupby('timing')}
    res = dict(checks=dict(rest_events=int(len(rest)), rest_new=int((~rest.filled_by_source).sum()), rows=int(len(t)),
                           issues=(i_src+i_new)[:20]), plans={}, rest_by_timing=desc)
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            res['plans'][name] = out
    # Parity: the alarm leg must equal the alarm study's 30-minute hold plan.
    old = json.loads((src/'summary.json').read_text())['results']['plans']
    res['parity_alarm'] = {v: [res['plans'][f'{v}|alarm']['all']['n'], old[f'alarm_{cfg["window_min"]}m|{v}|hold_all']['all']['n'],
                               res['plans'][f'{v}|alarm']['all'].get('replay', {}).get('final_median'),
                               old[f'alarm_{cfg["window_min"]}m|{v}|hold_all']['all'].get('replay', {}).get('final_median')]
                           for v in cfg['variants']}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False), json.dumps(res['parity_alarm']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
