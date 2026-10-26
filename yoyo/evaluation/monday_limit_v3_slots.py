"""Trade frequency, floating loss and slot-based leveraged sizing for the frozen v3 rule (owner2026-10-26).

Owner: "大于 0.5 分的一天有多少单，最多一天有多少单，把 1000u 的仓位分成 3 个仓位？... 止盈的单子大部分浮亏是多少？
能否 ... 使用一份仓位高杠杆？只要这份仓位不爆仓，然后这个仓位翻倍就提出来到总账户 ... 总仓位翻倍了，小仓位就又加大杠杆
... bit 浪浪那套仓位管理". Trades are the v3 framework fills (exp-monday-limit-v3-levels-20261025-v1, score >= 0.5):
target range 0.618 for 0.5-0.6 and opposite 1618 from 0.6, -30 percent backstop, worse intrabar path, 10bp
slippage on non-target exits; the two bad source bars are dropped. Floating loss (max adverse excursion) uses
Codex's 5m bars from the fill bar to the bar before the exit (the fill bar's extreme always follows a limit fill).
Slot scheme (the owner's description): equity split into N slots; each fill takes a free slot and posts a fixed
margin b (topped up from the reserve) at leverage L; a slot is liquidated (loses its margin) when the floating
loss reaches 100/L - MMR percent, otherwise it books margin * L * net (never below -margin); a slot that reaches
2b sends everything above b back to the reserve; when total equity doubles since the last resize, b becomes
total / N. Fills with no free slot are skipped. Realized equity only; medians over TIES random orders of
same-time fills. Read-only arithmetic on framework fills.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, INK, INK2, MUTED, GRID, SURF
from yoyo.evaluation import monday_limit_reclaim_optimize as opt

EXP = ROOT/'experiments/active/exp-monday-limit-v3-slots-20261026-v1'
LEV = ROOT/'experiments/active/exp-monday-limit-v3-levels-20261025-v1'
BAD_EVENTS = {'BABYUSDT:2026-06-01:-1', 'DASHUSDT:2020-04-13:+1'}
B25 = pd.Timestamp('2025-01-01', tz='UTC')
TIES = 10


def legs(symbol):
    """v3 trade per event (worse path) with its max adverse excursion from Codex 5m bars."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    cols = ['trade_id', 'side', 'entry', 'reason', 'entry_time', 'exit_time', 'entry_i', 'exit_i', 'net_notional_bp', 'score', 'variant']
    paths = {}
    for p in ('ohlc', 'olhc'):
        t = pd.read_csv(LEV/p/'symbols'/symbol/'trades.csv.gz', usecols=cols)
        want = np.where(t.score >= .6, 'opp1618_P30', 'r0618_P30')
        paths[p] = t[t.variant == want].set_index('trade_id')
    o, l = paths['ohlc'], paths['olhc']
    if o.empty:
        return pd.DataFrame()
    l = l.reindex(o.index)
    no = o.net_notional_bp-np.where(o.reason == 4, 0., opt.SLIP); nl = l.net_notional_bp-np.where(l.reason == 4, 0., opt.SLIP)
    worse = (nl < no).to_numpy()
    d = pd.DataFrame(dict(side=o.side, entry=o.entry, score=o.score, net=np.where(worse, nl, no),
                          reason=np.where(worse, l.reason, o.reason), entry_i=o.entry_i,
                          exit_i=np.where(worse, l.exit_i, o.exit_i),
                          entry_t=pd.to_datetime(o.entry_time, utc=True), exit_t=pd.to_datetime(np.where(worse, l.exit_time, o.exit_time), utc=True)),
                     index=o.index)
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    mae = []
    for a, b, s, e in zip(d.entry_i.astype(int), d.exit_i.astype(int), d.side, d.entry):
        seg = slice(a, max(a+1, b))
        worst = lo[seg].min() if s == 1 else h[seg].max()
        mae.append(max(0., -s*(worst-e)/e*100))
    d['mae_pct'] = mae
    d['event'] = [':'.join(i.split(':')[:3]) for i in d.index]
    d['symbol'] = symbol
    return d.reset_index(drop=True)


def simulate(t, lev, slots=3, extract=True, resize=True, mmr=1., seed=0):
    """Slot scheme on realized equity; returns final multiple, max drawdown, liquidations and skipped fills."""
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.random(len(t)), t.entry_ns.to_numpy()))
    t = t.iloc[order].reset_index(drop=True)
    ev = np.concatenate([np.c_[t.exit_ns, np.where(t.exit_ns == t.entry_ns, 2, 0), np.arange(len(t))],
                         np.c_[t.entry_ns, np.ones(len(t)), np.arange(len(t))]])
    ev = ev[np.lexsort((ev[:, 1], ev[:, 0]))]
    liq = 100./lev-mmr
    reserve, base = 1000.*(slots-1)/slots, 1000./slots
    bal = [base]*slots; busy = [None]*slots; margin = {}
    last_reset, peak, dd, nliq, skipped, taken = 1000., 1000., 0., 0, 0, 0
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
            bal[i] += pnl
            if extract and bal[i] >= 2*base:
                reserve += bal[i]-base; bal[i] = base
            total = reserve+sum(bal)
            if resize and total >= 2*last_reset:
                base, last_reset = total/slots, total
            peak = max(peak, total); dd = max(dd, 1-total/peak)
            if total <= 1e-6:
                return dict(final_multiple=0., max_drawdown_pct=100., liquidations=nliq, skipped=skipped, taken=taken)
    total = reserve+sum(bal)
    return dict(final_multiple=total/1000, max_drawdown_pct=100*dd, liquidations=nliq, skipped=skipped, taken=taken)


