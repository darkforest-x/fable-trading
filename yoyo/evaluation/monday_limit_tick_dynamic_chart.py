"""One event of exp-monday-limit-tick-dynamic-20261031-v1 drawn trade by trade (owner 2026-10-31).

Owner asked for one historical trade explained in detail, suspecting the score simply comes out too late. The figure
shows, for one touch: the 1m context with the resting fill at the 1.618 and the market fill after the trade-by-trade
score first clears the account threshold; the Binance aggTrades of the touch 5m bar; and the score itself, flat at the
pre-touch value until the touch and then recomputed after every trade exactly as the experiment's step 1 (frozen
model, partial-bar vol_ratio / liquidity / toward24). The recomputed path must reproduce dyn_events.csv.gz (first score
= instant score, last = final score, same crossing time and fill price) or the script stops. Outcomes are the
framework's: the resting row from exp-monday-limit-paper-accounts-20261028-v1 (worse intrabar path) and the chased
entry on the dynamic run's own exit price with the experiment's fee/slippage arithmetic. Read-only; no new fills.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC
from yoyo.evaluation.monday_limit_tick_dynamic import contrib, _fixed_symbol

EXP = ROOT/'experiments/active/exp-monday-limit-tick-dynamic-20261031-v1'
KLINE_1M = ROOT/'data/crypto/market/kline_binance_um1m_touchdays_20261027'
ACCOUNT = {'05': ('rest_v3', 'v3'), '06': ('rest_spike', 'spike')}


def score_path(event, tick, spec, ticks):
    """Trades of the touch bar and the frozen score after each trade from t_star (as monday_limit_tick_dynamic.dynamic)."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    symbol = event.split(':')[0]
    cache = ROOT/tick['cache']
    z_fixed = _fixed_symbol((symbol, ticks.loc[[event]], spec, str(cache)))[event]
    job = json.loads((SRC/symbol/'identity.json').read_text())['job']
    t = pd.read_csv(SRC/symbol/'trades.csv.gz')
    t = t[(t.population == 'signal') & (t.reclaim_minutes == 60)]
    t = t[(symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')) == event]
    targets = t.set_index('exit_mode').target_price.to_dict()
    r = t.iloc[0]
    f, _ = load_job(job)
    v, c = f.volume.to_numpy(), f.close.to_numpy()
    qv = v*c
    hour = pd.Series(qv).rolling(12, min_periods=12).sum().to_numpy()
    base = pd.Series(hour).rolling(2016, min_periods=1000).median().shift(12).to_numpy()
    day = pd.Series(qv).rolling(2016, min_periods=1000).sum().to_numpy()/7
    k, s, L = int(r.touch_i_hint), int(r.side), float(r.entry)
    o = pd.Timestamp(r.touch_interval_open)
    o_ms = int(o.value//1_000_000)
    tr = pd.read_csv(cache/symbol/f'{o.date()}.csv.gz').sort_values('ts', kind='mergesort').reset_index(drop=True)
    inbar = ((tr.ts >= o_ms) & (tr.ts < o_ms+300_000)).to_numpy()
    i0 = int(np.flatnonzero(inbar & ((tr.price <= L) if s > 0 else (tr.price >= L)).to_numpy())[0])
    last = int(np.flatnonzero(inbar)[-1])
    px, ts = tr.price.to_numpy(), tr.ts.to_numpy()
    cq = np.cumsum(np.where(inbar, tr.qty.to_numpy(), 0.))
    seg = np.arange(i0, last+1)
    qd = cq[seg]*px[seg]
    vr, liq, tw = (hour[k]-qv[k]+qd)/base[k], day[k]-(qv[k]-qd)/7, -s*100*(px[seg]/c[k-288]-1)
    z = z_fixed+contrib(spec, vr, liq, tw)
    f_ = spec['features']
    part = lambda name, x: spec['coef'][f_.index(name)]*(x-spec['mean'][f_.index(name)])/spec['scale'][f_.index(name)]
    parts = pd.DataFrame(dict(vol=part('log_vol_ratio', np.log(np.clip(vr, .05, None))),
                              liq=part('log_liquidity', np.log10(np.clip(liq, 1e4, None))), toward24=part('toward24', tw)))
    return dict(parts=parts, symbol=symbol, side=s, level=L, open_ms=o_ms, targets=targets, trades=tr[inbar | (tr.ts >= o_ms+300_000)],
                path=pd.DataFrame(dict(ts=ts[seg], price=px[seg], score=1/(1+np.exp(-z)))))


def outcomes(event, cut, tick, row, s, lat):
    """Resting framework row (worse path) and the chased entry on the dynamic run's exit, as the experiment's summary."""
    from yoyo.evaluation.monday_limit_paper_accounts import load_symbol, V3MODE
    from yoyo.evaluation.monday_limit_tick_entry import load_paths
    symbol = event.split(':')[0]
    variant, kind = ACCOUNT[cut]
    acfg = json.loads((ROOT/tick['accounts_config']).read_text())
    _, rest, _ = load_symbol((str(ROOT/tick['accounts_run']), symbol, 10., 10.))
    rest = rest[(rest.variant == variant) & (rest.event == event)]
    mode = 'near_monday_edge' if kind == 'spike' else str(V3MODE(np.array([row[f's_{cut}']]), acfg['hi'])[0])
    raw = load_paths((str(ROOT/json.loads((EXP/'config.json').read_text())['accounts_run']), symbol))
    raw = raw[(raw.variant == variant) & raw.trade_id.str.startswith(event+':') & (raw.trade_id.str.split(':').str[4] == mode)]
    p = row.get(f'p_{cut}_{lat}', np.nan)
    chased = None
    if len(raw) and np.isfinite(p):
        fee_exit = np.where(raw.reason == 4, tick['maker_fee_bp'], tick['taker_fee_bp'])*raw.exit_price/p
        net = s*(raw.exit_price/p-1)*1e4-tick['taker_fee_bp']-fee_exit-np.where(raw.reason == 4, 0., tick['slippage_bp'])
        w = int(np.argmin(net.to_numpy()))
        chased = dict(entry=float(p), exit_price=float(raw.exit_price.iat[w]), reason=int(raw.reason.iat[w]), net_bp=float(net.iat[w]),
                      mode=mode)
    return rest, chased, mode


def minute_bars(symbol, start, end):
    """Binance 1m klines from the 1m-features cache (touch day and the day before; some days are stored as plain .csv)."""
    days = pd.date_range(start.normalize(), end.normalize(), freq='D')
    files = [p for d in days for p in (KLINE_1M/symbol/f'{d.date()}.csv.gz', KLINE_1M/symbol/f'{d.date()}.csv') if p.exists()]
    if not files:
        raise FileNotFoundError(f'no 1m klines for {symbol} {start.date()}..{end.date()}')
    m = pd.concat([pd.read_csv(p) for p in files], ignore_index=True).drop_duplicates('open_time').sort_values('open_time')
    m['t'] = pd.to_datetime(m.open_time, unit='ms', utc=True)
    return m[(m.t >= start) & (m.t < end)].reset_index(drop=True)


def draw(event, cut, lat, out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Heiti SC', 'Arial Unicode MS'],
                         'axes.unicode_minus': False})
    tick = json.loads((ROOT/'experiments/active/exp-monday-limit-tick-entry-20261029-v1/config.json').read_text())
    spec = json.loads((ROOT/tick['score_model']).read_text())
    ticks = pd.read_csv(ROOT/'experiments/active/exp-monday-limit-tick-entry-20261029-v1/tick_events.csv.gz').set_index('event')
    row = pd.read_csv(EXP/'dyn_events.csv.gz').set_index('event').loc[event]
    thr = {'05': 0.5, '06': 0.6}[cut]
    g = score_path(event, tick, spec, ticks)
    path, s, L = g['path'], g['side'], g['level']
    cross = path[path.score >= thr]
    # Parity with the experiment's step 1, or stop.
    assert abs(path.score.iat[0]-row.instant) < 1e-9 and abs(path.score.iat[-1]-row.final_score) < 1e-9, 'score path parity'
    assert len(cross) and int(cross.ts.iat[0]) == int(row[f't_{cut}_ms']), 'crossing time parity'
    t_cross = int(cross.ts.iat[0])
    trades = g['trades']
    taker = trades.buyer_maker.astype(str).str.lower().isin(['false', '0'] if s > 0 else ['true', '1'])
    fills = trades[taker & (trades.ts >= t_cross+lat) & (trades.ts > t_cross)] if lat else trades[trades.ts == t_cross]
    t_fill, p_fill = int(fills.ts.iat[0]), float(row[f'p_{cut}_{lat}'])
    rest, chased, mode = outcomes(event, cut, tick, row, s, lat)
    target = g['targets'][mode]
    o = g['open_ms']
    hit = trades[(trades.ts >= int(path.ts.iat[0])) & ((trades.price >= target) if s > 0 else (trades.price <= target))]
    t_hit = int(hit.ts.iat[0]) if len(hit) else None
    causal, stored = float(ticks.at[event, 'causal']), float(ticks.at[event, 'stored'])

    fig, ax = plt.subplots(4, 1, figsize=(13, 17), gridspec_kw=dict(height_ratios=[1.1, 1, 1, 1]))
    ts0 = pd.Timestamp(o, unit='ms', tz='UTC')
    exit_t = rest.exit_t.iat[0] if len(rest) else ts0+pd.Timedelta(minutes=30)
    m = minute_bars(g['symbol'], ts0-pd.Timedelta(minutes=30), max(exit_t, ts0+pd.Timedelta(minutes=20))+pd.Timedelta(minutes=10))
    a = ax[0]
    for r in m.itertuples():
        col = '#2e9e6a' if r.close >= r.open else '#d64545'
        a.plot([r.t, r.t], [r.low, r.high], color=col, lw=.8)
        a.plot([r.t, r.t], [r.open, r.close], color=col, lw=3)
    a.axhline(L, color='#3366cc', ls='--', lw=1); a.text(m.t.iat[0], L, ' 1.618 挂单价', color='#3366cc', va='bottom')
    a.axhline(target, color='#999999', ls=':', lw=1); a.text(m.t.iat[0], target, f' 止盈 {mode}', color='#666666', va='bottom')
    a.axvspan(ts0, ts0+pd.Timedelta(minutes=5), color='#f3d36b', alpha=.25)
    a.scatter([pd.Timestamp(int(path.ts.iat[0]), unit='ms', tz='UTC')], [L], marker='o', s=70, color='#3366cc', zorder=5, label='挂单成交（触及即成交）')
    a.scatter([pd.Timestamp(t_fill, unit='ms', tz='UTC')], [p_fill], marker='^' if s > 0 else 'v', s=90, color='#d64545', zorder=5,
              label=f'逐笔评分过线后市价成交（+{lat}ms）')
    if len(rest):
        a.axvline(rest.exit_t.iat[0], color='#2e9e6a', lw=1, ls='-.', label=f'挂单那笔平仓（净 {rest.net.iat[0]:+.0f}bp）')
    a.set_title(f'{event}  1分钟K线（黄色=触及那根5分钟K线）', loc='left')
    a.legend(loc='best', fontsize=9)

    b = ax[1]
    sec = lambda x: (np.asarray(x, dtype='int64')-o)/1000
    b.plot(sec(trades.ts), trades.price, color='#555555', lw=.7)
    b.axhline(L, color='#3366cc', ls='--', lw=1)
    if np.isfinite(target) and abs(target/L-1) < .3:
        b.axhline(target, color='#999999', ls=':', lw=1)
    b.axvline(sec(path.ts.iat[0]), color='#3366cc', lw=1); b.text(sec(path.ts.iat[0]), b.get_ylim()[1], '①触及 ', color='#3366cc', va='top', ha='right')
    b.axvline(sec(t_cross), color='#d64545', lw=1); b.text(sec(t_cross), b.get_ylim()[0], ' ②评分过线', color='#d64545', va='bottom')
    if t_hit is not None:
        b.axvline(sec(t_hit), color='#2e9e6a', lw=1); b.text(sec(t_hit), b.get_ylim()[1], ' ③价格到止盈', color='#2e9e6a', va='top')
    b.scatter([sec(t_fill)], [p_fill], marker='^' if s > 0 else 'v', s=90, color='#d64545', zorder=5)
    b.set_xlim(0, 310); b.set_ylabel('逐笔成交价'); b.set_title('触及那根5分钟K线：币安逐笔成交（横轴=K线开盘后秒数）', loc='left')

    zx = ax[2]
    lo, hi_ = sec(path.ts.iat[0])-5, (sec(t_hit) if t_hit is not None else sec(t_fill))+8
    w = (sec(trades.ts) >= lo) & (sec(trades.ts) <= hi_)
    zx.plot(sec(trades.ts[w]), trades.price[w], color='#555555', lw=.9)
    zx.axhline(L, color='#3366cc', ls='--', lw=1)
    if np.isfinite(target) and abs(target/L-1) < .3:
        zx.axhline(target, color='#999999', ls=':', lw=1)
    zx.scatter([sec(path.ts.iat[0])], [L], s=60, color='#3366cc', zorder=5)
    zx.scatter([sec(t_fill)], [p_fill], marker='^' if s > 0 else 'v', s=90, color='#d64545', zorder=5)
    zx.set_ylabel('逐笔成交价'); zx.set_xlim(lo, hi_)
    zs = zx.twinx()
    zs.plot(sec(path.ts), path.score, color='#d64545', lw=1.2, alpha=.8); zs.axhline(thr, color='#d64545', ls='--', lw=.8)
    zs.set_ylabel('评分', color='#d64545'); zs.set_xlim(lo, hi_)
    i_c = int(np.flatnonzero(path.ts.to_numpy() == t_cross)[0])
    zx.set_title(f'放大：触及后 {sec(t_cross)-sec(path.ts.iat[0]):.1f} 秒评分才过线，这时成交价已比挂单价差 '
                 f'{s*(p_fill/L-1)*1e4:+.0f}bp；{"" if t_hit is None else f"{sec(t_hit)-sec(path.ts.iat[0]):.0f} 秒后价格到止盈"}',
                 loc='left')
    c = ax[3]
    c.plot([0, sec(path.ts.iat[0])], [causal, causal], color='#3366cc', lw=2, label=f'触及前评分（上一根收盘就已知）{causal:.3f}')
    c.plot(sec(path.ts), path.score, color='#d64545', lw=1.2, label=f'逐笔评分：触及瞬间 {path.score.iat[0]:.3f} → 收盘 {path.score.iat[-1]:.3f}')
    c.axhline(thr, color='#333333', ls='--', lw=1); c.text(2, thr, f' 门槛 {thr}', va='bottom')
    c.scatter([300], [stored], color='#7a3db8', zorder=5, label=f'原回测评分（含前视，收盘后才有）{stored:.3f}')
    c.axvline(sec(t_cross), color='#d64545', lw=1)
    c.set_xlim(0, 310); c.set_ylabel('评分'); c.set_xlabel('K线开盘后秒数'); c.legend(loc='best', fontsize=9)
    c.set_title('评分怎么变：触及那一刻往下掉，随着放量和价格回来才慢慢涨回去', loc='left')
    pp = g['parts']
    pick = lambda i: pp.iloc[i]
    txt = '三项K线特征对评分（logit）的贡献   触及瞬间 → 过线 → 收盘\n' + '\n'.join(
        f'{name}: {pick(0)[col]:+.3f} → {pick(i_c)[col]:+.3f} → {pick(-1)[col]:+.3f}'
        for name, col in (('成交量比', 'vol'), ('流动性', 'liq'), ('24小时涨跌', 'toward24')))
    c.text(.99, .03, txt, transform=c.transAxes, ha='right', va='bottom', fontsize=9,
           bbox=dict(boxstyle='round', fc='white', ec='#cccccc'))
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=130)
    facts = dict(event=event, cut=thr, latency_ms=lat, side=s, level=L, target=float(target), target_mode=mode,
                 bar_open_utc=str(ts0), touch_s=float(sec(path.ts.iat[0])), cross_s=float(sec(t_cross)), fill_s=float(sec(t_fill)),
                 target_hit_s=None if t_hit is None else float(sec(t_hit)), causal=causal, instant=float(path.score.iat[0]),
                 stored=stored, final=float(path.score.iat[-1]), market_fill=p_fill,
                 logit_parts={k: dict(touch=float(g['parts'][k].iat[0]), cross=float(g['parts'][k].iat[i_c]),
                                      close=float(g['parts'][k].iat[-1])) for k in ('vol', 'liq', 'toward24')},
                 fill_vs_level_bp=float(s*(p_fill/L-1)*1e4),
                 extreme=float(path.price.min() if s > 0 else path.price.max()),
                 resting=None if rest.empty else dict(entry=float(rest.entry.iat[0]), net_bp=float(rest.net.iat[0]),
                                                      reason=int(rest.reason.iat[0]), exit_utc=str(rest.exit_t.iat[0])),
                 chased=chased)
    out.with_suffix('.json').write_text(json.dumps(facts, ensure_ascii=False, indent=1))
    print(json.dumps(facts, ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--event', required=True)
    ap.add_argument('--cut', choices=['05', '06'], default='05')
    ap.add_argument('--lat', type=int, default=100)
    ap.add_argument('--out', type=Path)
    a = ap.parse_args()
    draw(a.event, a.cut, a.lat, a.out or EXP/'charts'/f"{a.event.replace(':', '_')}_{a.cut}.png")
