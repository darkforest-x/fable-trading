"""High win-rate spike rule (spike-HW v1): backtest statistics for the booklet and the paper track (owner 2026-10-26).

Owner: "你先把胜率最高的单独写一版本小册，回测有多少单，详细的回测数据也整理统计分析一下，然后我们模拟账户除了之前的4组也要加上这个".
Rule (frozen here, read-only on existing NautilusTrader fills):
  entry   limit at the NY Monday 1.618 (first weekly touch per side, alts), frozen entry score >= 0.6 and 15m EMA
          order score >= 3 (EMA 8/13/21/34/55 on completed 15m bars before the touch, side-aligned; l2_order_score);
  confirm next 60m candle close: back inside arms breakeven, otherwise exit at that close;
  target  resting limit at the near Monday edge;
  stop    -18 percent backstop from the fill until the confirmation (inside the 5x liquidation distance);
  sizing  10 slots at 5x: each fill posts margin = realized equity / 10, notional = 5 x margin, at most 10 open
          positions (later fills are skipped); a position whose floating loss reaches 19 percent (100/5 - 1) is
          liquidated and loses its margin; otherwise P&L = notional x net, never below -margin.
Fills: near_P18 of exp-monday-limit-v3-grid-20261025-v1 (breakeven at entry; paper uses entry +0.17 percent, which
changed nothing beyond tie noise elsewhere), worse intrabar path per trade, 10bp slippage on non-target exits, the
two bad source bars dropped; MAE from Codex 5m bars as in monday_limit_spike_opt. near_far and near_P30, and order
score >= 1, are kept as comparisons. Equity: one continuous 2020-2026 path and restarts per period, medians over
TIES random orders of same-time fills. In-sample: the order filter was chosen on the same history.
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
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, B, clean
from yoyo.evaluation.monday_limit_reclaim_patterns import INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_spike_opt as so

EXP = ROOT/'experiments/active/exp-monday-limit-spike-hw-20261026-v1'
A = ROOT/'experiments/active'
CAUSE = {4: '止盈', 5: '保本出场', 11: '没收回止损', 12: '收回不足保本价', 1: '兜底止损', 0: '其他'}


def slot_replay(t, slots, lev, liq, seed, trace=None):
    """Equity path of the slot rule; exits before entries at one timestamp except a trade's own same-bar exit."""
    rng = np.random.default_rng(seed)
    o = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[o].reset_index(drop=True)
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    eq, peak, dd, nliq, skipped, wins, taken = 1000., 1000., 0., 0, 0, 0, 0
    open_, path = {}, [(int(t.entry_ns.min()), 1000.)] if len(t) else []
    net, mae = t.net.to_numpy()/1e4, t.mae_pct.to_numpy()
    for ts, kind, j in ev:
        j = int(j)
        if kind == 1:
            if len(open_) >= slots or eq <= 0:
                skipped += 1; continue
            open_[j] = eq/slots; taken += 1
        elif j in open_:
            m = open_.pop(j)
            pnl = -m if mae[j] >= liq else max(-m, m*lev*net[j])
            nliq += mae[j] >= liq; wins += pnl > 0
            eq += pnl; peak = max(peak, eq); dd = max(dd, 1-eq/peak); path.append((int(ts), eq))
            if trace is not None:
                trace(t.iloc[j], int(ts), eq, pnl, m*lev)
    return dict(final_multiple=eq/1000, max_drawdown_pct=100*dd, liquidations=int(nliq), skipped=int(skipped), taken=int(taken),
                win_pct=100*wins/max(taken, 1)), path


def stats(x):
    n = len(x)
    if not n:
        return dict(n=0)
    w, l = x.net[x.win == 1], x.net[(x.win == 0) & (x.flat == 0)]
    hold = (x.exit_t-x.entry_t).dt.total_seconds()/60
    return dict(n=int(n), win_pct=float(100*x.win.mean()), flat_pct=float(100*x.flat.mean()), loss_pct=float(100*len(l)/n),
                mean_bp=float(x.net.mean()), median_bp=float(x.net.median()), win_mean_bp=float(w.mean()) if len(w) else None,
                loss_mean_bp=float(l.mean()) if len(l) else None, worst_bp=float(x.net.min()), best_bp=float(x.net.max()),
                profit_factor=float(w.sum()/-l.sum()) if len(l) and l.sum() < 0 else None,
                causes={CAUSE.get(int(k), str(k)): int(v) for k, v in x.reason.value_counts().items()},
                same_bar_pct=float(100*((x.exit_t == x.entry_t) & (x.reason == 4)).mean()),
                hold_min_median=float(hold.median()), hold_min_p90=float(hold.quantile(.9)),
                mae_pre_median=float(x.mae_pre_pct.median()), mae_pre_p90=float(x.mae_pre_pct.quantile(.9)))


