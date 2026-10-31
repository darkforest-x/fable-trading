"""Threshold robustness of the 25-coin alarm-entry rule (owner 2026-11-01).

Owner: "这个25是最好的参数？" then "先测门槛稳健性". 25 came from exp-monday-spike-alert-quality-20261027-v1 (paper-trade
quality by cluster size, OKX count), and exp-monday-limit-alarm-entry-20261029-v1 backtested only 25. This replays the
same rule at several thresholds with everything else fixed: an alarm when >= N distinct symbols touch their same-side
Monday 1.618 within 5 minutes (30-minute cooldown, known at the alarm bar's close), then every later same-side first
weekly touch for 30 minutes, a resting limit at the level, no score, hold (near30, opp30, near18: backstop, breakeven
+0.17 percent, 60m), frac N20 3x. The question is whether neighbouring thresholds give similar results; every threshold
was looked at on the same history, so the best one is not a new parameter.
Fills: the saved NautilusTrader fills of the alarm study and of exp-monday-limit-alarm-combo-20261031-v1 are reused;
only events neither run filled are run here, with monday_limit_alarm_entry.process unchanged (same orders, adapter,
fees). Threshold 25 must equal the alarm study's 30-minute hold plan exactly. The summary is read-only arithmetic:
worse path, 10bp slippage on non-target exits, frac N20 3x by period, alarm episodes as the independent unit (seeded
bootstrap over episodes), and the alarm study's seeded no-alarm sample as the unscored control.
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
from yoyo.evaluation.monday_limit_alarm_combo import load, source_events

A = ROOT/'experiments/active'


def filled(cfg):
    """Events whose fills already exist: the alarm study's windows and control, and the combo run's rest events."""
    combo = pd.read_csv(ROOT/cfg['combo']/'rest_events.csv.gz')
    return source_events(ROOT/cfg['source']), set(combo.event[~combo.filled_by_source])


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed('yoyo/evaluation/monday_limit_alarm_threshold.py', 'yoyo/evaluation/monday_limit_alarm_entry.py',
                 'yoyo/layers/l3_backtest/nautilus_independent.py', str(config.resolve().relative_to(ROOT)))
    path = exp/'alarm_windows.csv.gz'
    if not path.exists():
        win, _, al = ae.windows({**cfg, 'windows_min': [cfg['window_min']], 'seed': 0, 'control_share': 0.})
        win.to_csv(path, index=False)
        pd.concat([a.assign(threshold=n) for n, a in al.items()]).to_csv(exp/'alarms.csv.gz', index=False)
    src, combo = filled(cfg)
    events = set(pd.read_csv(path).event)-src-combo
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


def episode_ci(net, episode, seed, draws=2000):
    """Mean over alarm episodes of the per-episode mean net (bp), with a seeded bootstrap 95 percent interval."""
    per = net.groupby(episode).mean().to_numpy()
    if not len(per):
        return dict(episodes=0)
    rng = np.random.default_rng(seed)
    boot = per[rng.integers(0, len(per), (draws, len(per)))].mean(1)
    return dict(episodes=int(len(per)), episode_mean_bp=float(per.mean()), episodes_positive_pct=float(100*(per > 0).mean()),
                ci95_bp=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))])


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import replay
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed('yoyo/evaluation/monday_limit_alarm_threshold.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    src, combo = ROOT/cfg['source'], ROOT/cfg['combo']
    win = pd.read_csv(exp/'alarm_windows.csv.gz')
    al = pd.read_csv(exp/'alarms.csv.gz')
    control = set(pd.read_csv(src/'control_events.csv.gz').event)
    old, rest = filled(cfg)
    new = set(win.event)-old-rest
    parts, issues = [], []
    for e, keys in ((src, old), (combo, rest), (exp, new)):
        x, i = load(e, sorted({k.split(':')[0] for k in keys}), cfg['slippage_bp'], workers)
        parts.append(x[x.event.isin(keys)]); issues += i
    if sum(len(set(p.event)) for p in parts) != len(set().union(*[set(p.event) for p in parts])):
        raise RuntimeError('An event was filled twice')
    t = pd.concat(parts, ignore_index=True)
    t = t[(t.act == 'hold') & ~t.event.isin(BAD_EVENTS)].copy()
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    jobs, eps, alarms = [], {}, {}
    for n in cfg['thresholds']:
        w = win[win.threshold == n].drop_duplicates('event').set_index('event').alarm_ms
        alarms[str(n)] = dict(alarms=int((al.threshold == n).sum()), alarms_with_touch=int(w.nunique()), events=int(len(w)))
        for v in cfg['variants']:
            x = t[(t.kind == v) & t.event.isin(w.index)]
            jobs.append((f'{n}|{v}', 'frac', x.reset_index(drop=True), window, cfg, ro, sh, py))
            eps[f'{n}|{v}'] = episode_ci(x.net, x.event.map(w), cfg['bootstrap_seed'])
    for v in cfg['variants']:
        jobs.append((f'unscored_control|{v}', 'frac', t[(t.kind == v) & t.event.isin(control)].reset_index(drop=True), window, cfg, ro, sh, py))
    res = dict(checks=dict(rows=int(len(t)), new_events=len(new), issues=issues[:20]), alarms=alarms, episodes=eps, plans={})
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            res['plans'][name] = out
    # Parity: threshold 25 must equal the alarm study's 30-minute hold plan.
    prev = json.loads((src/'summary.json').read_text())['results']['plans']
    res['parity_25'] = {v: [res['plans'][f'25|{v}']['all']['n'], prev[f'alarm_{cfg["window_min"]}m|{v}|hold_all']['all']['n'],
                            res['plans'][f'25|{v}']['all'].get('replay', {}).get('final_median'),
                            prev[f'alarm_{cfg["window_min"]}m|{v}|hold_all']['all'].get('replay', {}).get('final_median')]
                        for v in cfg['variants']}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False), json.dumps(res['parity_25']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
