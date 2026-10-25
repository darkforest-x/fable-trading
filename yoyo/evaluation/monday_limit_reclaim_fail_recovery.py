"""After a failed reclaim: how often does price come back to the1618 entry? (owner2026-10-25)

Owner: "没收回止损 28,087 这些单 再后面几个小时又回到1.618开仓位的有多少". For every
failed-reclaim alt trade (Codex O-H-L-C ledger, 60m confirmation, near Monday
edge target) Codex's 5m bars after the exit bar are scanned for 1/2/4/8/24/72
hours: back to the entry, reaching the near Monday edge target, and as a
symmetric control moving the same distance further against the trade (exit
price minus the exit-to-entry gap). Read-only; no re-entry is executed.
"""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC, INK, INK2, MUTED, GRID, SURF, BLUE, ORANGE

EXP = ROOT/'experiments/active/exp-monday-limit-reclaim-fail-recovery-20261025-v1'
MFE = ROOT/'experiments/active/exp-monday-limit-reclaim-mfe-20261025-v1/trades_mfe.csv.gz'
HOURS = (1, 2, 4, 8, 24, 72)


def scan(args):
    symbol, rows = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    h, l = f.high.to_numpy(), f.low.to_numpy()
    led = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['trade_id', 'exit_price']).set_index('trade_id').exit_price
    out = []
    for r in rows.itertuples():
        b = int(r.exit_i); ex = float(led.get(r.trade_id, np.nan))
        gap = r.side*(r.entry-ex)  # distance back to entry, > 0 for a loss
        further = ex-r.side*gap
        rec = dict(trade_id=r.trade_id, symbol=symbol, side=r.side, gap_pct=100*gap/r.entry)
        for H in HOURS:
            seg = slice(b+1, min(len(f), b+1+H*12))
            hi, lo = h[seg], l[seg]
            if r.side == 1:
                back, tgt, adv = (hi >= r.entry).any(), (hi >= r.target_price).any(), (lo <= further).any()
            else:
                back, tgt, adv = (lo <= r.entry).any(), (lo <= r.target_price).any(), (hi >= further).any()
            rec.update({f'back_{H}h': bool(back), f'target_{H}h': bool(tgt), f'further_{H}h': bool(adv)})
        out.append(rec)
    return pd.DataFrame(out)


def run():
    p = 'yoyo/evaluation/monday_limit_reclaim_fail_recovery.py'
    if subprocess.run(['git','diff','--quiet','HEAD','--',p], cwd=ROOT).returncode or \
       subprocess.run(['git','ls-files','--error-unmatch',p], cwd=ROOT, capture_output=True).returncode:
        raise RuntimeError('Commit builder first')
    cfg = json.loads((EXP/'config.json').read_text())
    d = pd.read_csv(MFE)
    d = d[d.reason == 11]
    with ProcessPoolExecutor(4) as ex:
        r = pd.concat(list(ex.map(scan, list(d.groupby('symbol')), chunksize=4)), ignore_index=True)
    r = r.merge(d[['trade_id', 'net_bp', 'tp_pct', 'entry_time']], on='trade_id')
    r['net_R'] = r.net_bp/100/(r.tp_pct/1.618)
    EXP.mkdir(exist_ok=True)
    r.to_csv(EXP/'recovery.csv.gz', index=False)
    tab = {f'{H}h': dict(back_pct=float(100*r[f'back_{H}h'].mean()), further_pct=float(100*r[f'further_{H}h'].mean()),
                         target_pct=float(100*r[f'target_{H}h'].mean())) for H in HOURS}
    groups = {name: r[m] for name, m in (('亏损不到0.3R', r.net_R > -.3), ('0.3-1R', r.net_R.between(-1, -.3)), ('超过1R', r.net_R < -1))}
    by_loss = {k: {f'{H}h': float(100*g[f'back_{H}h'].mean()) for H in HOURS} | dict(n=len(g)) for k, g in groups.items()}
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.sans-serif': ['PingFang SC', 'Heiti SC', 'Arial Unicode MS'], 'axes.unicode_minus': False,
                         'axes.edgecolor': '#c3c2b7', 'xtick.color': MUTED, 'ytick.color': MUTED, 'axes.labelcolor': INK2,
                         'axes.facecolor': SURF, 'figure.facecolor': SURF})
    fig, ax = plt.subplots(figsize=(11, 4.8))
    x = np.arange(len(HOURS)); w = .27
    for off, key, col, lab in ((-w, 'back_pct', BLUE, '回到1.618开仓价'), (0, 'further_pct', ORANGE, '对照：同样距离继续往亏的方向走'),
                               (w, 'target_pct', '#1baf7a', '一路回到周一边界（原止盈）')):
        v = [tab[f'{H}h'][key] for H in HOURS]
        ax.bar(x+off, v, w*.92, color=col, label=lab)
        for xx, vv in zip(x+off, v):
            ax.text(xx, vv, f'{vv:.0f}%', ha='center', va='bottom', color=INK, fontsize=8)
    ax.set_xticks(x, [f'出场后{H}小时内' for H in HOURS]); ax.set_ylabel('占没收回止损单的比例 %')
    ax.legend(frameon=False, loc='upper left'); ax.grid(axis='y', color=GRID, lw=.6)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.set_title(f'没收回止损后价格去哪了（{len(r)} 笔，山寨币 60分钟确认）', color=INK, loc='left')
    fig.tight_layout(); fig.savefig(EXP/'1_after_failed_exit.png', dpi=130); plt.close(fig)
    dump(EXP/'summary.json', dict(config=cfg, n=len(r), by_hours=tab, back_by_loss=by_loss,
         source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(), training_eligible=False, production_eligible=False))
    print(json.dumps(dict(n=len(r), tab=tab, by_loss=by_loss), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    run()
