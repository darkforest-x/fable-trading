"""Figures of exp-live-ema-cloud-filter-20261103-v1 for the owner TG group.

Owner 2026-11-03: "以 wick 系列、12h 为例，顺云胜率 70% 给我几张图看看 发到tg群".
No new fills: per-trade bars read the run's summary.json. The equity curves and the random-control histogram replay
live-wick6x3 from 2020 with the run's own candidates(), its saved cloud columns (candidates.csv.gz, same row order)
and its control seed; replayed ends and control quantiles must equal summary.json or nothing is drawn.
Delivery uses Telegram sendMediaGroup (https://core.telegram.org/bots/api#sendmediagroup) to the verified owner
group; a pending receipt is written before the upload and an uncertain response blocks automatic resend.
"""
from __future__ import annotations
import argparse
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation import live_ema_cloud_filter as f
from yoyo.evaluation.strategy_booklet_paths import Trace

EXP = ROOT/'experiments/active/exp-live-ema-cloud-filter-20261103-v1'
OUT = EXP/'figures'
KEY, START = 'live-wick6x3|2020-01-01', '2020-01-01'
GROUP = 'Yolo均线密集交易系统'
# Light chart surface and categorical slots 1-2 of the dataviz reference palette; the unfiltered account is a gray reference.
SURF, INK, INK2, GRID = '#fcfcfb', '#0b0b0b', '#52514e', '#e4e3df'
WITH, AGAINST, BASE = '#2a78d6', '#eb6834', '#6f6e69'
plt.rcParams.update({'font.family': 'Hiragino Sans GB', 'axes.unicode_minus': False, 'figure.facecolor': SURF,
                     'axes.facecolor': SURF, 'axes.edgecolor': GRID, 'axes.labelcolor': INK2, 'xtick.color': INK2,
                     'ytick.color': INK2, 'text.color': INK, 'font.size': 11})
NOTE = '币安K线回测 2020-01-01 至 2026-10-27，样本内；每笔已扣手续费与滑点；EMA20/50 云带取进场时已收盘的那根'


def frame(title, sub, rows=1, h=6.2):
    """Header (title, subtitle, data note) in fixed inches from the top so every figure height lines up."""
    fig, ax = plt.subplots(rows, 1, figsize=(8, h), dpi=160)
    for y, t, kw in ((0.32, title, dict(fontsize=15, fontweight='bold')), (0.62, sub, dict(fontsize=10.5, color=INK2)),
                     (0.86, NOTE, dict(fontsize=8.5, color=INK2))):
        fig.text(0.06, 1-y/h, t, va='center', **kw)
    for a in np.atleast_1d(ax):
        a.grid(axis='y', color=GRID, lw=0.8); a.set_axisbelow(True)
        for s in ('top', 'right', 'left'):
            a.spines[s].set_visible(False)
        a.tick_params(length=0)
    return fig, ax


def bars(ax, groups, w, a, wl, al, base=None):
    x = np.arange(len(groups))
    if base is not None:
        ax.axhline(base, color=BASE, lw=1.4, ls='--', zorder=1)
    for off, vals, labels, colour in ((-0.19, w, wl, WITH), (0.19, a, al, AGAINST)):
        b = ax.bar(x+off, vals, 0.36, color=colour, edgecolor=SURF, linewidth=2, zorder=2)
        for r, v, t in zip(b, vals, labels):
            ax.text(r.get_x()+r.get_width()/2, v+(1 if v >= 0 else -1)*0.02*np.ptp(ax.get_ylim()), t, zorder=3,
                    ha='center', va='bottom' if v >= 0 else 'top', fontsize=10, color=INK,
                    bbox=dict(facecolor=SURF, edgecolor='none', pad=0.5))
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(x); ax.set_xticklabels(groups, fontsize=12)


def legend(fig, base=None):
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    h = [Patch(color=WITH, label='顺云（绿云只多 / 红云只空）'), Patch(color=AGAINST, label='逆云（参考）')]
    if base:
        h.append(Line2D([], [], color=BASE, lw=1.4, ls='--', label=base))
    fig.legend(handles=h, loc='lower left', bbox_to_anchor=(0.05, 0.005), ncol=len(h), frameon=False, fontsize=10,
               handlelength=1.6, columnspacing=1.4)


