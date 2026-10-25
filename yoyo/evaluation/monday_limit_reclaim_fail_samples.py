"""Sample charts of failed-reclaim exits in the Codex Monday1618 limit-reclaim run (owner2026-10-25).

Owner: "没收回止损 ... 具体是个什么情况 抽样给几张图看看". From the failed-reclaim
alt trades (60m confirmation, near Monday edge target, Codex O-H-L-C ledger)
three of each are drawn with a fixed seed: typical (loss -0.6R..-0.35R), large
(loss <= -2R) and floating profit first (best move >= 0.4R before the exit).
R = distance from the1618 to the2.0 extension; target = 1.618R. Bars are
Codex's own 5m inputs (load_job); the shaded span is the 60m confirmation
candle after the touch candle. Read-only.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, INK, INK2, MUTED, GRID, SURF

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-fail-samples-20261025-v1'
MFE = ROOT/'experiments/active/exp-monday-limit-reclaim-mfe-20261025-v1/trades_mfe.csv.gz'
GROUPS = (('典型亏损', lambda d: d.net_R.between(-.6, -.35)), ('大亏', lambda d: d.net_R <= -2),
          ('先有浮盈再失败', lambda d: d.mfe_R >= .4))


def candles(ax, w):
    x = np.arange(len(w)); up = (w.close >= w.open).to_numpy()
    for col, m in (('#2e9e6b', up), ('#d9534f', ~up)):
        ax.vlines(x[m], w.low.to_numpy()[m], w.high.to_numpy()[m], color=col, lw=.7)
        ax.bar(x[m], np.maximum(np.abs(w.close-w.open).to_numpy()[m], w.close.to_numpy()[m]*1e-5),
               bottom=np.minimum(w.open, w.close).to_numpy()[m], color=col, width=.7)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_fail_samples.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.facecolor': SURF,
                         'figure.facecolor': SURF})
    cfg = json.loads((EXP/'config.json').read_text())
    d = pd.read_csv(MFE)
    d = d[d.reason == 11].copy()
    d['R_pct'] = d.tp_pct/1.618; d['net_R'] = d.net_bp/100/d.R_pct; d['mfe_R'] = d.mfe_strict_pct.fillna(0)/d.R_pct
    rng = np.random.default_rng(cfg['seed'])
    picks = []
    for name, f in GROUPS:
        pool = d[f(d)]
        picks += [(name, r) for r in pool.iloc[rng.choice(len(pool), 3, replace=False)].itertuples()]
    fig, axes = plt.subplots(3, 3, figsize=(21, 15))
    rows = []
    for ax, (name, r) in zip(axes.flat, picks):
        led = pd.read_csv(SRC/r.symbol/'trades.csv.gz', usecols=['trade_id', 'exit_price', 'monday_high', 'monday_low'])
        x = led[led.trade_id == r.trade_id].iloc[0]
        job = json.loads((SRC/r.symbol/'identity.json').read_text())['job']
        f, _ = load_job(job)
        a, b = int(r.entry_i), int(r.exit_i)
        w = f.iloc[max(0, a-36):b+13]
        candles(ax, w)
        R = abs(r.target_price-r.entry)/1.618
        for y, col, ls, lab in ((r.entry, '#e69500', '--', '1.618 挂单'), (r.target_price, '#1D9E75', '-', '止盈：周一边界'),
                                (r.entry-r.side*R, '#d9534f', ':', '2.0 扩展（-1R）')):
            ax.axhline(y, color=col, ls=ls, lw=1.1)
            ax.text(len(w)-.5, y, f' {lab}', color=col, fontsize=8, va='center', ha='left', clip_on=False)
        touch = f.index[a]; c0 = touch.floor('60min')+pd.Timedelta(minutes=60); c1 = c0+pd.Timedelta(minutes=60)
        xi = lambda ts: int(np.clip(w.index.searchsorted(ts), 0, len(w)-1))
        ax.axvspan(xi(c0)-.5, xi(c1)-.5, color='#8888ff', alpha=.10)
        ax.plot(a-max(0, a-36), r.entry, marker='^' if r.side == 1 else 'v', color='black', ms=10)
        ax.plot(b-max(0, a-36), x.exit_price, marker='x', color='#d9534f', ms=11, mew=2)
        ny = touch.tz_convert('Asia/Shanghai')
        ax.set_title(f"{name} · {r.symbol.removesuffix('USDT')} {'做多' if r.side == 1 else '做空'} · {ny:%Y-%m-%d %H:%M}（北京）\n"
                     f"亏 {r.net_R:.2f}R（{r.net_bp/100:.2f}%）· 出场前最大浮盈 {r.mfe_R:.2f}R", fontsize=10, color=INK, loc='left')
        ticks = np.linspace(0, len(w)-1, 5).astype(int)
        ax.set_xticks(ticks, [w.index[i].tz_convert('Asia/Shanghai').strftime('%H:%M') for i in ticks], fontsize=8)
        ax.tick_params(axis='y', labelsize=8); ax.grid(alpha=.15)
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
        rows.append(dict(group=name, trade_id=r.trade_id, net_R=float(r.net_R), mfe_R=float(r.mfe_R), net_bp=float(r.net_bp)))
    fig.suptitle('没收回止损抽样（5分钟K线；黑色三角=成交，红叉=止损出场，蓝底=确认用的那根60分钟K线）', color=INK, x=.01, ha='left', fontsize=14)
    fig.tight_layout(rect=(0, 0, .96, .97)); EXP.mkdir(exist_ok=True); fig.savefig(EXP/'fail_samples.png', dpi=90); plt.close(fig)
    q = d.net_R.quantile([.1, .25, .5, .75, .9]).to_dict()
    dump(EXP/'summary.json', dict(config=cfg, failed_n=len(d), net_R_quantiles=q, net_R_mean=float(d.net_R.mean()),
         share_worse_than_minus1R=float((d.net_R < -1).mean()), share_worse_than_minus2R=float((d.net_R < -2).mean()), samples=rows,
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()))
    print(json.dumps(dict(n=len(d), q=q, mean=float(d.net_R.mean()), lt1=float((d.net_R < -1).mean()), lt2=float((d.net_R < -2).mean())), ensure_ascii=False))


if __name__ == '__main__':
    run()
