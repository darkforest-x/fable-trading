"""Aggregate two frozen, independently collateralized VectorBT books at 500 USDT each."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_cost as breakout
from yoyo.evaluation import cloud_close_waves_wallet as wick

EXP = wick.EXP.parent/'exp-cloud-close-breakout-blend-20261109-v1'
NAV = ['nav','open_nav','open_exit_nav','lower_nav','upper_nav']
FILL = ['event_id','symbol','entry_ms','exit_bar_ms','notional','cash_pnl','net_return']


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed two-sleeve blend study')
    out = {}
    for label,market_stress in [('fee_only',False),('extra_5bp',True)]:
        suffix = '_stress' if market_stress else ''
        paths = [(wick.EXP/f'hourly_1.5x{suffix}.csv.gz',wick.EXP/f'funded_1.5x{suffix}.csv.gz')]
        paths.append((breakout.EXP/'hourly_breakout_delayed_12h.csv.gz',breakout.EXP/'funded_breakout_delayed_12h.csv.gz')
                      if market_stress else (breakout.delay.EXP/'equity.csv.gz',breakout.delay.EXP/'funded.csv.gz'))
        curves,trades = [],[]
        for sleeve,(equity_file,fills_file) in zip(['reclaim','breakout'],paths):
            eq = pd.read_csv(equity_file,index_col=0,parse_dates=[0],float_precision='round_trip')[NAV]
            rows = pd.read_csv(fills_file,usecols=FILL,float_precision='round_trip')
            if not np.isclose(eq.nav.iat[-1],1000+rows.cash_pnl.sum(),atol=.005):
                raise ValueError('Sleeve does not reconcile before sizing')
            rows[['cash_pnl','notional']] *= .5
            rows['sleeve'] = sleeve
            trades.append(rows)
            curves.append(eq*.5)
        if not curves[0].index.equals(curves[1].index) or not curves[0].index.is_unique:
            raise ValueError('Nonidentical two-sleeve hourly clock')
        eq = curves[0]+curves[1]
        funds = pd.concat(trades,ignore_index=True)
        if not np.isclose(1000+funds.cash_pnl.sum(),eq.nav.iat[-1],atol=.005):
            raise ValueError('Combined account does not conserve initial capital')
        combined = {p:breakout.wallet.summarize(funds,eq,*bounds) for p,bounds in breakout.branch.PERIODS.items()}
        daily = pd.concat([c.nav for c in curves],axis=1).resample('1D').last().pct_change().dropna()
        out[label] = dict(metrics=combined,daily_return_correlation=float(daily.iloc[:,0].corr(daily.iloc[:,1])),
                          hourly_marks=len(eq),sleeve_ending_usdt=[float(c.nav.iat[-1]) for c in curves])
        eq.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        funds.to_csv(EXP/f'funded_{label}.csv.gz',index=False)
    breakout.branch._write_json(EXP/'summary.json',dict(cases=out,initial_allocation_usdt=[500,500]))
    print(json.dumps(out['extra_5bp']['metrics']['all']))


if __name__=='__main__':
    main()
