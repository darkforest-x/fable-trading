"""Two separately frozen V2.31 entry experiments using existing five-year data.

Retest changes only execution: after full15m admission, rest at its matched
key for30minutes, retain the original exact stop and confirmed15m2/2 exit.
Structure changes only selection: current confirmed1H2/2 swings determine
higher-high/higher-low, lower-high/lower-low, or mixed context. No fitting.
Features read OHLC ending at the admission close; pending orders read no future
bar until native matching advances. Both experiments keep every original
opportunity, including rejected/unfilled controls, at zero realized return.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run

CONFIGS = {kind: ROOT/f'experiments/active/exp-range-v231-{kind}-20261021-v1/config.json'
           for kind in ('retest', 'h1structure')}
CODE = ['yoyo/evaluation/range_entry_followups.py',
        'yoyo/evaluation/range_framework.py',
        'yoyo/layers/l3_backtest/nautilus_independent.py']
OHLC = ['open', 'high', 'low', 'close']
RETURNS = ['gross_r', 'fee_r', 'net_r', 'net_notional_bp']


def read(path):
    return pd.read_csv(path, float_precision='round_trip')


def load_source(cfg, symbol):
    source = ROOT/cfg['source']/symbol
    frame = read(source/'candles.csv.gz')
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop('ts'), unit='ms', utc=True)
                                  + pd.Timedelta(minutes=5)).as_unit('ns')
    return source, frame, read(source/'candidates.csv'), read(source/'trades.csv'), read(source/'signal_matches.csv')


def hourly_structure(five, strict=False):
    """Closed OHLC only; two most recently CONFIRMED 1H highs and lows.

    At each completed1H close, evaluate a center two bars earlier using its
    two left and two right neighbors. Higher highs AND higher lows => +1;
    lower highs AND lower lows => -1; mixed/equal =>0. Fewer than two each
    is unknown, not a range. No provisional swing or backward filling.
    """
    hour = five.resample('60min', closed='right', label='right').agg(
        {'open':'first', 'high':'max', 'low':'min', 'close':'last'})
    counts = five.close.resample('60min', closed='right', label='right').count()
    hour.loc[counts != 12, OHLC] = np.nan
    result = pd.DataFrame(index=hour.index)
    for name, sign in [('high', 1), ('low', -1)]:
        value = sign*hour[name]
        center = value.shift(2)
        left = ((center > value.shift(3)) & (center > value.shift(4))) if strict else (
            (center >= value.shift(3)) & (center >= value.shift(4)))
        valid = left & (center > value.shift(1)) & (center > value)
        pivots = sign*center[valid]
        result[f'last_{name}'] = pivots.reindex(hour.index).ffill()
        result[f'previous_{name}'] = pivots.shift().reindex(hour.index).ffill()
        stamps = pd.Series(pivots.index.asi8, index=pivots.index)
        result[f'{name}_confirmed_ns'] = stamps.reindex(hour.index).ffill()
    result['known'] = result[['previous_high', 'previous_low']].notna().all(axis=1)
    up = (result.last_high > result.previous_high) & (result.last_low > result.previous_low)
    down = (result.last_high < result.previous_high) & (result.last_low < result.previous_low)
    result['direction'] = np.where(up, 1, np.where(down, -1, 0))
    output = result.reindex(five.index, method='ffill')
    output['known'] = output.known.fillna(False).astype(bool)
    output['direction'] = output.direction.fillna(0).astype(int)
    return output


def classify_structure(features, entries):
    """Classify each signal/control at its OWN admission close."""
    out = entries.copy()
    selected = features.iloc[out.entry_i.to_numpy(int)].reset_index(drop=True)
    for col in features:
        out[col] = selected[col].to_numpy()
    relative = out.side*out.direction
    out['context'] = np.where(~out.known, 'unknown',
        np.where(relative > 0, 'aligned', np.where(relative < 0, 'counter', 'mixed')))
    return out


def limit_candidates(five, base, matches, tick, wait_minutes, timezone):
    """Order prices use the original touch key; controls copy relative distance.

    The deadline is fixed at submission, at most30minutes and before the first
    non-Tue/Fri eligible5m close. Buy limits floor/sell limits ceil to tick.
    Original stops are never widened. Invalid geometry is an unfilled original
    opportunity, not a new candidate or a reason to redraw a random time.
    """
    if wait_minutes <= 0 or wait_minutes % 5:
        raise ValueError('Wait must be a positive multiple of5minutes')
    matched = matches.set_index('pair_id')
    rows = []
    weekday = five.index.tz_convert(timezone).dayofweek
    for r in base.to_dict('records'):
        source = matched.loc[r['pair_id']]
        raw = source.level if r['population'] == 'signal' else (
            r['entry']*source.level/source.entry)
        limit = (math.floor(raw/tick+1e-8) if r['side'] == 1 else math.ceil(raw/tick-1e-8))*tick
        i = int(r['entry_i'])
        expiry = min(i+wait_minutes//5, int(r['end_i'])-1)
        for j in range(i+1, expiry+1):
            if not 1 <= weekday[j] <= 4:
                expiry = j-1
                break
        risk = r['side']*(limit-r['stop'])
        status = 'pending'
        if r['side']*(r['entry']-limit) < tick-tick*1e-7:
            status = 'not_better_price'
        elif risk < tick-tick*1e-7 or r['stop'] <= 0:
            status = 'invalid_risk'
        elif expiry <= i:
            status = 'no_eligible_interval'
        rows.append(dict(r, trade_id=f"{r['population']}-{r['pair_id']}",
            reference_entry=r['entry'], reference_risk=r['side']*(r['entry']-r['stop']),
            entry=limit, risk=risk, expire_i=expiry, status=status,
            matched_key=float(source.level), key=str(source.key)))
    return pd.DataFrame(rows)


def native_oracle(five, candidates, rows, opportunities, strict=False):
    """Independent bounded path audit; validation only, never native input.

    Resting limit must first touch strictly AFTER submission, on/before expiry.
    Stop crossings take precedence, including entry bar; true gaps use open.
    Confirmed pivot centers must begin at/after the conservative fill-bar close.
    """
    from yoyo.layers.l3_backtest.nautilus_independent import confirmed_pivots
    high, low, centers = confirmed_pivots(five, strict=strict)
    p = five[OHLC].to_numpy()
    actual = rows.set_index('trade_id') if len(rows) else pd.DataFrame()
    outcomes = {r['trade_id']: r for r in opportunities}
    checked, same_bar, gaps = 0, 0, 0
    for r in candidates.itertuples():
        fill = None
        for j in range(int(r.entry_i)+1, int(r.expire_i)+1):
            if (p[j,2] <= r.entry if r.side == 1 else p[j,1] >= r.entry):
                fill = j
                break
        if fill is None:
            assert outcomes[r.trade_id]['status'] == 'expired'
            assert actual.empty or r.trade_id not in actual.index
            continue
        assert outcomes[r.trade_id]['fill_i'] == fill
        a = actual.loc[r.trade_id]
        assert int(a.entry_i) == fill
        peak = 0.
        for j in range(fill, int(r.end_i)+1):
            o,h,l,c = p[j]
            stop_hit = l <= r.stop if r.side == 1 else h >= r.stop
            if stop_hit:
                price = min(o,r.stop) if r.side == 1 else max(o,r.stop)
                reason = 1
                same_bar += int(j == fill)
                gaps += int(r.side*(o-r.stop) < 0)
                break
            if j > fill:
                peak = max(peak, r.side*((h if r.side == 1 else l)-r.entry)/r.risk)
            pivot = high[j] if r.side == 1 else low[j]
            if centers[j] >= five.index[fill].value and np.isfinite(pivot) and r.side*(pivot-r.entry) > 0:
                price,reason = c,7
                break
            if j == int(r.end_i):
                price,reason = c,0
                break
        assert (int(a.exit_i), int(a.reason)) == (j, reason), (r.trade_id, a.exit_i, j, a.reason, reason)
        np.testing.assert_allclose([a.entry,a.exit_price,a.peak_r], [r.entry,price,peak], rtol=0, atol=1e-7)
        np.testing.assert_allclose(a.framework_commission, .002*a.entry*a.quantity, rtol=0, atol=1e-7)
        checked += 1
    return dict(opportunities=len(candidates), filled_paths=checked, expired=len(candidates)-checked,
                same_bar_stops=same_bar, gap_stops=gaps, status='passed')


def original_opportunities(ledger):
    out = ledger.copy()
    out['reference_entry'] = out.entry
    out['reference_risk'] = out.risk
    out['reference_net_r'] = out.net_r
    out['original_entry_time'] = out.entry_time
    out['traded'] = True
    out['status'] = 'filled'
    out['original_net_r'] = out.net_r
    out['original_net_bp'] = out.net_notional_bp
    out['actual_entry_i'] = out.entry_i
    out['actual_entry_time'] = out.entry_time
    return out


def realize_opportunities(original, fills, decisions):
    """Preserve zeros for all missing fills and an original-risk money bridge."""
    out = original_opportunities(original)
    out[RETURNS+['reference_net_r']] = 0.
    out['traded'] = False
    out['actual_entry_i'] = np.nan
    out['actual_entry_time'] = None
    status = decisions.set_index('trade_id').status
    out['status'] = out.trade_id.map(status)
    assert out.status.notna().all()
    if len(fills):
        by = fills.set_index('trade_id')
        mask = out.trade_id.isin(by.index)
        for col in RETURNS+['entry','stop','risk','exit_i','exit_price','exit_time','reason','peak_r']:
            out.loc[mask, col] = out.loc[mask, 'trade_id'].map(by[col])
        out.loc[mask, 'traded'] = True
        out.loc[mask, 'status'] = 'filled'
        out.loc[mask, 'actual_entry_i'] = out.loc[mask, 'trade_id'].map(by.entry_i)
        out.loc[mask, 'actual_entry_time'] = out.loc[mask, 'trade_id'].map(by.entry_time)
        out.loc[mask, 'reference_net_r'] = (
            out.loc[mask,'net_notional_bp']/10000*out.loc[mask,'entry']/out.loc[mask,'reference_risk'])
    return out


def zero_rejected(ledger, contexts, wanted):
    out = original_opportunities(ledger)
    out['context'] = out.trade_id.map(contexts.set_index('trade_id').context)
    assert out.context.notna().all()
    out['traded'] = out.context.eq(wanted)
    out.loc[~out.traded, RETURNS+['reference_net_r']] = 0.
    out.loc[~out.traded, 'status'] = 'filtered'
    return out


def block_test(values, weeks, seed):
    """One-sided weekly sign-randomization; bootstrap CI is descriptive."""
    grouped = pd.Series(np.asarray(values,float)).groupby(np.asarray(weeks)).sum().to_numpy()
    if not len(grouped):
        return dict(total=0.,p=1.,ci95=[0.,0.],weeks=0)
    rng = np.random.default_rng(seed)
    null = (rng.choice([-1.,1.],size=(20000,len(grouped)))*grouped).sum(axis=1)
    sample = rng.choice(grouped,size=(5000,len(grouped)),replace=True).sum(axis=1)
    return dict(total=float(grouped.sum()),p=float((1+np.sum(null>=grouped.sum()-1e-10))/20001),
                ci95=np.quantile(sample,[.025,.975]).tolist(),weeks=len(grouped))


def describe(ledger, ids, seed):
    s = ledger[(ledger.population=='signal') & ledger.pair_id.isin(ids)].sort_values('pair_id')
    r = ledger[(ledger.population=='random') & ledger.pair_id.isin(ids)].sort_values('pair_id')
    np.testing.assert_array_equal(s.pair_id,r.pair_id)
    assert len(s)==len(ids)
    sf,rf = s[s.traded],r[r.traded]
    curve = np.r_[0.,sf.sort_values(['exit_time','trade_id']).net_r.cumsum().to_numpy()]
    excess = block_test(s.net_notional_bp.to_numpy()-r.net_notional_bp.to_numpy(),s.week,seed)
    delta = block_test(s.net_notional_bp.to_numpy()-s.original_net_bp.to_numpy(),s.week,seed)
    incremental = block_test((s.net_notional_bp.to_numpy()-s.original_net_bp.to_numpy())-
        (r.net_notional_bp.to_numpy()-r.original_net_bp.to_numpy()),s.week,seed)
    absolute = block_test(s.net_notional_bp,s.week,seed)
    negative = -float(sf.loc[sf.net_r<0,'net_r'].sum())
    return dict(opportunities=len(s),n=len(sf),random_n=len(rf),
        fill_or_admission_rate=len(sf)/len(s) if len(s) else None,
        wins=int(sf.net_r.gt(1e-9).sum()),win_rate=float(sf.net_r.gt(1e-9).mean()) if len(sf) else None,
        gross_r=float(sf.gross_r.sum()),fee_r=float(sf.fee_r.sum()),net_r=float(sf.net_r.sum()),
        mean_r=float(sf.net_r.mean()) if len(sf) else None,
        mean_net_bp=float(sf.net_notional_bp.mean()) if len(sf) else None,
        per_opportunity_net_bp=float(s.net_notional_bp.mean()) if len(s) else None,
        random_net_r=float(rf.net_r.sum()),random_mean_net_bp=float(rf.net_notional_bp.mean()) if len(rf) else None,
        random_per_opportunity_net_bp=float(r.net_notional_bp.mean()) if len(r) else None,
        original_risk_net_r=float(sf.reference_net_r.sum()),
        random_original_risk_net_r=float(rf.reference_net_r.sum()),
        pf=float(sf.loc[sf.net_r>0,'net_r'].sum()/negative) if negative else None,
        realized_r_drawdown=float(np.max(np.maximum.accumulate(curve)-curve)),
        without_best_net_r=float(sf.net_r.sum()-sf.net_r.nlargest(1).sum()),
        without_best5_net_r=float(sf.net_r.sum()-sf.net_r.nlargest(5).sum()),
        initial_stops=int(sf.reason.eq(1).sum()),boundary_n=int(sf.reason.eq(0).sum()),
        boundary_net_r=float(sf.loc[sf.reason.eq(0),'net_r'].sum()),
        gross10r=int(sf.gross_r.ge(10-1e-8).sum()),gross20r=int(sf.gross_r.ge(20-1e-8).sum()),
        zero_opportunity_statuses=s.loc[~s.traded,'status'].value_counts().to_dict(),
        skipped_original_winners=int(s.loc[~s.traded,'original_net_r'].gt(0).sum()),
        skipped_original_net_r=float(s.loc[~s.traded,'original_net_r'].sum()),
        excess_bp_test=excess,absolute_bp_test=absolute,delta_vs_original_bp_test=delta,
        incremental_vs_random_bp_test=incremental)


def make_summaries(cfg, symbol, ledgers, matches):
    summaries=[]
    t=pd.to_datetime(matches.entry_time,utc=True)
    periods=[('all',np.ones(len(t),bool)),('earlier',t<pd.Timestamp(cfg['midpoint'])),
             ('later',t>=pd.Timestamp(cfg['midpoint']))]
    periods += [(f'year{j+1}',(t>=pd.Timestamp(cfg['start'])+pd.DateOffset(years=j)) &
        (t<pd.Timestamp(cfg['start'])+pd.DateOffset(years=j+1))) for j in range(5)]
    for arm,ledger in ledgers.items():
        for period,mask in periods:
            summaries.append(dict(symbol=symbol,arm=arm,period=period,
                **describe(ledger,matches.loc[mask,'pair_id'].tolist(),cfg['seed'])))
    return summaries


def verify_baseline(cfg, symbol, summary):
    prior=json.loads((ROOT/cfg['source']/'summary.json').read_text())
    expected=next(r for r in prior['overall'] if r['symbol']==symbol)
    for key in ['n','wins','net_r','gross_r','fee_r','mean_net_bp','random_net_r']:
        np.testing.assert_allclose(summary[key],expected[key],rtol=0,atol=1e-7,err_msg=key)


def run_retest(cfg, dest):
    summaries,checks=[],[]
    for spec in cfg['symbols']:
        symbol=spec['symbol'];folder=dest/symbol
        source,five,base,original,matches=load_source(cfg,symbol)
        identity=dict(config=cfg,spec=spec,inputs=[file_stamp(source/p) for p in
            ['candles.csv.gz','candidates.csv','signal_matches.csv','trades.csv']],
            code=[file_stamp(ROOT/p) for p in CODE])
        meta=folder/'preparation.json'
        if meta.exists():
            if json.loads(meta.read_text())['identity']!=identity:
                raise RuntimeError('Changed preparation; choose new experiment directory')
            planned=read(folder/'opportunities.csv')
        else:
            planned=limit_candidates(five,base,matches,spec['tick'],cfg['wait_minutes'],cfg['timezone'])
            folder.mkdir(exist_ok=False)
            planned.to_csv(folder/'opportunities.csv',index=False)
            candidates=planned[planned.status.eq('pending')]
            candidates.to_csv(folder/'candidates.csv',index=False)
            native=json.loads((source/'native.json').read_text())
            native.update(experiment_id=cfg['experiment_id'],entry_order_mode='limit',
                entry_candidates=str((folder/'candidates.csv').relative_to(ROOT)),
                output=str((folder/'native').relative_to(ROOT)))
            dump(folder/'native.json',native)
            dump(meta,dict(identity=identity,opportunities=len(planned),submitted=len(candidates)))
        run(folder/'native.json')
        ledgers={'baseline':original_opportunities(original)}
        for policy,name in [('pivot','retest'),('strict_ties','retest_strict')]:
            native=folder/'native'/policy
            try:
                rows=read(native/'trades.csv')
            except pd.errors.EmptyDataError:
                rows=pd.DataFrame()
            audit=json.loads((native/'audit.json').read_text())
            decisions=planned[['trade_id','status']].copy().set_index('trade_id')
            for item in audit['opportunities']:
                decisions.loc[item['trade_id'],'status']=item['status']
            decisions=decisions.reset_index()
            validation=native_oracle(five,planned[planned.status.eq('pending')],rows,
                audit['opportunities'],strict=policy=='strict_ties')
            ledger=realize_opportunities(original,rows,decisions)
            ledger.to_csv(folder/f'{name}_opportunities.csv',index=False)
            ledgers[name]=ledger
            checks.append(dict(symbol=symbol,policy=policy,**validation))
        current=make_summaries(cfg,symbol,ledgers,matches)
        verify_baseline(cfg,symbol,next(r for r in current if r['arm']=='baseline' and r['period']=='all'))
        summaries.extend(current)
    return summaries,checks


def run_structure(cfg,dest):
    summaries,checks=[],[]
    for spec in cfg['symbols']:
        symbol=spec['symbol'];folder=dest/symbol;folder.mkdir(exist_ok=True)
        source,five,base,original,matches=load_source(cfg,symbol)
        base['trade_id']=base.population+'-'+base.pair_id.astype(str)
        features=hourly_structure(five)
        contexts=classify_structure(features,base)
        contexts.to_csv(folder/'contexts.csv',index=False)
        prefixes=0
        probes=np.unique(np.linspace(0,len(base)-1,8,dtype=int))
        for pos in probes:
            end=int(base.iloc[pos].entry_i)+1
            partial=hourly_structure(five.iloc[:end])
            pd.testing.assert_frame_equal(partial.tail(2),features.iloc[:end].tail(2))
            prefixes+=1
        strict=classify_structure(hourly_structure(five,True),base)
        ledgers={'baseline':original_opportunities(original)}
        for state in ['aligned','counter','mixed','unknown']:
            ledgers[state]=zero_rejected(original,contexts,state)
            ledgers[state].to_csv(folder/f'{state}_opportunities.csv',index=False)
        # Compare alternate tie convention with no new executions.
        strict_summary=[]
        for state in ['aligned','counter','mixed']:
            strict_summary.append(dict(context=state,**describe(zero_rejected(original,strict,state),
                matches.pair_id.tolist(),cfg['seed'])))
        np.testing.assert_allclose(sum(ledgers[k][RETURNS].to_numpy() for k in
            ['aligned','counter','mixed','unknown']),ledgers['baseline'][RETURNS],rtol=0,atol=1e-8)
        current=make_summaries(cfg,symbol,ledgers,matches)
        verify_baseline(cfg,symbol,next(r for r in current if r['arm']=='baseline' and r['period']=='all'))
        summaries.extend(current)
        checks.append(dict(symbol=symbol,reused_native_paths=len(original),prefix_checks=prefixes,
            partition_fields=RETURNS,strict_changed_contexts=int((contexts.context!=strict.context).sum()),
            strict_context_results=strict_summary,
            inputs=[file_stamp(source/p) for p in ['candles.csv.gz','candidates.csv','trades.csv']]))
    return summaries,checks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=list(CONFIGS))
    args=parser.parse_args();config=CONFIGS[args.kind]
    cfg=json.loads(config.read_text());dest=config.parent
    for path in [*CODE,str(config.relative_to(ROOT))]:
        subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(config)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generating outputs')
    identity=dict(config=cfg,code=[file_stamp(ROOT/p) for p in CODE],
        inputs=[file_stamp(ROOT/cfg['source']/spec['symbol']/name)
                for spec in cfg['symbols'] for name in
                ['candles.csv.gz','candidates.csv','signal_matches.csv','trades.csv','native.json']])
    if (dest/'summary.json').exists():
        old=json.loads((dest/'summary.json').read_text())
        if old['identity']!=identity:raise RuntimeError('Changed completed study; preserve outputs')
        print(json.dumps(dict(cached=True,experiment=cfg['experiment_id'])))
        return
    rows,checks=(run_retest if args.kind=='retest' else run_structure)(cfg,dest)
    from yoyo.evaluation.range_management_study import holm
    primary=[r for r in rows if r['period']=='all' and r['arm'] in
             (['retest'] if args.kind=='retest' else ['aligned','counter','mixed'])]
    for key in ['excess_bp_test','absolute_bp_test','incremental_vs_random_bp_test']:
        for row,p in zip(primary,holm([r[key]['p'] for r in primary])):
            row[key]['holm_p']=p
    result=dict(identity=identity,config=cfg,rows=rows,checks=checks,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        training_eligible=False,production_eligible=False,
        limitations='Known-history independent research, not an account return. Fees20bp unchanged; no funding/slippage/latency/capacity. Original matched opportunity times; random applies its own selection/execution. Zeros include all unfilled/filtered opportunities. Actual-risk R and original-risk bridge both reported. OHLC synthetic path, not observed intrabar ticks. Mixed swings do not prove range-bound conditions.')
    dump(dest/'summary.json',result)
    pd.DataFrame(rows).to_csv(dest/'comparison.csv',index=False)
    print(json.dumps([r for r in rows if r['period']=='all'],indent=2),flush=True)


if __name__=='__main__':main()
