"""Finite hedged account accounting over frozen Nautilus 1.231.0 executions.

The existing SPIKE/ChartArt account utilities require a single position and do
not model maintenance margin. This adapter retains their realized-loss sizing
contract but consumes the already-validated native entry/exit ledger; it never
generates signals or substitutes a new execution engine. Entry sizing sees only
entry price, stop distance, current equity and previously settled PnL. OHLC is
used solely for portfolio marks, with native O-H-L-C order and stops before close
exits/entries. Overlapping long/short positions remain independent.

Liquidation is a research screen: current OKX tiers applied retrospectively,
trade-price OHLC as a mark proxy, zero fees/funding/slippage, gross (not netted)
margin. These inputs cannot certify historical exchange non-liquidation.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp


def event_tape(frame, trades):
    """Audit native fills against O-H-L-C and emit marks and frozen fill events."""
    rows = trades.sort_values(['entry_i', 'trade_id']).to_dict('records')
    if len({r['trade_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate trade IDs')
    prices = frame[['open', 'high', 'low', 'close']].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError('Invalid OHLC')
    entries, occupied = {}, set()
    for r in rows:
        a, b = int(r['entry_i']), int(r['exit_i'])
        if not 0 <= a < b < len(frame) or r['side'] not in (-1, 1):
            raise ValueError('Invalid native trade interval/side')
        if r['reason'] not in (1, 7):
            raise ValueError('Only standalone initial stops and pivot closes are supported')
        if abs(prices[a, 3]-r['entry']) > 1e-6:
            raise ValueError('Entry is not the frozen close')
        if abs(r['side']*(r['entry']-r['stop'])-r['risk']) > 1e-6:
            raise ValueError('Invalid initial stop risk')
        actual_r = r['side']*(r['exit_price']-r['entry'])/r['risk']
        if abs(actual_r-r['gross_r']) > 1e-8:
            raise ValueError('Native gross R mismatch')
        if r['reason'] == 7 and abs(prices[b, 3]-r['exit_price']) > 1e-6:
            raise ValueError('Pivot exit is not the confirmation close')
        entries.setdefault(a, []).append(r)
        occupied.update(range(a, b+1))
    tape, active = [], {}
    def mark(i, p):
        if active:
            tape.append(('mark', i, float(p), None))
    def close(i, r, price):
        if i != r['exit_i'] or abs(price-r['exit_price']) > 1e-6:
            raise ValueError(f"Native stop path mismatch: {r['trade_id']} at {i}")
        tape.append(('exit', i, float(price), r))
        del active[r['trade_id']]
    for i in sorted(occupied):
        o, h, l, c = prices[i]
        mark(i, o)
        for r in list(active.values()):
            if r['side']*(o-r['stop']) <= 0:
                # Gap fills must be native open fills, not an invented stop fill.
                close(i, r, o)
        previous = o
        for endpoint in (h, l, c):
            hits = [r for r in active.values()
                    if r['side']*(endpoint-r['stop']) <= 0]
            hits.sort(key=lambda r: abs(r['stop']-previous))
            for r in hits:
                mark(i, r['stop'])
                close(i, r, r['stop'])
            mark(i, endpoint)
            previous = endpoint
        for r in list(active.values()):
            if r['exit_i'] == i:
                if r['reason'] != 7:
                    raise ValueError('A native stop did not cross its OHLC path')
                close(i, r, c)
        for r in entries.get(i, []):
            tape.append(('entry', i, float(c), r))
            active[r['trade_id']] = r
        mark(i, c)
    if active or sum(e[0] == 'exit' for e in tape) != len(rows):
        raise ValueError('Unclosed positions')
    return tape


def replay(tape, base_risks, tiers, *, leverage=100., multiplier=2.,
           initial_cash=1000., mark_stress=0., record=False):
    """Vectorized fixed-U risk schedules, shared cash, and causal loss resets.

    Every realized negative PnL doubles the *next entry's* requested stop risk;
    every positive PnL resets it. Concurrent entries see the same realized
    state. Rejections neither advance nor reset it. There is no doubling cap,
    resizing, cash replenishment or silent restart. Native fills are unchanged.
    Quantities round down to current OKX 0.0001 BTC lots. Margin applies the tier
    of gross BTC exposure to all exposure, conservatively foregoing hedge relief.
    Initial margin uses max(1/leverage, tier IMR); liquidation screen uses MMR.
    Mark stress is a common +/- basis on price, choosing the adverse portfolio
    margin cushion, not contradictory independent long/short marks.
    """
    base = np.asarray(base_risks, dtype=float)
    if (base.ndim != 1 or not len(base) or not np.isfinite(base).all()
            or (base <= 0).any() or not 0 < leverage <= 100
            or multiplier not in (1., 2.) or initial_cash <= 0
            or not 0 <= mark_stress < 1 or (record and len(base) != 1)):
        raise ValueError('Invalid account settings')
    n = len(base)
    bounds = np.array([float(t['maxSz'])*.01 for t in tiers])
    imrs = np.array([float(t['imr']) for t in tiers] + [np.inf])
    mmrs = np.array([float(t['mmr']) for t in tiers] + [np.inf])
    if not np.all(np.diff(bounds) > 0) or (imrs[:-1] < mmrs[:-1]).any():
        raise ValueError('Invalid tiers')
    cash = np.full(n, initial_cash, dtype=float)
    peak = cash.copy(); minimum = cash.copy(); dd = np.zeros(n)
    level = np.zeros(n, int); max_level = level.copy()
    accepted = level.copy(); rejected = level.copy(); wins = level.copy()
    liquidated = np.zeros(n, bool); first_breach = np.full(n, -1, int)
    max_risk = np.zeros(n); max_notional = np.zeros(n)
    max_exposure = np.zeros(n); min_margin_ratio = np.full(n, np.inf)
    active, ledger, curve = {}, [], []

    def portfolio(price):
        qty = np.zeros(n); signed = np.zeros(n); entry_value = np.zeros(n)
        for r, q in active.values():
            qty += q
            signed += r['side']*q
            entry_value += r['side']*q*r['entry']
        return qty, signed, cash + signed*price-entry_value

    def observe(i, price):
        nonlocal peak, minimum, dd, liquidated, max_exposure, min_margin_ratio
        q, signed, eq = portfolio(price)
        tier = np.searchsorted(bounds, q-1e-10)
        mmr = mmrs[tier]
        # The smaller equity-minus-maintenance endpoint is adverse because
        # this expression is affine between position changes.
        stress_sign = np.where(signed-q*mmr >= 0, -1., 1.)
        stressed_price = price*(1 + stress_sign*mark_stress)
        stressed_eq = eq + signed*(stressed_price-price)
        maintenance = np.where(q > 0, q*stressed_price*mmr, 0.)
        live = ~liquidated
        peak = np.where(live, np.maximum(peak, stressed_eq), peak)
        minimum = np.where(live, np.minimum(minimum, stressed_eq), minimum)
        dd = np.where(live, np.maximum(dd, (peak-stressed_eq)/peak), dd)
        ratio = np.divide(stressed_eq, maintenance, out=np.full(n, np.inf), where=maintenance>0)
        min_margin_ratio = np.where(live, np.minimum(min_margin_ratio, ratio), min_margin_ratio)
        exposure = np.divide(q*price, eq, out=np.full(n, np.inf), where=eq>0)
        max_exposure = np.where(live, np.maximum(max_exposure, exposure), max_exposure)
        breach = live & ((stressed_eq <= maintenance) | (stressed_eq <= 0))
        first_breach[breach] = i
        liquidated |= breach
        if record:
            curve.append(dict(bar_i=i, price=price, equity=float(stressed_eq[0]),
                maintenance=float(maintenance[0]), margin_ratio=float(ratio[0]),
                cash=float(cash[0]), level=int(level[0]), liquidated=bool(liquidated[0])))

    for kind, i, price, r in tape:
        if kind == 'mark':
            observe(i, price)
        elif kind == 'entry':
            planned = base * multiplier**level
            q = np.floor(planned/r['risk']/.0001 + 1e-9)*.0001
            old_q, _, eq = portfolio(price)
            tier = np.searchsorted(bounds, old_q+q-1e-10)
            initial_margin = (old_q+q)*price*np.maximum(1/leverage, imrs[tier])
            ok = (~liquidated) & (q > 0) & (q <= 350.) & (initial_margin <= eq+1e-9)
            rejected += (~liquidated & ~ok)
            accepted += ok
            max_level = np.maximum(max_level, np.where(ok, level, 0))
            max_risk = np.maximum(max_risk, np.where(ok, q*r['risk'], 0))
            max_notional = np.maximum(max_notional, np.where(ok, q*price, 0))
            q = np.where(ok, q, 0.)
            active[r['trade_id']] = (r, q)
            if record:
                ledger.append(dict(trade_id=r['trade_id'], action='entry', bar_i=i,
                    accepted=bool(ok[0]), level=int(level[0]), planned_risk=float(planned[0]),
                    quantity=float(q[0]), initial_margin=float(initial_margin[0]), equity=float(eq[0])))
            observe(i, price)
        else:
            _, q = active.pop(r['trade_id'])
            pnl = q*r['side']*(price-r['entry'])
            live = ~liquidated
            cash += np.where(live, pnl, 0.)
            wins += live & (pnl > 1e-10)
            level = np.where(live & (pnl < -1e-10), level+1,
                             np.where(live & (pnl > 1e-10), 0, level))
            if record:
                ledger.append(dict(trade_id=r['trade_id'], action='exit', bar_i=i,
                    quantity=float(q[0]), pnl=float(pnl[0]), cash=float(cash[0]), level=int(level[0])))
            observe(i, price)
    if active:
        raise AssertionError('Replay ended with open positions')
    result = pd.DataFrame(dict(base_risk=base, leverage=leverage, multiplier=multiplier,
        mark_stress=mark_stress, ending_equity=np.where(liquidated, np.nan, cash),
        net_profit=np.where(liquidated, np.nan, cash-initial_cash), accepted=accepted,
        rejected=rejected, wins=wins, liquidated=liquidated, first_breach_i=first_breach,
        max_drawdown=dd, minimum_equity=minimum, max_level=max_level,
        max_risk=max_risk, max_notional=max_notional, max_exposure=max_exposure,
        min_margin_ratio=min_margin_ratio))
    return result, pd.DataFrame(ledger), pd.DataFrame(curve)


def main(config_path):
    cfg = json.loads(Path(config_path).read_text())
    dest = ROOT/cfg['output']
    builder = Path(__file__).relative_to(ROOT)
    if subprocess.run(['git','diff','--quiet','HEAD','--',str(builder)],cwd=ROOT).returncode:
        raise RuntimeError('Commit account builder before running')
    if (dest/'summary.json').exists():
        print('Reuse '+str(dest/'summary.json')); return
    source, candles = ROOT/cfg['trades'], ROOT/cfg['candles']
    trades = pd.read_csv(source)
    frame = pd.read_csv(candles)
    tiers = json.loads((dest/'okx_tiers.json').read_text())['data']
    bases = np.unique(np.round(np.r_[np.arange(.01,5.01,.01),np.arange(5.1,1000.01,.1)],2))
    summaries = []
    for years in (5,2,1):
        start = pd.Timestamp(cfg['end']) - pd.DateOffset(years=years)
        end = pd.Timestamp(cfg['end'])
        signal = trades[(trades.population=='signal') & (pd.to_datetime(trades.entry_time,utc=True)>=start)]
        control = trades[(trades.population=='random') & trades.pair_id.isin(signal.pair_id)]
        for name, subset in [('signal',signal),('random',control)]:
            times = pd.to_datetime(subset.entry_time,utc=True)
            exits = pd.to_datetime(subset.exit_time,utc=True)
            if not ((times>=start)&(exits<=end)).all():
                raise ValueError('Matched cohort crosses account window')
        tape = event_tape(frame, signal)
        random_tape = event_tape(frame, control)
        screen,_,_ = replay(tape,bases,tiers)
        screen.to_csv(dest/f'{years}y_search.csv.gz',index=False)
        alive = screen[~screen.liquidated]
        full = alive[(alive.accepted==len(signal)) & alive.rejected.eq(0)]
        if full.empty:
            raise RuntimeError('No full-stream survivor; do not report skipped-only success')
        selected = [('full_stream_best',full.sort_values('ending_equity',ascending=False).iloc[0]),
                    ('skips_allowed_best',alive.sort_values('ending_equity',ascending=False).iloc[0])]
        for kind, row in selected:
            base = float(row.base_risk)
            result, ledger, curve = replay(tape,[base],tiers,record=True)
            ledger.to_csv(dest/f'{years}y_{kind}_ledger.csv',index=False)
            curve.to_csv(dest/f'{years}y_{kind}_marks.csv.gz',index=False)
            controls,_,_ = replay(random_tape,[base],tiers)
            baseline,_,_ = replay(tape,[base],tiers,multiplier=1.)
            stressed,_,_ = replay(tape,[base],tiers,mark_stress=.005)
            levs = pd.concat([replay(tape,[base],tiers,leverage=x)[0] for x in cfg['leverage_checks']],ignore_index=True)
            levs.to_csv(dest/f'{years}y_{kind}_leverage.csv',index=False)
            summaries.append(dict(years=years,kind=kind,start=start.isoformat(),end=end.isoformat(),
                candidates=len(signal),signal=result.iloc[0].to_dict(),random=controls.iloc[0].to_dict(),
                same_base_no_doubling=baseline.iloc[0].to_dict(),mark_basis_stress_50bp=stressed.iloc[0].to_dict()))
        print(json.dumps(summaries[-2:],ensure_ascii=False),flush=True)
    # Replace non-finite numbers with JSON null; a breached account has no claimed final balance.
    clean = json.loads(pd.Series(summaries).to_json(orient='values'))
    dump(dest/'summary.json',dict(rows=clean,config=cfg,grid_size=len(bases),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs=[file_stamp(p) for p in (source,candles,dest/'okx_tiers.json')],
        execution='Frozen NautilusTrader 1.231.0 fills; new hedged cash/margin accounting only.',
        limitations='Ex-post grid maxima, not out-of-sample optimization. Current tiers, gross margin and trade OHLC proxy; no historical mark prices or tier changes. No fees, funding, spread, latency or slippage. No live-liquidation guarantee.'))


def leverage_search(config_path):
    """Reuse the 100x search and search lower ceilings without altering old output."""
    cfg = json.loads(Path(config_path).read_text())
    dest = ROOT/cfg['output']
    builder = str(Path(__file__).relative_to(ROOT))
    if subprocess.run(['git','diff','--quiet','HEAD','--',builder],cwd=ROOT).returncode:
        raise RuntimeError('Commit accounting builder first')
    output = dest/'leverage_search_summary.json'
    if output.exists():
        print('Reuse '+str(output)); return
    trades = pd.read_csv(ROOT/cfg['trades'])
    frame = pd.read_csv(ROOT/cfg['candles'])
    tiers = json.loads((dest/'okx_tiers.json').read_text())['data']
    results = []
    for years in (5,2,1):
        start = pd.Timestamp(cfg['end']) - pd.DateOffset(years=years)
        signal = trades[(trades.population=='signal') & (pd.to_datetime(trades.entry_time,utc=True)>=start)]
        random = trades[(trades.population=='random') & trades.pair_id.isin(signal.pair_id)]
        tape, rtape = event_tape(frame,signal), event_tape(frame,random)
        high = pd.read_csv(dest/f'{years}y_search.csv.gz')
        base = high.base_risk.to_numpy()
        searches = [high]
        for leverage in cfg['leverage_checks']:
            if leverage == 100:
                continue
            path = dest/f'{years}y_leverage{leverage}_search.csv.gz'
            if path.exists():
                screen = pd.read_csv(path)
            else:
                screen,_,_ = replay(tape,base,tiers,leverage=leverage)
                screen.to_csv(path,index=False)
            searches.append(screen)
        all_runs = pd.concat(searches,ignore_index=True)
        alive = all_runs[~all_runs.liquidated]
        full = alive[(alive.accepted==len(signal)) & alive.rejected.eq(0)]
        max_full_base = float(full.base_risk.max())
        stress,_,_ = replay(tape,base[base<=max_full_base],tiers,mark_stress=.005)
        stress.to_csv(dest/f'{years}y_full_50bp_search.csv',index=False)
        stress_full = stress[(~stress.liquidated)&(stress.accepted==len(signal))&stress.rejected.eq(0)]
        for name, cohort in [('full_stream_best',full),('skips_allowed_best',alive),
                             ('full_stream_50bp_stress_best',stress_full)]:
            row = cohort.sort_values(['ending_equity','leverage'],ascending=[False,True]).iloc[0]
            kwargs = dict(leverage=float(row.leverage),mark_stress=float(row.mark_stress))
            s,l,c = replay(tape,[float(row.base_risk)],tiers,record=True,**kwargs)
            r,_,_ = replay(rtape,[float(row.base_risk)],tiers,**kwargs)
            fixed,_,_ = replay(tape,[float(row.base_risk)],tiers,multiplier=1.,**kwargs)
            l.to_csv(dest/f'{years}y_all_leverages_{name}_ledger.csv',index=False)
            c.to_csv(dest/f'{years}y_all_leverages_{name}_marks.csv.gz',index=False)
            results.append(dict(years=years,kind=name,candidates=len(signal),signal=s.iloc[0].to_dict(),
                random=r.iloc[0].to_dict(),same_base_no_doubling=fixed.iloc[0].to_dict()))
        print(json.dumps(results[-3:],ensure_ascii=False),flush=True)
    clean = json.loads(pd.Series(results).to_json(orient='values'))
    dump(output,dict(rows=clean,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        search_config=dict(base_risks=len(base),leverage_caps=cfg['leverage_checks'],horizons=[5,2,1]),
        limitations='Same frozen native executions and current-tier/trade-mark limitations as summary.json. Highest observed grid result only. Each horizon optimized separately after observing history.'))


if __name__ == '__main__':
    import sys
    (leverage_search if '--leverage-search' in sys.argv else main)(sys.argv[1])
