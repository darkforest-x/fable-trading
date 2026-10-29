"""After a market-wide spike alarm, enter every later same-side 1.618 touch and decide at the touch close (owner 2026-10-29).

Owner: "验证一个场景，当监控到全局报警的时候，任何一个币种打到了1.618立马入场，等最后一根k线收盘了，分数出来了再决定要不要走".
Alarm as yoyo/monitor/spike_alert.py, replayed on Codex first-touch fill bars (monday_spike_alert_history.rows, Binance
5m): per side, >= N distinct symbols whose touch bar falls in the last 5 minutes, one alarm per side per 30 minutes;
N = 25, the live threshold since 2026-10-27 (owner: "不是25个吗"). An alarm is known at the close of its 5m bar, so
only touches in later bars are eligible, up to W minutes after the alarm (30 / 120 / 360; the page keeps an alarm 6h).
Entry: a resting limit at the level from the bar before the touch (Codex order), no score filter. Decision at the
touch bar's close with the frozen touch-bar score (`stored`, known at that close): keep if >= the cut, else market
exit at that close (adapter fill_close_exit, reason 14). Cuts 0.5 / 0.6, plus 'hold' (no exit). The target must be
set before the score exists, so it is fixed per variant: near Monday edge, range 0.618 or opposite 1.618 with a -30
percent backstop, and near edge with -18 percent (spike); breakeven +0.17 percent, 60m confirmation.
Controls: the same rows for a seeded 15 percent sample of touches outside every alarm window, and the paper engine's
resting rule (causal score >= 0.5) inside the alarm windows. Native NautilusTrader run, both intrabar paths; the summary
is read-only arithmetic on framework fills (worse path, 10bp slippage on non-target exits, frac N20 3x replays,
alarm episodes as the independent unit).
"""
from __future__ import annotations
import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump
from yoyo.evaluation.monday_limit_backstop import CODEX, stops
from yoyo.evaluation.monday_limit_reclaim_patterns import SRC

KEEP = ['trade_id', 'side', 'entry', 'stop', 'target_price', 'exit_price', 'reason', 'entry_time', 'exit_time', 'entry_i',
        'exit_i', 'reclaim_status', 'net_notional_bp']
A = ROOT/'experiments/active'
VARIANTS = {'near30': ('near_monday_edge', 30), 'r0618': ('range_0.618', 30), 'opp30': ('opposite_1618', 30), 'near18': ('near_monday_edge', 18)}


def touch_rows(symbol):
    x = pd.read_csv(SRC/symbol/'trades.csv.gz', usecols=['population', 'reclaim_minutes', 'exit_mode', 'side', 'week', 'entry_time'])
    x = x[(x.population == 'signal') & (x.reclaim_minutes == 60) & (x.exit_mode == 'near_monday_edge')]
    return x[['side', 'week', 'entry_time']].assign(symbol=symbol)


def touches():
    """First weekly same-side touches (fill bar open, ms) of every Codex symbol, keyed like the score files."""
    syms = sorted(d.name for d in SRC.iterdir() if (d/'trades.csv.gz').exists())
    with ProcessPoolExecutor(6) as ex:
        t = pd.concat([d for d in ex.map(touch_rows, syms, chunksize=8) if len(d)], ignore_index=True)
    # Pin the unit: pandas 3 parses to microseconds, and ms arithmetic below assumes nanoseconds // 1e6.
    t['ms'] = pd.to_datetime(t.entry_time, utc=True).dt.as_unit('ns').astype('int64')//10**6-5*60_000
    t['event'] = t.symbol+':'+t.week+':'+np.where(t.side == 1, '+1', '-1')
    return t


def alarms(t, threshold, window_ms=5*60_000, cooldown_ms=30*60_000):
    """Alarm bars (ms of the 5m bar open) per side, as monday_spike_alert_history.episodes."""
    out = []
    for side, g in t.groupby('side'):
        ms, sym = g.ms.to_numpy(), g.symbol.to_numpy(); o = np.argsort(ms, kind='mergesort'); ms, sym = ms[o], sym[o]
        last, lo = -10**15, 0
        for i in range(len(ms)):
            while ms[lo] <= ms[i]-window_ms:
                lo += 1
            n = len(set(sym[lo:i+1]))
            if n >= threshold and ms[i]-last >= cooldown_ms:
                last = ms[i]
                out.append(dict(side=int(side), ms=int(ms[i]), n=n))
    return pd.DataFrame(out)


