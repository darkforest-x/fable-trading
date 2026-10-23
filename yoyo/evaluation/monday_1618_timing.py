"""When does a first weekly1618 touch hold most often? Descriptive timing study.

Owner2026-10-23: "详细统计分析一下 什么时间点打到1.618生效率最高". Reads only
frozen touch ledgers (labels unchanged: Monday near edge before the2.0
extension; driftless odds1/2.618=38.2percent) and slices by the touch bar's
open time, which is known at the touch:
  hour_bj     Beijing hour (UTC+8) and4h blocks
  dow_ny      New York weekday of the touch (the Monday range is NY-based)
  since_mon   hours from Monday NY close to the touch
  dow x block heatmap (OKX universe only)
Datasets: okx (177 OKX swaps2025-06..2026-08, 15m labels, with earlier/
later halves), bin_pre (Binance12 majors before2025-06, 15m labels, no
time overlap with okx), btc (Binance BTC1m labels2022-11..2026-09), xau
(XAUUSD spot2011-2026, 1m labels).
Per bin: Wilson and week-cluster bootstrap95percent intervals, two-sided
binomial p versus38.2 with Benjamini-Hochberg q inside each table, mean R
of the1618 limit trade (SL2.0, TP Monday edge, fee0). Per table: whether
validity differs across bins at all, by chi-square and by a permutation that
shuffles bin labels only among touches of the same NY week (removes
market-wide weekly regime). Stability: Spearman of bin validities between
independent slices. Descriptive; no rule is selected or promoted here.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-monday-1618-timing-20261023-v1'
A = ROOT/'experiments/active'
ODDS = 1/2.618
DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
SINCE = [0, 6, 12, 24, 48, 72, 96, 1e9]


def datasets():
    okx = pd.read_csv(A/'exp-monday-1618-validity-20261023-v1/touches.csv.gz')
    flow = pd.read_csv(A/'exp-monday-1618-flow-20261023-v1/touches_flow.csv.gz')
    pre = flow[pd.to_datetime(flow.touch_time, utc=True) < pd.Timestamp('2025-06-01', tz='UTC')].copy()
    m1 = pd.read_csv(A/'exp-monday-1618-oos-1m-20261023-v1/touches_1m.csv.gz')
    btc = m1[m1.symbol.eq('BTC')].copy()
    xau = pd.read_csv(A/'exp-monday-1618-xau-spot-20261023-v1/touches_xau.csv.gz')
    out = dict(okx=okx, okx_earlier=okx[okx.segment.eq(0)], okx_later=okx[okx.segment.eq(1)],
               bin_pre=pre, btc=btc, xau=xau)
    for d in out.values():
        t = pd.DatetimeIndex(pd.to_datetime(d.touch_time, utc=True))
        bj = t.tz_convert('Asia/Shanghai'); ny = t.tz_convert('America/New_York')
        d['hour_bj'] = bj.hour
        d['block_bj'] = [f'{4*(h//4):02d}-{4*(h//4)+4:02d}' for h in bj.hour]
        d['dow_ny'] = [DOW[x] for x in ny.dayofweek]
        d['since_mon'] = pd.cut(d.hours, SINCE, right=False,
                                labels=['0-6h', '6-12h', '12-24h', '1-2d', '2-3d', '3-4d', '4d+']).astype(str)
    return out


def wilson(k, n, z=1.96):
    if not n:
        return [None, None]
    p = k/n; d = 1+z*z/n
    c = (p+z*z/(2*n))/d; h = z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/d
    return [100*(c-h), 100*(c+h)]


def cluster_ci(d, key, rng, draws=1000):
    """Resample whole NY weeks; per-bin validity quantiles."""
    weeks = d.week.unique()
    g = {w: x for w, x in d.groupby('week')}
    stats = {b: [] for b in d[key].unique()}
    for _ in range(draws):
        s = pd.concat([g[w] for w in rng.choice(weeks, len(weeks))])
        for b, x in s.groupby(key):
            stats[b].append(100*x.valid.mean())
    return {b: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] if v else [None, None] for b, v in stats.items()}


def heterogeneity(d, key, rng, draws=2000):
    from scipy.stats import chi2_contingency
    tab = pd.crosstab(d[key], d.valid)
    chi_p = float(chi2_contingency(tab)[1]) if tab.shape == (tab.shape[0], 2) and len(tab) > 1 else None
    def stat(lbl):
        x = pd.DataFrame(dict(b=lbl, v=d.valid.to_numpy()))
        m = x.v.mean(); g = x.groupby('b').v.agg(['size', 'mean'])
        return float((g['size']*(g['mean']-m)**2).sum())
    lbl = d[key].to_numpy(); obs = stat(lbl)
    groups = [np.flatnonzero(d.week.to_numpy() == w) for w in d.week.unique()]
    count = 0
    for _ in range(draws):
        s = lbl.copy()
        for g in groups:
            s[g] = rng.permutation(s[g])
        count += stat(s) >= obs
    return dict(chi2_p=chi_p, within_week_perm_p=float((count+1)/(draws+1)))


def table(d, key, rng, order=None):
    from scipy.stats import binomtest
    d = d.reset_index(drop=True)
    ci = cluster_ci(d, key, rng)
    rows = []
    for b, x in d.groupby(key):
        n = len(x); k = int(x.valid.sum())
        rows.append(dict(bin=str(b), n=n, valid_pct=100*k/n, wilson95=wilson(k, n), week_boot95=ci[b],
                         p_vs_382=float(binomtest(k, n, ODDS).pvalue), mean_r=float(x.r.mean())))
    p = np.array([r['p_vs_382'] for r in rows]); order_p = np.argsort(p); m = len(p)
    q = np.empty(m); q[order_p] = np.minimum(1, np.minimum.accumulate((p[order_p]*m/np.arange(1, m+1))[::-1])[::-1])
    for r, qq in zip(rows, q):
        r['bh_q'] = float(qq)
    if order:
        rows.sort(key=lambda r: order.index(r['bin']) if r['bin'] in order else 99)
    return dict(rows=rows, **heterogeneity(d, key, rng))


def spearman(a, b):
    from scipy.stats import spearmanr
    common = [r['bin'] for r in a['rows'] if r['bin'] in {x['bin'] for x in b['rows']}]
    va = [next(r['valid_pct'] for r in a['rows'] if r['bin'] == c) for c in common]
    vb = [next(r['valid_pct'] for r in b['rows'] if r['bin'] == c) for c in common]
    rho, p = spearmanr(va, vb)
    return dict(bins=len(common), rho=float(rho), p=float(p))


def run():
    if subprocess.run(['git','diff','--quiet','HEAD','--','yoyo/evaluation/monday_1618_timing.py'], cwd=ROOT).returncode:
        raise RuntimeError('Commit builder first')
    rng = np.random.default_rng(20261023)
    data = datasets()
    blocks = [f'{h:02d}-{h+4:02d}' for h in range(0, 24, 4)]
    dims = dict(block_bj=blocks, dow_ny=DOW, since_mon=['0-6h', '6-12h', '12-24h', '1-2d', '2-3d', '3-4d', '4d+'])
    out = {name: {k: table(d, k, rng, order) for k, order in dims.items()} for name, d in data.items()}
    out['okx']['hour_bj'] = table(data['okx'], 'hour_bj', rng, list(range(24)))
    okx = data['okx']
    heat = okx.groupby(['dow_ny', 'block_bj']).valid.agg(['size', 'mean']).reset_index()
    out['okx']['heatmap'] = [dict(dow=r.dow_ny, block=r.block_bj, n=int(r.size), valid_pct=100*r.mean) for r in heat.itertuples()]
    stability = {k: dict(okx_earlier_vs_later=spearman(out['okx_earlier'][k], out['okx_later'][k]),
                         okx_vs_bin_pre=spearman(out['okx'][k], out['bin_pre'][k]))
                 for k in dims}
    summary = dict(question='Which touch times have the highest Monday1618 validity?', driftless_pct=100*ODDS,
        sizes={k: len(v) for k, v in data.items()}, tables=out, stability=stability,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False)
    EXP.mkdir(exist_ok=True); dump(EXP/'summary.json', summary)
    print(json.dumps(dict(sizes=summary['sizes'], stability=stability), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
