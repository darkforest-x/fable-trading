"""Which Monday1618 touches still pay when entered at the touch 5m close (owner 2026-11-01).

Owner: "围绕5min插针收盘这个特点，展开分析，收盘之后，有哪些还有不错的盈利空间的". The informative part of the paper
score is the touch bar itself, so this asks which touch-bar facts, all known at that bar's close, still leave profit
for a market entry at the close. No new fills: exp-monday-limit-close-reclaim-20261028-v1 already entered every signal
event at market at the completed touch close in NautilusTrader (P30 when the bar closed back inside the level, B30 when
it closed at or beyond it; five target modes; -30 percent from the level; 60m reclaim then stop to the level; taker
entry, 10bp entry and non-target-exit slippage; worse path). Read-only arithmetic on those rows.
Features, one value per event, all from the touch bar (5m bar touch_i_hint of the Codex limit-reclaim ledger) or
earlier, so known at its close:
  score       frozen Monday score on the touch bar (`stored` of the leak audit), fixed bands;
  close       closed back inside the level (P30) or at / beyond it (B30);
  depth_pct   side x (level - touch-bar extreme) / level, percent: how far the wick went past the 1.618;
  recovery    side x (close - extreme) / side x (level - extreme): share of that excursion recovered by the close;
  vol_ratio   features_v2 log_vol_ratio (last hour of quote volume ending at the touch bar vs the prior week's median);
  cluster_bar distinct symbols whose first weekly same-side touch is in the same 5m bar (25+ is the alarm bar itself);
  side        long (lower 1.618) or short (upper 1.618).
Protocol, fixed in the config before any result: tercile edges from 2020-2024 events; every bucket x target mode alone
(layer all) and within score >= 0.5 (layer ge_0.5); a slice is a candidate when 2020-2024 has >= min_n events, mean net
> 0 and a cluster-bootstrap 95 percent lower bound > 0 (clusters = side x touch bar, the market shock); it survives when
2025-2026 pooled has a lower bound > 0 and 2025 and 2026 means are each > 0. The null is the same target's
all-touch mean in the same period (reported per period); survivors get frac N20 3x replays and need a matched random-time
control before any paper account. Everything is reported, not only survivors.
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
from yoyo.evaluation import monday_limit_close_reclaim as cr
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC

A = ROOT/'experiments/active'
LCOLS = ['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'level', 'touch_i_hint']
PER = {'y2020_2024': ('2020-01-01', '2025-01-01'), 'y2025': ('2025-01-01', '2026-01-01'), 'y2026': ('2026-01-01', '2100-01-01'),
       'test': ('2025-01-01', '2100-01-01')}


def symbol_rows(args):
    """Close-entry rows (P30 / B30) of one symbol with the touch-bar facts of each event."""
    exp, symbol, slip_entry, slip_exit = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    _, d, issues = cr.load_symbol((exp, symbol, slip_entry, slip_exit))
    if d.empty:
        return symbol, d, issues
    d = d[d.variant.isin(['P30', 'B30'])].copy()
    lv = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    lv = lv[(lv.population == 'signal') & (lv.reclaim_minutes == 60) & (lv.exit_mode == 'near_monday_edge')].copy()
    lv['event'] = symbol+':'+lv.week+':'+np.where(lv.side == 1, '+1', '-1')
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    k = lv.touch_i_hint.astype(int).to_numpy()
    s, L = lv.side.to_numpy(float), lv.level.to_numpy(float)
    ext = np.where(s > 0, f.low.to_numpy()[k], f.high.to_numpy()[k])
    c = f.close.to_numpy()[k]
    past = s*(L-ext)
    lv['depth_pct'] = 100*past/L
    lv['recovery'] = np.where(past > 0, s*(c-ext)/np.where(past > 0, past, 1), np.nan)
    d = d.merge(lv[['event', 'depth_pct', 'recovery']].drop_duplicates('event'), on='event', how='left')
    return symbol, d, issues


def boot(net, cluster, rng, draws):
    """Mean and cluster-bootstrap 95 percent interval of per-trade net (bp)."""
    if not len(net):
        return dict(n=0)
    g = pd.DataFrame(dict(net=net.to_numpy(), c=cluster.to_numpy())).groupby('c').net.agg(['sum', 'count'])
    s, n = g['sum'].to_numpy(), g['count'].to_numpy()
    idx = rng.integers(0, len(g), (draws, len(g)))
    m = s[idx].sum(1)/n[idx].sum(1)
    return dict(n=int(len(net)), clusters=int(len(g)), mean_bp=float(net.mean()), win_pct=float(100*(net > 1).mean()),
                ci95=[float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))])


def period(x, p):
    a, b = PER[p]
    return x[(x.entry_t >= pd.Timestamp(a, tz='UTC')) & (x.entry_t < pd.Timestamp(b, tz='UTC'))]


def summarize(config, workers):
    from yoyo.evaluation import monday_limit_alarm_entry as ae
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    cr.committed('yoyo/evaluation/monday_limit_close_slices.py', str(config.resolve().relative_to(ROOT)))
    src = ROOT/cfg['source']
    scfg = json.loads((src/'config.json').read_text())
    slip = scfg['slippage_bp']
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(symbol_rows, [(str(src), s, slip['market_entry'], slip['non_target_exit']) for s in cr.symbols(scfg)], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS) & (t.entry_t >= pd.Timestamp('2020-01-01', tz='UTC'))].copy()
    t['score'] = t.event.map(pd.read_csv(ROOT/scfg['scores']).set_index('event').stored)
    t['vol_ratio'] = t.event.map(pd.read_csv(ROOT/scfg['features'], usecols=['event', 'log_vol_ratio']).set_index('event').log_vol_ratio)
    tc = ae.touches().drop_duplicates('event')
    tc['cluster_bar'] = tc.groupby(['side', 'ms']).symbol.transform('nunique')
    t['cluster_bar'] = t.event.map(tc.set_index('event').cluster_bar)
    t['bar'] = t.event.map(tc.set_index('event').ms)
    t['shock'] = t.side.astype(int).astype(str)+':'+t.bar.astype('Int64').astype(str)
    t['close'] = np.where(t.variant == 'P30', 'inside', 'beyond')
    t['side_name'] = np.where(t.side > 0, 'long', 'short')
    # Frozen bucket edges: terciles of 2020-2024 events, then fixed bands from the config.
    ev = t.drop_duplicates('event')
    early = ev[ev.entry_t < pd.Timestamp('2025-01-01', tz='UTC')]
    edges = {d: [float(q) for q in early[d].quantile([1/3, 2/3])] for d in cfg['tercile_dims']}
    buckets = {}
    for d, e in edges.items():
        buckets[d] = pd.cut(t[d], [-np.inf, *e, np.inf], labels=['low', 'mid', 'high']).astype(str)
    buckets['score'] = pd.cut(t.score, cfg['score_bands'], right=False, labels=cfg['score_labels']).astype(str)
    buckets['cluster_bar'] = pd.cut(t.cluster_bar, cfg['cluster_bands'], right=False, labels=cfg['cluster_labels']).astype(str)
    buckets['close'] = t.close
    buckets['side'] = t.side_name
    rng = np.random.default_rng(cfg['bootstrap_seed'])
    rows, base = [], {}
    for mode in cfg['targets']:
        m = t[t['mode'] == mode]
        base[mode] = {p: boot(period(m, p).net, period(m, p).shock, rng, cfg['draws']) for p in PER}
    layers = {'all': t.score.notna(), 'ge_0.5': t.score >= 0.5}
    for layer, keep in layers.items():
        for dim, b in buckets.items():
            if layer == 'ge_0.5' and dim == 'score':
                continue
            for val in sorted(v for v in b[keep].unique() if v != 'nan'):
                for mode in cfg['targets']:
                    x = t[keep & (b == val) & (t['mode'] == mode)]
                    st = {p: boot(period(x, p).net, period(x, p).shock, rng, cfg['draws']) for p in PER}
                    e, ts = st['y2020_2024'], st['test']
                    cand = e['n'] >= cfg['min_n'] and e['mean_bp'] > 0 and e['ci95'][0] > 0
                    surv = bool(cand and ts['n'] and ts['ci95'][0] > 0 and st['y2025'].get('mean_bp', -1) > 0 and st['y2026'].get('mean_bp', -1) > 0)
                    rows.append(dict(layer=layer, dim=dim, bucket=val, target=mode, candidate=bool(cand), survives=surv, **{
                        f'{p}_{k}': (v if k != 'ci95' else v[0]) for p in PER for k, v in st[p].items() if k in ('n', 'mean_bp', 'ci95', 'win_pct')}))
    table = pd.DataFrame(rows).rename(columns=lambda c: c.replace('_ci95', '_ci_lo'))
    table.to_csv(exp/'slices.csv', index=False)
    # Survivors: account replay (frac N20 3x) per period, as the close-reclaim plans.
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    jobs = []
    for r in table[table.survives].itertuples():
        keep = layers[r.layer] & (buckets[r.dim] == r.bucket) & (t['mode'] == r.target)
        jobs.append((f'{r.layer}|{r.dim}|{r.bucket}|{r.target}', 'frac', t[keep].reset_index(drop=True), window, ro, sh, py))
    plans = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(cr.replay, jobs):
            plans[name] = out
    res = dict(checks=dict(rows=int(len(t)), events=int(t.event.nunique()), unmatched_cluster=int(t.cluster_bar.isna().sum()),
                           issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20]),
               edges=edges, baseline=base, slices=int(len(table)), candidates=int(table.candidate.sum()),
               survivors=int(table.survives.sum()), plans=plans)
    dump(exp/'summary.json', dict(config=cfg, results=clean(res), source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks']), res['slices'], res['candidates'], res['survivors'])


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    summarize(a.config, a.workers)