def windows(cfg):
    """Events per (threshold, window): first alarm whose window holds the touch; plus the control sample."""
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    t = touches()
    t = t[t.event.isin(sc.index)].drop_duplicates('event')
    assign, al = [], {}
    for n in cfg['thresholds']:
        a = alarms(t, n)
        al[n] = a
        for w in cfg['windows_min']:
            for side, g in a.groupby('side'):
                tt = t[t.side == side]
                ams = np.sort(g.ms.to_numpy())
                k = np.searchsorted(ams, tt.ms.to_numpy(), side='left')-1  # latest alarm strictly before the touch bar
                ok = k >= 0
                gap = np.where(ok, tt.ms.to_numpy()-ams[np.clip(k, 0, None)], -1)
                inside = ok & (gap > 0) & (gap <= w*60_000)
                assign.append(pd.DataFrame(dict(event=tt.event[inside].to_numpy(), threshold=n, window=w,
                                                alarm_ms=ams[k[inside]], side=side)))
    win = pd.concat(assign, ignore_index=True)
    rng = np.random.default_rng(cfg['seed'])
    outside = t[~t.event.isin(win.event)]
    control = outside[rng.random(len(outside)) < cfg['control_share']]
    return win, control.event.tolist(), al


def process(symbol, intrabar, exp, cfg, events):
    from yoyo.evaluation.monday_limit_reclaim import prepare
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.week >= cfg['since_week'])].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(events)]
    stored = pd.read_csv(ROOT/cfg['scores']).set_index('event').stored
    e['full'] = e.event.map(stored)
    parts = []
    for name, (mode, pct) in VARIANTS.items():
        y = e[e.exit_mode == mode].copy()
        y['stop'] = stops(y, 'P', pct, tick)
        y['be_offset_bp'] = cfg['be_offset_bp']
        hold = y.assign(fill_close_exit=False, trade_id=y.trade_id+f':v_{name}_hold')
        chk = y[y.full < max(cfg['cuts'])].assign(fill_close_exit=True)
        chk['trade_id'] = chk.trade_id+f':v_{name}_chk'
        parts += [hold[hold.side*(hold.entry-hold.stop) > 0], chk[chk.side*(chk.entry-chk.stop) > 0]]
    x = pd.concat(parts, ignore_index=True)
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x['risk'] = x.entry
    x['reclaim_ref'] = np.nan
    x['fill_close_exit'] = x.fill_close_exit.astype(bool)
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    rows, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                x.drop(columns=['event', 'full', 'population', 'exit_mode']), policy, tick=tick,
                                symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                                entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    rows = rows[[k for k in KEEP if k in rows]]
    rows['variant'] = rows.trade_id.str.rsplit(':v_', n=1).str[-1]
    rows.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), positions=audit['positions'], fills=audit['fills'],
                engine=audit.get('engine'), version=audit.get('version'), seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def committed(*paths):
    for path in paths:
        if subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', path], cwd=ROOT).returncode or \
           subprocess.run(['git', 'ls-files', '--error-unmatch', path], cwd=ROOT, capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_alarm_entry.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
              str(config.resolve().relative_to(ROOT)))
    win_path = exp/'alarm_windows.csv.gz'
    if not win_path.exists():
        win, control, al = windows(cfg)
        win.to_csv(win_path, index=False)
        pd.DataFrame(dict(event=control)).to_csv(exp/'control_events.csv.gz', index=False)
        pd.concat([a.assign(threshold=n) for n, a in al.items()]).to_csv(exp/'alarms.csv.gz', index=False)
    events = set(pd.read_csv(win_path).event) | set(pd.read_csv(exp/'control_events.csv.gz').event)
    syms = sorted({e.split(':')[0] for e in events})
    root = exp/intrabar.lower()
    todo = [s for s in syms if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, {e for e in events if e.startswith(s+':')}): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