def fig_timeframes(pt, path):
    tfs = list(f.TF)
    w = [pt[f'with_{t}']['all'] for t in tfs]; a = [pt[f'against_{t}']['all'] for t in tfs]; b = pt['base']['all']
    fig, (top, bot) = frame('顺云 vs 逆云：每笔胜率和平均净收益', f'live-wick 候选单 {b["n"]} 笔（6x3 / 3x3 / 6x3hi 共用），按 12h / 4h / 1h 云带分组', 2, 7.8)
    top.set_ylim(0, 100); bot.set_ylim(min(0, min(r['mean_bp'] for r in a))-20, 290)
    bars(top, tfs, [r['win_pct'] for r in w], [r['win_pct'] for r in a], [f'{r["win_pct"]:.0f}%\n{r["n"]}笔' for r in w],
         [f'{r["win_pct"]:.0f}%\n{r["n"]}笔' for r in a], b['win_pct'])
    bars(bot, tfs, [r['mean_bp'] for r in w], [r['mean_bp'] for r in a], [f'+{r["mean_bp"]:.0f}' for r in w],
         [f'{r["mean_bp"]:+.0f}' for r in a], b['mean_bp'])
    top.set_ylabel('胜率 %'); bot.set_ylabel('平均每笔净收益 bp')
    legend(fig, f'不过滤 {b["win_pct"]:.0f}% · +{b["mean_bp"]:.0f}bp')
    fig.subplots_adjust(top=1-1.2/7.8, bottom=0.09, hspace=0.3, left=0.1, right=0.97)
    fig.savefig(path); plt.close(fig)


def fig_years(pt, path):
    ps, names = ['y2020_2024', 'y2025', 'y2026'], ['2020–2024', '2025', '2026（至10-27）']
    w = [pt['with_12h'][p] for p in ps]; a = [pt['against_12h'][p] for p in ps]
    fig, (top, bot) = frame('12h 顺云 vs 逆云：分年份', '2025 年顺云明显好，2026 年反过来逆云更好，优势不稳定', 2, 7.8)
    top.set_ylim(0, 100); bot.set_ylim(-110, 340)
    bars(top, names, [r['win_pct'] for r in w], [r['win_pct'] for r in a], [f'{r["win_pct"]:.0f}%\n{r["n"]}笔' for r in w],
         [f'{r["win_pct"]:.0f}%\n{r["n"]}笔' for r in a])
    bars(bot, names, [r['mean_bp'] for r in w], [r['mean_bp'] for r in a], [f'{r["mean_bp"]:+.0f}' for r in w],
         [f'{r["mean_bp"]:+.0f}' for r in a])
    top.set_ylabel('胜率 %'); bot.set_ylabel('平均每笔净收益 bp')
    legend(fig); fig.subplots_adjust(top=1-1.2/7.8, bottom=0.09, hspace=0.3, left=0.1, right=0.97)
    fig.savefig(path); plt.close(fig)


def curve(x, spec, start):
    """Realized equity path of the account's frac allocator (live-wick6x3 has no boost)."""
    from yoyo.evaluation.monday_limit_return_opt import frac
    trace = Trace(pd.Timestamp(start, tz='UTC').value)
    frac(x, spec['slots'], spec['leverage'], spec['seed'], trace=trace)
    t = pd.DataFrame(trace.rows)
    return pd.Series(t.equity.to_numpy(), pd.to_datetime(t.time_ms, unit='ms', utc=True))


def fig_equity(curves, acc, path):
    fig, ax = frame('live-wick6x3 资金曲线：加 12h 云带过滤后', '1000U 起步，6份×3倍，原规则不变，只按 12h 云带取舍 · 对数纵轴', 1, 6.4)
    for name, colour, ls, lab in (('base', BASE, '--', '不过滤'), ('with_12h', WITH, '-', '只做顺云'), ('against_12h', AGAINST, '-', '只做逆云')):
        s, r = curves[name], acc[name]
        ax.step(s.index, s.to_numpy(), where='post', color=colour, lw=2, ls=ls)
        # The unfiltered and with-cloud ends are close on the log axis; nudge their labels apart.
        y = s.iloc[-1]*{'base': 1.12, 'with_12h': 0.92}.get(name, 1.)
        ax.text(1.015, y, f'{lab} {r["end"]:,.0f}U\n回撤{r["max_drawdown_pct"]:.0f}% · {r["taken"]}笔', transform=ax.get_yaxis_transform(),
                va='center', fontsize=10, color=INK, clip_on=False)
    ax.set_yscale('log'); ax.set_ylabel('账户权益 U')
    ax.set_yticks([1000, 2000, 5000, 10000, 20000]); ax.set_yticklabels(['1000', '2000', '5000', '1万', '2万'])
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.set_xlim(pd.Timestamp('2019-11-01', tz='UTC'), pd.Timestamp('2026-11-15', tz='UTC'))
    fig.subplots_adjust(top=1-1.15/6.4, bottom=0.07, left=0.1, right=0.79)
    fig.savefig(path); plt.close(fig)


