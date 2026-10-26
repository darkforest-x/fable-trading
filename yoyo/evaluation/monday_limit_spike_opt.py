"""Spike subset (score >= 0.6, EMA order > 0): floating loss, tighter backstops, slots and leverage (owner 2026-10-26).

Owner: "继续优化这套插针策略 ... 胜率高挺好的 ... 兜底止损还是有点大 ... 浮亏的单子的分布是怎么样的 能否提高兜底止损 同时是否
能分仓并加大杠杆 整个回测爆仓几个仓位没事 主要不要总账户回撤大于40%就好了 力求胜率高 而且总的收益也高".
Trades are NautilusTrader fills only, no replay here: near Monday edge and range 0.5 targets with backstops none / 10 /
12 / 15 / 18 / 20 / 25 / 30 percent (exp-monday-limit-v3-grid-20261025-v1, exp-monday-limit-v3-levels-20261025-v1)
and 3 / 5 / 7 percent (exp-monday-limit-spike-stops-20261026-v1), breakeven at entry; worse intrabar path per trade,
10bp slippage on non-target exits, the two bad source bars dropped; population score >= 0.6 and l2_order_score > 0.
Floating loss (MAE) from Codex 5m bars as in monday_limit_v3_slots: the fill bar through the bar before the exit;
'before confirmation' stops at the 60m confirmation close (touch hour + 2h). Win = net > 1bp.
Slots: the owner's scheme from monday_limit_v3_slots.simulate (N slots, fixed margin at leverage L, a slot is
liquidated when MAE >= 100/L - 1 percent, profits above 2x a slot go to the reserve, slots resize when equity
doubles), extended to count winning slots after liquidation. Selection respects the owner's limit (max drawdown
<= 40 percent) on 2020-2024; 2025, 2026 and the last five months check the chosen settings. In-sample: the order
score was found on the same history.
"""
from __future__ import annotations
import argparse
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, RUN, clean
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-spike-opt-20261026-v1'
A = ROOT/'experiments/active'
COLS = ['trade_id', 'side', 'entry', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i', 'net_notional_bp', 'score', 'variant']


def legs(args):
    """Worse-path rows for every wanted variant of one symbol, with MAE to exit and before the confirmation close."""
    symbol, wanted, events = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    rows = []
    for run, names in wanted.items():
        paths = {}
        for p in ('ohlc', 'olhc'):
            f = A/run/p/'symbols'/symbol/'trades.csv.gz'
            if not f.exists():
                continue
            t = pd.read_csv(f, usecols=COLS)
            t = t[t.variant.isin(names)]
            t = t[t.trade_id.str.split(':').str[:3].str.join(':').isin(events)]
            paths[p] = t.set_index('trade_id')
        if len(paths) < 2 or paths['ohlc'].empty:
            continue
        o = paths['ohlc']; l = paths['olhc'].reindex(o.index)
        no = o.net_notional_bp-np.where(o.reason == 4, 0., opt.SLIP); nl = l.net_notional_bp-np.where(l.reason == 4, 0., opt.SLIP)
        w = (nl < no).to_numpy()
        rows.append(pd.DataFrame(dict(variant=o.variant, side=o.side, entry=o.entry, score=o.score, net=np.where(w, nl, no),
                                      reason=np.where(w, l.reason, o.reason), entry_i=o.entry_i, exit_i=np.where(w, l.exit_i, o.exit_i),
                                      entry_t=pd.to_datetime(o.entry_time, utc=True),
                                      exit_t=pd.to_datetime(pd.Series(np.where(w, l.exit_time, o.exit_time), index=o.index), utc=True)), index=o.index))
    if not rows:
        return pd.DataFrame()
    d = pd.concat(rows)
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    h, lo, idx = f.high.to_numpy(), f.low.to_numpy(), f.index
    mae, pre = [], []
    for a, b, s, e in zip(d.entry_i.astype(int), d.exit_i.astype(int), d.side, d.entry):
        touch = idx[a]
        dec = int(idx.searchsorted(touch.floor('60min')+pd.Timedelta(minutes=120)))
        for stop, out in ((max(a+1, b), mae), (max(a+1, min(b, dec)), pre)):
            seg = slice(a, stop)
            worst = lo[seg].min() if s == 1 else h[seg].max()
            out.append(max(0., -s*(worst-e)/e*100))
    d['mae_pct'] = mae; d['mae_pre_pct'] = pre
    d['event'] = [':'.join(i.split(':')[:3]) for i in d.index]
    return d.reset_index(drop=True)


def simulate(t, lev, slots, extract=True, resize=True, mmr=1., seed=0):
    """monday_limit_v3_slots.simulate plus the share of taken trades that end positive after liquidation."""
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[order].reset_index(drop=True)
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-mmr
    reserve, base = 1000.*(slots-1)/slots, 1000./slots
    bal = [base]*slots; busy = [None]*slots; margin = {}
    last_reset, peak, dd, nliq, skipped, taken, wins = 1000., 1000., 0., 0, 0, 0, 0
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            free = [i for i in range(slots) if busy[i] is None]
            if not free:
                skipped += 1; continue
            i = free[0]
            if bal[i] < base:
                add = min(base-bal[i], reserve); bal[i] += add; reserve -= add
            m = min(bal[i], base)
            if m <= 1e-9:
                skipped += 1; continue
            busy[i] = j; margin[j] = (i, m); taken += 1
        elif j in margin:
            i, m = margin.pop(j)
            busy[i] = None
            if lev > 1 and mae[j] >= liq:
                pnl = -m; nliq += 1
            else:
                pnl = max(-m, m*lev*net[j])
            wins += pnl > m*1e-4
            bal[i] += pnl
            if extract and bal[i] >= 2*base:
                reserve += bal[i]-base; bal[i] = base
            total = reserve+sum(bal)
            if resize and total >= 2*last_reset:
                base, last_reset = total/slots, total
            peak = max(peak, total); dd = max(dd, 1-total/peak)
            if total <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., liquidations=nliq, skipped=skipped, taken=taken, win_pct=100*wins/max(taken, 1))
    total = reserve+sum(bal)
    return dict(final_multiple=total/1000, max_drawdown_pct=100*dd, liquidations=nliq, skipped=skipped, taken=taken, win_pct=100*wins/max(taken, 1))


