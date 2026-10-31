"""Random-time control inside the market-wide spike alarm windows (owner 2026-10-31: "先补报警对照").

exp-monday-limit-alarm-entry-20261029-v1 found that entering every same-side Monday 1.618 touch within 30 minutes of a
25-coin spike alarm and holding it (near Monday edge target, -30 percent backstop, breakeven +0.17 percent, 60m
confirmation) gave +80bp over 2146 trades and 3.70x (frac N20 3x) since 2020, against -6bp outside alarms. Its learning
note leaves one question open: does the 1.618 matter, or would any dip bought inside the alarm window do as well?

Control, single variable = entry time inside the same alarm window. For each signal touch, every other 5m submission bar
of the same symbol from the alarm bar to 25 minutes after it (orders active from alarm+5 to alarm+30 min, the span of
the signal's touch bars) gets the signal's own geometry, as monday_limit_reclaim.prepare builds its random controls: a
resting limit at the same percentage distance from that bar's close as the signal's level was from its submission close,
the target scaled by the same ratio, a -30 percent backstop from that entry, the same breakeven, confirmation, fees and
slippage. Control orders expire at the end of the window, since the signal fills inside it by construction; unfilled
controls are no trade and are counted. Same symbol and same 30 minutes are the matching block. The 14-bar volatility
bucket is recorded and a bucket-matched subset reported, not used as a filter (at most five candidate bars).

The signal rows run again in the same native NautilusTrader run (both intrabar paths) and are checked against the 10-29
fills. Summary (read-only arithmetic on framework fills, worse path, 10bp slippage on non-target exits): per trade, per
event (signal minus the mean filled control), per alarm episode (bootstrap CI over episodes), and the frac N20 3x
replay of the signal against seeded control portfolios that draw one control order per event.
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
from yoyo.evaluation.monday_limit_alarm_entry import KEEP, committed

MIN = 60_000


def process(symbol, intrabar, exp, cfg, win):
    """Signal touches plus same-window random-time controls for one symbol, one native run."""
    from yoyo.evaluation.monday_limit_reclaim import prepare, segment_ends
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    ccfg = json.loads((CODEX/'config.json').read_text())
    job = json.loads((CODEX/'symbols'/symbol/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        return dict(symbol=symbol, skipped=job['group'])
    t0 = time.perf_counter()
    f, quality = load_job(job)
    p, e, _, _, tick = prepare(f, job, ccfg, quality)
    mode, pct = cfg['variant']
    e = e[(e.population == 'signal') & (e.reclaim_minutes == 60) & (e.exit_mode == mode)].copy()
    e['event'] = e.trade_id.str.split(':').str[:3].str.join(':')
    e = e[e.event.isin(win.index)]
    opens = f.index.as_unit('ns').asi8//10**6
    c = p.close.to_numpy()
    ends = segment_ends(f, job)
    tr = pd.concat([p.high-p.low, (p.high-p.close.shift()).abs(), (p.low-p.close.shift()).abs()], axis=1).max(axis=1)
    vol = tr.rolling(14, min_periods=14).mean()/p.close
    bucket = np.floor(np.log2(vol.where(vol > 0))*4).fillna(-999).to_numpy(int)
    decimals = int(round(-np.log10(tick)))
    rows, meta = [], []
    for r in e.to_dict('records'):
        a, s, j0, entry = int(win.at[r['event'], 'alarm_ms']), int(r['side']), int(r['entry_i']), float(r['entry'])
        rows.append(dict(r, trade_id=r['trade_id']+':v_sig'))
        meta.append(dict(trade_id=r['trade_id']+':v_sig', event=r['event'], alarm_ms=a, offset=0, bucket=int(bucket[j0]),
                         sig_bucket=int(bucket[j0]), status='signal'))
        last = int(np.flatnonzero(opens <= a+cfg['window_min']*MIN)[-1])
        for j in np.flatnonzero((opens >= a) & (opens <= a+(cfg['window_min']-5)*MIN)):
            j = int(j)
            if j == j0:
                continue
            raw = c[j]*entry/c[j0]
            ce = float(np.floor(raw/tick+1e-7)*tick if s == 1 else np.ceil(raw/tick-1e-7)*tick)
            tid = f"{r['trade_id']}:v_ctrl{(j-j0):+d}"
            m = dict(trade_id=tid, event=r['event'], alarm_ms=a, offset=j-j0, bucket=int(bucket[j]), sig_bucket=int(bucket[j0]))
            end = int(ends[j+1]) if j+1 < len(ends) else j
            expire = min(last, end)
            if ce <= 0 or s*(c[j]-ce) < tick*(1-1e-7) or expire <= j:
                meta.append(dict(m, status='invalid_geometry')); continue
            target = float(np.round(ce*r['target_price']/entry, decimals))
            rows.append(dict(r, trade_id=tid, entry=ce, risk=ce, target_price=target, entry_i=j, expire_i=expire, end_i=end,
                             touch_i_hint=-1, bucket=int(bucket[j])))
            meta.append(dict(m, status='submitted'))
    out = Path(exp)/intrabar.lower()/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(meta).to_csv(out/'orders.csv.gz', index=False)
    x = pd.DataFrame(rows)
    if x.empty:
        pd.DataFrame(columns=KEEP+['variant']).to_csv(out/'trades.csv.gz', index=False)
        dump(out/'done.json', dict(symbol=symbol, intrabar=intrabar, orders=0, positions=0))
        return dict(symbol=symbol, orders=0)
    x['stop'] = stops(x, 'P', pct, tick)
    x = x[x.side*(x.entry-x.stop) > 0].copy()
    x['be_offset_bp'] = cfg['be_offset_bp']
    x['fill_close_exit'] = False
    x['risk'] = x.entry
    x['reclaim_ref'] = np.nan
    frame = p.copy(); frame.index = frame.index+pd.Timedelta(minutes=job['execution_minutes'])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0, target_from_entry=True,
                        intrabar_path=intrabar, reclaim_next_bar=True, breakeven_includes_cost=False,
                        fill_maker_fee=ccfg['maker_fee'], fill_taker_fee=ccfg['taker_fee'], monotone_segments=True,
                        reclaim_backstop=True)
    res, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                               x.drop(columns=['event', 'population', 'exit_mode']), policy, tick=tick,
                               symbol=symbol.removesuffix('USDT'), execution_minutes=job['execution_minutes'],
                               entry_order_mode='limit', batch_bars=ccfg['streaming_batch_bars'])
    res = res[[k for k in KEEP if k in res]]
    res['variant'] = res.trade_id.str.rsplit(':v_', n=1).str[-1]
    res.to_csv(out/'trades.csv.gz', index=False)
    done = dict(symbol=symbol, intrabar=intrabar, orders=len(x), signals=int((x.trade_id.str.endswith(':v_sig')).sum()),
                positions=audit['positions'], fills=audit['fills'], engine=audit.get('engine'), version=audit.get('version'),
                seconds=time.perf_counter()-t0)
    dump(out/'done.json', done)
    return done


def window_table(cfg):
    w = pd.read_csv(ROOT/cfg['source_experiment']/'alarm_windows.csv.gz')
    w = w[(w.window == cfg['window_min']) & (w.threshold == cfg['threshold'])]
    return w.drop_duplicates('event').set_index('event')


def run(config, workers, intrabar):
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_alarm_random_control.py', 'yoyo/layers/l3_backtest/nautilus_independent.py',
              str(config.resolve().relative_to(ROOT)))
    win = window_table(cfg)
    syms = sorted({e.split(':')[0] for e in win.index})
    root = exp/intrabar.lower()
    todo = [s for s in syms if not (root/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as ex:
        futs = {ex.submit(process, s, intrabar, str(exp), cfg, win[win.index.str.startswith(s+':')]): s for s in todo}
        for k, fu in enumerate(as_completed(futs), 1):
            try:
                r = fu.result()
            except Exception as err:  # keep going; record the failure per symbol
                r = dict(symbol=futs[fu], error=repr(err))
                (root/'symbols'/futs[fu]).mkdir(parents=True, exist_ok=True); dump(root/'symbols'/futs[fu]/'error.json', r)
            print(json.dumps(dict(progress=k, total=len(todo), **r)), flush=True)


def parity(exp, src, syms):
    """Signal rows of this run against the 10-29 near30 hold fills, per path."""
    out = {}
    for p in ('ohlc', 'olhc'):
        diffs, missing, reason = [], 0, 0
        for s in syms:
            new, old = exp/p/'symbols'/s/'trades.csv.gz', src/p/'symbols'/s/'trades.csv.gz'
            if not (new.exists() and old.exists()):
                continue
            n = pd.read_csv(new); o = pd.read_csv(old)
            n = n[n.variant == 'sig'].assign(base=lambda y: y.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base')
            o = o[o.variant == 'near30_hold'].assign(base=lambda y: y.trade_id.str.rsplit(':v_', n=1).str[0]).set_index('base')
            o = o.reindex(n.index)
            missing += int(o.net_notional_bp.isna().sum())
            ok = o.net_notional_bp.notna()
            diffs.append((n.net_notional_bp[ok]-o.net_notional_bp[ok]).abs())
            reason += int((n.reason[ok] != o.reason[ok]).sum())
        d = pd.concat(diffs) if diffs else pd.Series(dtype=float)
        out[p] = dict(rows=int(len(d)), missing_in_old=missing, reason_mismatch=reason, max_abs_net_bp=float(d.max()) if len(d) else None)
    return out


def load(args):
    """As monday_limit_alarm_entry.load_symbol (worse path, slippage, fill-bar entry time, MAE), keeping the trade id."""
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
    return symbol, d.rename_axis('trade_id').reset_index(), issues


def summarize(config, workers):
    from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
    from yoyo.evaluation.monday_limit_paper_accounts import replay, in_period, PERIODS
    exp = config.resolve().parent
    cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/monday_limit_alarm_random_control.py')
    A = ROOT/'experiments/active'
    ro = json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh = json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py = json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    win = window_table(cfg)
    syms = sorted({e.split(':')[0] for e in win.index})
    missing = [(p, s) for p in ('ohlc', 'olhc') for s in syms if not (exp/p/'symbols'/s/'done.json').exists()]
    if missing:
        raise RuntimeError(f'Incomplete run: {len(missing)}')
    with ProcessPoolExecutor(workers) as ex:
        got = list(ex.map(load, [(str(exp), s, cfg['slippage_bp']) for s in syms], chunksize=4))
    meta = pd.concat([pd.read_csv(exp/'ohlc'/'symbols'/s/'orders.csv.gz') for s in syms
                      if (exp/'ohlc'/'symbols'/s/'orders.csv.gz').exists()], ignore_index=True)
    meta = meta[~meta.event.isin(BAD_EVENTS)]
    t = pd.concat([g[1] for g in got if len(g[1])], ignore_index=True)
    t = t[~t.event.isin(BAD_EVENTS)].merge(meta[['trade_id', 'offset', 'bucket', 'sig_bucket', 'alarm_ms']], on='trade_id', how='left')
    sig, ctl = t[t.variant == 'sig'], t[t.variant != 'sig']
    window = pd.Timestamp(json.loads((A/'exp-monday-limit-reclaim-recent-20261025-v1/summary.json').read_text())['results']['window'][0])
    sub = meta[meta.status == 'submitted']
    res = dict(checks=dict(symbols=len(syms), issues=[f'{g[0]}: {i}' for g in got for i in g[2]][:20], rows=int(len(t)),
                           parity_vs_20261029=parity(exp, ROOT/cfg['source_experiment'], syms),
                           orders={k: int(v) for k, v in meta.status.value_counts().items()},
                           signals_filled=int(len(sig)), controls_submitted=int(len(sub)), controls_filled=int(len(ctl))))

    def stats(x):
        return dict(n=int(len(x)), mean_bp=float(x.net.mean()) if len(x) else None, win_pct=float(100*(x.net > 1).mean()) if len(x) else None,
                    target_pct=float(100*(x.reason == 4).mean()) if len(x) else None)

    # Per event: the signal against the mean of its filled controls; the signal's fill time sets the period.
    sm = sig.set_index('event')
    def per_event(c):
        ev = pd.DataFrame(dict(sig=sm.net, ctl=c.groupby('event').net.mean(), n_ctl=c.groupby('event').size(),
                               alarm_ms=sm.alarm_ms, entry_t=sm.entry_t)).dropna(subset=['sig', 'ctl'])
        ev['diff'] = ev.sig-ev.ctl
        return ev
    rng = np.random.default_rng(cfg['seed'])

    def episode_ci(ev):
        ep = ev.groupby('alarm_ms')['diff'].mean().to_numpy()
        if not len(ep):
            return dict(episodes=0)
        boot = rng.choice(ep, (cfg['bootstrap'], len(ep)), replace=True).mean(axis=1)
        return dict(episodes=int(len(ep)), mean_bp=float(ep.mean()), positive_pct=float(100*(ep > 0).mean()),
                    ci95=[float(np.quantile(boot, .025)), float(np.quantile(boot, .975))])
    res['trades'], res['events'], res['episodes'], res['bucket_matched'] = {}, {}, {}, {}
    ev_all = per_event(ctl)
    ev_b = per_event(ctl[ctl.bucket == ctl.sig_bucket])
    for per in PERIODS:
        res['trades'][per] = dict(signal=stats(sig[in_period(sig, per, window)]), control=stats(ctl[in_period(ctl, per, window)]))
        for name, ev, out in (('all', ev_all, res['events']), ('bucket', ev_b, res['bucket_matched'])):
            e = ev[in_period(ev, per, window)]
            out[per] = dict(events=int(len(e)), signal_bp=float(e.sig.mean()) if len(e) else None,
                            control_bp=float(e.ctl.mean()) if len(e) else None, diff_bp=float(e['diff'].mean()) if len(e) else None,
                            **({'episode': episode_ci(e)} if name == 'all' else {}))
    # Control value per submitted order, unfilled = 0.
    res['control_per_submitted_bp'] = float(ctl.net.sum()/max(len(sub), 1))
    res['control_by_offset'] = {str(k): dict(n=int(len(g)), mean_bp=float(g.net.mean())) for k, g in ctl.groupby('offset')}
    jobs = [('signal', 'frac', sig.reset_index(drop=True), window, cfg, ro, sh, py)]
    for r in range(cfg['control_portfolios']):
        pick = sub.groupby('event').trade_id.agg(lambda v: v.to_numpy()[rng.integers(len(v))])
        jobs.append((f'control_{r}', 'frac', ctl[ctl.trade_id.isin(pick.to_numpy())].reset_index(drop=True), window, cfg, ro, sh, py))
    res['plans'] = {}
    with ProcessPoolExecutor(workers) as ex:
        for name, out in ex.map(replay, jobs):
            res['plans'][name] = out
    res = clean(res)
    dump(exp/'summary.json', dict(config=cfg, results=res, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                 training_eligible=False, production_eligible=False))
    print(json.dumps(res['checks'], ensure_ascii=False))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--config', type=Path, required=True); ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--intrabar', choices=['OHLC', 'OLHC']); ap.add_argument('--summary', action='store_true')
    a = ap.parse_args()
    summarize(a.config, a.workers) if a.summary else run(a.config, a.workers, a.intrabar)
