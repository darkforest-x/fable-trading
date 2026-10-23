"""Render the BTC second-touch1618 events as candlestick charts for owner review.

Owner2026-10-23 asked how "BTC 23, 60.9%" was produced and to see the K-lines.
Reads the frozen ledgers (exp-monday-1618-second-touch, exp-monday-1618-validity)
and the same OKX15m bars; labels are not recomputed except to locate the bar
where the already-recorded outcome (Monday edge or2.0 stop) was reached.
Display uses1h candles; every event marker sits at its15m bar time.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation import monday_1618_validity as v

A = ROOT/'experiments/active'
OUT = A/'exp-monday-1618-second-touch-20261023-v1/charts_btc'
plt.rcParams['font.sans-serif'] = ['PingFang SC', 'Heiti SC', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False


def candles(ax, h1):
    x = np.arange(len(h1))
    up = h1.close >= h1.open
    for col, m in (('#2e9e6b', up), ('#d9534f', ~up)):
        ax.vlines(x[m], h1.low[m], h1.high[m], color=col, lw=.6)
        ax.bar(x[m], (h1.close-h1.open)[m].abs().clip(lower=h1.close[m]*1e-5), bottom=np.minimum(h1.open, h1.close)[m],
               color=col, width=.7)
    return x


def outcome_bar(f, t2, side, stop, edge, horizon=14*96):
    h, l = f.high.to_numpy(), f.low.to_numpy()
    end = min(len(f), t2+horizon)
    sh = (l[t2:end] <= stop) if side == 1 else (h[t2:end] >= stop)
    eh = (h[t2+1:end] >= edge) if side == 1 else (l[t2+1:end] <= edge)
    s = int(np.argmax(sh)) if sh.any() else 10**9
    e = int(np.argmax(eh))+1 if eh.any() else 10**9
    return (t2+e, 'edge') if e < s else (t2+s, 'stop')


def run():
    st = pd.read_csv(A/'exp-monday-1618-second-touch-20261023-v1/second_touches.csv.gz')
    led = pd.read_csv(A/'exp-monday-1618-validity-20261023-v1/touches.csv.gz')
    vcfg = json.loads((A/'exp-monday-1618-validity-20261023-v1/config.json').read_text())
    f = v.load(v.universe(vcfg)['BTC']); f = f[f.index >= pd.Timestamp(vcfg['start'])]
    rows = st[st.symbol == 'BTC'].merge(led[['symbol', 'week', 'side', 'level', 'stop', 'edge', 'R']], on=['symbol', 'week', 'side'])
    OUT.mkdir(parents=True, exist_ok=True)
    panels = []
    for k, r in enumerate(rows.itertuples(), 1):
        t, t2, side = int(r.touch_i), int(r.second_i), int(r.side)
        ex, kind = outcome_bar(f, t2, side, r.stop, r.edge)
        wk = pd.Timestamp(r.week).tz_localize(v.TZ).tz_convert('UTC')
        start = wk-pd.Timedelta(hours=12); stop_t = f.index[min(ex, len(f)-1)]+pd.Timedelta(hours=18)
        seg = f[(f.index >= start) & (f.index <= stop_t)]
        h1 = seg.resample('1h').agg(dict(open='first', high='max', low='min', close='last')).dropna()
        L = r.edge if side == 1 else r.edge-r.R; H = L+r.R
        retrace = r.level+side*.236*r.R
        panels.append(dict(k=k, r=r, h1=h1, t=f.index[t], t2=f.index[t2], ex=f.index[min(ex, len(f)-1)], kind=kind,
                           H=H, L=L, retrace=retrace, wk=wk))
    for page in range(0, len(panels), 12):
        chunk = panels[page:page+12]
        fig, axes = plt.subplots(4, 3, figsize=(21, 22))
        for ax, p in zip(axes.flat, chunk):
            r, h1 = p['r'], p['h1']
            x = candles(ax, h1)
            def xi(ts):
                return int(np.clip(h1.index.searchsorted(ts.floor('1h'), side='left'), 0, len(h1)-1))
            mon = (h1.index >= p['wk']) & (h1.index < p['wk']+pd.Timedelta(days=1))
            if mon.any():
                ax.axvspan(x[mon][0]-.5, x[mon][-1]+.5, color='#8888ff', alpha=.08)
            for y, c, ls, lab in ((p['H'], '#555', '-', '周一高'), (p['L'], '#555', '-', '周一低'),
                                  (r.level, '#e69500', '-', '1.618'), (r.stop, '#d9534f', '--', '2.0 止损'),
                                  (p['retrace'], '#9b59b6', ':', '回撤 0.236R')):
                ax.axhline(y, color=c, ls=ls, lw=1.1)
                ax.text(len(h1)-.5, y, f' {lab} {y:,.1f}', color=c, fontsize=8, va='center', ha='left', clip_on=False)
            ax.plot(xi(p['t']), r.level, 'o', color='#1f77b4', ms=8, label='第一次触及')
            ax.plot(xi(p['t2']), r.level, marker='v' if r.side == -1 else '^', color='black', ms=11, label='二次触及入场')
            col = '#2e9e6b' if p['kind'] == 'edge' else '#d9534f'
            y = r.edge if p['kind'] == 'edge' else r.stop
            ax.plot(xi(p['ex']), y, marker='*', color=col, ms=15, label='止盈（回到周一边界）' if p['kind'] == 'edge' else '止损（到 2.0）')
            res = '有效 +1.62R' if r.valid else '失败 -1R'
            ax.set_title(f"#{p['k']}  {r.week} 当周  {'做空' if r.side == -1 else '做多'}  {res}\n"
                         f"首触 {p['t'].tz_convert('Asia/Shanghai'):%m-%d %H:%M}  二触 {p['t2'].tz_convert('Asia/Shanghai'):%m-%d %H:%M}"
                         f"（间隔 {r.hours_between:.1f}h，北京时间）", fontsize=10, color='#2e7d32' if r.valid else '#c62828')
            ticks = np.linspace(0, len(h1)-1, 6).astype(int)
            ax.set_xticks(ticks); ax.set_xticklabels([h1.index[i].tz_convert('Asia/Shanghai').strftime('%m-%d\n%H:%M') for i in ticks], fontsize=7)
            ax.tick_params(axis='y', labelsize=7); ax.grid(alpha=.15)
            ax.legend(fontsize=7, loc='upper left' if r.side == 1 else 'lower left')
        for ax in axes.flat[len(chunk):]:
            ax.axis('off')
        fig.suptitle('BTC 周一 1.618 回撤 0.236R 后二次触及（OKX 15m 判定，图为 1h K 线，蓝底=纽约周一）', fontsize=15)
        fig.tight_layout(rect=(0, 0, .97, .98))
        out = OUT/f'btc_second_touch_{page//12+1}.png'
        fig.savefig(out, dpi=80); plt.close(fig)
        print(out)


if __name__ == '__main__':
    run()
