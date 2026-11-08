"""Reclaim confirmation length for the 25-coin alarm-entry rule (owner 2026-11-08).

Owner: "你能基于这些研究 再找找还有没有更好的方案吗 做成策略", then on the split: "2020 2024年的币种很少 为啥不用2025年的".
The alarm-entry rule (exp-monday-limit-alarm-entry-20261029-v1, 30-minute window, near Monday edge target, -30 percent
backstop, breakeven +0.17 percent, no score) loses mainly on fills whose next 60-minute candle does not close back inside
the 1.618 (737 of 2146 trades, -190bp mean). The single variable here is that confirmation length: the touch-timeframe
candle and its immediate successor must close before protection exists (adapter reclaim_next_bar), at 5, 15 or the
frozen 60 minutes; 1 and 3 minutes are not whole multiples of the 5m execution bars. Entries, target, backstop,
breakeven offset, fees and sizing are unchanged, so the control is the paired 60-minute exit on the same fills.
Pre-registered split (owner): choose on 2025 (universe closest to now), test on 2026, and 2020-24 must not reverse.
Native NautilusTrader run, both intrabar paths, worse path with 10bp slippage on non-target exits (load_symbol of the
alarm study), frac N20 3x replay by period, alarm episodes as the independent unit.
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation import monday_limit_alarm_entry as ae
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

A = ROOT/'experiments/active'
CODE = 'yoyo/evaluation/monday_limit_alarm_reclaim_time.py'


def window_events(cfg):
    win = pd.read_csv(ROOT/cfg['threshold_study']/'alarm_windows.csv.gz')
    w = win[(win.threshold == cfg['threshold']) & (win.window == cfg['window_min'])]
    return w.drop_duplicates('event').set_index('event').alarm_ms


def process(symbol, intrabar, exp, cfg, events):
    """ae.process with the confirmation length as the only change: near Monday edge, -30 percent, hold."""
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    if job['group'] != 'crypto_5m':
        out.mkdir(parents=True, exist_ok=True); dump(out/'skipped.json', dict(symbol=symbol, group=job['group']))
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & e.reclaim_minutes.isin(cfg['reclaim_minutes']) & (e.week >= cfg['since_week'])
          & (e.exit_mode == cfg['exit_mode'])].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    y = e[e.event.isin(events)].copy()
    y['stop'] = stops(y, 'P', cfg['backstop_pct'], tick)
    y['be_offset_bp'] = cfg['be_offset_bp']
    y['fill_close_exit'] = False
    y['trade_id'] = y.trade_id+':v_m'+y.reclaim_minutes.astype(int).astype(str)+'_hold'
    x = y[y.side*(y.entry-y.stop) > 0]
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=ae.KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x = x.copy()
    x['risk'] = x.entry
    x['reclaim_ref'] = np.nan
    x['fill_close_exit'] = x.fill_close_exit.astype(bool)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                x.drop(columns=['event', 'population', 'exit_mode']), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k for k in ae.KEEP if k in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed(CODE, 'yoyo/evaluation/monday_limit_alarm_entry.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
                 str(config.resolve().relative_to(ROOT)))
    events = set(window_events(cfg).index)
    syms = sorted({e.split(':')[0] for e in events})
    root = exp/intrabar.lower()
    todo = [s for s in syms if not ((root/'symbols'/s/'done.json').exists() or (root/'symbols'/s/'skipped.json').exists())]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, {e for e in events if e.startswith(s+':')}): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


def paired(c, b, episode, seed, draws=2000):
    """Candidate minus 60-minute net on the same events, averaged per alarm episode, seeded bootstrap 95 percent CI."""
    d = (c-b.reindex(c.index)).dropna()
    per = d.groupby(episode.reindex(d.index)).mean().to_numpy()
    if not len(per):
        return dict(episodes=0)
    boot = per[np.random.default_rng(seed).integers(0, len(per), (draws, len(per)))].mean(1)
    return dict(trades=int(len(d)), episodes=int(len(per)), trade_diff_bp=float(d.mean()), episode_diff_bp=float(per.mean()),
                episodes_better_pct=float(100*(per > 0).mean()),
                ci95_bp=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))])


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import replay
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    ae.committed(CODE)
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    ep = window_events(cfg)
    syms = sorted({e.split(':')[0] for e in ep.index})
    syms = [s for s in syms if not (exp/'ohlc'/'symbols'/s/'skipped.json').exists()]
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(ae.load_symbol, [(str(exp), s, cfg['slippage_bp']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS) & t.event.isin(ep.index)].copy()
    t['episode'] = t.event.map(ep)
    yr = pd.to_datetime(t.episode, unit='ms', utc=True).dt.year
    t['period'] = np.where(yr <= 2024, '2020-24', yr.astype(str))
    # Parity: the 60-minute rows must equal the alarm study's near30 hold fills on the same events.
    src = ROOT/cfg['source']
    s_syms = sorted({e.split(':')[0] for e in set(t.event)})
    with ProcessPoolExecutor(workers) as ex:
        old = pd.concat([g[1] for g in ex.map(ae.load_symbol, [(str(src), s, cfg['slippage_bp']) for s in s_syms], chunksize=4) if len(g[1])])
    old = old[(old.kind == 'near30') & (old.act == 'hold') & old.event.isin(set(t.event))].set_index('event').net
    m60 = t[t.kind == 'm60'].set_index('event').net
    par = (m60-old.reindex(m60.index)).abs()
    parity = dict(m60=int(len(m60)), source=int(len(old)), max_abs_diff_bp=float(par.max()), unmatched=int(par.isna().sum()))
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    jobs, stats, pairs = [], {}, {}
    for k in sorted(t.kind.unique()):
        x = t[t.kind == k]
        jobs.append((k, 'frac', x.reset_index(drop=True), window, cfg, ro, sh, py))
        stats[k] = {p: dict(n=int(len(g)), mean_bp=float(g.net.mean()), target_pct=float(100*(g.reason == 4).mean()),
                            reasons={str(r): int(n) for r, n in g.reason.value_counts().items()})
                    for p, g in x.groupby('period')}
        if k != 'm60':
            c = x.set_index('event'); b = t[t.kind == 'm60'].set_index('event')
            pairs[k] = {p: paired(c.net[c.period == p], b.net, b.episode, cfg['bootstrap_seed']) for p in ('2020-24', '2025', '2026')}
    plans = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            plans[name] = out
    mult = lambda k, per: (plans[k].get(per) or {}).get('replay', {}).get('final_median')
    # Pre-registered decision (config.decision_rule).
    cands = [k for k in plans if k != 'm60' and (mult(k, 'y2025') or 0) > (mult('m60', 'y2025') or 0)]
    pick = max(cands, key=lambda k: mult(k, 'y2025')) if cands else None
    decision = dict(selected_on_2025=pick)
    if pick:
        ok26 = mult(pick, 'y2026') > mult('m60', 'y2026') and stats[pick]['2026']['mean_bp'] > stats['m60']['2026']['mean_bp']
        ok_old = stats[pick]['2020-24']['mean_bp'] >= stats['m60']['2020-24']['mean_bp']
        decision.update(passes_2026=bool(ok26), no_reversal_2020_24=bool(ok_old), adopt=bool(ok26 and ok_old))
    else:
        decision['adopt'] = False
    res = clean(dict(checks=dict(rows=int(len(t)), parity=parity, issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20]),
                     stats=stats, paired_vs_m60=pairs, plans=plans, decision=decision))
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))
    print(json.dumps(decision, ensure_ascii=False))
    for k in plans:
        print(k, {p: (stats[k].get(q, {}).get('n'), round(stats[k].get(q, {}).get('mean_bp', np.nan), 1), mult(k, p))
                  for p, q in (('all', None), ('y2020_2024', '2020-24'), ('y2025', '2025'), ('y2026', '2026'))})


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
