"""Independently reconcile frozen selected fills and close cashbooks.

This is a read-only validation of existing results, not a new backtest. It does
not import the evaluation engine, choose events, match controls, or tune any
parameter. Inputs: authenticated selected-ledger fills, original open/close
feature prices, final summary tables, and source manifests. Future prices are
used only to verify the already recorded realized outcomes and close NAV.
"""

from pathlib import Path
import hashlib, json
import numpy as np
import pandas as pd
ROOT=Path('/Users/zhangzc/fable-trading')
R=ROOT/'experiments/active/exp-spike-burst-three-year-20260910-v1/results'
START=pd.Timestamp('2023-09-09T00:00:00Z'); END=pd.Timestamp('2026-09-09T00:00:00Z')
CUTS=[START,pd.Timestamp('2024-09-09T00:00:00Z'),pd.Timestamp('2025-09-09T00:00:00Z'),END]
assert (END-START).days==1096
vm_path=R/'validation_manifest.json'
assert vm_path.exists(),'Final validation not available'
vm=json.loads(vm_path.read_text()); dm=json.loads((R/'dataset_manifest.json').read_text())
assert vm['status']==dm['status']=='complete'
assert vm['source_hashes']==dm['source_hashes']
assert hashlib.sha256((R/'dataset_manifest.json').read_bytes()).hexdigest()==vm['input_manifest_sha256']
auth={str(Path(a['path']).resolve()):a['sha256'] for a in dm['artifacts']+vm['artifacts']}
verified={}; issues=[]; checks=0; marks={}; rows=[]
def verify(path):
    path=Path(path).resolve(); key=str(path)
    if key not in verified:
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest==auth[key],key
        verified[key]=digest
    return path
def read(path):
    path=verify(path)
    try: return pd.read_csv(path,low_memory=False)
    except pd.errors.EmptyDataError: return pd.DataFrame()
def compare(label,actual,expected,atol=1e-6,rtol=1e-10):
    global checks
    checks+=1
    a=np.asarray(actual,dtype=float); e=np.asarray(expected,dtype=float)
    if a.shape!=e.shape or not np.allclose(a,e,atol=atol,rtol=rtol,equal_nan=True):
        issues.append(dict(label=label,actual_shape=list(a.shape),expected_shape=list(e.shape),
                           maximum_absolute_difference=float(np.nanmax(np.abs(a-e))) if a.shape==e.shape and a.size else None))
def assert_flag(label,value):
    global checks
    checks+=1
    if not value: issues.append(dict(label=label))
def frame_prices(path):
    key=str(Path(path).resolve())
    if key not in marks:
        q=pd.read_pickle(verify(path))
        marks[key]=q[['open','close']].copy()
    return marks[key]