def fig_control(vals, mine, base, ctl, path):
    k = ctl['k']
    fig, ax = frame(f'12h 顺云 ≈ 随机删单：随机抽 {k} 笔跑 300 次', 'live-wick6x3 从 2020 起，同一批候选里随机取同样笔数，仓位规则不变', 1, 6.4)
    ax.hist(vals*1000, bins=30, color=GRID, edgecolor=SURF, linewidth=1.5)
    top = ax.get_ylim()[1]
    p95 = ctl['random_p95']*1000
    ax.axvspan(p95, ax.get_xlim()[1], color=WITH, alpha=0.06, lw=0)
    ax.text(base*1000+250, top*0.62, f'随机结果前 5%\n≥{p95:,.0f}U\n到这个区域才算有效', fontsize=9, color=INK2, va='top')
    for v, colour, ls, lab, ha in ((mine*1000, WITH, '-', f'只做顺云 {mine*1000:,.0f}U\n胜过 {ctl["share_below"]:.0f}% 的随机结果', 'right'),
                                   (base*1000, BASE, '--', f'不过滤（全做）\n{base*1000:,.0f}U', 'left')):
        ax.axvline(v, color=colour, lw=2.2, ls=ls)
        ax.text(v+(-150 if ha == 'right' else 150), top*0.97, lab, ha=ha, va='top', fontsize=10, color=INK,
                bbox=dict(facecolor=SURF, edgecolor='none', pad=2))
    ax.set_xlabel(f'随机 {k} 笔的期末权益 U（灰柱 = 300 次分布，中位 {ctl["random_median"]*1000:,.0f}U）')
    ax.set_ylabel('次数')
    fig.subplots_adjust(top=1-1.15/6.4, bottom=0.11, left=0.09, right=0.97)
    fig.savefig(path); plt.close(fig)


def data(workers):
    """Saved per-trade stats plus replayed live-wick6x3 curves and control draws, each checked against summary.json."""
    cfg = json.loads((EXP/'config.json').read_text())
    res = json.loads((EXP/'summary.json').read_text())['results']
    pt, acc, ctl = res['per_trade'][KEY], res['accounts'][KEY], res['control'][KEY]['12h']
    sub = dict(cfg, accounts={'live-wick6x3': cfg['accounts']['live-wick6x3']})
    spec, x, _ = f.candidates(sub, workers)[('live-wick6x3', START)]
    c = pd.read_csv(EXP/'candidates.csv.gz')
    c = c[c.account+'|'+c.start == KEY].reset_index(drop=True)
    if len(c) != len(x) or (c.event.to_numpy() != x.event.to_numpy()).any():
        raise ValueError('candidate order differs from the saved run')
    x = x.assign(cloud_12h=c.cloud_12h.to_numpy())
    arms = {'base': x, 'with_12h': x[x.side == x.cloud_12h], 'against_12h': x[x.side == -x.cloud_12h]}
    curves = {k: curve(v.reset_index(drop=True), spec, START) for k, v in arms.items()}
    for k, s in curves.items():
        if abs(s.iloc[-1]-acc[k]['end']) > 1e-6*acc[k]['end']:
            raise ValueError(f'{k}: replay {s.iloc[-1]} != summary {acc[k]["end"]}')
    # Same draws as the run's control job 0 (first account, first timeframe): seed = control seed + 0.
    k = len(arms['with_12h']); rng = np.random.default_rng(cfg['control']['seed'])
    vals = np.array([f.replay(x.iloc[np.sort(rng.choice(len(x), k, replace=False))].reset_index(drop=True), spec, START)['multiple']
                     for _ in range(cfg['control']['draws'])])
    for q, v in (('random_median', np.median(vals)), ('random_p5', np.percentile(vals, 5)), ('random_p95', np.percentile(vals, 95))):
        if abs(v-ctl[q]) > 1e-9*ctl[q]:
            raise ValueError(f'control {q} {v} != summary {ctl[q]}')
    return dict(pt=pt, acc=acc, ctl=dict(ctl, k=k), curves=curves, vals=vals)


