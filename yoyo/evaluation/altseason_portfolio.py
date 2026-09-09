"""Cash-constrained, asset-exclusive portfolios for fixed altseason hypotheses.

No entry ranking uses outcomes: candidates are ordered by their already-known
prior24h USDT turnover, then stable venue/symbol ids. A trade's frozen quantity
is capped by10% of entry equity,0.5% equity initial risk, and available cash.
At most10 assets can be held. Different venues for the same registered asset
cannot duplicate risk. Each arm/timeframe is a separate hypothetical account.

Marks are source OHLC at the common timeframe. Unknown intrabar exits release
cash only at their enclosing close; next-open exits release it at that open.
Realized cash includes10bp entry notional at both entry and exit, consistent
with the frozen event engine. There is no funding or cross-margin borrowing.
The caller supplies a complete price map while any selected position is held.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def run_portfolio(events, prices, start, end, minutes, initial=100000.0):
    """Return close-equity path and every candidate's allocation decision.

events: one arm/timeframe, UTC entry/exit clocks, unique event_id, instrument,
asset, venue, symbol, initial_risk_frac, entry_price, exit_price and net_return.
prices[instrument]: UTC open-stamped open/close frame at this exact timeframe.
All entry-time information is copied from closed decision rows by the caller.
"""
    start,end=pd.Timestamp(start),pd.Timestamp(end)
    step=pd.Timedelta(minutes=minutes)
    if start.tz is None or end.tz is None or end<=start or start.utcoffset().total_seconds()!=0 or end.utcoffset().total_seconds()!=0 or start.value%step.value or end.value%step.value:
        raise ValueError('Explicit nonempty UTC time window required')
    e=events.copy()
    if not e.empty and e.event_id.duplicated().any():
        raise ValueError('Portfolio event ids must be unique')
    records=e.to_dict('records')
    entries={}
    for row in records:
        row.update(portfolio_selected=False,portfolio_rejection='',notional=0.,quantity=0.,realized_net_pnl=0.)
        if not row.get('valid',False): row['portfolio_rejection']='invalid_event';continue
        row['entry_time']=pd.Timestamp(row['entry_time']);row['exit_time']=pd.Timestamp(row['exit_time'])
        if any(t.tz is None or t.utcoffset().total_seconds()!=0 or t.value%step.value for t in (row['entry_time'],row['exit_time'])):
            raise ValueError('Off-grid or non-UTC event clock')
        if any(not np.isfinite(float(row[k])) or float(row[k])<=0 for k in ('entry_price','exit_price','initial_risk_frac')):
            raise ValueError('Nonpositive or nonfinite event price/risk')
        observed=float(prices[row['instrument']].loc[row['entry_time'],'open'])
        if not np.isclose(observed,row['entry_price'],rtol=1e-10,atol=0):raise ValueError('Entry price does not match source open')
        expected=row['exit_price']/row['entry_price']-1-.002
        if not np.isclose(expected,row['net_return'],rtol=1e-9,atol=1e-10):raise ValueError('Event net return disagrees with price/cost')
        if not start<=row['entry_time']<end:row['portfolio_rejection']='outside_window';continue
        if row['exit_time']<=row['entry_time']:
            raise ValueError('Exit must follow entry; zero-duration initial-risk trade is invalid')
        entries.setdefault(row['entry_time'],[]).append(row)
    cash=float(initial);held={};path=[];high=float(initial)

    def settle(clock):
        nonlocal cash
        for asset,position in list(held.items()):
            if position['exit_time']<=clock:
                cash+=position['quantity']*position['exit_price']-position['notional']*.001
                position['realized_net_pnl']=position['quantity']*(position['exit_price']-position['entry_price'])-position['notional']*.002
                del held[asset]

    def mark(clock,field):
        value=cash
        for position in held.values():
            frame=prices[position['instrument']]
            try: row=frame.loc[clock]
            except KeyError:raise ValueError('Missing held-position mark: '+position['instrument']+' '+str(clock))
            if isinstance(row,pd.DataFrame):raise ValueError('Duplicate held mark')
            value+=position['quantity']*float(row['open' if field==0 else 'close'])
        return value

    for clock in pd.date_range(start,end,freq=f'{minutes}min',inclusive='left'):
        settle(clock)
        equity_open=mark(clock,0)
        pending=entries.get(clock,[])
        def priority(row):
            volume=float(row.get('prior24h_quote_volume',0) or 0)
            return (-volume if np.isfinite(volume) else 0.,str(row['venue']),str(row['symbol']),str(row['event_id']))
        pending.sort(key=priority)
        for row in pending:
            if row['asset'] in held:row['portfolio_rejection']='asset_already_held';continue
            if len(held)>=10:row['portfolio_rejection']='ten_asset_limit';continue
            risk=float(row['initial_risk_frac'])
            if not np.isfinite(risk) or risk<=0:raise ValueError('Invalid event risk')
            capacity=[float(row.get(k,np.nan)) for k in ('signal_quote_volume','prior24h_quote_volume')]
            if not np.isfinite(capacity).all():row['portfolio_rejection']='capacity_missing';continue
            if min(capacity)<=0:row['portfolio_rejection']='capacity_zero';continue
            nominal=min(.10*equity_open,.005*equity_open/risk,cash/1.001,.01*capacity[0],.001*capacity[1])
            if nominal<=1e-8:row['portfolio_rejection']='cash_limit';continue
            row.update(portfolio_selected=True,notional=nominal,quantity=nominal/row['entry_price'],entry_equity=equity_open)
            cash-=nominal*1.001
            equity_open-=nominal*.001
            held[row['asset']]=row
        # Compute known close marks before removing exits whose bar ends here.
        # Their fill value replaces the close so a stopped trade cannot revive.
        open_positions=len(held)
        ending=[]
        for asset,position in held.items():
            if (position['exit_time']<clock+step or
                (position['exit_time']==clock+step and position.get('exit_timing','open')!='open')):
                ending.append(asset)
        for asset in ending:
            position=held.pop(asset)
            cash+=position['quantity']*position['exit_price']-position['notional']*.001
            position['realized_net_pnl']=position['quantity']*(position['exit_price']-position['entry_price'])-position['notional']*.002
        equity_close=mark(clock,1)
        high=max(high,equity_close)
        path.append(dict(time=clock+step,equity=equity_close,cash=cash,positions=len(held),positions_open=open_positions,drawdown=equity_close/high-1))
        if cash < -1e-6:raise AssertionError('Cash borrowing occurred')
    if held:raise ValueError('All positions must have an explicit end mark at the cutoff')
    path=pd.DataFrame(path)
    selected=pd.DataFrame(records)
    return path,selected


def summarize_portfolio(path,selected,initial=100000.0):
    """Concentration is a static attribution, never an optimized reallocation."""
    filled=selected.loc[selected.portfolio_selected] if not selected.empty else selected
    pnl=filled.realized_net_pnl.to_numpy(float) if not filled.empty else np.array([])
    positive=np.sort(pnl[pnl>0])[::-1]
    net=float(path.equity.iloc[-1]-initial) if not path.empty else 0.
    return dict(return_pct=net/initial*100,max_drawdown_pct=float(-path.drawdown.min()*100) if not path.empty else 0.,
        trades=len(filled),win_rate=float((pnl>0).mean()) if len(pnl) else np.nan,
        profit_factor=float(pnl[pnl>0].sum()/-pnl[pnl<0].sum()) if np.any(pnl<0) else np.nan,
        peak_positions=int(path.positions_open.max()) if not path.empty else 0,
        top1_positive_profit_share=float(positive[:1].sum()/positive.sum()) if len(positive) else np.nan,
        top5_positive_profit_share=float(positive[:5].sum()/positive.sum()) if len(positive) else np.nan,
        return_minus_top1_contribution_pct=(net-positive[:1].sum())/initial*100,
        return_minus_top5_contribution_pct=(net-positive[:5].sum())/initial*100,
        natural_exits=int(filled.natural_exit.sum()) if len(filled) else 0,
        boundary_marks=int(filled.censored.sum()) if len(filled) else 0,
        rejected=len(selected)-len(filled))