def max_losing_streak(x):
    s = best = 0
    for v in x.sort_values('entry_ns').win.to_numpy():
        s = 0 if v == 1 else s+1; best = max(best, s)
    return int(best)


def charts(t, paths, exp):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.facecolor': SURF, 'figure.facecolor': SURF})
    def style(ax, title):
        ax.set_title(title, color=INK, loc='left', fontsize=11); ax.grid(color=GRID, lw=.6); ax.set_axisbelow(True)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    fig, ax = plt.subplots(figsize=(11, 4.2))
    for k, (name, p) in enumerate(paths.items()):
        d = pd.DataFrame(p, columns=['ts', 'equity']); d['ts'] = pd.to_datetime(d.ts, utc=True)
        ax.plot(d.ts, d.equity, color=('#2a78d6', '#898781')[k], lw=1.6 if k == 0 else 1.1, label=name)
    ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1); ax.legend(frameon=False)
    style(ax, '1000U 资金曲线（2020-01 至 2026-09，连续复利，已实现权益，对数坐标）')
    fig.tight_layout(); fig.savefig(exp/'1_equity.png', dpi=130); plt.close(fig)
    m = t.assign(month=t.entry_t.dt.strftime('%Y-%m')).groupby('month').agg(n=('win', 'size'), win=('win', 'mean'))
    y = t.assign(year=t.entry_t.dt.year).groupby('year').agg(n=('win', 'size'), win=('win', 'mean'), mean=('net', 'mean'))
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
    axes[0].bar(y.index.astype(str), y.n, color='#2a78d6')
    for i, (n, w) in enumerate(zip(y.n, y.win)):
        axes[0].text(i, n, f'{100*w:.0f}%', ha='center', va='bottom', fontsize=9, color=INK2)
    style(axes[0], '每年笔数（柱顶为胜率）')
    axes[1].hist(t.net/100, bins=np.arange(-20, 21, 1), color='#2a78d6', edgecolor=SURF)
    axes[1].set_xlabel('每笔净收益（%，名义金额，未加杠杆）'); style(axes[1], '每笔净收益分布（−20% 到 +20%，超出部分截断）')
    fig.tight_layout(); fig.savefig(exp/'2_years_and_returns.png', dpi=130); plt.close(fig)
    fig, ax = plt.subplots(figsize=(11, 3.6))
    edges = [0, 1, 2, 3, 5, 7, 10, 15, 18, 100]
    lab = [f'{a}–{b}%' if b < 100 else f'≥{a}%' for a, b in zip(edges[:-1], edges[1:])]
    for k, (name, sel) in enumerate((('赢单', t.win == 1), ('非赢单', t.win == 0))):
        c = pd.cut(t.mae_pre_pct[sel], edges, right=False, labels=lab).value_counts().reindex(lab)
        ax.bar(np.arange(len(lab))+(k-.5)*.4, c.to_numpy(), width=.4, color=('#2a78d6', '#e34948')[k], label=name)
    ax.set_xticks(range(len(lab)), lab); ax.legend(frameon=False)
    style(ax, '确认前最大浮亏分布（成交到 60 分钟确认收盘之间）')
    fig.tight_layout(); fig.savefig(exp/'3_floating_loss.png', dpi=130); plt.close(fig)
    _ = m  # monthly table is written to summary.json


