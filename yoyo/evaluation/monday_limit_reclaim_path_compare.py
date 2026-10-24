"""O-H-L-C versus O-L-H-C outcomes of the Codex limit-reclaim signals and the 1000U replay (owner2026-10-25).

Joins the Codex ledger (O-H-L-C, exp-monday-limit-reclaim-20261024-v1) with the
same signals replayed natively under O-L-H-C with monotone segments
(exp-monday-limit-reclaim-olhc-20261025-v2; v1 was invalid). A 20-symbol O-H-L-C
rerun with monotone segments (exp-monday-limit-reclaim-ohlc-check-20261025-v1)
checks that the Codex ledger is unaffected by the segment-order defect;
trade by trade: alts, 60m confirmation, near Monday edge target, snapshot symbols.
Reports how many trades change, whether the short same-bar targets survive, and
per-side means for O-H-L-C, O-L-H-C and the per-trade minimum (pessimistic: each
trade takes its worse path). Then repeats the 1000 USDT replay of
monday_limit_reclaim_filtered_replay (same filters, sizing grid, slippage) for the
three versions. Arithmetic on framework fills; nothing new is executed here.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, SNAP, SPLIT, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE
from yoyo.evaluation.monday_limit_reclaim_filtered_replay import COLS, replay, yearly, NAMES, FEAT

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-path-compare-20261025-v1'
OLHC = ROOT/'experiments/active/exp-monday-limit-reclaim-olhc-20261025-v2/symbols'
CHECK = ROOT/'experiments/active/exp-monday-limit-reclaim-ohlc-check-20261025-v1'
VARIANTS = {'ohlc': '按原记录（开→高→低→收）', 'olhc': '开→低→高→收', 'worst': '悲观（每笔取两种顺序中较差的）'}


def load_olhc(symbol):
    return load_olhc_from(OLHC, symbol)


def load_olhc_from(root, symbol):
    path = root/symbol/'trades.csv.gz'
    if not path.exists():
        return pd.DataFrame()
    t = pd.read_csv(path, usecols=['trade_id', 'reason', 'entry_time', 'exit_time', 'net_notional_bp'])
    return t.rename(columns={'reason': 'reason_olhc', 'entry_time': 'entry_time_olhc', 'exit_time': 'exit_time_olhc',
                             'net_notional_bp': 'net_olhc'})


def load_ohlc(symbol):
    t = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', *COLS])
    return t[(t.asset_group == 'ALTCOINS') & (t.population == 'signal') & (t.reclaim_minutes == 60)
             & (t.exit_mode == 'near_monday_edge') & t.closed]


def charts(res, paths, table):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': INK2, 'xtick.color': MUTED, 'ytick.color': MUTED,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    cols = {'all': MUTED, 'vol_top': BLUE, 'vol_spike': ORANGE, 'fast_long': '#1baf7a', 'any_strong': '#6250d6'}
    fig, axes = plt.subplots(1, 3, figsize=(21, 5.4), sharey=True)
    for ax, v in zip(axes, VARIANTS):
        for f, col in cols.items():
            p = np.array(paths[f'{v}|{f}'][::max(1, len(paths[f'{v}|{f}'])//3000)])
            ax.plot(pd.to_datetime(p[:, 0]), p[:, 1], color=col, lw=1.8, label=NAMES[f])
        ax.set_yscale('log'); ax.axhline(1000, color='#c3c2b7', lw=1)
        for mult in (10, 100, 1000):
            ax.axhline(1000*mult, color=GRID, lw=.8, ls='--')
            ax.text(ax.get_xlim()[1], 1000*mult, f'{mult}倍 ', color=MUTED, va='bottom', ha='right', fontsize=8)
        ax.set_title(VARIANTS[v], color=INK, loc='left')
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
    axes[0].set_ylabel('已实现权益（USDT，对数）'); axes[0].legend(frameon=False, loc='upper left', fontsize=9)
    fig.suptitle('1000U 复利 · 每笔 10% 权益 · 总仓位 ≤ 1 倍 · 平仓滑点 10bp · 两种K线内顺序', color=INK, x=.01, ha='left')
    fig.tight_layout(); fig.savefig(EXP/'1_equity_by_path.png', dpi=120); plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 4.2))
    labels = [('做多', 1), ('做空', -1)]; x = np.arange(2); w = .26
    for off, (v, col) in zip((-w, 0, w), (('ohlc', MUTED), ('olhc', BLUE), ('worst', ORANGE))):
        vals = [res['by_side'][str(s)][f'mean_{v}'] for _, s in labels]
        ax.bar(x+off, vals, w*.92, color=col, label=VARIANTS[v])
        for xx, vv in zip(x+off, vals):
            ax.text(xx, vv, f'{vv:+.1f}', ha='center', va='bottom' if vv >= 0 else 'top', color=INK, fontsize=9)
    ax.axhline(0, color='#c3c2b7', lw=1); ax.set_xticks(x, [l for l, _ in labels]); ax.set_ylabel('每笔扣费后平均 bp')
    ax.legend(frameon=False, loc='upper right'); ax.grid(axis='y', color=GRID, lw=.6)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title('同一批信号，只换K线内顺序 · 山寨币 60分钟确认 · 止盈近侧周一边界', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'2_mean_by_path.png', dpi=130); plt.close(fig)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_path_compare.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    syms = [r['symbol'] for r in json.loads(SNAP.read_text())['studies']['limit-reclaim']]
    syms += json.loads((OLHC.parent/'config.json').read_text()).get('extension_symbols', [])
    with ProcessPoolExecutor(4) as ex:
        a = pd.concat(list(ex.map(load_ohlc, syms, chunksize=8)), ignore_index=True)
        b = pd.concat(list(ex.map(load_olhc, syms, chunksize=8)), ignore_index=True)
    t = a.merge(b, on='trade_id', how='inner')
    missing = len(a)-len(t)
    chk_syms = json.loads((CHECK/'config.json').read_text())['symbols']
    chk = pd.concat([load_olhc_from(CHECK/'symbols', s) for s in chk_syms], ignore_index=True).merge(a, on='trade_id')
    parity = dict(trades=len(chk), identical=int(np.isclose(chk.net_olhc, chk.net_bp, atol=1e-6).sum()))
    t['changed'] = ~np.isclose(t.net_bp, t.net_olhc, atol=1e-6)
    t['same_bar_ohlc'] = (t.reason == 4) & (t.exit_time == t.entry_time)
    t['same_bar_olhc'] = (t.reason_olhc == 4) & (t.exit_time_olhc == t.entry_time_olhc)
    t['net_worst'] = np.minimum(t.net_bp, t.net_olhc)
    short_sb = t[(t.side == -1) & t.same_bar_ohlc]
    res = dict(trades=len(t), missing=int(missing), changed=int(t.changed.sum()),
               short_same_bar_ohlc=len(short_sb), short_same_bar_still_target_olhc=int((short_sb.reason_olhc == 4).sum()),
               short_same_bar_still_same_bar_olhc=int(short_sb.same_bar_olhc.sum()),
               short_same_bar_mean_ohlc=float(short_sb.net_bp.mean()), short_same_bar_mean_olhc=float(short_sb.net_olhc.mean()),
               by_side={str(s): dict(n=len(g), changed=int(g.changed.sum()), mean_ohlc=float(g.net_bp.mean()),
                                     mean_olhc=float(g.net_olhc.mean()), mean_worst=float(g.net_worst.mean()),
                                     win_ohlc=float(100*(g.net_bp > 0).mean()), win_olhc=float(100*(g.net_olhc > 0).mean()),
                                     win_worst=float(100*(g.net_worst > 0).mean())) for s, g in t.groupby('side')})
    f = pd.read_csv(FEAT).rename(columns={'side_n': 'side'})[['symbol', 'week', 'side', 'approach_min', 'vol_ratio', 'opp_first']]
    t = t.merge(f, on=['symbol', 'week', 'side'], how='left')
    disc = pd.to_datetime(t.entry_time, utc=True) < SPLIT
    thr = float(np.quantile(t.bucket[disc], .8))
    masks = {'all': t.net_bp.notna(), 'vol_top': t.bucket >= thr, 'vol_spike': t.vol_ratio >= 8,
             'fast_long': (t.approach_min < 30) & (t.side == 1), 'opp_first': t.opp_first == '是'}
    masks['any_strong'] = masks['vol_top'] | masks['vol_spike'] | masks['fast_long'] | masks['opp_first']
    masks['vol_top_opp'] = masks['vol_top'] & masks['opp_first']
    versions = {'ohlc': t.assign(entry=pd.to_datetime(t.entry_time, utc=True).astype('int64'),
                                 exit=pd.to_datetime(t.exit_time, utc=True).astype('int64')),
                'olhc': t.assign(net_bp=t.net_olhc, reason=t.reason_olhc,
                                 entry=pd.to_datetime(t.entry_time_olhc, utc=True).astype('int64'),
                                 exit=pd.to_datetime(t.exit_time_olhc, utc=True).astype('int64'))}
    use_olhc = t.net_olhc < t.net_bp
    w = versions['ohlc'].copy()
    w.loc[use_olhc, ['net_bp', 'reason', 'entry', 'exit']] = versions['olhc'].loc[use_olhc, ['net_bp', 'reason', 'entry', 'exit']].to_numpy()
    versions['worst'] = w
    table, paths, years, sets = [], {}, {}, {}
    for v, d in versions.items():
        for name, m in masks.items():
            x = d[m.fillna(False).to_numpy()].reset_index(drop=True)
            sets[f'{v}|{name}'] = dict(n=len(x), win_pct=float(100*(x.net_bp > 0).mean()), mean_bp=float(x.net_bp.mean()))
            for k in (.05, .10, .20, .30):
                for gross in (1, 3):
                    r, path = replay(x, k, gross, 10)
                    table.append(dict(variant=v, filter=name, k=k, gross=gross, slip_bp=10, **r))
                    if k == .10 and gross == 1:
                        paths[f'{v}|{name}'] = path; years[f'{v}|{name}'] = yearly(path)
    EXP.mkdir(exist_ok=True)
    charts(res, paths, table)
    dump(EXP/'summary.json', dict(config=cfg, vol_bucket_threshold=thr, ohlc_parity=parity, comparison=res, sets=sets, replays=table,
        yearly_k10_gross1_slip10=years, source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(dict(parity=parity, **res), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
