"""Price/volume patterns before the touch for the Codex Monday1618 limit-reclaim ledgers.

Owner2026-10-24 "还有吗" (third round). Same snapshot, alts, near Monday edge
target, 5/15/60m confirmation, 2025-01-01 split as monday_limit_reclaim_patterns2.
Each symbol's 5m bars are loaded with Codex's own monday_limit_reclaim_inputs.load_job
from its identity.json, so ledger bar indexes match. Features use bars up to
the submission bar (the bar before the touch) unless stated:
  vol_ratio: quote volume of the last 12 bars / median 12-bar quote volume of
    the prior 7 days (volume spike into the level);
  approach_min: minutes from the first post-Monday bar crossing the near Monday
    edge to the touch bar (the crossing may be the touch bar itself);
  monday_close_side: where Monday closed, 0 = at the edge away from this touch,
    1 = at the edge this touch extends;
  liquidity: mean daily quote volume of the prior 7 days (USDT);
  toward24: percent move toward the level over the prior 24 hours;
  cluster24: other symbols filling the same side in the prior 24 hours.
Read-only; nothing is re-executed. Bars per symbol are checked against the
ledger (touch bar trades the entry).
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP, table, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE
from yoyo.evaluation import monday_limit_reclaim_patterns2 as p2

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-patterns3-20261024-v1'
LCOLS = ['population', 'reclaim_minutes', 'exit_mode', 'week', 'side', 'touch_i_hint', 'entry_i', 'monday_high', 'monday_low', 'entry']


def price_features(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=LCOLS)
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60) & (t.exit_mode == 'near_monday_edge')]
    if job['group'] != 'crypto_5m' or t.empty:
        return pd.DataFrame(), 0
    f, _ = load_job(job)
    h, l, c, v = (f[k].to_numpy() for k in ('high', 'low', 'close', 'volume'))
    qv = v*c; idx = f.index
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    rows, bad = [], 0
    for r in t.itertuples():
        k, s = int(r.touch_i_hint), int(r.entry_i)
        if not (0 < k < len(f)) or not ((l[k] <= r.entry) if r.side == 1 else (h[k] >= r.entry)):
            bad += 1; continue
        mon_end = (pd.Timestamp(r.week).tz_localize('America/New_York')+pd.Timedelta(days=1)).tz_convert('UTC')
        k0 = idx.searchsorted(mon_end)
        seg = (l[k0:k+1] <= r.monday_low) if r.side == 1 else (h[k0:k+1] >= r.monday_high)
        cross = k0+int(np.argmax(seg)) if seg.any() else k
        mc = c[k0-1] if k0 > 0 else np.nan
        pos = (mc-r.monday_low)/(r.monday_high-r.monday_low)
        rows.append(dict(symbol=symbol, week=r.week, side=r.side,
            vol_ratio=hour[s]/base[s] if base[s] > 0 else np.nan,
            approach_min=(idx[k]-idx[cross]).total_seconds()/60,
            monday_close_side=(1-pos) if r.side == 1 else pos,
            liquidity=day[s], toward24=-r.side*100*(c[s]/c[s-288]-1) if s >= 288 else np.nan))
    return pd.DataFrame(rows), bad


BINS = {'vol_ratio': ([0, 1, 2, 4, 8, np.inf], ['<1倍', '1-2倍', '2-4倍', '4-8倍', '≥8倍']),
        'approach_min': ([-1, 30, 120, 480, 1440, np.inf], ['<30分钟', '30分-2时', '2-8小时', '8-24小时', '≥1天']),
        'monday_close_side': ([-.01, .2, .4, .6, .8, 1.01], ['收在远侧', '偏远', '中间', '偏近', '收在近侧']),
        'liquidity': ([0, 2e6, 1e7, 5e7, 2e8, np.inf], ['<200万U', '200万-1千万', '1千万-5千万', '5千万-2亿', '≥2亿']),
        'toward24': ([-np.inf, 5, 10, 20, 35, np.inf], ['<5%', '5-10%', '10-20%', '20-35%', '≥35%']),
        'cluster24': ([-1, 5, 20, 60, 150, np.inf], ['0-5', '6-20', '21-60', '61-150', '>150'])}
NAMES = dict(vol_ratio='触及前1小时成交额 / 平时', approach_min='跌破周一边界到触及1.618用时', monday_close_side='周一收盘位置',
             liquidity='近7天日均成交额', toward24='触及前24小时朝1.618方向的涨跌幅', cluster24='前24小时同向触及的其他币数')


def cluster24(s):
    out = pd.Series(0, index=s.index)
    for _, g in s.groupby('side'):
        ts = g.t.astype('int64').to_numpy(); order = np.argsort(ts); tsorted = ts[order]; syms = g.symbol.to_numpy()[order]
        lo = np.searchsorted(tsorted, tsorted-86_400_000_000_000, 'left'); hi = np.searchsorted(tsorted, tsorted, 'left')
        out.loc[g.index[order]] = [len(set(syms[a:b])-{syms[k]}) for k, (a, b) in enumerate(zip(lo, hi))]
    return out


def charts(res):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    for metric, fname, ylab in (('win_pct', '1_win_price_features.png', '胜率 %'), ('mean_bp', '2_mean_price_features.png', '每笔扣费后平均 bp')):
        fig, axes = plt.subplots(3, 6, figsize=(27, 11))
        for r, m in enumerate((5, 15, 60)):
            for c, (f, title) in enumerate(NAMES.items()):
                ax = axes[r, c]; t = res[m][f]; bins = BINS[f][1]
                x = np.arange(len(bins)); w = .38
                for off, half, col, lab in ((-w/2, 'discovery', BLUE, '2025年前'), (w/2, 'validation', ORANGE, '2025年起')):
                    ax.bar(x+off, [t[half].get(b, {}).get(metric, np.nan) for b in bins], w*.92, color=col, label=lab)
                ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, bins, fontsize=7.5); ax.grid(axis='y', color=GRID, lw=.6)
                ax.set_title(title if r == 0 else '', color=INK, loc='left', fontsize=10.5)
                if c == 0:
                    ax.set_ylabel(f'{m}分钟确认\n{ylab}')
                for sp in ('top', 'right'):
                    ax.spines[sp].set_visible(False)
        h, l = axes[0, 0].get_legend_handles_labels(); fig.legend(h, l, frameon=False, ncols=2, loc='upper right')
        fig.suptitle(f'{ylab} · 触及前的价格与成交量 · 山寨币 1.618 挂单 · 止盈近侧周一边界', color=INK, x=.01, ha='left')
        fig.tight_layout(); fig.savefig(EXP/fname, dpi=105); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_patterns3.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    with ProcessPoolExecutor(3) as ex:
        got = list(ex.map(price_features, syms, chunksize=4))
    pf = pd.concat([a for a, _ in got if len(a)], ignore_index=True); bad = int(sum(b for _, b in got))
    with ProcessPoolExecutor(4) as ex:
        parts = list(ex.map(p2.load, syms, chunksize=8))
    d = pd.concat([a for a, _ in parts], ignore_index=True); o = pd.concat([b for _, b in parts], ignore_index=True)
    btc = p2.weekly_trend(pd.read_csv(SRC/'BTCUSDT/opportunities.csv.gz').query("status in ['admitted','no_observed_touch']"))
    trend = p2.weekly_trend(o)
    res, cover = {}, {}
    EXP.mkdir(exist_ok=True)
    for m in (5, 15, 60):
        s = p2.base(d, o, trend, btc, m)
        s['side_n'] = np.where(s.side == '做多', 1, -1) if s.side.dtype != 'int64' else s.side
        s = s.merge(pf.rename(columns={'side': 'side_n'}), on=['symbol', 'week', 'side_n'], how='left')
        s['cluster24'] = cluster24(s)
        cover[m] = dict(events=len(s), with_price_features=int(s.vol_ratio.notna().sum()))
        res[m] = {f: table(s, f, BINS[f]) for f in NAMES}
        if m == 60:
            s[['symbol', 'week', 'side_n', 't', 'net_bp', 'complete', 'cm', 'vol_q', 'opp_first', *NAMES]].to_csv(EXP/'events_60m.csv.gz', index=False)
    charts(res)
    dump(EXP/'summary.json', dict(config=cfg, ledger_bar_mismatches=bad, coverage=cover,
        features={f'{m}m': r for m, r in res.items()},
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(bad=bad, cover=cover), ensure_ascii=False))


if __name__ == '__main__':
    run()