def run():
    p = 'yoyo/evaluation/monday_limit_v3_slots.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = json.loads((LEV/'config.json').read_text())['symbols']
    with ProcessPoolExecutor(4) as ex:
        t = pd.concat([d for d in ex.map(legs, syms, chunksize=8) if len(d)], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].reset_index(drop=True)
    t['entry_ns'] = t.entry_t.astype('int64'); t['exit_ns'] = t.exit_t.astype('int64')
    window = pd.Timestamp(json.loads((ROOT/'experiments/active/exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    per = {'all': t.entry_t.notna(), 'from_2025': t.entry_t >= B25, 'last5m': t.entry_t >= window}
    res = dict(n=len(t), time_range=[str(t.entry_t.min()), str(t.entry_t.max())], frequency={}, concurrency={}, mae={}, liquidation_share={}, slots={})
    bj = t.entry_t.dt.tz_convert('Asia/Shanghai')
    for name, m in per.items():
        days = pd.date_range(bj[m].dt.normalize().min(), bj[m].dt.normalize().max(), freq='D')
        cnt = bj[m].dt.normalize().value_counts().reindex(days, fill_value=0)
        res['frequency'][name] = dict(days=len(days), trades=int(m.sum()), mean_per_day=float(cnt.mean()), median_per_day=float(cnt.median()),
                                      p90_per_day=float(cnt.quantile(.9)), max_per_day=int(cnt.max()), max_day=str(cnt.idxmax().date()),
                                      zero_day_pct=float(100*(cnt == 0).mean()), top_days={str(k.date()): int(v) for k, v in cnt.nlargest(5).items()})
        x = t[m]
        s = np.sort(x.entry_ns.to_numpy()); e = np.sort(x.exit_ns.to_numpy())
        open_at = np.searchsorted(s, x.entry_ns.to_numpy(), side='right')-np.searchsorted(e, x.entry_ns.to_numpy(), side='right')
        res['concurrency'][name] = dict(max_open=int(open_at.max()), share_fills_with_more_than_3_open=float(100*(open_at > 3).mean()),
                                        hold_hours_median=float(((x.exit_ns-x.entry_ns)/3.6e12).median()))
        for g, y in (('winners', x[x.net > 0]), ('losers', x[x.net <= 0]), ('all', x)):
            res['mae'][f'{name}|{g}'] = dict(n=len(y), **{f'q{int(100*q)}': float(y.mae_pct.quantile(q)) for q in (.5, .75, .9, .95, .99)})
        res['liquidation_share'][name] = {str(L): dict(threshold_pct=100/L-1, all=float(100*(x.mae_pct >= 100/L-1).mean()),
                                                       winners=float(100*(x[x.net > 0].mae_pct >= 100/L-1).mean())) for L in (2, 3, 5, 10, 20, 50)}
    for name, m in (('from_2025', per['from_2025']), ('last5m', per['last5m']), ('all', per['all'])):
        x = t[m].reset_index(drop=True)
        for L in (1, 2, 3, 5, 10, 20):
            for extract, resize, tag in ((True, True, 'extract_resize'), (False, True, 'no_extract'), (True, False, 'extract_fixed')):
                reps = [simulate(x, L, 3, extract, resize, seed=20261026+k) for k in range(TIES)]
                res['slots'][f'{name}|L{L}|{tag}'] = {k: float(np.median([r[k] for r in reps])) for k in reps[0]}
    EXP.mkdir(exist_ok=True)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
                                  training_eligible=False, production_eligible=False))
    print(json.dumps(dict(frequency=res['frequency'], concurrency=res['concurrency']), ensure_ascii=False, indent=1))
    for k, v in res['mae'].items():
        print('mae', k, {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()})
    print('liq', json.dumps(res['liquidation_share']['from_2025']))
    for k, v in res['slots'].items():
        print(f'{k:32s}', ' '.join(f"{kk} {vv:.2f}" for kk, vv in v.items()))


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    Ls = [1, 2, 3, 5, 10, 20]
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.8))
    for tag, col, lab in (('extract_resize', '#2a78d6', '翻倍抽出 + 总账户翻倍再放大'), ('no_extract', '#898781', '不抽出（每份自己滚）'), ('extract_fixed', '#eb6834', '翻倍抽出，每份大小不变')):
        axes[0].plot(range(len(Ls)), [res['slots'][f'from_2025|L{L}|{tag}']['final_multiple'] for L in Ls], marker='o', lw=2, color=col, label=lab)
        axes[1].plot(range(len(Ls)), [res['slots'][f'from_2025|L{L}|{tag}']['max_drawdown_pct'] for L in Ls], marker='o', lw=2, color=col)
    for ax_, title in zip(axes, ('2025年起 · 1000U 期末倍数（分3份，每单用一份）', '最大回撤（%）')):
        ax_.set_xticks(range(len(Ls)), [f'{L}倍' for L in Ls]); ax_.set_xlabel('杠杆'); ax_.set_title(title, color=INK, loc='left'); ax_.grid(color=GRID, lw=.6)
        for sp in ('top', 'right'):
            ax_.spines[sp].set_visible(False)
    axes[0].set_yscale('log'); axes[0].axhline(1, color='#c3c2b7', lw=1); axes[0].legend(frameon=False)
    fig.suptitle('v3 规则（分数≥0.5，-30%兜底，0.618/对侧1.618止盈）· 浮亏超过 100/杠杆-1% 视为爆仓 · 同时最多3单', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_slots_leverage.png', dpi=130); plt.close(fig)


if __name__ == '__main__':
    run()
