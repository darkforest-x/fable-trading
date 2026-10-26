"""Win-rate levers for the Monday1618 limit-reclaim rule, read from backtested trades (owner 2026-10-26).

Owner: "还有什么办法提高胜率？详细分析回测过的单子". Read-only arithmetic on existing framework fills, no replay.
Trades: exp-monday-limit-time-news-20261026-v1/trades.csv.gz (worse intrabar path per trade of the NautilusTrader
fills in exp-monday-limit-be-scalein-20261026-v1, 10bp slippage on non-target exits; win = net > 1bp, flat = |net|
<= 1bp), families v3_be17 (score >= 0.5) and v2_be17 (score >= 0.226). Entry features: the causal feature table
exp-monday-limit-score-features-v2-20261025-v1 (bars before the touch bar, earlier exited events), joined on event.
Analyses, each split 2020-2024 (early) / 2025-2026 (late):
  1. what the trades are made of: target, breakeven, failed reclaim, reclaim inside the fee band, backstop;
  2. win rate by score threshold and by side;
  3. single-feature screen, select on early and confirm on late: per feature, early quintile edges (categories when
     <= 6 values); the early-worst bin (n >= 30) is dropped and the early-best bin kept alone; on late data the
     dropped-vs-kept gap gets a one-sided week-block bootstrap p and Holm over features; mean net is reported next to
     win rate because a filter can raise one and lower the other;
  4. target distance (percent of entry and in units of the 14-bar ATR percent) against win rate;
  5. deeper entries: the -5 / -10 percent scale-in legs of score >= 0.6 v3 events taken alone (worse path per leg,
     an unfilled leg counts as no trade), fill rate, win rate and mean net;
  6. added after the first run put the 15m EMA order score (l2_order_score, side-aligned, bars before the touch)
     far ahead: order score > 0 versus = 0 inside each score band, for both families (v2_be17 at score >= 0.5 is the
     near-target version of the v3 events).
Exploratory: no rule is adopted from this; a confirmed lever still needs a framework run of its own.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, RUN, worse

EXP = ROOT/'experiments/active/exp-monday-limit-winrate-levers-20261026-v1'
SKIP = {'event', 'trade_id', 'symbol', 'touch', 'exit_time', 'net', 'win', 'reclaim', 'hit_r05', 'hit_r0618', 'hit_far',
        'hit_opp1618', 'dow', 'hours_since_monday'}
REASON = {4: 'target', 5: 'breakeven', 11: 'failed_reclaim', 12: 'reclaimed_inside_be', 1: 'backstop', 0: 'other'}


def legs(symbol):
    t = {}
    for path in ('ohlc', 'olhc'):
        x = pd.read_csv(RUN/path/'symbols'/symbol/'trades.csv.gz')
        t[path] = x[x.variant.isin(['v3_add5', 'v3_add10', 'v3_be0', 'v3_be17', 'v2_be17'])]
    geo = t['ohlc'][t['ohlc'].variant.isin(['v3_be17', 'v2_be17'])][['trade_id', 'entry', 'target_price']]
    deep = ['v3_add5', 'v3_add10', 'v3_be0']
    return worse(t['ohlc'][t['ohlc'].variant.isin(deep)], t['olhc'][t['olhc'].variant.isin(deep)]), geo


def stats(x):
    n = len(x)
    return dict(n=int(n), win_pct=float(100*x.win.mean()) if n else None, flat_pct=float(100*x.flat.mean()) if n else None,
                loss_pct=float(100*(1-x.win-x.flat).mean()) if n else None, mean_bp=float(x.net.mean()) if n else None)


def periods(x, early):
    return dict(early=stats(x[early]), late=stats(x[~early]), all=stats(x))


def boot_gap(late, drop, rng, draws):
    """One-sided p that win(kept) - win(dropped) <= 0, resampling NY weeks."""
    wk = pd.factorize(late.week)[0]; nw = wk.max()+1
    w = late.win.to_numpy(); d = drop.to_numpy()
    agg = lambda m: (np.bincount(wk, w*m, nw), np.bincount(wk, m.astype(float), nw))
    (wd, nd), (wk_, nk) = agg(d), agg(~d)
    W = rng.multinomial(nw, np.full(nw, 1/nw), size=draws).astype(float)
    with np.errstate(all='ignore'):  # Accelerate BLAS raises spurious overflow flags in matmul
        gap = (W@wk_)/(W@nk)-(W@wd)/(W@nd)
    gap = gap[np.isfinite(gap)]
    return float((gap <= 0).mean()) if len(gap) else None


def bins(x, early, k=5):
    e = x[early].dropna()
    if e.nunique() <= 6:
        return x.astype(object).where(x.notna(), None), sorted(e.unique().tolist())
    edges = np.unique(np.quantile(e, np.linspace(0, 1, k+1)))
    if len(edges) < 3:
        return None, None
    edges[0], edges[-1] = -np.inf, np.inf
    b = pd.cut(x, edges, labels=False, include_lowest=True)
    return b.astype(object).where(b.notna(), None), list(range(len(edges)-1))


def screen(g, early, rng, cfg):
    out = []
    feats = [c for c in g.columns if c.startswith('f_')]
    for c in feats:
        x = g[c].replace([np.inf, -np.inf], np.nan)
        if x[early].notna().mean() < .8:
            continue
        b, labels = bins(x, early)
        if b is None:
            continue
        rows = []
        for lab in labels:
            m = b == lab
            if (m & early).sum() >= cfg['min_bin']:
                rows.append((lab, g.win[m & early].mean(), int((m & early).sum())))
        if len(rows) < 2:
            continue
        worst = min(rows, key=lambda r: r[1]); best = max(rows, key=lambda r: r[1])
        late = g[~early]; drop = (b[~early] == worst[0])
        keep_best = g[(b == best[0]) & ~early]
        out.append(dict(feature=c[2:], worst_bin=str(worst[0]), best_bin=str(best[0]),
            early_all=float(100*g.win[early].mean()), early_drop_worst=float(100*g.win[early & (b != worst[0])].mean()),
            early_best_only=float(100*best[1]), late_all=stats(late), late_drop_worst=stats(late[~drop]),
            late_dropped=stats(late[drop]), late_best_only=stats(keep_best),
            p_drop=boot_gap(late, drop, rng, cfg['boot_draws']),
            edges=None if g[c][early].nunique() <= 6 else [float(v) for v in np.quantile(x[early].dropna(), np.linspace(0, 1, 6))]))
    ps = np.array([r['p_drop'] if r['p_drop'] is not None else 1. for r in out])
    order = np.argsort(ps); m = len(ps); adj = np.empty(m)
    adj[order] = np.minimum(1, np.maximum.accumulate(ps[order]*(m-np.arange(m))))
    for r, a in zip(out, adj):
        r['holm_p'] = float(a)
    return sorted(out, key=lambda r: -(r['late_drop_worst']['win_pct']-r['late_all']['win_pct']))


def deeper(m, cfg, split):
    """Scale-in legs alone: worse path per leg; a leg filled under neither path is no trade."""
    out = {}
    par = m[(m.variant == 'v3_be0') & (m.score >= cfg['add_from_score'])].copy()
    par['base'] = par.trade_id.str.replace(':v_v3_be0', '', regex=False)
    for dd in ('add5', 'add10'):
        c = m[m.variant == f'v3_{dd}'].copy(); c['base'] = c.trade_id.str.replace(f':v_v3_{dd}', '', regex=False)
        c = par[['base', 'entry_time_o', 'entry_time_l']].merge(c[['base', 'net_o', 'net_l']], on='base', how='left')
        no = c.net_o.isna().to_numpy(); nl = c.net_l.isna().to_numpy()
        net = np.minimum(np.where(no, 0., c.net_o), np.where(nl, 0., c.net_l))
        filled_worse = np.where(np.where(no, 0., c.net_o) <= np.where(nl, 0., c.net_l), ~no, ~nl)
        c['net'] = net; c['filled'] = filled_worse
        c['win'] = (c.net > cfg['win_bp']).astype(float); c['flat'] = (c.net.abs() <= cfg['win_bp']).astype(float)
        c['t'] = pd.to_datetime(c.entry_time_o.fillna(c.entry_time_l), utc=True)
        early = (c.t < split).to_numpy()
        f = c[c.filled]
        out[dd] = dict(events=dict(early=int(early.sum()), late=int((~early).sum())),
                       fill_pct=dict(early=float(100*c.filled[early].mean()), late=float(100*c.filled[~early].mean())),
                       legs=periods(f, (f.t < split).to_numpy()))
    return out


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_winrate_levers.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    rng = np.random.default_rng(cfg['seed']); split = pd.Timestamp(cfg['split'], tz='UTC')
    t = pd.read_csv(ROOT/cfg['trades'], parse_dates=['t'])
    f = pd.read_csv(ROOT/cfg['features'])
    f = f[[c for c in f.columns if c not in SKIP or c == 'event']].rename(columns=lambda c: c if c == 'event' else 'f_'+c)
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        got = list(ex.map(legs, syms, chunksize=8))
    m = pd.concat([a for a, _ in got], ignore_index=True)
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':'); m = m[~m.event.isin(BAD_EVENTS)]
    geo = pd.concat([b for _, b in got], ignore_index=True)
    t = t.merge(geo, on='trade_id', how='left').merge(f, on='event', how='left')
    t['f_target_pct'] = 100*(t.target_price-t.entry).abs()/t.entry
    t['f_target_atr'] = t.f_target_pct/(100*t.f_l2_atr_pct)
    t['cause'] = t.reason.map(REASON)
    res = dict(deeper=deeper(m, cfg, split))
    for v in cfg['variants']:
        g = t[t.variant == v].reset_index(drop=True); early = (g.t < split).to_numpy()
        r = dict(all=periods(g, early))
        r['causes'] = {c: dict(early=float(100*(g.cause[early] == c).mean()), late=float(100*(g.cause[~early] == c).mean()),
                               mean_bp=float(g.net[g.cause == c].mean())) for c in g.cause.unique()}
        r['score'] = {str(s): periods(g[g.score >= s], early[g.score >= s]) for s in cfg['score_steps'][v]}
        r['side'] = {('long' if s == 1 else 'short'): periods(g[g.side == s], early[g.side == s]) for s in (1, -1)}
        r['side_score'] = {f"{'long' if s == 1 else 'short'}>={c}": periods(g[(g.side == s) & (g.score >= c)], early[(g.side == s) & (g.score >= c)])
                           for s in (1, -1) for c in cfg['score_steps'][v][:3]}
        q = pd.qcut(g.f_target_pct.rank(method='first'), 5, labels=False)
        r['target_pct'] = {f"Q{k+1} {g.f_target_pct[q == k].min():.1f}-{g.f_target_pct[q == k].max():.1f}%": periods(g[q == k], early[q == k]) for k in range(5)}
        r['screen'] = screen(g, early, rng, cfg)
        cuts = cfg['score_steps'][v]+[1.01]
        r['order_x_score'] = {f"{lo}-{hi} order{'>0' if pos else '=0'}": periods(g[sel], early[sel.to_numpy()])
                              for lo, hi in zip(cuts[:-1], cuts[1:]) for pos in (True, False)
                              for sel in [(g.score >= lo) & (g.score < hi) & ((g.f_l2_order_score > 0) == pos)]}
        res[v] = r
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    for v in cfg['variants']:
        r = res[v]; print(v, json.dumps(r['all']))
        for s in r['screen'][:8]:
            print(' ', s['feature'], 'drop', s['worst_bin'], f"early {s['early_all']:.1f}->{s['early_drop_worst']:.1f}",
                  f"late {s['late_all']['win_pct']:.1f}->{s['late_drop_worst']['win_pct']:.1f}", f"mean {s['late_all']['mean_bp']:.0f}->{s['late_drop_worst']['mean_bp']:.0f}",
                  f"p {s['p_drop']:.3f} holm {s['holm_p']:.3f}")
    print('deeper', json.dumps(res['deeper']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