def draw(d):
    OUT.mkdir(exist_ok=True)
    paths = [OUT/f'{i}_{n}.png' for i, n in enumerate(['timeframes', 'years_12h', 'equity_12h', 'random_control_12h'], 1)]
    fig_timeframes(d['pt'], paths[0]); fig_years(d['pt'], paths[1])
    fig_equity(d['curves'], d['acc'], paths[2])
    fig_control(d['vals'], d['ctl']['filtered'], d['acc']['base']['multiple'], d['ctl'], paths[3])
    print(json.dumps({p.name: p.stat().st_size for p in paths}))
    return paths


CAPTION = ('EMA cloud 20/50 方向过滤（绿云只做多 / 红云只做空），live-wick 系列，原规则不变\n'
           '1) 每笔：12h 顺云 277 笔胜率 70%、+198bp；逆云 130 笔 62%、+126bp；差距不显著（年内置换 p=0.14）\n'
           '2) 分年：2026 年反过来，逆云 +266bp，顺云 +120bp\n'
           '3) 账户 live-wick6x3：不过滤 16438U；只做顺云 10621U，回撤没降；逆云单也赚钱，删掉就少了复利\n'
           '4) 随机删掉同样多的单：顺云只胜过 85% 的随机结果，不到 95%\n'
           '结论：不加这个过滤，模拟账户没动。币安K线回测到 2026-10-27，样本内。')


def deliver(paths):
    import requests
    from yoyo.notify import _load
    creds = _load()
    if creds is None:
        raise ValueError('configured TG credentials missing')
    token, chat = creds
    url = 'https://api.telegram.org/bot'+token+'/'
    info = requests.post(url+'getChat', json={'chat_id': chat}, timeout=(10, 25)).json()
    if not info.get('ok'):
        raise ValueError('getChat rejected')
    target = info['result']
    if target.get('type') not in ('channel', 'supergroup', 'group') or target.get('title') != GROUP:
        raise ValueError('configured destination differs from verified owner group')
    rp = OUT/'telegram_receipt.json'
    if rp.exists():
        raise ValueError('receipt exists: inspect it before any resend')
    receipt = dict(target_title=target['title'], target_type=target['type'], target_id=target['id'],
                   owner_request='以 wick 系列、12h 为例，顺云胜率 70% 给我几张图看看 发到tg群',
                   images=[p.name for p in paths], status='pending')
    rp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    files = {f'img{i}': (p.name, p.read_bytes(), 'image/png') for i, p in enumerate(paths)}
    media = [dict(type='photo', media=f'attach://img{i}', **({'caption': CAPTION} if i == 0 else {})) for i in range(len(paths))]
    try:
        payload = requests.post(url+'sendMediaGroup', data={'chat_id': chat, 'media': json.dumps(media, ensure_ascii=False)},
                                files=files, timeout=(10, 60)).json()
    except Exception as exc:
        receipt.update(status='unknown', error_type=type(exc).__name__); rp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        raise RuntimeError('upload response uncertain; no automatic resend') from None
    if not payload.get('ok'):
        receipt.update(status='rejected', error_code=payload.get('error_code')); rp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        raise RuntimeError('Telegram upload rejected; error code '+str(payload.get('error_code')))
    msgs = payload['result']
    ok = len(msgs) == len(paths) and all(m['chat']['id'] == target['id'] for m in msgs)
    receipt.update(status='sent' if ok else 'unknown', message_ids=[m['message_id'] for m in msgs],
                   sent_at=pd.Timestamp.now(tz='UTC').isoformat())
    rp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: receipt[k] for k in ('target_title', 'status', 'message_ids')}, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--workers', type=int, default=6); ap.add_argument('--send', action='store_true')
    a = ap.parse_args()
    ps = draw(data(a.workers))
    if a.send:
        deliver(ps)
