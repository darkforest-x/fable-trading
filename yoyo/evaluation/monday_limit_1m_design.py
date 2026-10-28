"""A strategy designed for the 1m entry score: parameters chosen on 2025, tested once on 2026 (owner 2026-10-29).

Owner: "是不是要针对这个1min的模型重新设计策略？" then "那你去重新弄吧 ... 排列过滤啥的 ... 还能用在1min级别吗？".
Two 1m models (exp-monday-limit-score-1m-fit-*): the selection model trained before 2025 (its 2025 scores are unseen
data) and the test model trained before 2026. Score cuts are quantiles of each model's scores over the events of its
own training weeks, so a cut means the same share of touches under either model.
Native NautilusTrader run (both intrabar paths): resting limits at the level (Codex orders, 60m confirmation,
breakeven +0.17 percent) for every event in the top 20 percent of either model, all five targets x three stops
(far = no backstop, -18 and -30 percent from the level).
Pre-registered design space, chosen before any 2025 or 2026 strategy result was seen:
  cut      top 20 / 10 / 5 percent of training scores;
  order    15m EMA order score (completed 15m bars before the touch): none, >= 1, >= 3;
  target   near Monday edge, range 0.5, range 0.618, opposite Monday edge, opposite 1.618, or 'band' (opposite 1.618
           in the top 10 percent, range 0.618 below it, the v3 idea on the new scale);
  stop     far, P18, P30.
Sizing for selection and test: margin = realized equity / 10 at 3x (monday_limit_return_opt.frac), 10 tie seeds.
Selection rule (fixed in advance): on 2025 with the selection model, the highest median final multiple among combos
with >= 40 trades, median max drawdown <= 50 percent and a positive net sum in both halves of 2025. The chosen combo
is then evaluated once on 2026 to date with the test model; the 2026 results of every combo are reported only as a
distribution, to show whether the choice is special. Read-only arithmetic on framework fills for the summary.
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
from yoyo.evaluation.monday_limit_backstop import CODEX, stops

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'
TARGETS = ['near_monday_edge', 'range_0.5', 'range_0.618', 'opposite_monday_edge', 'opposite_1618']
STOPS = {'far': ('far', 0), 'P18': ('P', 18), 'P30': ('P', 30)}


def model_scores(cfg):
    """Per-model scores and their training-week cuts."""
    out = {}
    for name in ('selection', 'test'):
        m = cfg['models'][name]
        s = pd.read_csv(ROOT/m['scores']).set_index('event').causal
        week = s.index.str.split(':').str[1]
        train = s[week < m['split'][:10]].dropna()
        out[name] = dict(score=s, cuts={str(q): float(train.quantile(1-q/100)) for q in cfg['cut_pct']}, split=m['split'])
    return out


def wanted(cfg):
    """Events in the widest cut of either model (the design never needs rows outside it)."""
    lo = str(max(cfg['cut_pct']))
    return set().union(*({e for e, v in m['score'].items() if v >= m['cuts'][lo]} for m in model_scores(cfg).values()))


def process(symbol, intrabar, exp, cfg, events):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week']) & e.exit_mode.isin(TARGETS)].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(events)]
    parts = []
    for name, (kind, value) in STOPS.items():
        y = e.copy()
        y['stop'] = stops(y, kind, value, tick)
        y['be_offset_bp'] = cfg['be_offset_bp']
        y['trade_id'] = y.trade_id+f':v_rest_{name}'
        parts.append(y[y.side*(y.entry-y.stop) > 0])
    x = pd.concat(parts, ignore_index=True)
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x['risk'] = x.entry
    x['reclaim_ref'] = np.nan
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                x.drop(columns=['event', 'population', 'exit_mode']), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k for k in KEEP if k in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def symbols(cfg):
    return sorted({e.split(':')[0] for e in wanted(cfg) if e.split(':')[1] >= cfg['since_week']})


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_1m_design.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
              str(config.resolve().relative_to(ROOT)))
    events = wanted(cfg)
    root = exp/intrabar.lower()
    todo = [s for s in symbols(cfg) if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, {e for e in events if e.startswith(s+':')}): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


def pick(t, ms, model, cut, order, target, stop):
    """Rows of one design combo under one model."""
    s = t[f's_{model}']
    k = (s >= ms[model]['cuts'][str(cut)]) & (t.kind == stop)
    if order == 'ge1':
        k &= t.order >= 1
    elif order == 'ge3':
        k &= t.order >= 3
    if target == 'band':
        k &= t['mode'] == np.where(s >= ms[model]['cuts']['10'], 'opposite_1618', 'range_0.618')
    else:
        k &= t['mode'] == target
    return t[k]


def evaluate(args):
    """Trades, half-year sums and the frac 10 x 3x replay of one combo in one window."""
    key, x, ties, seed, mid = args
    from yoyo.evaluation.monday_limit_return_opt import frac
    x = x.assign(entry_ns=x.entry_t.astype('int64'), exit_ns=x.exit_t.astype('int64')).reset_index(drop=True)
    out = dict(n=int(len(x)))
    if len(x):
        mid = mid if mid is not None else x.entry_t.min()+(x.entry_t.max()-x.entry_t.min())/2
        reps = [frac(x, 10, 3, seed+r) for r in range(ties)]
        out.update(mean_bp=float(x.net.mean()), win_pct=float(100*(x.net > 1).mean()), sum_pct=float(x.net.sum()/100),
                   first_half_sum_pct=float(x.net[x.entry_t < mid].sum()/100), second_half_sum_pct=float(x.net[x.entry_t >= mid].sum()/100),
                   final_median=float(np.median([r['final_multiple'] for r in reps])),
                   dd_median=float(np.median([r['max_drawdown_pct'] for r in reps])),
                   liquidations_median=float(np.median([r['liquidations'] for r in reps])))
    return key, out


def summarize(config, workers):
    from itertools import product
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_1m_design.py')
    seed = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())['seed']
    ties = cfg['sizing']['ties']
    syms = symbols(cfg)
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(exp), s, 10., 10.) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    t['kind'] = t.variant.str.split('_', n=1).str[1]
    ms = model_scores(cfg)
    for name, m in ms.items():
        t[f's_{name}'] = t.event.map(m['score'])
    t['order'] = t.event.map(pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event').l2_order_score)
    b25, b26 = pd.Timestamp('2025-01-01', tz='UTC'), pd.Timestamp('2026-01-01', tz='UTC')
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    combos = list(product(cfg['cut_pct'], cfg['order_filters'], cfg['targets'], cfg['stops']))
    name = lambda c: '|'.join(map(str, c))
    # Stage 1: selection on 2025 with the model that never saw 2025.
    jobs = []
    for c in combos:
        x = pick(t, ms, 'selection', *c)
        jobs.append((name(c), x[(x.entry_t >= b25) & (x.entry_t < b26)][['entry_t', 'exit_t', 'net', 'mae_pct']], ties, seed,
                     pd.Timestamp('2025-07-01', tz='UTC')))
    with ProcessPoolExecutor(workers) as ex:
        sel = dict(ex.map(evaluate, jobs))
    ok = {k: v for k, v in sel.items() if v['n'] >= cfg['min_trades_2025'] and v.get('dd_median', 100) <= cfg['max_dd_2025']
          and v.get('first_half_sum_pct', -1) > 0 and v.get('second_half_sum_pct', -1) > 0}
    ranked = sorted(ok, key=lambda k: -ok[k]['final_median'])
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]], rows=int(len(t)),
                           cuts={k: m['cuts'] for k, m in ms.items()}),
               selection_2025=dict(combos=len(combos), qualified=len(ok), top10={k: sel[k] for k in ranked[:10]}))
    if not ranked:
        res['verdict'] = 'no combo qualified on 2025; nothing is tested on 2026'
    else:
        chosen = ranked[0]
        c = tuple(int(v) if i == 0 else v for i, v in enumerate(chosen.split('|')))
        res['chosen'] = chosen
        # Stage 2: one test of the frozen choice on 2026 with the model trained before 2026.
        tests = {}
        for label, model, lo in (('test_model_2026', 'test', b26), ('test_model_last5m', 'test', window),
                                 ('selection_model_2026', 'selection', b26)):
            x = pick(t, ms, model, *c)
            tests[label] = evaluate((label, x[x.entry_t >= lo][['entry_t', 'exit_t', 'net', 'mae_pct']], ties, seed, None))[1]
        res['test'] = tests
        # Context only: every combo's 2026 result under the test model.
        jobs = []
        for cc in combos:
            x = pick(t, ms, 'test', *cc)
            jobs.append((name(cc), x[x.entry_t >= b26][['entry_t', 'exit_t', 'net', 'mae_pct']], ties, seed, None))
        with ProcessPoolExecutor(workers) as ex:
            all26 = dict(ex.map(evaluate, jobs))
        finals = np.array([v.get('final_median', np.nan) for v in all26.values()], float)
        res['all_combos_2026'] = dict(final_q={f'q{int(100*q)}': float(np.nanquantile(finals, q)) for q in (.1, .25, .5, .75, .9)},
                                      chosen_rank=int(1+(finals > tests['test_model_2026'].get('final_median', np.nan)).sum()),
                                      combos=len(combos))
        bench = json.loads((A/'exp-monday-limit-paper-accounts-20261028-v1/summary.json').read_text())['results']['plans']
        res['benchmarks_2026'] = {k: bench[f'{k}|A_rest_causal']['y2026'] for k in ('v3add20', 'spike', 'v3')}
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: res[k] for k in ('chosen', 'test', 'verdict') if k in res}, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