def load_symbol(args):
    """Worse path per row (10bp slippage on non-target exits), fill time at the fill bar close, MAE from the fill bar."""
    exp, symbol, slip = args
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    paths, issues = {}, []
    for p in ('ohlc', 'olhc'):
        d = Path(exp)/p/'symbols'/symbol
        if (d/'error.json').exists() or not (d/'done.json').exists():
            issues.append(f'{p}: missing or failed'); continue
        t = pd.read_csv(d/'trades.csv.gz')
        if json.loads((d/'done.json').read_text()).get('positions', 0) != len(t):
            issues.append(f'{p}: rows != positions')
        paths[p] = t.assign(net=t.net_notional_bp-np.where(t.reason == 4, 0., slip)).set_index('trade_id')
    if len(paths) < 2 or paths['ohlc'].empty:
        return symbol, pd.DataFrame(), issues
    o = paths['ohlc']; l = paths['olhc'].reindex(o.index)
    w = (l.net < o.net).fillna(False).to_numpy()
    pick = lambda c: np.where(w, l[c], o[c])
    d = pd.DataFrame(dict(variant=o.variant, side=o.side, entry=o.entry, net=pick('net'), reason=pick('reason'),
                          entry_i=o.entry_i, exit_i=pick('exit_i'),
                          exit_t=pd.to_datetime(pd.Series(pick('exit_time'), index=o.index), utc=True).dt.as_unit('ns')), index=o.index)
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    f, _ = load_job(job)
    d['entry_t'] = pd.Series(f.index[d.entry_i.astype(int).to_numpy()]+pd.Timedelta(minutes=job['execution_minutes']), index=d.index).dt.as_unit('ns')
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    d['mae_pct'] = [max(0., -s*((lo[a:max(a+1, b)].min() if s == 1 else h[a:max(a+1, b)].max())-e)/e*100)
                    for a, b, s, e in zip(d.entry_i.astype(int), d.exit_i.astype(int), d.side, d.entry)]
    d['event'] = d.index.str.split(':').str[:3].str.join(':')
    d['kind'] = d.variant.str.split('_').str[0]; d['act'] = d.variant.str.split('_').str[1]
    return symbol, d.reset_index(drop=True), issues


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import replay
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_alarm_entry.py')
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    win = pd.read_csv(exp/'alarm_windows.csv.gz')
    control = set(pd.read_csv(exp/'control_events.csv.gz').event)
    syms = sorted({e.split(':')[0] for e in set(win.event) | control})
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load_symbol, [(str(exp), s, cfg['slippage_bp']) for s in syms], chunksize=4))
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].copy()
    sc = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    t['full'] = t.event.map(sc.stored); t['pre'] = t.event.map(sc.causal)
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20], rows=int(len(t)),
                           alarms=int(pd.read_csv(exp/'alarms.csv.gz').shape[0])), plans={}, exits={})
    jobs = []

    def plans(g, tag, episodes):
        for v in VARIANTS:
            hold = g[(g.kind == v) & (g.act == 'hold')]
            chk = g[(g.kind == v) & (g.act == 'chk')]
            out = {'hold_all': hold}
            for cut in cfg['cuts']:
                out[f'exit_below_{cut}'] = pd.concat([hold[hold.full >= cut], chk[chk.full < cut]])
            out['resting_causal_0.5'] = hold[hold.pre >= .5]
            for name, x in out.items():
                jobs.append((f'{tag}|{v}|{name}', 'frac', x.assign(episode=x.event.map(episodes)).reset_index(drop=True), window, cfg, ro, sh, py))
            # Paired: what the close exit did to the touches it closed.
            ex_ = chk[chk.full < min(cfg['cuts'])].set_index('event').net
            hd = hold.set_index('event').net.reindex(ex_.index)
            res['exits'][f'{tag}|{v}'] = dict(n=int(len(ex_)), exit_mean_bp=float(ex_.mean()) if len(ex_) else None,
                                               hold_mean_bp=float(hd.mean()) if len(hd) else None,
                                               exit_reason={str(k): int(n) for k, n in chk[chk.full < min(cfg['cuts'])].reason.value_counts().items()})
    for w in cfg['windows_min']:
        ww = win[win.window == w]
        ep = ww.drop_duplicates('event').set_index('event').alarm_ms
        plans(t[t.event.isin(ep.index)], f'alarm_{w}m', ep)
    plans(t[t.event.isin(control)], 'no_alarm_control', pd.Series(dtype='int64'))
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            res['plans'][name] = out
    eps = {}
    for job in jobs:
        x = job[2]
        if 'episode' in x and x.episode.notna().any():
            per = x.groupby('episode').net.mean()
            eps[job[0]] = dict(episodes=int(per.size), episode_mean_bp=float(per.mean()), episodes_positive_pct=float(100*(per > 0).mean()))
    res['episodes'] = eps
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=5)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