def run(config):
    exp = config.resolve().parent
    for p in ('yoyo/evaluation/monday_limit_spike_hw.py', str(config.resolve().relative_to(ROOT))):
        if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
           subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    cfg = json.loads(config.read_text())
    tn = pd.read_csv(ROOT/cfg['subset_trades'], usecols=['event', 'variant', 'score'])
    feat = pd.read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).rename(columns={'l2_order_score': 'order'})
    ev = tn[(tn.variant == 'v2_be17') & (tn.score >= cfg['min_score'])].merge(feat, on='event')
    ev = ev[(ev.order >= 1) & ~ev.event.isin(BAD_EVENTS)]
    events = set(ev.event); syms = sorted({e.split(':')[0] for e in events})
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(so.legs, [(s, cfg['sources'], events) for s in syms], chunksize=4) if len(d)], ignore_index=True)
    t = t.merge(ev[['event', 'order']], on='event')
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64')
    t['win'] = (t.net > cfg['win_bp']).astype(float); t['flat'] = (t.net.abs() <= cfg['win_bp']).astype(float)
    t['symbol'] = t.event.str.split(':').str[0]
    rule = t[(t.variant == cfg['variant']) & (t.order >= cfg['min_order'])].reset_index(drop=True)
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    pers = {'y2020_2024': lambda x: x.entry_t < B[0], 'y2025': lambda x: (x.entry_t >= B[0]) & (x.entry_t < B[1]),
            'y2026': lambda x: x.entry_t >= B[1], 'last5m': lambda x: x.entry_t >= window, 'all': lambda x: x.entry_t.notna()}
    sl = cfg['sizing']
    res = dict(rule=cfg['rule'], trades=int(len(rule)), symbols=int(rule.symbol.nunique()), weeks=int(rule.event.str.split(':').str[1].nunique()),
               first=str(rule.entry_t.min()), last=str(rule.entry_t.max()), max_losing_streak=max_losing_streak(rule))
    res['periods'] = {k: stats(rule[f(rule)]) for k, f in pers.items()}
    res['years'] = {str(y): stats(g) for y, g in rule.groupby(rule.entry_t.dt.year)}
    res['months'] = {m: dict(n=int(len(g)), win_pct=float(100*g.win.mean()), mean_bp=float(g.net.mean()))
                     for m, g in rule.groupby(rule.entry_t.dt.strftime('%Y-%m'))}
    res['side'] = {('做多' if s == 1 else '做空'): stats(rule[rule.side == s]) for s in (1, -1)}
    res['order'] = {str(int(o)): stats(rule[rule.order == o]) for o in (3, 4)}
    res['score'] = {f'{lo}-{hi}': stats(rule[(rule.score >= lo) & (rule.score < hi)]) for lo, hi in ((.6, .7), (.7, .8), (.8, 1.01))}
    res['same_bar'] = dict(yes=stats(rule[(rule.exit_t == rule.entry_t) & (rule.reason == 4)]),
                           no=stats(rule[~((rule.exit_t == rule.entry_t) & (rule.reason == 4))]))
    res['quantiles_bp'] = {f'q{int(100*q)}': float(rule.net.quantile(q)) for q in (.01, .05, .1, .25, .5, .75, .9, .95, .99)}
    res['top_symbols'] = rule.symbol.value_counts().head(15).to_dict()
    cols = ['event', 'side', 'score', 'order', 'entry_t', 'exit_t', 'net', 'reason', 'mae_pre_pct', 'mae_pct']
    res['worst'] = json.loads(rule.nsmallest(10, 'net')[cols].to_json(orient='records', date_format='iso'))
    res['best'] = json.loads(rule.nlargest(10, 'net')[cols].to_json(orient='records', date_format='iso'))
    res['compare'] = {f'{v}|order>={o}': {k: stats(g[f(g)]) for k, f in pers.items()}
                      for v in cfg['compare_variants'] for o in (1, 3) for g in [t[(t.variant == v) & (t.order >= o)]]}
    res['equity'] = {}
    paths = {}
    for name, x in (('规则：分10份×5倍', rule), ('对照：排列分≥1', t[(t.variant == cfg['variant']) & (t.order >= 1)].reset_index(drop=True))):
        for k, f in pers.items():
            y = x[f(x)].reset_index(drop=True)
            reps = [slot_replay(y, sl['slots'], sl['leverage'], sl['liq_pct'], cfg['seed']+r) for r in range(cfg['ties'])]
            res['equity'][f'{name}|{k}'] = {kk: float(np.median([r[0][kk] for r in reps])) for kk in reps[0][0]}
            if k == 'all':
                med = np.argsort([r[0]['final_multiple'] for r in reps])[len(reps)//2]
                paths[name] = reps[med][1]
                if name.startswith('规则'):
                    d = pd.DataFrame(reps[med][1], columns=['ts', 'equity']); d['ts'] = pd.to_datetime(d.ts, utc=True)
                    yearly = d.groupby(d.ts.dt.year).equity.last()
                    start = pd.concat([pd.Series({yearly.index[0]-1: 1000.}), yearly])
                    res['equity_yearly_return_pct'] = {str(y): float(100*(yearly[y]/start[y-1]-1)) for y in yearly.index}
    charts(rule, paths, exp)
    rule[cols+['symbol']].sort_values('entry_t').to_csv(exp/'trades.csv', index=False)
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps({k: res[k] for k in ('trades', 'symbols', 'weeks', 'first', 'last', 'max_losing_streak')}, ensure_ascii=False))
    for k, v in res['periods'].items():
        print(k, json.dumps(v, ensure_ascii=False))
    for k, v in res['equity'].items():
        print(k, json.dumps(v))
    print('yearly', json.dumps(res.get('equity_yearly_return_pct')))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, default=EXP/'config.json')
    run(ap.parse_args().config)
