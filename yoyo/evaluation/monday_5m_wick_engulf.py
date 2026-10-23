"""Owner 2026-10-23: adjacent 5m wick rejection + body engulf at Monday1618.

Monday levels use completed NY Monday OHLC. Long: red pin low<=lower1618,
both body endpoints>=level, nonzero lower wick; immediately next green body
engulfs the red body (inclusive endpoints, at least one strictly beyond).
Short mirrors this. No wick/body-ratio, first-touch or first-per-week filter.
Entry is the next observed contiguous 5m OPEN, SL exact pin extreme, TP near
Monday edge. No holding timer; end-of-data/gaps are censored, not wins/losses.
Features: ATR14 uses OHLC through the prior bar only; matching uses symbol,
NY week, side and fixed log2(ATR14/open)*4 volatility bins, identical fractional
barriers. No fitted model, AUC/ranking/top-decile metrics are inapplicable.

Reuse Nautilus1.231.0 run_native, including its independent limit FillModel.
An observed OPEN point followed by the actual OHLC bar permits next-open
market entry through the existing close-entry API. This is an execution event,
NOT fabricated lower-timeframe data. No management features consume these
points. Offline first-barrier search only bounds replay windows; every bar
while a position can be open is retained, and EVERY native result is checked
against the independent static-path oracle. All fills come from Nautilus.

HistData source timestamps are fixed EST (UTC-5), separate from NY sessions:
https://www.histdata.com/f-a-q/ . Bid-only XAU has no verified spread/fees;
report its gross returns and unknown net. Crypto uses owner-approved current
OKX fee scenario on Binance prices, not claimed as actual OKX executions.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import timezone, timedelta
import json
from pathlib import Path
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

EXP = ROOT/'experiments/active/exp-monday-5m-wick-engulf-20261023-v1'
OHLC = ['open', 'high', 'low', 'close']
STEP = pd.Timedelta(minutes=5)
TZ = 'America/New_York'
CODE = ['yoyo/evaluation/monday_5m_wick_engulf.py',
        'tests/evaluation/test_monday_5m_wick_engulf.py']


def histdata_index(values):
    return pd.DatetimeIndex(pd.to_datetime(values, format='%Y%m%d %H%M%S')).tz_localize(
        timezone(timedelta(hours=-5))).tz_convert('UTC')


def load_gold(paths):
    parts = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            name = next(n for n in archive.namelist() if n.lower().endswith('.csv'))
            with archive.open(name) as handle:
                raw = pd.read_csv(handle, sep=';', header=None,
                                  names=['time', *OHLC, 'volume'])
        raw.index = histdata_index(raw.time)
        parts.append(raw[OHLC].astype(float))
    m = pd.concat(parts).sort_index()
    dup = m[m.index.duplicated(keep=False)]
    if len(dup) and dup.groupby(level=0).nunique().gt(1).any().any():
        raise ValueError('Conflicting HistData overlapping minutes')
    m = m[~m.index.duplicated()]
    group = m.resample('5min', closed='left', label='left')
    f = group.agg(dict(open='first', high='max', low='min', close='last')).dropna()
    f['count'] = group.close.count().reindex(f.index)
    f.index = f.index.as_unit('ns')
    return f


def load_crypto(paths):
    parts = []
    for path in paths:
        f = pd.read_csv(path, usecols=['ts', *OHLC], float_precision='round_trip')
        f.index = pd.DatetimeIndex(pd.to_datetime(f.pop('ts'), unit='ms', utc=True))
        parts.append(f)
    f = pd.concat(parts).sort_index()
    dup = f[f.index.duplicated(keep=False)]
    if len(dup) and dup.groupby(level=0).nunique().gt(1).any().any():
        raise ValueError('Conflicting crypto overlapping bars')
    f = f[~f.index.duplicated()].copy(); f['count'] = 5
    f.index = f.index.as_unit('ns')
    return f


def signal_mask(f, level, side):
    """Only current confirmation and immediately previous completed pin OHLC."""
    p = f.shift(1)
    if side == 1:
        pin = (p.close < p.open) & (p.low <= level) & (p.close >= level) & (p.low < p.close)
        engulf = (f.close > f.open) & (f.open <= p.close) & (f.close >= p.open)
        strict = (f.open < p.close) | (f.close > p.open)
    else:
        pin = (p.close > p.open) & (p.high >= level) & (p.close <= level) & (p.high > p.close)
        engulf = (f.close < f.open) & (f.open >= p.close) & (f.close <= p.open)
        strict = (f.open > p.close) | (f.close < p.open)
    adjacent = f.index.to_series().diff().eq(STEP)
    return pin & engulf & strict & adjacent & f['count'].eq(5) & p['count'].eq(5)


def candidates(f, symbol, cfg):
    """Freeze per-trade geometry before reading any post-entry price."""
    local = f.index.tz_convert(TZ)
    dates = local.normalize()
    # Date arithmetic in local wall time avoids the DST Sunday 23/25h shift.
    week = (local.tz_localize(None).normalize()-pd.to_timedelta(local.dayofweek, unit='D')).strftime('%Y-%m-%d')
    monday = pd.DataFrame({'week':week, 'mon':local.dayofweek == 0,
                          'high':f.high.to_numpy(), 'low':f.low.to_numpy()})
    anchors = monday[monday.mon].groupby('week').agg(high=('high','max'), low=('low','min'), n=('mon','size'))
    anchors = anchors[anchors.n.ge(240 if symbol == 'XAUUSD' else 288)]
    hi = pd.Series(week, index=f.index).map(anchors.high)
    lo = pd.Series(week, index=f.index).map(anchors.low)
    tr = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(),
                    (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    vol = tr.rolling(14, min_periods=14).mean().shift(1)/f.open
    bucket = np.floor(np.log2(vol.where(vol > 0))*4).fillna(-999).to_numpy(int)
    good_time = local.dayofweek >= 1
    contiguous_next = (f.index.to_series().shift(-1)-f.index.to_series()).eq(STEP)
    rows = []; rejected = 0
    for side in (1,-1):
        level = lo-.618*(hi-lo) if side == 1 else hi+.618*(hi-lo)
        mask = signal_mask(f, level, side) & good_time & hi.notna() & (hi > lo) & contiguous_next
        # Pin and confirmation must both follow the completed Monday.
        mask &= np.r_[False, local.dayofweek[:-1] >= 1]
        for j in np.flatnonzero(mask.to_numpy()):
            i = j+1; entry = float(f.open.iloc[i])
            stop = float(f.low.iloc[j-1] if side == 1 else f.high.iloc[j-1])
            target = float(lo.iloc[j] if side == 1 else hi.iloc[j])
            risk = side*(entry-stop)
            if risk <= 0 or side*(target-entry) <= 0 or bucket[i] == -999:
                rejected += 1; continue
            pair = f'{symbol}-{i}-{side}'
            rows.append(dict(trade_id=pair, pair_id=pair, population='signal', symbol=symbol,
                entry_i=i, pin_i=j-1, confirm_i=j, side=side, entry=entry, stop=stop,
                target_price=target, risk=risk, level=float(level.iloc[j]),
                week=week[j], bucket=int(bucket[i]), entry_time=str(f.index[i]),
                segment=int(f.index[i] >= pd.Timestamp(cfg['split'])),
                monday_high=float(hi.iloc[j]), monday_low=float(lo.iloc[j])))
    signals = pd.DataFrame(rows)
    if signals.empty: return signals, dict(valid_mondays=len(anchors), rejected_geometry=rejected, unmatched=0)
    rng = np.random.default_rng(cfg['seed'])
    controls = []; unmatched = 0
    eligible = (local.dayofweek >= 1) & (bucket != -999) & np.r_[False, np.diff(f.index.asi8) == STEP.value]
    pools = {}
    for i in np.flatnonzero(eligible): pools.setdefault((week[i],bucket[i]), []).append(i)
    for r in signals.itertuples():
        pool = np.array(pools.get((r.week,r.bucket), []), dtype=int)
        pool = pool[pool != r.entry_i]
        if len(pool) < cfg['controls_per_signal']:
            unmatched += 1; continue
        for k,i in enumerate(rng.choice(pool, cfg['controls_per_signal'], replace=False)):
            entry = float(f.open.iloc[i]); scale = entry/r.entry
            controls.append(dict(trade_id=f'{r.pair_id}-c{k}', pair_id=r.pair_id,
                population='random', symbol=symbol, entry_i=int(i), side=r.side,
                entry=entry, stop=entry-r.side*r.risk*scale,
                target_price=entry+(r.target_price-r.entry)*scale,
                risk=r.risk*scale, week=r.week, bucket=r.bucket, entry_time=str(f.index[i]),
                segment=int(f.index[i] >= pd.Timestamp(cfg['split']))))
    return pd.concat([signals,pd.DataFrame(controls)],ignore_index=True), dict(
        valid_mondays=len(anchors),rejected_geometry=rejected,unmatched=unmatched)


def barrier_bounds(prices, entries, ends):
    """Static offline audit, no trading decisions; also identifies replay windows."""
    result = []
    for r,end in zip(entries.itertuples(), ends):
        i = int(r.entry_i); stop = r.stop; target = r.target_price; side = r.side
        bars = prices[i:end+1]
        hit_s = bars[:,2] <= stop if side == 1 else bars[:,1] >= stop
        hit_t = bars[:,1] >= target if side == 1 else bars[:,2] <= target
        hit = np.flatnonzero(hit_s | hit_t)
        j = int(hit[0]) if len(hit) else len(bars)-1
        o,h,l,c = bars[j]; reason = 0; price = c
        if len(hit):
            if side*(o-stop) <= 0: reason,price = 1,o
            elif side*(o-target) >= 0: reason,price = 4,target
            elif hit_s[j] and hit_t[j]: reason,price = (4,target) if side == 1 else (1,stop)
            elif hit_s[j]: reason,price = 1,stop
            else: reason,price = 4,target
        ambiguous = bool(len(hit) and hit_s[j] and hit_t[j] and side*(o-stop)>0 and side*(o-target)<0)
        result.append(dict(exit_original_i=i+j, expected_price=float(price), expected_reason=reason,
                           ambiguous_bar=ambiguous, stop_first_price=float(stop if ambiguous else price)))
    return pd.DataFrame(result)


def execute(f, entries, symbol, folder, cfg):
    """Native orders on losslessly bounded windows; fees applied per actual fill."""
    # Use sufficient decimal precision for random scaled geometry without
    # rounding the owner's exact pin/target or historical OHLC tick changes.
    prices = f[OHLC].to_numpy(float)
    decimals = max(2, min(9, int(np.ceil(-np.log10(np.min(prices))))+7))
    tick = 10.**(-decimals)
    for c in ['entry','stop','target_price']:
        entries[c] = np.round(entries[c],decimals)
    entries['risk'] = entries.side*(entries.entry-entries.stop)
    entries = entries[entries.risk.ge(tick*.999) & (entries.side*(entries.target_price-entries.entry)>0)].copy()
    prices = np.round(prices,decimals)
    ends = np.full(len(entries),len(f)-1,dtype=int)
    if symbol != 'XAUUSD':
        gap_before = np.flatnonzero(np.diff(f.index.asi8) != STEP.value)
        for k,i in enumerate(entries.entry_i):
            pos = np.searchsorted(gap_before,i)
            if pos < len(gap_before): ends[k] = int(gap_before[pos])
    expected = barrier_bounds(prices, entries, ends)
    # Keep the entire path from every entry to its first barrier/censor plus
    # one bar. A rejected native/oracle comparison invalidates this run.
    keep = np.zeros(len(f),bool)
    for i,j in zip(entries.entry_i,expected.exit_original_i): keep[int(i):min(len(f),int(j)+2)] = True
    indices = np.flatnonzero(keep); inverse = np.full(len(f),-1,int);inverse[indices]=np.arange(len(indices))*2
    actual = prices[indices]
    points = np.repeat(actual[:,0,None],4,axis=1)
    expanded = np.empty((len(indices)*2,4)); expanded[::2]=points;expanded[1::2]=actual
    times = np.empty(len(expanded),np.int64)
    times[::2]=f.index.as_unit('ns').asi8[indices]+1
    times[1::2]=f.index.as_unit('ns').asi8[indices]+STEP.value
    frame = pd.DataFrame(expanded,columns=OHLC,index=pd.to_datetime(times,utc=True))
    prepared = entries.copy(); prepared['original_entry_i']=entries.entry_i
    prepared['entry_i'] = inverse[entries.entry_i.to_numpy(int)]
    # Unresolved records terminate at their original data boundary; resolved
    # trades also get that same available boundary, never a forced target exit.
    prepared['end_i'] = [int(np.searchsorted(indices,e,side='right')*2-1) for e in ends]
    prepared.loc[prepared.end_i.le(prepared.entry_i),'end_i']=prepared.entry_i+1
    rows,fills,audit = run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),prepared,
        ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=0),
        tick=tick,symbol='XAU' if symbol=='XAUUSD' else symbol.removesuffix('USDT'),execution_minutes=5)
    rows.to_csv(folder/'native_gross.csv.gz',index=False); fills.to_csv(folder/'native_fills.csv.gz',index=False)
    checks = expected.set_index(entries.trade_id)
    for r in rows.itertuples():
        e = checks.loc[r.trade_id]
        if indices[int(r.exit_i)//2] != e.exit_original_i or abs(r.exit_price-e.expected_price)>max(tick*2,1e-8):
            raise AssertionError(f'Native/oracle mismatch {r.trade_id}: {r.exit_price} vs {e.to_dict()}')
        if r.reason != e.expected_reason and not (r.exit_price == r.target_price and e.expected_reason == 4):
            raise AssertionError(f'Native reason mismatch {r.trade_id}')
    rows = rows.merge(expected.assign(trade_id=entries.trade_id.to_numpy()),on='trade_id',validate='one_to_one')
    rows['closed'] = rows.reason.ne(0)
    rows['exit_interval_start'] = [str(f.index[int(i)]) for i in rows.exit_original_i]
    rows['exit_interval_end'] = [str(f.index[int(i)]+STEP) for i in rows.exit_original_i]
    rows['gross_bp'] = rows.side*(rows.exit_price/rows.entry-1)*10000
    rows['stop_first_gross_r'] = rows.side*(rows.stop_first_price-rows.entry)/rows.risk
    rows['fee_entry_rate'] = np.nan if symbol=='XAUUSD' else cfg['taker_fee']
    rows['fee_exit_rate'] = np.nan if symbol=='XAUUSD' else np.where(rows.reason.eq(4),cfg['maker_fee'],cfg['taker_fee'])
    rows['fee_r'] = (rows.entry*rows.fee_entry_rate+rows.exit_price*rows.fee_exit_rate)/rows.risk
    rows['net_r'] = rows.gross_r-rows.fee_r
    rows['net_bp'] = rows.gross_bp-(rows.fee_entry_rate+rows.exit_price/rows.entry*rows.fee_exit_rate)*10000
    rows['stop_first_net_r'] = rows.stop_first_gross_r-(rows.entry*rows.fee_entry_rate+rows.stop_first_price*np.where(rows.ambiguous_bar,cfg['taker_fee'],rows.fee_exit_rate))/rows.risk
    fills['fee_rate'] = np.nan if symbol=='XAUUSD' else np.where(fills.phase.eq('target'),cfg['maker_fee'],cfg['taker_fee'])
    fills['applied_commission'] = fills.price*fills.quantity*fills.fee_rate
    fee_by_trade = fills.groupby('trade_id').applied_commission.sum()
    if symbol!='XAUUSD':
        np.testing.assert_allclose(rows.trade_id.map(fee_by_trade),rows.fee_r*rows.risk*rows.quantity,atol=1e-7,rtol=1e-9)
    rows.to_csv(folder/'trades.csv.gz',index=False); fills.to_csv(folder/'fills.csv.gz',index=False)
    audit.update(oracle_checked=len(rows),source_bars=len(f),replayed_actual_bars=len(indices),
        open_events=len(indices),fee_policy='post-native per-fill notional; no fee-dependent decisions',
        source_gaps=int((np.diff(f.index.asi8)!=STEP.value).sum()),spot_bid_only=symbol=='XAUUSD')
    dump(folder/'audit.json',audit)
    return rows,audit


def jobs(cfg):
    base = ROOT/cfg['crypto_root']; metadata=json.loads((base/'exchange_info.json').read_text())
    meta={s['symbol']:s for s in metadata['symbols']}
    selected=[]; excluded=[]
    for path in sorted((base/'series').glob('*.csv')):
        symbol=path.name.split('_')[2]; info=meta.get(symbol,{})
        reason = ('excluded_asset' if symbol in cfg['exclude_symbols'] else
                  'traditional_or_index' if set(info.get('underlyingSubType',[])) & {'TradFi','Index','Pre-IPO','ETF'} else '')
        if reason: excluded.append(dict(symbol=symbol,reason=reason));continue
        paths=[str(path)]
        tail=ROOT/cfg['crypto_tail']/f'{symbol}.csv'
        if tail.exists(): paths.append(str(tail))
        selected.append((symbol,paths))
    selected.sort(key=lambda x:(x[0] not in ('BTCUSDT','ETHUSDT'),x[0]))
    selected.append(('XAUUSD',[str(p) for p in sorted((ROOT/'data/histdata/XAUUSD').glob('HISTDATA_COM_ASCII_XAUUSD_M1_*.zip'))]))
    return selected,excluded


def process(job,cfg):
    symbol,paths=job; folder=EXP/'symbols'/symbol; folder.mkdir(parents=True,exist_ok=True)
    identity=dict(config=cfg,inputs=[file_stamp(p) for p in paths],code=[file_stamp(ROOT/p) for p in CODE])
    if (folder/'done.json').exists():
        assert json.loads((folder/'identity.json').read_text())==identity,'Changed completed inputs/code'
        return json.loads((folder/'done.json').read_text())
    f=load_gold(paths) if symbol=='XAUUSD' else load_crypto(paths)
    if not np.isfinite(f[OHLC]).all().all() or (f[OHLC]<=0).any().any(): raise ValueError('Invalid OHLC')
    if (f.high<f[OHLC].max(axis=1)).any() or (f.low>f[OHLC].min(axis=1)).any(): raise ValueError('Invalid high/low')
    entries,detail=candidates(f,symbol,cfg)
    entries.to_csv(folder/'candidates.csv.gz',index=False)
    audit={}
    if len(entries): _,audit=execute(f,entries,symbol,folder,cfg)
    done=dict(symbol=symbol,bars=len(f),first=str(f.index[0]),last=str(f.index[-1]),
        signals=int(entries.population.eq('signal').sum()) if len(entries) else 0,
        positions=audit.get('positions',0),**detail)
    dump(folder/'identity.json',identity);dump(folder/'done.json',done)
    return done


def stats(frame):
    closed=frame[frame.closed]; net=closed.net_r.notna().all() and len(closed)>0
    if not len(closed): return dict(n=0,open=len(frame))
    profit=closed.net_r if net else closed.gross_r
    return dict(n=len(closed),open=int((~frame.closed).sum()),tp_pct=float(closed.reason.eq(4).mean()*100),
        win_pct=float(profit.gt(0).mean()*100),gross_r=float(closed.gross_r.sum()),
        mean_gross_r=float(closed.gross_r.mean()),mean_net_r=float(closed.net_r.mean()) if net else None,
        mean_gross_bp=float(closed.gross_bp.mean()),net_r=float(profit.sum()) if net else None,
        mean_net_bp=float(closed.net_bp.mean()) if net else None,
        profit_factor=float(profit[profit>0].sum()/-profit[profit<0].sum()) if (profit<0).any() else None,
        ambiguity=int(closed.ambiguous_bar.sum()),stop_first_net_r=float(closed.stop_first_net_r.sum()) if net else None,
        median_risk_pct=float((closed.risk/closed.entry*100).median()))


def summarize(cfg):
    paths=list((EXP/'symbols').glob('*/trades.csv.gz'))
    f=pd.concat([pd.read_csv(p) for p in paths],ignore_index=True)
    f['group']=np.where(f.symbol.isin(['BTCUSDT','ETHUSDT','XAUUSD']),'BTC_ETH_GOLD','ALTCOINS')
    tables={}
    sets={'BTC':f[f.symbol.eq('BTCUSDT')],'ETH':f[f.symbol.eq('ETHUSDT')],
          'XAUUSD':f[f.symbol.eq('XAUUSD')],'BTC_ETH_GOLD':f[f.group.eq('BTC_ETH_GOLD')],
          'ALTCOINS':f[f.group.eq('ALTCOINS')]}
    comparisons={}
    for name,g in sets.items():
        tables[name]={}
        for segment in ['all','earlier','later']:
            x=g if segment=='all' else g[g.segment.eq(int(segment=='later'))]
            tables[name][segment]={p:stats(x[x.population.eq(p)]) for p in ['signal','random']}
        a=g[g.population.eq('signal') & g.closed].set_index('pair_id')
        b=g[g.population.eq('random') & g.closed].groupby('pair_id')
        averages=b[['gross_r','net_r','gross_bp','net_bp']].mean()
        complete=b.size().eq(cfg['controls_per_signal'])
        paired=a.join(averages[complete],rsuffix='_control',how='inner')
        metric='gross_bp' if name in ['XAUUSD','BTC_ETH_GOLD'] else 'net_bp'
        diff=paired[metric]-paired[metric+'_control']
        # Weekly clusters preserve shared regime and repeated-event dependence.
        clusters=pd.DataFrame({'week':paired.week,'diff':diff}).groupby('week')['diff'].agg(['sum','count'])
        rng=np.random.default_rng(cfg['seed']); draws=[]; null=[]
        if len(clusters):
            sums=clusters['sum'].to_numpy();counts=clusters['count'].to_numpy()
            for _ in range(2000):
                ix=rng.integers(len(sums),size=len(sums));draws.append(sums[ix].sum()/counts[ix].sum())
                null.append((sums*rng.choice([-1,1],len(sums))).sum()/counts.sum())
            comparisons[name]=dict(paired=len(paired),metric=metric,mean_excess_bp=float(diff.mean()),
                ci95=list(map(float,np.quantile(draws,[.025,.975]))),
                cluster_signflip_p=float((1+np.sum(np.array(null)>=diff.mean()))/2001))
    f.to_csv(EXP/'all_trades.csv.gz',index=False)
    bysymbol={s:stats(g[g.population.eq('signal')]) for s,g in f.groupby('symbol')}
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    out=dict(config=cfg,tables=tables,comparisons=comparisons,by_symbol=bysymbol,
        completed_symbols=len(done),signals=sum(x['signals'] for x in done),
        native_positions=sum(x['positions'] for x in done),coverage=done,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        training_eligible=False,production_eligible=False)
    dump(EXP/'summary.json',out);print(json.dumps(dict(tables=tables,comparisons=comparisons),ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--symbols',nargs='*');parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--summary',action='store_true');args=parser.parse_args()
    cfg=json.loads((EXP/'config.json').read_text())
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode: raise RuntimeError('Commit builder first')
    if args.summary: summarize(cfg);return
    selected,excluded=jobs(cfg);dump(EXP/'universe.json',dict(selected=[s for s,_ in selected],excluded=excluded))
    if args.symbols: selected=[j for j in selected if j[0] in args.symbols]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(process,j,cfg):j[0] for j in selected}
        for future in as_completed(futures):
            try: print(json.dumps(future.result()),flush=True)
            except Exception as e: print('FAILED '+futures[future]+': '+repr(e),flush=True);raise
    if not args.symbols: summarize(cfg)


if __name__=='__main__': main()
