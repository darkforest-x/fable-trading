"""More entry-time patterns for the Codex Monday1618 limit-reclaim ledgers (owner2026-10-24 "还有吗").

Same snapshot, rule (alts, 60m confirmation, near Monday edge) and 2025-01-01
split as monday_limit_reclaim_patterns. New features, all known at the NY
Monday close of the trade's week (weekly Monday ranges come from the
opportunities ledgers, which list touched and untouched weeks):
  trend4: side x percent change of the symbol's Monday mid versus 4 weeks
    earlier (>0 = buying a dip in an uptrend / selling a rally in a downtrend);
  btc4: side x the same change for BTCUSDT (Binance1m, from 2022-11);
  last_week: the symbol's previous-week same-side outcome (not touched / won /
    lost, using that trade's recorded exit before this week's fill).
Owner then asked for faster confirmation than 60 minutes ("太久了 有小级别的吗"):
every feature table and the monday_limit_reclaim_patterns combinations are
also reported for 5m and 15m confirmation, plus the mean result of every
confirmation x target cell inside each pre-touch volatility quintile.
Read-only over framework fills; nothing is re-executed.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP, SPLIT, COLS, features, table, combos, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns2-20261024-v1'
TARGETS = ['near_monday_edge', 'range_0.5', 'range_0.618', 'opposite_monday_edge', 'opposite_1618']
TZH = ['近侧周一边界', '区间0.5', '区间0.618', '远侧周一边界', '对侧1.618']
VQ = ['最低', '低', '中', '高', '最高']


def load(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    t = t[(t.asset_group == 'ALTCOINS') & t.reclaim_minutes.isin([5, 15, 60])]
    o = pd.read_csv(SRC/symbol/'opportunities.csv.gz', usecols=['event_id', 'symbol', 'week', 'side', 'status', 'monday_high', 'monday_low'])
    o = o[o.status.isin(['admitted', 'no_observed_touch']) & ~o.event_id.str.contains(':c')]
    return t, o


def weekly_trend(o):
    """Percent change of the Monday mid versus 4 NY weeks earlier, per symbol-week."""
    w = o.drop_duplicates(['symbol', 'week'])[['symbol', 'week', 'monday_high', 'monday_low']].copy()
    w['mid'] = (w.monday_high+w.monday_low)/2; w['wk'] = pd.to_datetime(w.week)
    prev = w[['symbol', 'wk', 'mid']].assign(wk=w.wk+pd.Timedelta(weeks=4)).rename(columns={'mid': 'mid4'})
    w = w.merge(prev, on=['symbol', 'wk'], how='left')
    w['chg4'] = 100*(w.mid/w.mid4-1)
    return w[['symbol', 'week', 'chg4']]


def base(d, o, trend, btc, m):
    """Signal events for confirmation m (near edge) with old and new entry-time features."""
    x = d[(d.reclaim_minutes == m) & (d.exit_mode == 'near_monday_edge')]
    grp = x.trade_id.str.rsplit(':', n=1).str[0]
    ctl = x[x.population == 'random'].groupby(grp[x.population == 'random']).agg(n=('closed', 'sum'), cm=('net_bp', 'mean'))
    sig = x[(x.population == 'signal') & x.closed].copy()
    j = ctl.reindex(grp[sig.index]); sig['complete'] = (j.n == 3).to_numpy(); sig['cm'] = j.cm.to_numpy()
    s = features(sig)
    s = s.merge(trend, on=['symbol', 'week'], how='left').merge(btc[['week', 'chg4']].rename(columns={'chg4': 'btc_chg4'}), on='week', how='left')
    s['trend4'] = s.side*s.chg4; s['btc4'] = s.side*s.btc_chg4
    # Previous week, same side: untouched, won or lost (exit before this fill).
    prev = s[['symbol', 'week', 'side', 'net_bp', 'exit_time']].copy()
    prev['week'] = (pd.to_datetime(prev.week)+pd.Timedelta(weeks=1)).dt.strftime('%Y-%m-%d')
    s = s.merge(prev.rename(columns={'net_bp': 'prev_net', 'exit_time': 'prev_exit'}), on=['symbol', 'week', 'side'], how='left')
    seen = o.assign(week=(pd.to_datetime(o.week)+pd.Timedelta(weeks=1)).dt.strftime('%Y-%m-%d'))[['symbol', 'week', 'side', 'status']]
    s = s.merge(seen.rename(columns={'status': 'prev_status'}), on=['symbol', 'week', 'side'], how='left')
    done = pd.to_datetime(s.prev_exit, utc=True) < s.t
    s['last_week'] = np.select([s.prev_status.eq('no_observed_touch'), done & (s.prev_net > 0), done & (s.prev_net <= 0)],
                               ['上周同侧没触及', '上周同侧赢', '上周同侧输'], '未知')
    s['vol_q'] = pd.qcut(s.bucket.rank(method='first'), 5, labels=VQ)
    days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    s['weekday'] = pd.Categorical(s.weekday.map(dict(enumerate(days))), days)
    s['opp_first'] = pd.Categorical(s.opp_first.map({0: '否', 1: '是'}), ['否', '是'])
    return s


BINS = {'trend4': ([-np.inf, -20, -5, 5, 20, np.inf], ['逆势>20%', '逆势5-20%', '持平±5%', '顺势5-20%', '顺势>20%']),
        'btc4': ([-np.inf, -10, -3, 3, 10, np.inf], ['逆BTC>10%', '逆BTC3-10%', 'BTC持平±3%', '顺BTC3-10%', '顺BTC>10%'])}
FEATS = ('vol_q', 'opp_first', 'trend4', 'btc4', 'last_week')


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_patterns2.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(4) as ex:
        parts = list(ex.map(load, syms, chunksize=8))
    d = pd.concat([a for a, _ in parts], ignore_index=True); o = pd.concat([b for _, b in parts], ignore_index=True)
    btc = weekly_trend(pd.read_csv(SRC/'BTCUSDT/opportunities.csv.gz').query("status in ['admitted','no_observed_touch']"))
    trend = weekly_trend(o)
    res, cmb = {}, {}
    for m in (5, 15, 60):
        s = base(d, o, trend, btc, m)
        res[m] = {f: table(s if f != 'last_week' else s[s.last_week != '未知'], f, BINS.get(f)) for f in FEATS}
        cmb[m] = combos(s)
    # Exit choice by volatility quintile, all confirmation x target cells.
    a = d[(d.population == 'signal') & d.closed].copy()
    q = np.quantile(s.bucket, [.2, .4, .6, .8])
    a['vq'] = pd.cut(a.bucket, [-np.inf, *q, np.inf], labels=VQ)
    a['half'] = np.where(pd.to_datetime(a.entry_time, utc=True) < SPLIT, 'discovery', 'validation')
    grid = {f'{m}m|{tg}|{v}|{h}': dict(n=len(g), mean_bp=float(g.net_bp.mean()), win_pct=float(100*(g.net_bp > 0).mean()))
            for (m, tg, v, h), g in a.groupby(['reclaim_minutes', 'exit_mode', 'vq', 'half'], observed=True)}
    EXP.mkdir(exist_ok=True)
    charts(res, grid, cmb)
    dump(EXP/'summary.json', dict(config=cfg, features={f'{m}m': r for m, r in res.items()}, combos={f'{m}m': c for m, c in cmb.items()},
        exit_grid=grid, volatility_bucket_cuts=q.tolist(),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps({f'{m}m': c for m, c in cmb.items()}, ensure_ascii=False)[:4000])


def charts(res, grid, cmb):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    names = dict(vol_q='触及前波动（五分位）', opp_first='本周另一侧1.618已先触及', trend4='该币4周趋势（相对开仓方向）',
                 btc4='BTC 4周趋势（相对开仓方向）', last_week='上周同侧1.618的结果')
    for metric, fname, ylab in (('win_pct', '1_win_by_feature_confirm.png', '胜率 %'), ('mean_bp', '1b_mean_by_feature_confirm.png', '每笔扣费后平均 bp')):
        fig, axes = plt.subplots(3, 5, figsize=(24, 11))
        for r, m in enumerate((5, 15, 60)):
            for c, (f, title) in enumerate(names.items()):
                ax = axes[r, c]; t = res[m][f]; bins = list(dict.fromkeys(list(t['discovery'])+list(t['validation'])))
                x = np.arange(len(bins)); w = .38
                for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
                    ax.bar(x+off, [t[half].get(b, {}).get(metric, np.nan) for b in bins], w*.92, color=col, label=lab)
                ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, bins, fontsize=7.5); ax.grid(axis='y', color=GRID, lw=.6)
                ax.set_title(f'{title}' if r == 0 else '', color=INK, loc='left', fontsize=11)
                if c == 0:
                    ax.set_ylabel(f'{m}分钟确认\n{ylab}')
                for sp in ('top', 'right'):
                    ax.spines[sp].set_visible(False)
        h, l = axes[0, 0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
        fig.suptitle(f'{ylab} · 山寨币 1.618 挂单 · 止盈近侧周一边界 · 每行一个确认周期', color=INK, x=.01, ha='left')
        fig.tight_layout(); fig.savefig(EXP/fname, dpi=110); plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7))
    names_c = list(cmb[60]); rows = [(n, m) for n in names_c for m in (5, 15, 60)]
    y = np.arange(len(rows)); w = .38
    for ax, metric, title in ((axes[0], 'win_pct', '胜率 %'), (axes[1], 'mean_bp', '每笔扣费后平均 bp')):
        for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
            v = [cmb[m][n][half][metric] for n, m in rows]
            ax.barh(y+off, v, w*.92, color=col, label=lab)
            for yy, vv, (n, m) in zip(y+off, v, rows):
                txt = f'{vv:.0f}%' if metric == 'win_pct' else f'{vv:+.0f}'
                ax.text(vv, yy, ' '+txt, va='center', ha='left' if vv >= 0 else 'right', color=INK, fontsize=7)
        ax.set_yticks(y, [f'{n} · {m}分钟' for n, m in rows], fontsize=8); ax.invert_yaxis()
        ax.axvline(0, color='#c3c2b7', lw=1); ax.grid(axis='x', color=GRID, lw=.6); ax.set_title(title, color=INK, loc='left')
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    axes[1].set_yticks(y, [''] * len(rows))
    h, l = axes[0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
    fig.suptitle('条件组合 × 确认周期（5/15/60分钟）· 两段分别统计，事后挑选需前向验证', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'3_combos_by_confirm.png', dpi=120); plt.close(fig)
    div = LinearSegmentedColormap.from_list('d', ['#d03b3b', '#f0efec', BLUE])
    fig, axes = plt.subplots(1, 3, figsize=(20, 4.6))
    for ax, m in zip(axes, (5, 15, 60)):
        val = np.array([[min(grid.get(f'{m}m|{tg}|{v}|discovery', {}).get('mean_bp', np.nan),
                             grid.get(f'{m}m|{tg}|{v}|validation', {}).get('mean_bp', np.nan)) for tg in TARGETS] for v in VQ])
        lim = 120
        ax.imshow(val, cmap=div, norm=TwoSlopeNorm(0, -lim, lim), aspect='auto')
        for (i, k), _ in np.ndenumerate(val):
            a_ = grid.get(f'{m}m|{TARGETS[k]}|{VQ[i]}|discovery', {}).get('mean_bp', np.nan)
            b_ = grid.get(f'{m}m|{TARGETS[k]}|{VQ[i]}|validation', {}).get('mean_bp', np.nan)
            ax.text(k, i, f'{a_:+.0f} / {b_:+.0f}', ha='center', va='center', color=INK, fontsize=9)
        ax.set_xticks(range(5), TZH, fontsize=9); ax.set_yticks(range(5), [f'波动{v}' for v in VQ])
        ax.set_title(f'{m}分钟确认', color=INK, loc='left')
        for sp in ax.spines.values():
            sp.set_visible(False)
    fig.suptitle('每笔扣费后平均 bp（2025年前 / 2025年起；颜色取两段中较差的那个）· 不同波动下该选哪个止盈', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'2_exit_by_volatility.png', dpi=120); plt.close(fig)


if __name__ == '__main__':
    run()
