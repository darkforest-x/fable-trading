"""Strategy-optimization answers for the Codex Monday1618 limit-reclaim rule (owner2026-10-25).

Owner sub-page "策略优化" asks: floating profit before breakeven/stop, median
floating profit in R, better or partial take-profit, and whether to size by a
per-coin score. Inputs (all framework fills, nothing executed here):
  exp-monday-limit-reclaim-exits-{ohlc,olhc}-20261025-v1: alt signals, 60m,
    targets at 50/75/100 percent of the distance to the near Monday edge,
    both intrabar paths with monotone segments;
  exp-monday-limit-reclaim-mfe-20261025-v1: floating profit between fill and exit;
  exp-monday-limit-reclaim-patterns3-20261024-v1 events: entry-time features.
Per trade the worse of the two paths is used ("worst"). The 50/50 partial is
the mean of the 50 and 100 percent legs per path, then the worse path. R is
the distance from the1618 to the2.0 extension (0.382 of the Monday range),
so the near-edge target is 1.618R. Exit slippage of 10bp is charged on every
non-target exit leg. The score is a logistic regression fitted only on fills
before 2025-01-01 (win = worst net > 0, near-edge target) with features known
at the fill; it is judged on fills from 2025-01-01 and by a 1000 USDT replay of
that period (equal 10 percent per trade versus top-40 percent only versus
quintile-weighted 2-10 percent, gross open notional <= 1x equity).
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-optimize-20261025-v1'
A = ROOT/'experiments/active'
LEGS = ('tp50', 'tp75', 'tp100')
VAR_ZH = {'tp50': '止盈50%', 'tp75': '止盈75%', 'tp100': '止盈100%（原规则）', 'partial': '分批：一半50%、一半100%'}
FEATS = ['bucket', 'log_vol_ratio', 'log_approach', 'opp_first', 'monday_close_side', 'log_liquidity', 'toward24',
         'cluster24', 'side', 'range_pct']
SLIP = 10.


def load_path(args):
    root, symbol = args
    p = root/'symbols'/symbol/'trades.csv.gz'
    if not p.exists():
        return pd.DataFrame()
    want = {'trade_id', 'side', 'entry', 'target_price', 'reason', 'entry_time', 'exit_time', 'net_notional_bp'}
    t = pd.read_csv(p, usecols=lambda c: c in want)
    t['leg'] = t.trade_id.str.rsplit(':', n=1).str[-1]
    t['base'] = t.trade_id.str.rsplit(':', n=1).str[0]
    t['symbol'] = symbol
    return t


def ledger_extra(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'week',
                                                          'bucket', 'monday_high', 'monday_low', 'level'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
    return t.assign(range_pct=100*(t.monday_high-t.monday_low)/t.level)[['trade_id', 'week', 'bucket', 'range_pct']]


def replay_w(t, k, gross=1.):
    """Realized equity from 1000 USDT; per-trade fraction k; same-bar exits follow their entry."""
    net = t.net.to_numpy()/1e4
    same = t.exit.to_numpy() == t.entry.to_numpy()
    ev = np.concatenate([np.c_[t.exit.to_numpy(), np.where(same, 2., 0.), np.arange(len(t))],
                         np.c_[t.entry.to_numpy(), np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    eq, peak, dd, opened, notional, path = 1000., 1000., 0., 0., np.zeros(len(t)), []
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            notional[j] = max(0., min(k[j]*eq, gross*eq-opened)); opened += notional[j]
        else:
            eq += notional[j]*net[j]; opened -= notional[j]
            peak = max(peak, eq); dd = max(dd, 1-eq/peak); path.append((ts, eq))
            if eq <= 0:
                return dict(final_multiple=0., max_drawdown_pct=100.), path
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd), path


def stats(x, col='net'):
    v = x[col]
    return dict(n=len(x), win_pct=float(100*(v > 0).mean()), mean_bp=float(v.mean()), median_bp=float(v.median()))


def tp100_from_ledgers():
    """Near-edge trades from the Codex ledger (O-H-L-C) and the monotone O-L-H-C v2 replay."""
    v2 = A/'exp-monday-limit-reclaim-olhc-20261025-v2/symbols'
    syms = sorted(d.name for d in v2.iterdir() if (d/'done.json').exists())
    with ProcessPoolExecutor(4) as ex:
        o = pd.concat(list(ex.map(codex_rows, syms, chunksize=8)), ignore_index=True)
        l = pd.concat(list(ex.map(load_path, [(v2.parent, s) for s in syms], chunksize=8)), ignore_index=True)
        extra = pd.concat(list(ex.map(ledger_extra, syms, chunksize=8)), ignore_index=True)
    return o, l.drop(columns=['leg', 'base']).assign(leg='tp100', base=l.trade_id), extra


def codex_rows(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'population', 'reclaim_minutes', 'exit_mode', 'closed',
                                                          'side', 'reason', 'entry_time', 'exit_time', 'net_bp'])
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge') & t.closed]
    return t.rename(columns={'net_bp': 'net_notional_bp'}).assign(leg='tp100', base=t.trade_id, symbol=symbol)[
        ['trade_id', 'side', 'reason', 'entry_time', 'exit_time', 'net_notional_bp', 'leg', 'base', 'symbol']]


def run(part='all'):
    p = 'yoyo/evaluation/monday_limit_reclaim_optimize.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    if part == 'score':
        o, l, extra = tp100_from_ledgers()
    else:
        ro, rl = A/'exp-monday-limit-reclaim-exits-ohlc-20261025-v1', A/'exp-monday-limit-reclaim-exits-olhc-20261025-v1'
        syms = sorted(d.name for d in (ro/'symbols').iterdir())
        with ProcessPoolExecutor(4) as ex:
            o = pd.concat(list(ex.map(load_path, [(ro, s) for s in syms], chunksize=8)), ignore_index=True)
            l = pd.concat(list(ex.map(load_path, [(rl, s) for s in syms], chunksize=8)), ignore_index=True)
            extra = pd.concat(list(ex.map(ledger_extra, syms, chunksize=8)), ignore_index=True)
    for d in (o, l):
        d['net_s'] = d.net_notional_bp-np.where(d.reason == 4, 0., SLIP)
        d['entry_ns'] = pd.to_datetime(d.entry_time, utc=True).astype('int64'); d['exit_ns'] = pd.to_datetime(d.exit_time, utc=True).astype('int64')
    m = o.merge(l[['trade_id', 'net_s', 'entry_ns', 'exit_ns', 'reason']], on='trade_id', suffixes=('_o', '_l'))
    worse = m.net_s_l < m.net_s_o
    m['net'] = np.where(worse, m.net_s_l, m.net_s_o)
    m['entry'] = np.where(worse, m.entry_ns_l, m.entry_ns_o); m['exit'] = np.where(worse, m.exit_ns_l, m.exit_ns_o)
    m['reason_w'] = np.where(worse, m.reason_l, m.reason_o)
    legs = {leg: m[m.leg == leg].set_index('base') for leg in (LEGS if part != 'score' else ('tp100',))}
    variants = dict(legs)
    if part != 'score':
        common = legs['tp50'].index.intersection(legs['tp100'].index)
        halves = {}
        for suf in ('o', 'l'):
            a, b = legs['tp50'].loc[common], legs['tp100'].loc[common]
            halves[suf] = (a[f'net_s_{suf}']+b[f'net_s_{suf}'])/2
        pw = halves['l'] < halves['o']
        partial = legs['tp100'].loc[common].copy()
        partial['net'] = np.where(pw, halves['l'], halves['o'])
        partial['exit'] = np.maximum(legs['tp50'].loc[common, 'exit'], legs['tp100'].loc[common, 'exit'])
        variants['partial'] = partial
    ex_ = extra.set_index('trade_id')
    t100 = variants['tp100']
    base_ids = t100.index
    meta = ex_.reindex(base_ids)
    thr = float(np.quantile(meta.bucket[pd.to_datetime(t100.entry_time, utc=True) < SPLIT].dropna(), .8))
    res = {'variants': {}, 'replays': {}}
    for name, d in variants.items():
        d = d.copy(); d['half'] = np.where(pd.to_datetime(d.entry_time, utc=True) < SPLIT, 'discovery', 'validation')
        res['variants'][name] = dict(all=stats(d), **{h: stats(g) for h, g in d.groupby('half')})
        vt = ex_.reindex(d.index).bucket >= thr
        for filt, mask in (('all', np.ones(len(d), bool)), ('vol_top', vt.to_numpy())):
            x = d[mask].reset_index(drop=True)
            for gross in (1., 3.):
                r, _ = replay_w(x, np.full(len(x), .10), gross)
                res['replays'][f'{name}|{filt}|k10|g{int(gross)}'] = r
    # R units: the near-edge target is 1.618R.
    mfe = pd.read_csv(A/'exp-monday-limit-reclaim-mfe-20261025-v1/trades_mfe.csv.gz')
    mfe['R_pct'] = mfe.tp_pct/1.618
    mfe['mfe_R'] = mfe.mfe_strict_pct.fillna(0)/mfe.R_pct
    mfe['net_R'] = mfe.net_bp/100/mfe.R_pct
    lab = {4: '止盈', 5: '收回后保本出场', 11: '没收回止损'}
    res['r_units'] = {lab[int(r)]: dict(n=len(g), mfe_R_median=float(g.mfe_R.median()), mfe_R_p75=float(g.mfe_R.quantile(.75)),
                                        net_R_mean=float(g.net_R.mean()), net_R_median=float(g.net_R.median()))
                      for r, g in mfe.groupby('reason') if int(r) in lab}
    res['r_units']['全部'] = dict(n=len(mfe), mfe_R_median=float(mfe.mfe_R.median()), net_R_mean=float(mfe.net_R.mean()),
                                  R_pct_median=float(mfe.R_pct.median()))
    # Score: fitted before 2025, judged from 2025.
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    ev = pd.read_csv(A/'exp-monday-limit-reclaim-patterns3-20261024-v1/events_60m.csv.gz')
    ev['base'] = ev.symbol+':'+ev.week+':'+np.where(ev.side_n == 1, '+1', '-1')+':m60:near_monday_edge:c0'
    s = t100.join(ev.set_index('base')[['vol_ratio', 'approach_min', 'opp_first', 'monday_close_side', 'liquidity', 'toward24', 'cluster24']], how='inner')
    s = s.join(meta[['bucket', 'range_pct']])
    s['log_vol_ratio'] = np.log(s.vol_ratio.clip(lower=.05)); s['log_approach'] = np.log1p(s.approach_min.clip(lower=0))
    s['opp_first'] = (s.opp_first == '是').astype(int); s['log_liquidity'] = np.log10(s.liquidity.clip(lower=1e4))
    s = s.dropna(subset=FEATS)
    s['t'] = pd.to_datetime(s.entry_time, utc=True); s['y'] = (s.net > 0).astype(int)
    tr, va = s[s.t < SPLIT], s[s.t >= SPLIT].copy()
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(tr[FEATS], tr.y)
    tr_score = model.predict_proba(tr[FEATS])[:, 1]; va['score'] = model.predict_proba(va[FEATS])[:, 1]
    qcut = np.quantile(tr_score, [.2, .4, .6, .8])
    va['q'] = np.searchsorted(qcut, va.score, side='right')+1
    coefs = dict(zip(FEATS, model[-1].coef_[0].round(3).tolist()))
    score = dict(train_n=len(tr), valid_n=len(va), train_auc=float(roc_auc_score(tr.y, tr_score)),
                 valid_auc=float(roc_auc_score(va.y, va.score)), coefficients_standardized=coefs,
                 valid_by_quintile={int(q): dict(n=len(g), win_pct=float(100*g.y.mean()), mean_bp=float(g.net.mean()))
                                    for q, g in va.groupby('q')})
    vv = va.reset_index(drop=True)
    sizing = {}
    paths = {}
    for name, k in (('等额每笔10%', np.full(len(vv), .10)), ('只做分数前40%', np.where(vv.q >= 4, .10, 0.)),
                    ('按分数分档2%-10%', .02*vv.q.to_numpy())):
        r, path = replay_w(vv, k, 1.); sizing[name] = r; paths[name] = path
    score['valid_sizing_replay_from_2025'] = sizing
    res['score'] = score
    EXP.mkdir(exist_ok=True)
    charts(res, paths, part)
    dump(EXP/('summary_score.json' if part == 'score' else 'summary.json'), dict(config=cfg, part=part, vol_bucket_threshold=thr, results=res,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), training_eligible=False, production_eligible=False))
    print(json.dumps(res, ensure_ascii=False, indent=1)[:6000])


def charts(res, paths, part='all'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    names = [n for n in VAR_ZH if n in res['variants']]
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.6))
    for ax, metric, title in ((axes[0], 'win_pct', '胜率 %'), (axes[1], 'mean_bp', '每笔扣费、滑点后平均 bp')):
        x = np.arange(len(names)); w = .38
        for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
            v = [res['variants'][n][half][metric] for n in names]
            ax.bar(x+off, v, w*.92, color=col, label=lab)
            for xx, vv in zip(x+off, v):
                ax.text(xx, vv, f'{vv:.1f}', ha='center', va='bottom' if vv >= 0 else 'top', color=INK, fontsize=9)
        ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, [VAR_ZH[n] for n in names], fontsize=9)
        ax.set_title(title, color=INK, loc='left'); ax.grid(axis='y', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    h, l = axes[0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
    fig.suptitle('止盈放近一点 / 分批止盈 · 每笔取两种K线顺序中较差的结果 · 山寨币 60分钟确认', color=INK, x=.01, ha='left')
    fig.tight_layout()
    if part != 'score':
        fig.savefig(EXP/'1_targets.png', dpi=130)
    plt.close(fig)
    q = res['score']['valid_by_quintile']; ks = sorted(q, key=int)
    fig, axes = plt.subplots(1, 2, figsize=(14, 4.4))
    axes[0].bar([str(k) for k in ks], [q[k]['win_pct'] for k in ks], color=BLUE)
    axes[1].bar([str(k) for k in ks], [q[k]['mean_bp'] for k in ks], color=[ORANGE if q[k]['mean_bp'] < 0 else BLUE for k in ks])
    for ax, t in zip(axes, ('胜率 %（2025年起）', '每笔平均 bp（2025年起）')):
        ax.set_xlabel('分数五分位（1=最低，5=最高；阈值只用2025年前数据定）'); ax.set_title(t, color=INK, loc='left')
        ax.axhline(0, color='#c3c2b7', lw=1); ax.grid(axis='y', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    fig.suptitle(f"币种打分（2025年前训练，2025年起检验）· 检验AUC {res['score']['valid_auc']:.3f}", color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'2_score_quintiles.png', dpi=130); plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    for (name, path), col in zip(paths.items(), (MUTED, BLUE, ORANGE)):
        pp = np.array(path[::max(1, len(path)//3000)])
        ax.plot(pd.to_datetime(pp[:, 0]), pp[:, 1], color=col, lw=2, label=name)
    ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1); ax.legend(frameon=False, loc='upper left')
    ax.set_ylabel('已实现权益（USDT，对数）')
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('2025年起 1000U · 按分数配仓 vs 等额 · 总仓位≤1倍 · 较差K线顺序 · 平仓滑点10bp', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'3_score_sizing.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    import sys
    run('score' if '--score-only' in sys.argv else 'all')
