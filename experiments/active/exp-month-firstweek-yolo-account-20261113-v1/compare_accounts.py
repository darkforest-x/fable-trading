"""Read actually admitted fills. Summarize realized returns, risk and excursions.
Never rerun the matching engine or infer a stop/target from future OHLC here.
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
HERE=Path(__file__).resolve().parent

def summarize(trades,accounts):
    result=[]
    for pool,all_rows in trades.groupby('pool'):
        account=accounts[accounts.pool.eq(pool)].iloc[0]
        closed=all_rows[all_rows.account_realized_net_u.notna()].copy()
        net=closed.account_realized_net_u
        gains=net[net>0].sum()
        losses=-net[net<0].sum()
        assert len(closed)==account.closed_trades
        assert np.isclose(1000+net.sum()+account.final_unrealized_pnl_u,account.final_equity_u,atol=.011)
        result.append(dict(pool=pool,settled=len(net),winners=int((net>0).sum()),losers=int((net<0).sum()),
            win_rate_pct=100*(net>0).mean(),net_profit_factor=gains/losses if losses else np.nan,
            final_equity_u=account.final_equity_u,net_u=net.sum(),return_pct=account.total_return_pct,
            drawdown_5m_pct=account.max_drawdown_5m_close_pct,intrabar_envelope_pct=account.max_intrabar_dd_envelope_pct,
            fees_u=account.entry_fees_u+account.exit_fees_u,funding_u=account.funding_net_u,
            stop=int(closed.exit_reason.eq('stop').sum()),target=int(closed.exit_reason.eq('target').sum()),
            timeout=int(closed.exit_reason.eq('timeout').sum()),median_mfe_pct=closed.mfe_pct.median(),
            median_mae_pct=closed.mae_pct.median()))
    return pd.DataFrame(result)
if __name__=='__main__':
    cli=argparse.ArgumentParser();cli.add_argument('--trades',nargs='+',type=Path,required=True);cli.add_argument('--accounts',nargs='+',type=Path,required=True)
    args=cli.parse_args()
    x=summarize(pd.concat(map(pd.read_csv,args.trades)),pd.concat(map(pd.read_csv,args.accounts)))
    x.to_csv(HERE/'comparison.csv',index=False)
    print(x.round(4).to_string(index=False))