accounts=read(R/'accounts_summary.csv'); annual=read(R/'annual_summary.csv')
assert len(accounts)==36 and len(annual)==108
for summary in accounts.to_dict('records'):
    context={k:summary[k] for k in ['minutes','cohort','arm','account']}
    prefix='_'.join(str(v) for v in context.values()); m=int(summary['minutes']); step=pd.Timedelta(minutes=m)
    ledger=read(R/'accounts'/(prefix+'_ledger.csv.gz')); curve=read(R/'accounts'/(prefix+'_curve.csv.gz'))
    index=pd.DatetimeIndex(pd.to_datetime(curve.time,utc=True)); expected_index=pd.date_range(START+step,END,freq=step)
    assert_flag(prefix+' clock',index.equals(expected_index))
    selected=ledger.loc[ledger.portfolio_selected.eq(True)].copy() if len(ledger) else ledger
    cash_changes=np.zeros(len(index)); closed_changes=np.zeros(len(index),dtype=int)
    open_changes=np.zeros(len(index),dtype=int); mark_values=np.zeros(len(index))
    fees=0.
    def add(array,t,value):
        at=int(index.searchsorted(t))
        if at<len(index): array[at]+=value
    for trade in selected.to_dict('records'):
        eid=trade['event_id']; entry=pd.Timestamp(trade['entry_time']); exit=pd.Timestamp(trade['exit_time'])
        quantity=float(trade['quantity']); nominal=float(trade['notional'])
        fill=float(trade['entry_price']); out=float(trade['exit_price'])
        pnl=quantity*(out-fill)-.002*nominal
        compare(prefix+' pnl '+eid,trade['realized_net_pnl'],pnl)
        compare(prefix+' quantity '+eid,nominal,quantity*fill)
        compare(prefix+' price return '+eid,trade['net_return'],out/fill-1-.002,atol=1e-10)
        assert_flag(prefix+' entry/risk '+eid,START<=entry<END and exit>entry and float(trade['initial_risk_frac'])>0)
        caps=[.1*float(trade['entry_equity']),.005*float(trade['entry_equity'])/float(trade['initial_risk_frac']),
              .01*float(trade['signal_quote_volume']),.001*float(trade['prior24h_quote_volume'])]
        assert_flag(prefix+' frozen capacity '+eid,nominal>0 and nominal<=min(caps)+1e-6)
        source=frame_prices(trade['features_path'])
        compare(prefix+' native entry '+eid,fill,source.loc[entry,'open'],atol=1e-10)
        settle=exit+step if trade['exit_timing']=='open' else exit
        first_close=entry+step
        add(cash_changes,first_close,-nominal*1.001); add(cash_changes,settle,quantity*out-nominal*.001)
        add(closed_changes,first_close,1); add(closed_changes,settle,-1)
        add(open_changes,first_close,1); add(open_changes,exit+step,-1)
        a=int(index.searchsorted(first_close)); b=int(index.searchsorted(settle))
        held_stamps=index[a:b]-step
        if len(held_stamps):
            assert_flag(prefix+' source marks '+eid,held_stamps.isin(source.index).all())
            mark_values[a:b]+=quantity*source.loc[held_stamps,'close'].to_numpy(float)
        fees+=.002*nominal
    cash=100000.+cash_changes.cumsum(); equity=cash+mark_values
    positions=closed_changes.cumsum(); positions_open=open_changes.cumsum()
    compare(prefix+' every close cash',curve.cash,cash)
    compare(prefix+' every close equity',curve.equity,equity)
    compare(prefix+' every close positions',curve.positions,positions,atol=0,rtol=0)
    compare(prefix+' pre-close-exit positions',curve.positions_open,positions_open,atol=0,rtol=0)
    assert_flag(prefix+' no borrowing/crowding',bool(np.all(cash>=-1e-6) and np.all(positions_open<=10)))
    pnl=selected.realized_net_pnl.to_numpy(float) if len(selected) else np.array([])
    win=float(np.mean(pnl>0)) if len(pnl) else np.nan
    positive=pnl[pnl>0]; negative=pnl[pnl<0]
    pf=positive.sum()/-negative.sum() if len(negative) else np.nan
    peaks=np.maximum.accumulate(np.r_[100000.,equity])[1:]; drawdown=equity/peaks-1
    compare(prefix+' recorded drawdown',curve.drawdown,drawdown,atol=1e-10)
    compare(prefix+' summary return',summary['return_pct'],(equity[-1]/100000.-1)*100)
    compare(prefix+' final all PNL',equity[-1]-100000.,pnl.sum())
    compare(prefix+' summary win',summary['win_rate'],win,atol=1e-12)
    compare(prefix+' summary trades',summary['trades'],len(pnl),atol=0)
    compare(prefix+' summary PF',summary['profit_factor'],pf)
    compare(prefix+' summary MDD',summary['max_drawdown_pct'],-drawdown.min()*100)
    natural=selected.loc[selected.natural_exit.eq(True)] if len(selected) else selected
    naturalwin=natural.realized_net_pnl.gt(0).mean() if len(natural) else np.nan
    compare(prefix+' natural win',summary['natural_win_rate'],naturalwin,atol=1e-12)
    matches=annual.copy()
    for k,v in context.items():matches=matches.loc[matches[k].eq(v)]
    matches=matches.set_index('period'); annual_returns=[]; assigned=0
    for k,(start,end) in enumerate(zip(CUTS[:-1],CUTS[1:])):
        previous=equity[index<=start]; opening=float(previous[-1]) if len(previous) else 100000.
        segment=equity[(index>start)&(index<=end)]
        values=np.r_[opening,segment]; dd=values/np.maximum.accumulate(values)-1
        if len(selected):
            clocks=pd.to_datetime(selected.exit_time,utc=True)
            at_open=selected.exit_timing.eq('open')
            mask=(at_open&clocks.ge(start)&clocks.lt(end))|(~at_open&clocks.gt(start)&clocks.le(end))
            exits=selected.loc[mask]
        else: exits=selected
        annualwin=exits.realized_net_pnl.gt(0).mean() if len(exits) else np.nan
        ne=exits.loc[exits.natural_exit.eq(True)] if len(exits) else exits
        actual=matches.loc['year'+str(k+1)]
        expected={'opening_equity':opening,'ending_equity':float(values[-1]),
                  'return_pct':(values[-1]/opening-1)*100,'max_drawdown_pct':-dd.min()*100,
                  'exits':len(exits),'win_rate':annualwin,'natural_exits':len(ne),
                  'natural_win_rate':ne.realized_net_pnl.gt(0).mean() if len(ne) else np.nan}
        for key,value in expected.items():compare(prefix+' year'+str(k+1)+' '+key,actual[key],value)
        annual_returns.append(expected['return_pct']);assigned+=len(exits)
    compare(prefix+' annual exit partition',assigned,len(selected),atol=0)
    compare(prefix+' annual compound',100*(np.prod(1+np.array(annual_returns)/100)-1),summary['return_pct'])
    rows.append(dict(**context,selected=len(selected),wins=int((pnl>0).sum()),natural=len(natural),
        natural_wins=int(natural.realized_net_pnl.gt(0).sum()) if len(natural) else 0,
        terminal_equity=float(equity[-1]),return_pct=(equity[-1]/100000.-1)*100,annual_returns=annual_returns))
    print('audited',prefix,'selected',len(selected),'issues',len(issues),flush=True)
payload=dict(schema='three-year-cashbook-independent-audit-v1',checks=checks,
    verified_sources=verified,accounts=rows,issues=issues,
    input_manifest_sha256=hashlib.sha256((R/'dataset_manifest.json').read_bytes()).hexdigest(),
    validation_manifest_sha256=hashlib.sha256(vm_path.read_bytes()).hexdigest(),
    method='Reconstruct only previously selected fills/cash/marks; no new candidates, matching, allocation or trade simulation')
output=ROOT/'experiments/active/exp-spike-burst-three-year-20260910-v1/diagnostics/three_year_cashbook_audit.json'
output.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(checks=checks,source_count=len(verified),accounts=len(rows),issues=issues,output=str(output),sha256=hashlib.sha256(output.read_bytes()).hexdigest()),indent=2,allow_nan=False))
assert not issues,issues

