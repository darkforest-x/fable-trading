"""Win rate by weekday, hour and US macro releases for the Monday1618 limit-reclaim rule (owner 2026-10-26).

Owner: "详细分析时间段的胜率，一周哪些天胜率高，一天什么时间段胜率高，以及胜率是否和时间区间和大的新闻会议有关".
Read-only arithmetic on existing framework fills, no replay: exp-monday-limit-be-scalein-20261026-v1 (NautilusTrader,
both intrabar paths, full history), variants v2_be17 (score >= top-40 threshold, near Monday edge) and v3_be17
(score >= 0.5, range 0.618 / opposite 1618, -30 percent backstop), i.e. the rules now in paper trading. Per trade the
worse intrabar path, 10bp slippage on non-target exits, the two bad source bars dropped. Win = net > 1bp: a
breakeven exit at entry +0.17 percent nets 0 +- 0.01bp after fees and slippage and counts as flat, not as a win.
Time keys use the fill time: Beijing weekday and hour (UTC+8, no DST), and the NY weekday. Events reuse the official
calendar of exp-monday-1618-macro-20261023-v1 (BLS CPI and Employment Situation 08:30 ET, scheduled FOMC 14:00 ET).
Trades are clustered (many alts fill in the same market move), so:
  - confidence intervals resample whole NY weeks;
  - heterogeneity p-values permute bucket labels between clusters inside each NY week (weekday: Beijing dates;
    hour and news window: clock hours), statistic = n-weighted squared deviation of bucket rates from the pooled rate;
  - stability = Spearman of bucket rates between 2020-2024 and 2025-2026;
  - score-adjusted excess = observed win rate minus the rate expected from the trade's side x score decile;
  - select then confirm (added after the first run showed Beijing 21-24h low in both periods, a post-hoc pick):
    the worst 3h Beijing window chosen on 2020-2024 alone with a scan-corrected p, then the same window on
    2025-2026 with a one-sided within-week permutation; the post-hoc 21-24h window is reported for both periods.
Exploratory: buckets were not chosen in advance, so no bucket is a filter until forward data confirm it.
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
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF

EXP = ROOT/'experiments/active/exp-monday-limit-time-news-20261026-v1'
DAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
BJ = pd.Timedelta(hours=8)


def load(args):
    symbol, variants = args
    t = {}
    for path in ('ohlc', 'olhc'):
        x = pd.read_csv(RUN/path/'symbols'/symbol/'trades.csv.gz')
        t[path] = x[x.variant.isin(variants)]
    return worse(t['ohlc'], t['olhc'])


def trades(cfg):
    syms = json.loads((RUN/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        m = pd.concat(ex.map(load, [(s, cfg['variants']) for s in syms], chunksize=8), ignore_index=True)
    m['event'] = m.trade_id.str.split(':').str[:3].str.join(':')
    m = m[~m.event.isin(BAD_EVENTS)].copy()
    w = (m.net_l < m.net_o).to_numpy()
    m['net'] = np.where(w, m.net_l, m.net_o)
    m['reason'] = np.where(w, m.reason_l, m.reason_o)
    m['t'] = pd.to_datetime(m.entry_time_o.fillna(m.entry_time_l), utc=True)
    m['exit_t'] = pd.to_datetime(np.where(w, m.exit_time_l, m.exit_time_o), utc=True)
    m['side'] = np.where(m.event.str.endswith('-1'), -1, 1)
    m['week'] = m.event.str.split(':').str[1]
    m['win'] = (m.net > cfg['win_bp']).astype(float)
    m['flat'] = (m.net.abs() <= cfg['win_bp']).astype(float)
    bj = m.t.dt.tz_convert(None)+BJ
    m['bj_day'], m['bj_hour'], m['bj_date'] = bj.dt.dayofweek, bj.dt.hour, bj.dt.strftime('%Y-%m-%d')
    ny = m.t.dt.tz_convert('America/New_York')
    m['ny_day'], m['ny_date'] = ny.dt.dayofweek, ny.dt.strftime('%Y-%m-%d')
    m['hour_key'] = m.t.dt.floor('h').astype('int64')
    return m[['trade_id', 'variant', 'event', 'side', 'score', 'week', 't', 'exit_t', 'net', 'reason', 'win', 'flat',
              'bj_day', 'bj_hour', 'bj_date', 'ny_day', 'ny_date', 'hour_key']]


def news_flags(m, ev, hours):
    """Per clock-hour cluster: the hour [h, h+1) overlaps [event - hours, event + hours]."""
    e = np.sort(pd.to_datetime(ev.utc, utc=True).astype('int64').to_numpy())
    kinds = ev.assign(ns=pd.to_datetime(ev.utc, utc=True).astype('int64')).sort_values('ns').kind.to_numpy()
    h = m.hour_key.to_numpy(); H = 3600*10**9; w = hours*H
    i = np.searchsorted(e, h)
    near = np.zeros(len(h), bool); kind = np.array(['']*len(h), dtype=object); after = np.zeros(len(h), bool)
    for j in (i-1, i, i+1):
        ok = (j >= 0) & (j < len(e)); jj = np.clip(j, 0, len(e)-1)
        hit = ok & (h+H > e[jj]-w) & (h < e[jj]+w) & ~near
        near |= hit; kind[hit] = kinds[jj[hit]]; after[hit] = h[hit]+H/2 >= e[jj[hit]]
    t = m.t.astype('int64').to_numpy(); x = m.exit_t.astype('int64').to_numpy()
    k = np.searchsorted(e, t)  # first event at or after the fill
    through = (k < len(e)) & (e[np.clip(k, 0, len(e)-1)] < x)
    return near, kind, after, through


def stats(x):
    return dict(n=int(len(x)), win_pct=float(100*x.win.mean()) if len(x) else None,
                flat_pct=float(100*x.flat.mean()) if len(x) else None,
                mean_bp=float(x.net.mean()) if len(x) else None, excess_pp=float(100*(x.win-x.exp).mean()) if len(x) else None,
                weeks=int(x.week.nunique()))


def week_boot(m, key, labels, rng, draws):
    """95 percent CI of win rate and mean net per bucket, resampling NY weeks."""
    wk = pd.factorize(m.week)[0]; b = pd.Categorical(m[key], categories=labels).codes
    nw, nb = wk.max()+1, len(labels)
    cnt = np.zeros((nw, nb)); win = np.zeros((nw, nb)); net = np.zeros((nw, nb))
    np.add.at(cnt, (wk, b), 1); np.add.at(win, (wk, b), m.win.to_numpy()); np.add.at(net, (wk, b), m.net.to_numpy())
    W = rng.multinomial(nw, np.full(nw, 1/nw), size=draws).astype(float)
    with np.errstate(all='ignore'):  # Accelerate BLAS raises spurious overflow flags in matmul
        r = 100*(W@win)/(W@cnt); u = (W@net)/(W@cnt)
    def q(a):
        with np.errstate(all='ignore'):
            v = np.nanpercentile(a, [2.5, 97.5], axis=0).T.round(2)
        return [[None if x != x else float(x) for x in row] for row in v]
    return dict(zip(labels, [dict(win_ci=a, mean_ci=c) for a, c in zip(q(r), q(u))]))


def perm_p(m, cluster, key, rng, draws, value='win'):
    """Heterogeneity across buckets beats label shuffles between clusters in the same NY week."""
    g = m.groupby(cluster, sort=False).agg(week=('week', 'first'), lab=(key, 'first'), n=('win', 'size'), s=(value, 'sum'))
    lab = pd.factorize(g.lab)[0]; n = g.n.to_numpy(float); s = g.s.to_numpy(float); wk = pd.factorize(g.week)[0]
    pooled = s.sum()/n.sum(); k = lab.max()+1
    def stat(l):
        nb = np.bincount(l, n, k); sb = np.bincount(l, s, k); ok = nb > 0
        return float((nb[ok]*(sb[ok]/nb[ok]-pooled)**2).sum())
    obs = stat(lab)
    base = np.argsort(wk, kind='stable'); L = lab[base]
    hits = 0
    for _ in range(draws):
        order = np.lexsort((rng.random(len(wk)), wk))
        new = np.empty_like(lab); new[order] = L
        hits += stat(new) >= obs
    return float((hits+1)/(draws+1))


def spearman(a, b):
    ok = [k for k in a if k in b and a[k]['n'] >= 30 and b[k]['n'] >= 30]
    if len(ok) < 4:
        return None
    x = pd.Series([a[k]['win_pct'] for k in ok]).rank(); y = pd.Series([b[k]['win_pct'] for k in ok]).rank()
    return dict(rho=float(np.corrcoef(x, y)[0, 1]), buckets=len(ok))


def window_gaps(h, n, s, width):
    nb, sb = np.bincount(h, n, 24), np.bincount(h, s, 24)
    out = np.empty(24)
    for k in range(24):
        w = np.zeros(24, bool); w[[(k+j) % 24 for j in range(width)]] = True
        out[k] = 100*(sb[w].sum()/nb[w].sum()-sb[~w].sum()/nb[~w].sum())
    return out


def select_confirm(m, early, rng, draws, width=3, posthoc=21):
    def clusters(x):
        c = x.groupby('hour_key').agg(week=('week', 'first'), h=('bj_hour', 'first'), n=('win', 'size'), s=('win', 'sum'))
        return pd.factorize(c.week)[0], c.h.to_numpy(), c.n.to_numpy(float), c.s.to_numpy(float)
    def shuffle(wk, h):
        L = h[np.argsort(wk, kind='stable')]; order = np.lexsort((rng.random(len(wk)), wk))
        new = np.empty_like(h); new[order] = L
        return new
    wk, h, n, s = clusters(m[early]); g = window_gaps(h, n, s, width); k = int(np.argmin(g))
    scan = (1+sum(window_gaps(shuffle(wk, h), n, s, width).min() <= g.min() for _ in range(draws)))/(draws+1)
    wk2, h2, n2, s2 = clusters(m[~early]); g2 = window_gaps(h2, n2, s2, width)
    conf = (1+sum(window_gaps(shuffle(wk2, h2), n2, s2, width)[k] <= g2[k] for _ in range(draws)))/(draws+1)
    return dict(width_hours=width, selected_start_hour=k, early_gap_pp=float(g[k]), early_scan_p=float(scan),
                late_gap_pp=float(g2[k]), late_one_sided_p=float(conf),
                posthoc_start_hour=posthoc, posthoc_early_gap_pp=float(g[posthoc]), posthoc_late_gap_pp=float(g2[posthoc]))


def table(m, key, labels):
    return {lab: stats(m[m[key] == lab]) for lab in labels}


def family(m, cfg, rng, ev):
    m = m.copy()
    m['dec'] = pd.qcut(m.score.rank(method='first'), 10, labels=False)
    m['exp'] = m.groupby(['side', 'dec']).win.transform('mean')
    near, kind, after, through = news_flags(m, ev, cfg['window_hours'])
    m['news'] = np.where(near, np.where(after, 'after', 'before'), 'none'); m['kind'] = kind; m['through'] = through
    early = m.t < pd.Timestamp(cfg['split'], tz='UTC')
    out = dict(all=stats(m), periods=dict(early=stats(m[early]), late=stats(m[~early])))
    hours, days = list(range(24)), list(range(7))
    m['block'] = m.bj_hour//4
    for key, labels, cluster in (('bj_day', days, 'bj_date'), ('ny_day', days, 'ny_date'), ('bj_hour', hours, 'hour_key'),
                                 ('block', list(range(6)), 'hour_key')):
        out[key] = dict(table=table(m, key, labels), ci=week_boot(m, key, labels, rng, cfg['boot_draws']),
                        perm_p_win=perm_p(m, cluster, key, rng, cfg['perm_draws']),
                        perm_p_mean=perm_p(m, cluster, key, rng, cfg['perm_draws'], 'net'),
                        early=table(m[early], key, labels), late=table(m[~early], key, labels))
        out[key]['stability'] = spearman(out[key]['early'], out[key]['late'])
    out['select_confirm'] = select_confirm(m, early, rng, cfg['scan_draws'])
    m['news_any'] = np.where(m.news == 'none', 'none', 'news')
    out['news'] = dict(window_hours=cfg['window_hours'], table=table(m, 'news_any', ['news', 'none']),
                       ci=week_boot(m, 'news_any', ['news', 'none'], rng, cfg['boot_draws']),
                       perm_p_win=perm_p(m, 'hour_key', 'news_any', rng, cfg['perm_draws']),
                       perm_p_mean=perm_p(m, 'hour_key', 'news_any', rng, cfg['perm_draws'], 'net'),
                       side=table(m, 'news', ['before', 'after', 'none']),
                       kind={k: stats(m[(m.news != 'none') & (m.kind == k)]) for k in ('cpi', 'empsit', 'fomc')},
                       early=table(m[early], 'news_any', ['news', 'none']), late=table(m[~early], 'news_any', ['news', 'none']),
                       held_through=dict(yes=stats(m[m.through]), no=stats(m[~m.through])))
    nd = set(pd.to_datetime(ev.utc, utc=True).dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d'))
    m['event_day'] = np.where(m.ny_date.isin(nd), 'event_day', 'other')
    out['event_day'] = dict(table=table(m, 'event_day', ['event_day', 'other']),
                            perm_p_win=perm_p(m, 'ny_date', 'event_day', rng, cfg['perm_draws']),
                            kind={k: stats(m[m.ny_date.isin(set(pd.to_datetime(ev[ev.kind == k].utc, utc=True)
                                  .dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d')))]) for k in ('cpi', 'empsit', 'fomc')})
    return out, m


def chart(res, path):
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'Arial Unicode MS']
    fams = list(res)
    fig, axes = plt.subplots(len(fams), 2, figsize=(13, 3.4*len(fams)), gridspec_kw=dict(width_ratios=[3, 1.2]), facecolor=SURF)
    for row, f in zip(np.atleast_2d(axes), fams):
        for ax, key, labels, names in ((row[0], 'bj_hour', list(range(24)), [f'{h}' for h in range(24)]),
                                       (row[1], 'bj_day', list(range(7)), DAYS)):
            tb, ci = res[f][key]['table'], res[f][key]['ci']
            y = [tb[k]['win_pct'] or 0 for k in labels]
            lo = [y[i]-(ci[k]['win_ci'][0] if ci[k]['win_ci'][0] is not None else y[i]) for i, k in enumerate(labels)]
            hi = [(ci[k]['win_ci'][1] if ci[k]['win_ci'][1] is not None else y[i])-y[i] for i, k in enumerate(labels)]
            ax.bar(range(len(labels)), y, color=INK2, width=.7, yerr=[lo, hi], ecolor=MUTED, capsize=2)
            ax.axhline(res[f]['all']['win_pct'], color=INK, lw=1, ls='--')
            ax.set_xticks(range(len(labels))); ax.set_xticklabels(names, fontsize=8)
            ax.set_facecolor(SURF); ax.grid(axis='y', color=GRID, lw=.6); ax.set_axisbelow(True)
            for s in ('top', 'right'):
                ax.spines[s].set_visible(False)
            p = res[f][key]['perm_p_win']
            ax.set_title(f"{f} · {'北京时间小时' if key == 'bj_hour' else '北京时间星期'} · 胜率% (虚线=整体, 误差=按周重抽95%) · 置换p={p:.3f}",
                         fontsize=9, color=INK, loc='left')
    fig.tight_layout(); fig.savefig(path, dpi=130, facecolor=SURF); plt.close(fig)


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_time_news.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    ev = pd.read_csv(ROOT/cfg['events'])
    m = trades(cfg)
    rng = np.random.default_rng(cfg['seed'])
    res, rows = {}, []
    for v in cfg['variants']:
        res[v], mm = family(m[m.variant == v], cfg, rng, ev)
        rows.append(mm)
    pd.concat(rows).drop(columns=['exp', 'dec']).to_csv(exp/'trades.csv.gz', index=False)
    chart(res, exp/'1_time_winrate.png')
    summary = dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                   training_eligible=False, production_eligible=False)
    dump(exp/'summary.json', summary)
    for v, r in res.items():
        print(v, json.dumps(r['all'], ensure_ascii=False))
        for key in ('bj_day', 'bj_hour', 'block'):
            print(' ', key, 'p_win', r[key]['perm_p_win'], 'p_mean', r[key]['perm_p_mean'], 'stability', r[key]['stability'])
        print('  news', json.dumps(r['news']['table'], ensure_ascii=False), 'p', r['news']['perm_p_win'], r['news']['perm_p_mean'])
        print('  select_confirm', json.dumps(r['select_confirm']))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