def linear(y, cut, mexcess, ties, rng):
    y = y.sort_values(['entry_ns', 'tid'], kind='mergesort').reset_index(drop=True)
    z = y[['tid', 'net', 'score', 'entry_ns', 'exit_ns']].rename(columns={'entry_ns': 'entry', 'exit_ns': 'exit'})
    k = np.minimum(.05*(y.score.to_numpy()-cut)/mexcess, .5)
    rep = []
    for _ in range(ties):
        o = np.lexsort((rng.random(len(z)), z.entry.to_numpy()))
        rep.append(opt.replay_w(z.iloc[o].reset_index(drop=True), k[o], 1.)[0])
    return dict(final_median=float(np.median([r['final_multiple'] for r in rep])), dd_median=float(np.median([r['max_drawdown_pct'] for r in rep])))


def stats(x):
    n = len(x)
    if not n:
        return dict(n=0)
    return dict(n=int(n), win_pct=float(100*x.win.mean()), flat_pct=float(100*x.flat.mean()), loss_pct=float(100*(1-x.win-x.flat).mean()),
                backstops=int((x.reason == 1).sum()), mean_bp=float(x.net.mean()), worst_bp=float(x.net.min()),
                loss_mean_bp=float(x.net[(x.win == 0) & (x.flat == 0)].mean()) if ((x.win == 0) & (x.flat == 0)).any() else None)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_spike_opt.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    tn = pd.read_csv(ROOT/cfg['subset_trades'], usecols=['event', 'variant', 'score'])
    feat = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score'])
    sub = tn[(tn.variant == 'v2_be17') & (tn.score >= cfg['min_score'])].merge(feat, on='event')
    events = set(sub[sub.l2_order_score > 0].event)-BAD_EVENTS
    syms = sorted({e.split(':')[0] for e in events})
    wanted = {run: names for run, names in cfg['sources'].items()}
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(legs, [(s, wanted, events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64'); t['tid'] = t.event+':'+t.variant
    t['win'] = (t.net > cfg['win_bp']).astype(float); t['flat'] = (t.net.abs() <= cfg['win_bp']).astype(float)
    t['target'] = t.variant.str.split('_').str[0]; t['stop'] = t.variant.str.split('_').str[1]
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda x: x.entry_t < B[0], 'y2025': lambda x: (x.entry_t >= B[0]) & (x.entry_t < B[1]),
            'y2026': lambda x: x.entry_t >= B[1], 'last5m': lambda x: x.entry_t >= window}
    cut = json.loads((RUN/'scores.json').read_text())['threshold']
    mexcess = json.loads((A/'exp-monday-limit-v3-compare-20261025-v1/summary.json').read_text())['results']['linear_mean_excess']
    rng = np.random.default_rng(cfg['seed'])
    res = dict(events=len(events), rows={v: int((t.variant == v).sum()) for v in sorted(t.variant.unique())})
    # 1. Floating loss of the no-stop trades.
    base = t[t.variant == 'near_far']
    q = (.5, .75, .9, .95, .99)
    res['mae'] = {}
    for per, f in pers.items():
        x = base[f(base)]
        for g, y in (('winners', x[x.win == 1]), ('losers', x[x.win == 0]), ('all', x)):
            res['mae'][f'{per}|{g}'] = dict(n=int(len(y)), **{f'pre_q{int(100*k)}': float(y.mae_pre_pct.quantile(k)) for k in q},
                                            **{f'full_q{int(100*k)}': float(y.mae_pct.quantile(k)) for k in q},
                                            **{f'pre_ge_{s}': float(100*(y.mae_pre_pct >= s).mean()) for s in cfg['mae_marks']})
    res['mae_buckets'] = {}
    edges = [0, 1, 2, 3, 5, 7, 10, 15, 20, 30, 1e9]
    for g, y in (('winners', base[base.win == 1]), ('losers', base[base.win == 0])):
        c = pd.cut(y.mae_pre_pct, edges, right=False).value_counts().sort_index()
        res['mae_buckets'][g] = {str(k): int(v) for k, v in c.items()}
    # 2. Backstop x target on framework fills.
    res['stops'] = {}
    for v in sorted(t.variant.unique()):
        g = t[t.variant == v]
        res['stops'][v] = {per: dict(**stats(g[f(g)]), **linear(g[f(g)], cut, mexcess, cfg['ties'], rng)) for per, f in pers.items()}
    # 3. Slots x leverage; selection on 2020-2024 under the drawdown limit.
    res['slots'] = {}
    grid = [(v, n, L, sch) for v in cfg['slot_variants'] for n in cfg['slots'] for L in cfg['leverage'] for sch in cfg['schemes']]
    for v, n, L, sch in grid:
        g = t[t.variant == v]
        for per, f in pers.items():
            x = g[f(g)].reset_index(drop=True)
            reps = [simulate(x, L, n, sch != 'no_extract', sch != 'extract_fixed', seed=cfg['seed']+k) for k in range(cfg['ties'])]
            res['slots'][f'{v}|N{n}|L{L}|{sch}|{per}'] = {k: float(np.median([r[k] for r in reps])) for k in reps[0]}
    ok = [(v, n, L, sch) for v, n, L, sch in grid if res['slots'][f'{v}|N{n}|L{L}|{sch}|y2020_2024']['max_drawdown_pct'] <= cfg['max_dd']]
    rank = sorted(ok, key=lambda c: -res['slots'][f'{c[0]}|N{c[1]}|L{c[2]}|{c[3]}|y2020_2024']['final_multiple'])
    res['selected_on_2020_2024'] = [dict(variant=v, slots=n, leverage=L, scheme=sch,
                                         **{per: res['slots'][f'{v}|N{n}|L{L}|{sch}|{per}'] for per in pers}) for v, n, L, sch in rank[:cfg['top']]]
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(dict(events=res['events'], rows=res['rows']), ensure_ascii=False))
    for k, v in res['mae'].items():
        print('mae', k, {kk: round(vv, 1) for kk, vv in v.items()})
    print('buckets', json.dumps(res['mae_buckets']))
    for v, r in res['stops'].items():
        print(f'{v:10s}', *(f"{per}: n{x['n']} win {x.get('win_pct') or 0:.1f} bs {x.get('backstops', 0)} mean {x.get('mean_bp') or 0:.0f} worst {x.get('worst_bp') or 0:.0f} x{x['final_median'] or 0:.2f} dd {x['dd_median'] or 0:.0f}" for per, x in r.items()))
    for s in res['selected_on_2020_2024']:
        print(s['variant'], s['slots'], s['leverage'], s['scheme'], *(f"{per}: x{s[per]['final_multiple']:.2f} dd {s[per]['max_drawdown_pct']:.0f} liq {s[per]['liquidations']:.0f} win {s[per]['win_pct']:.1f} skip {s[per]['skipped']:.0f}" for per in pers))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
