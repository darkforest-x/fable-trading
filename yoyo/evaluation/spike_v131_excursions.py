"""Describe frozen V13.1 trades using only prices reached before actual exit.

Owner 2026-10-19 asks for order distributions and open losses/profits. No entry,
exit or cost changes. Read registered OHLCV history; rebuild complete chart bars
and ATR14 exactly as the prior exit study. Intrabar stop bars have unknown wick
order: MAE ends at the stop fill, MFE has a known lower and full-wick upper bound.
The 1H trail2 archive stores the incumbent exit timestamp, so replay protection
to recover the actual earlier exit before measuring excursions or duration.
These are outcome diagnostics, never admission features. No content hashing.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import _smma
from yoyo.evaluation.spike_exit_giveback import simulate
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v131_symbol_summary import CONFIG as SUMMARY_CONFIG, load, metrics, paired_metrics

EXP = Path('experiments/active/exp-spike-v131-excursions-20261019-v1')
PARENT = Path('experiments/active/exp-spike-v128-entry-clock-long-20260923-v1/retest_v1/streams')
EXTRA = Path('experiments/active/exp-spike-v131-30m4h-bottom20-20261017-v1/results_v1')
MANIFEST = Path('data/research/spike_v128_entry_clock_long_20260923/manifest.json')
EPS = 1e-9


def trace(*, side, entry, stop0, tick, o, h, l, c, atr, e, x, opposite, trail):
    """Recover exit and bounded gross excursions, with old protection first."""
    risk = side * (entry - stop0)
    assert risk > 0 and x >= e
    stop, armed, lo, hi, peak_close = stop0, False, 0.0, 0.0, 0.0
    for j in range(e, x + 1):
        r_open = side * (o[j] - entry) / risk
        lo, hi = min(lo, r_open), max(hi, r_open)
        gap_stop = side * (o[j] - stop) <= 0
        stop_hit = l[j] <= stop if side == 1 else h[j] >= stop
        if gap_stop or (j == x and opposite) or stop_hit:
            price = float(o[j]) if gap_stop or (j == x and opposite) else stop
            r_exit = side * (price - entry) / risk
            lo, hi = min(lo, r_exit), max(hi, r_exit)
            intrabar = not gap_stop and not (j == x and opposite)
            upper = max(hi, side * ((h[j] if side == 1 else l[j]) - entry) / risk) if intrabar else hi
            return dict(actual_exit_i=j, actual_exit_price=price, mae_r=-lo,
                        mfe_lower_r=hi, mfe_upper_r=upper, peak_close_r=peak_close,
                        stop_bar_ambiguous=upper > hi + EPS,
                        actual_exit_kind='gap_stop' if gap_stop else 'opposite' if opposite and j == x else 'stop',
                        first_bar_mae_r=first_mae if j > e else -lo,
                        first_bar_mfe_lower_r=first_mfe if j > e else hi)
        favorable = side * ((h[j] if side == 1 else l[j]) - entry) / risk
        adverse = side * ((l[j] if side == 1 else h[j]) - entry) / risk
        hi, lo = max(hi, favorable), min(lo, adverse)
        if j == e:
            first_mae, first_mfe = -lo, hi
        r_close = side * (c[j] - entry) / risk
        peak_close = max(peak_close, r_close)
        armed = armed or r_close >= 2.0
        if armed and math.isfinite(atr[j]) and atr[j] > 0:
            raw = c[j] - side * trail * atr[j]
            new = (math.floor(raw / tick) if side == 1 else math.ceil(raw / tick)) * tick
            stop = max(stop, new) if side == 1 else min(stop, new)
    raise ValueError('recorded exit not reproduced')


def provenance(path):
    p = Path(path); st = p.stat()
    return dict(path=str(p), realpath=str(p.resolve()), bytes=st.st_size, mtime_ns=st.st_mtime_ns)


def worker(args):
    symbol, source, selected, tick = args
    before = provenance(source)
    raw = pd.read_csv(source, usecols=['ts', 'open', 'high', 'low', 'close', 'volume'])
    assert raw.ts.is_monotonic_increasing and not raw.ts.duplicated().any()
    base = raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(), unit='ms', utc=True))
    base = base[(base.index >= pd.Timestamp('2022-11-01T00:00:00Z')) &
                (base.index + pd.Timedelta(minutes=5) <= pd.Timestamp('2026-09-23T04:00:00Z'))]
    out = []
    for minutes, targets in selected.groupby('timeframe_min'):
        bars, _ = complete_bars(base, int(minutes))
        prev = bars.close.shift()
        tr = pd.concat([bars.high-bars.low, (bars.high-prev).abs(), (bars.low-prev).abs()], axis=1).max(axis=1)
        bars['atr'] = _smma(tr, 14)
        arrays = {k: bars[k].to_numpy(float) for k in ['open', 'high', 'low', 'close', 'atr']}
        loc = {str(t): i for i,t in enumerate(bars.index)}
        if minutes in (15, 60):
            archived = pd.read_csv(PARENT / f'{symbol}_{minutes}m' / 'serial_trades.csv.gz', low_memory=False)
            archived = archived[archived.arm.eq('v9_both') & archived.policy.eq('retest')].set_index('trade_key')
        for row in targets.to_dict('records'):
            old = archived.loc[row['trade_key']].to_dict() if minutes in (15, 60) else row
            entry, original_stop = float(old['entry_price']), float(old['initial_stop'])
            stop = round(original_stop / tick) * tick
            risk = int(row['side']) * (entry-stop)
            e, x = loc[str(pd.Timestamp(old['entry_time']))], loc[str(pd.Timestamp(old['exit_time']))]
            assert e == int(old['entry_i']) and x == int(old['exit_i'])
            assert np.all(np.diff(bars.index[e:x+1].asi8) == pd.Timedelta(minutes=int(minutes)).value)
            assert abs(entry-arrays['open'][e]) < max(tick*1e-6, entry*1e-12)
            trail = 2 if minutes == 60 else 4
            kwargs = dict(side=int(row['side']), entry=entry, stop0=stop, tick=tick,
                          o=arrays['open'], h=arrays['high'], l=arrays['low'], c=arrays['close'], atr=arrays['atr'], e=e, x=x)
            path = trace(**kwargs, opposite=str(old['exit_reason']).startswith('opposite'), trail=trail)
            gross = int(row['side']) * (path['actual_exit_price']/entry-1)
            net_r = (gross-.002)/(risk/entry)
            np.testing.assert_allclose(net_r, row['net_r'], atol=1e-8, rtol=0)
            old_exit_price = float(old.get('exit_price', entry*(1+int(row['side'])*old['gross_return'])))
            canonical = simulate('trail2' if minutes == 60 else 'incumbent', **kwargs,
                                 exit_price=old_exit_price, exit_reason=str(old['exit_reason']))
            np.testing.assert_allclose([net_r,path['peak_close_r']], [canonical['net_r'],canonical['peak_close_r']], atol=1e-8, rtol=0)
            assert canonical['parity'] == 'ok'
            if minutes != 60:
                assert path['actual_exit_i'] == x
            out.append({k: row[k] for k in ['trade_key','symbol','timeframe_min','side','entry_time','net_r']} |
                       dict(entry_price=entry, initial_risk=risk, cost_r=.002*entry/risk,
                            source_exit_time=str(bars.index[x]), actual_exit_time=str(bars.index[path['actual_exit_i']]),
                            held_bars=path['actual_exit_i']-e+1,
                            earlier_than_source_exit=path['actual_exit_i']<x,
                            mae_bp=path['mae_r']*risk/entry*10000,
                            mfe_lower_bp=path['mfe_lower_r']*risk/entry*10000) | path)
    assert before == provenance(source)
    return out, before


def describe(frame, pairs):
    n = len(frame); r = metrics(frame) | paired_metrics(pairs[pairs.trade_key.isin(frame.trade_key)], n)
    for key in ['mae_r', 'mfe_lower_r', 'mfe_upper_r', 'peak_close_r', 'held_bars', 'mae_bp', 'mfe_lower_bp']:
        r[key+'_quantiles'] = {str(q):float(frame[key].quantile(q)) for q in [.25,.5,.75,.9,.95,.99]}
    for threshold in [0,.25,.5,1,2,3,5,10]:
        tag = str(threshold)
        condition = (lambda a: a > EPS) if threshold == 0 else (lambda a: a >= threshold-EPS)
        for key in ['mae_r','mfe_lower_r','mfe_upper_r','peak_close_r']:
            r[key+'_at_'+tag] = int(condition(frame[key]).sum())
    r['both_loss_and_profit_known'] = int(((frame.mae_r>EPS)&(frame.mfe_lower_r>EPS)).sum())
    r['profit_unknown_zero_lower_positive_upper'] = int(((frame.mfe_lower_r<=EPS)&(frame.mfe_upper_r>EPS)).sum())
    r['first_bar_loss'] = int((frame.first_bar_mae_r>EPS).sum())
    r['first_bar_profit_known'] = int((frame.first_bar_mfe_lower_r>EPS).sum())
    r['winner_with_loss'] = int(((frame.net_r>0)&(frame.mae_r>EPS)).sum())
    r['loser_with_profit_known'] = int(((frame.net_r<0)&(frame.mfe_lower_r>EPS)).sum())
    r['closed_loss_after_1r_known'] = int(((frame.net_r<0)&(frame.mfe_lower_r>=1-EPS)).sum())
    r['closed_loss_after_2r_close'] = int(((frame.net_r<0)&(frame.peak_close_r>=2-EPS)).sum())
    r['intrabar_mfe_ambiguous'] = int(frame.stop_bar_ambiguous.sum())
    r['earlier_than_source_exit'] = int(frame.earlier_than_source_exit.sum())
    r['net_r_bins'] = {label:int(mask.sum()) for label,mask in {
        '<=-1':frame.net_r<=-1, '(-1,0]':(frame.net_r>-1)&(frame.net_r<=0),
        '(0,1)':(frame.net_r>0)&(frame.net_r<1), '[1,2)':(frame.net_r>=1)&(frame.net_r<2),
        '[2,5)':(frame.net_r>=2)&(frame.net_r<5), '>=5':frame.net_r>=5}.items()}
    return r


def run(output, workers):
    assert not output.exists(), 'refusing to overwrite results'
    subprocess.run(['git','diff','--exit-code','HEAD','--',__file__], check=True)
    subprocess.run(['git','ls-files','--error-unmatch',__file__], check=True, stdout=subprocess.DEVNULL)
    trades, pairs, audit, sources = load(json.loads(SUMMARY_CONFIG.read_text()))
    extra = pd.read_csv(EXTRA/'trades.csv.gz'); extra = extra[extra.status.eq('closed')]
    controls = pd.read_csv(EXTRA/'controls.csv.gz')
    controls = controls[controls.eligible.eq(True)]
    ep = extra[['trade_key','net_r']].merge(controls[['trade_key','control_net_r']],on='trade_key',validate='one_to_one')
    pairs = pd.concat([pairs,ep],ignore_index=True)
    trades = pd.concat([trades,extra],ignore_index=True)
    assert len(trades)==21547 and not trades.trade_key.duplicated().any()
    manifest = json.loads(MANIFEST.read_text()); bysym={r['symbol']:r['path'] for r in manifest['streams']}
    meta = json.loads(Path('data/kline_preholdout_binance_um5m/exchange_info.json').read_text())
    ticks = {r['symbol']:float(next(f['tickSize'] for f in r['filters'] if f['filterType']=='PRICE_FILTER')) for r in meta['symbols']}
    tasks = [(s,bysym[s],g,ticks[s]) for s,g in trades.groupby('symbol')]
    rows, inputs = [], []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(worker,t) for t in tasks]
        for i,f in enumerate(as_completed(futures),1):
            r,p=f.result();rows.extend(r);inputs.append(p)
            if i%50==0 or i==len(tasks):print(json.dumps(dict(done=i,total=len(tasks),trades=len(rows))),flush=True)
    frame = pd.DataFrame(rows).sort_values(['timeframe_min','symbol','entry_time'])
    assert len(frame)==len(trades) and set(frame.trade_key)==set(trades.trade_key)
    groups = []
    for minutes,g in frame.groupby('timeframe_min'):
        for side in [0,1,-1]:
            selected = g if side==0 else g[g.side.eq(side)]
            groups.append(dict(timeframe_min=int(minutes),side=side)|describe(selected,pairs))
    result = dict(generated_at=datetime.now(timezone.utc).isoformat(),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                  sources=[provenance(p) for p in sources+[EXTRA/'trades.csv.gz',EXTRA/'controls.csv.gz',MANIFEST]],
                  price_inputs=inputs, audit=audit|dict(all_net_r_and_close_peaks_match=True,closed_trades=len(frame)),groups=groups,
                  training_eligible=False,production_eligible=False,
                  definitions={'mae_r':'Maximum gross adverse price excursion through exit, nonnegative; includes exit fill, caps intrabar lows/highs at stop.',
                               'mfe_lower_r':'Known gross favorable excursion before exit; full previous bars, exit open and fill.',
                               'mfe_upper_r':'Also includes the entire intrabar stop-bar favorable wick; an upper bound only.',
                               'scope':'Original full universe, before retrospective bottom20 exclusions; 1H frozen entry replay, preserves 19 December2022 entries.',
                               'cost':'Original 20bp round trip for net R; excursions are gross price marks, not account leverage PnL.',
                               'controls':'Original matched random returns reused; excursion distributions are descriptive, no strategy-change or causal claim.'})
    output.mkdir(parents=True)
    frame.to_csv(output/'trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(dict(output=str(output),rows=len(frame)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=EXP/'results_v1');parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args();run(args.output,args.workers)
