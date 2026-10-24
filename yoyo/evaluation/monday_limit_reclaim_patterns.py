"""Entry-time patterns in the Codex Monday1618 limit-reclaim ledgers (owner2026-10-24).

Owner: "怎么提高胜率呢？分析规律想想办法". Reads the framework ledgers of the
snapshot output/monday-partial-snapshot-20261024-2224 (alts, 60m
confirmation, near Monday edge target; plus 5/15m for the outcome mix). Win =
net return >0 (in practice the target fill). Every feature is known at the
fill time:
  range_pct: Monday range / level (known at Monday close);
  range_vs_typical: range_pct / median range_pct of the symbol's previous
    8 ledger weeks;
  side; hours from the NY Monday close to the fill; NY weekday; UTC hour;
  age_weeks: weeks since the symbol's first ledger week (listing proxy);
  vol_q: quintile of the ledger's causal prior-14-bar relative-volatility bucket;
  cluster: other symbols filling the same side in the 60 minutes before;
  opp_first: the same symbol-week's opposite 1618 filled earlier;
  prior_win: the symbol's earlier closed trades' win rate (>=5 trades).
Each bin reports n, win rate, mean/median net bp and the complete-group
excess over its own three random controls, separately for fills before
2025-01-01 (discovery) and from 2025-01-01 (validation). The feature list is
fixed here before reading either half; nothing is re-executed.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns-20261024-v1'
SRC = ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
SNAP = ROOT/'output/monday-partial-snapshot-20261024-2224/manifest.json'
COLS = ['trade_id', 'symbol', 'asset_group', 'week', 'side', 'population', 'reclaim_minutes', 'exit_mode', 'net_bp',
        'reason', 'closed', 'entry_time', 'exit_time', 'level', 'entry', 'target_price', 'monday_high', 'monday_low', 'bucket']
SPLIT = pd.Timestamp('2025-01-01', tz='UTC')
INK, INK2, MUTED, GRID, SURF = '#0b0b0b', '#52514e', '#898781', '#e1e0d9', '#fcfcfb'
BLUE, ORANGE = '#2a78d6', '#eb6834'


def load(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=COLS)
    return t[(t.asset_group == 'ALTCOINS') & (t.exit_mode == 'near_monday_edge') & t.reclaim_minutes.isin([5, 15, 60])]


def features(sig):
    """Entry-time features for unique signal events (one row per symbol-week-side)."""
    s = sig.copy()
    s['t'] = pd.to_datetime(s.entry_time, utc=True)
    s['range_pct'] = 100*(s.monday_high-s.monday_low)/s.level
    s['tp_pct'] = 100*s.side*(s.target_price-s.entry)/s.entry
    mon_end = pd.to_datetime(s.week).dt.tz_localize('America/New_York')+pd.Timedelta(days=1)
    s['hours_after'] = (s.t-mon_end.dt.tz_convert('UTC')).dt.total_seconds()/3600
    s['weekday'] = s.t.dt.tz_convert('America/New_York').dt.dayofweek
    s['hour_utc'] = s.t.dt.hour
    first = s.groupby('symbol').week.transform('min')
    s['age_weeks'] = (pd.to_datetime(s.week)-pd.to_datetime(first)).dt.days//7
    wk = s.drop_duplicates(['symbol', 'week'])[['symbol', 'week', 'range_pct']].sort_values(['symbol', 'week'])
    wk['typical'] = wk.groupby('symbol').range_pct.transform(lambda r: r.shift(1).rolling(8, min_periods=3).median())
    s = s.merge(wk[['symbol', 'week', 'typical']], on=['symbol', 'week'], how='left')
    s['range_vs_typical'] = s.range_pct/s.typical
    s['cluster'] = 0
    for side, g in s.groupby('side'):
        ts = g.t.astype('int64').to_numpy(); order = np.argsort(ts); tsorted = ts[order]
        syms = g.symbol.to_numpy()[order]
        lo = np.searchsorted(tsorted, tsorted-3_600_000_000_000, 'left'); hi = np.searchsorted(tsorted, tsorted, 'left')
        cnt = np.array([len(set(syms[a:b])-{syms[k]}) for k, (a, b) in enumerate(zip(lo, hi))])
        s.loc[g.index[order], 'cluster'] = cnt
    other = s[['symbol', 'week', 'side', 't']].assign(side=-s.side).rename(columns={'t': 'opp_t'})
    s = s.merge(other, on=['symbol', 'week', 'side'], how='left')
    s['opp_first'] = (s.opp_t < s.t).astype(int)
    s = s.sort_values(['symbol', 't']).reset_index(drop=True)
    prior = np.full(len(s), np.nan)
    for _, g in s.groupby('symbol'):
        ent = g.t.astype('int64').to_numpy(); ex = pd.to_datetime(g.exit_time, utc=True).astype('int64').to_numpy()
        win = (g.net_bp.to_numpy() > 0)
        for k, i in enumerate(g.index):
            done = ex < ent[k]
            if done.sum() >= 5:
                prior[i] = win[done].mean()
    s['prior_win'] = prior
    return s


BINS = {
    'range_pct': ([0, 3, 5, 8, 12, np.inf], ['<3%', '3-5%', '5-8%', '8-12%', '≥12%']),
    'range_vs_typical': ([0, .7, 1., 1.4, 2., np.inf], ['<0.7', '0.7-1', '1-1.4', '1.4-2', '≥2']),
    'hours_after': ([-1, 12, 24, 48, 96, np.inf], ['<12h', '12-24h', '1-2天', '2-4天', '≥4天']),
    'age_weeks': ([-1, 8, 26, 52, 104, np.inf], ['<8周', '8-26周', '半年-1年', '1-2年', '≥2年']),
    'cluster': ([-1, 0, 3, 10, 30, np.inf], ['0', '1-3', '4-10', '11-30', '>30']),
    'prior_win': ([-.01, .15, .25, .35, 1.01], ['<15%', '15-25%', '25-35%', '≥35%']),
}


def table(s, col, labels=None):
    out = {}
    for half, h in (('discovery', s[s.t < SPLIT]), ('validation', s[s.t >= SPLIT])):
        key = h[col] if labels is None else pd.cut(h[col], bins=labels[0], labels=labels[1])
        rows = {}
        for b, g in h.groupby(key, observed=True):
            if len(g) < 50:
                continue
            c = g[g.complete]
            rows[str(b)] = dict(n=len(g), win_pct=float(100*(g.net_bp > 0).mean()), mean_bp=float(g.net_bp.mean()),
                                median_bp=float(g.net_bp.median()), tp_pct_median=float(g.tp_pct.median()),
                                excess_bp=float((c.net_bp-c.cm).mean()) if len(c) else None, complete_n=len(c))
        out[half] = rows
    return out


def charts(res, mix):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    names = dict(range_pct='周一区间 / 价格', range_vs_typical='本周区间 / 该币前8周常态', side='方向',
                 hours_after='周一收盘后多久触及', weekday='触及在周几（纽约）', age_weeks='上线多久', vol_q='触及前波动（五分位）',
                 cluster='前1小时同向触及的其他币数', opp_first='本周另一侧1.618已先触及', prior_win='该币此前胜率')
    for metric, fname, ylab in (('win_pct', '1_winrate_by_feature.png', '胜率 %'), ('mean_bp', '2_mean_by_feature.png', '每笔扣费后平均 bp')):
        fig, axes = plt.subplots(2, 5, figsize=(22, 8.4))
        for ax, (f, title) in zip(axes.flat, names.items()):
            t = res[f]; bins = list(dict.fromkeys(list(t['discovery'])+list(t['validation'])))
            x = np.arange(len(bins)); w = .38
            for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
                v = [t[half].get(b, {}).get(metric, np.nan) for b in bins]
                ax.bar(x+off, v, w*.92, color=col, label=lab)
            ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, bins, fontsize=8)
            ax.set_title(title, color=INK, fontsize=11, loc='left'); ax.grid(axis='y', color=GRID, lw=.6)
            for sp in ('top', 'right'):
                ax.spines[sp].set_visible(False)
        axes.flat[0].set_ylabel(ylab); axes.flat[5].set_ylabel(ylab)
        h, l = axes.flat[0].get_legend_handles_labels()
        fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
        fig.suptitle(f"{ylab} · 山寨币 1.618 挂单 · 60分钟确认 · 止盈近侧周一边界（只用入场时已知的信息）", color=INK, x=.01, ha='left')
        fig.tight_layout(); fig.savefig(EXP/fname, dpi=120); plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    labs = ['止盈', '收回后保本出场', '没收回止损']; cols = ['#1baf7a', MUTED, ORANGE]
    left = np.zeros(3)
    for lab, col in zip(labs, cols):
        v = np.array([mix[p][lab] for p in (5, 15, 60)])
        ax.barh(['5分钟确认', '15分钟确认', '60分钟确认'], v, left=left, color=col, label=lab, height=.55, edgecolor=SURF, linewidth=2)
        for y, (l0, vv) in enumerate(zip(left, v)):
            ax.text(l0+vv/2, y, f'{vv:.0f}%', ha='center', va='center', color=INK, fontsize=10)
        left += v
    ax.set_xlim(0, 100); ax.invert_yaxis(); ax.legend(frameon=False, ncols=3, loc='lower center', bbox_to_anchor=(.5, 1.0))
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('确认时间越短，保本出场越多、止盈越少', color=INK, loc='left', pad=28)
    fig.tight_layout(); fig.savefig(EXP/'3_outcome_mix_by_confirm.png', dpi=130); plt.close(fig)


COMBOS = (('全部', lambda e: e.net_bp.notna()), ('波动最高20%', lambda e: e.vol_q == '最高'),
          ('波动前40%', lambda e: e.vol_q.isin(['高', '最高'])), ('另一侧1.618先触及', lambda e: e.opp_first == '是'),
          ('该币此前胜率≥35%', lambda e: e.prior_win >= .35),
          ('波动最高 + 周二/周三', lambda e: (e.vol_q == '最高') & e.weekday.isin(['周二', '周三'])),
          ('波动最高 + 前1h无其他币同向', lambda e: (e.vol_q == '最高') & (e.cluster == 0)),
          ('波动最高 + 另一侧先触及', lambda e: (e.vol_q == '最高') & (e.opp_first == '是')))


def combos(s):
    out = {}
    for name, f in COMBOS:
        m = f(s); out[name] = {}
        for half, h in (('discovery', s.t < SPLIT), ('validation', s.t >= SPLIT)):
            g = s[m & h]; c = g[g.complete]
            out[name][half] = dict(n=len(g), win_pct=float(100*(g.net_bp > 0).mean()), mean_bp=float(g.net_bp.mean()),
                                   median_bp=float(g.net_bp.median()), excess_bp=float((c.net_bp-c.cm).mean()))
    return out


def combo_chart(cmb):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.2))
    names = list(cmb); y = np.arange(len(names)); w = .38
    for ax, metric, title in ((axes[0], 'win_pct', '胜率 %'), (axes[1], 'mean_bp', '每笔扣费后平均 bp')):
        for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
            v = [cmb[n][half][metric] for n in names]
            ax.barh(y+off, v, w*.92, color=col, label=lab)
            for yy, vv, n in zip(y+off, v, names):
                txt = f'{vv:.0f}%' if metric == 'win_pct' else f'{vv:+.0f}'
                if metric == 'win_pct':
                    txt += f"（{cmb[n][half]['n']}笔）"
                ax.text(vv, yy, ' '+txt, va='center', ha='left', color=INK, fontsize=8)
        ax.set_yticks(y, names); ax.invert_yaxis(); ax.set_title(title, color=INK, loc='left')
        ax.axvline(0, color='#c3c2b7', lw=1); ax.grid(axis='x', color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    axes[0].set_xlim(0, 65); axes[1].set_xlim(0, 125); axes[1].set_yticks(y, [''] * len(names))
    h, l = axes[0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
    fig.suptitle('入场前可知条件的组合 · 两个时间段分别统计（事后挑选，需前向验证）', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'4_combos.png', dpi=130); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_patterns.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(4) as ex:
        d = pd.concat(list(ex.map(load, syms, chunksize=8)), ignore_index=True)
    labs = {4: '止盈', 5: '收回后保本出场', 11: '没收回止损'}
    mix = {int(p): {labs[r]: float(100*(g.reason == r).mean()) for r in labs}
           for p, g in d[(d.population == 'signal') & d.closed].groupby('reclaim_minutes')}
    x = d[d.reclaim_minutes == 60]
    grp = x.trade_id.str.rsplit(':', n=1).str[0]
    ctl = x[x.population == 'random'].groupby(grp[x.population == 'random']).agg(n=('closed', 'sum'), cm=('net_bp', 'mean'))
    sig = x[(x.population == 'signal') & x.closed].copy()
    j = ctl.reindex(grp[sig.index]); sig['complete'] = (j.n == 3).to_numpy(); sig['cm'] = j.cm.to_numpy()
    s = features(sig)
    s['vol_q'] = pd.qcut(s.bucket.rank(method='first'), 5, labels=['最低', '低', '中', '高', '最高'])
    s['side'] = pd.Categorical(s.side.map({1: '做多', -1: '做空'}), ['做多', '做空'])
    days = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    s['weekday'] = pd.Categorical(s.weekday.map(dict(enumerate(days))), days)
    s['opp_first'] = pd.Categorical(s.opp_first.map({0: '否', 1: '是'}), ['否', '是'])
    res = {f: table(s, f, BINS.get(f)) for f in ('range_pct', 'range_vs_typical', 'side', 'hours_after', 'weekday',
                                                  'age_weeks', 'vol_q', 'cluster', 'opp_first', 'prior_win')}
    res['hour_utc'] = table(s.assign(h4=(s.hour_utc//6)*6), 'h4')
    EXP.mkdir(exist_ok=True)
    s[['symbol', 'week', 'side', 't', 'net_bp', 'complete', 'cm', 'range_pct', 'tp_pct', 'range_vs_typical', 'hours_after',
       'weekday', 'hour_utc', 'age_weeks', 'vol_q', 'cluster', 'opp_first', 'prior_win']].to_csv(EXP/'events.csv.gz', index=False)
    charts(res, mix); cmb = combos(s); combo_chart(cmb)
    dump(EXP/'summary.json', dict(config=cfg, n=len(s), outcome_mix_by_confirm=mix, features=res, combos=cmb,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(n=len(s), mix=mix), ensure_ascii=False))


if __name__ == '__main__':
    run()
