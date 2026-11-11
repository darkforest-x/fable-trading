"""Fund the existing 1h ETC reclaim fills with a single 1,000 USDT wallet.

All exits, entry prices, fee classes, position excursions and matched clocks
come from the previously frozen target-cap study. Slip applies to market fills;
the old resting barriers remain unchanged. Compare 10% of NAV and full unlevered exposure.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[2]))
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation.month_first_week_alt_account import TAKER, MAKER, millis

SOURCE = HERE.parent/'exp-alt-month-first-week-near-target-20261110-v1/trades.csv.gz'
BARS = HERE.parent/'exp-cloud-allmarket-screen-20261108-v1/inputs/ETC/bars1h.csv.gz'


def run(allocation=.1):
    quotes = pd.read_csv(BARS,float_precision='round_trip')
    clock = quotes.time.to_numpy()
    ohlc = quotes[['open','high','low','close']].to_numpy()
    rows = pd.read_csv(SOURCE)
    rows = rows[rows.symbol.eq('ETC')].copy()
    rows['event_id'] = rows.trade_id
    rows['entry_price'] = rows.entry_fill
    rows['entry_ms'] = rows.entry_time.map(millis)
    rows['exit_bar_ms'] = rows.exit_time.map(millis)
    rows['rail_side'] = rows.side
    rows['side'] = np.where(rows.rail_side.eq('lower'),1,-1)
    oi = clock.searchsorted(rows.exit_bar_ms)
    assert np.array_equal(clock[oi],rows.exit_bar_ms)
    op = ohlc[oi,0]
    target = rows.exit_reason.eq('target')
    at_open = ((target & ((op-rows.target_price)*rows.side >= 0)) |
               (rows.exit_reason.eq('stop') & ((op-rows.stop_price)*rows.side <= 0)) |
               rows.exit_reason.eq('timeout'))
    rows['exit_phase'] = np.where(at_open,'open','intrabar')
    rows['exit_fee_rate'] = np.where(target,MAKER,TAKER)
    cache = lambda _: (clock,ohlc)
    results, all_trades = [], []
    for (cap,control),part in rows.groupby(['cap','is_control']):
        assert len(part)==29
        for period,start,end in [('full','2022-02-01T00:00Z','2026-11-08T00:00Z'),
                                 ('early','2022-02-01T00:00Z','2025-01-01T00:00Z'),
                                 ('later','2025-01-01T00:00Z','2026-11-08T00:00Z')]:
            sample = part[(part.entry_ms>=millis(start)) & (part.exit_bar_ms<millis(end))]
            data=wallet.prepare(sample,1,millis(start),millis(end),quote_loader=cache)
            assert data['gap_marks']==0
            for bps in (0,1,5):
                trades,curve=wallet.account(data,slip=bps/10000,exposure=allocation)
                intrabar=trades[trades.exit_phase.eq('intrabar')]
                slip=np.where(intrabar.exit_fee_rate.eq(TAKER),bps/10000,0.)
                reserve=intrabar.quantity*intrabar.exit_price*(slip+intrabar.exit_fee_rate*(1+slip))
                exit_ends=pd.to_datetime(intrabar.exit_bar_ms+wallet.HOUR,unit='ms',utc=True)
                curve['lower_nav']-=reserve.groupby(exit_ends).sum().reindex(curve.index,fill_value=0.)
                if bps==0:
                    np.testing.assert_allclose(trades.net_return*100,trades.net_return_pct,atol=1e-8)
                result=wallet.summarize(trades,curve)
                np.testing.assert_allclose(1000+trades.cash_pnl.sum(),result['final_usdt'],atol=.0001)
                results.append(dict(cap=cap,control=control,slip_bps=bps,period=period,eligible=len(sample),
                                    wins=trades.cash_pnl.gt(0).sum(),**result))
                if not control and period=='full' and bps==1:
                    all_trades.append(trades)
    suffix = '' if allocation == .1 else '_full_notional'
    pd.DataFrame(results).to_csv(HERE/f'first_touch_target_accounts{suffix}.csv',index=False)
    pd.concat(all_trades,ignore_index=True).to_csv(HERE/f'first_touch_target_trades{suffix}.csv',index=False)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allocation',type=float,choices=(.1,1.),default=.1)
    run(parser.parse_args().allocation)
