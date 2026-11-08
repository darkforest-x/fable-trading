"""Post-selection uncertainty of equal-notional pair differences, not compounded NAV."""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet


def draws_by_week(differences, timestamps):
    """Sample 4-week circular moving blocks across NY entry weeks, including empty weeks."""
    ny=pd.to_datetime(timestamps,unit='ms',utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
    weekly=differences.groupby(ny.dt.to_period('W-SUN').dt.start_time).agg(['sum','count'])
    weekly=weekly.reindex(pd.date_range(weekly.index.min(),weekly.index.max(),freq='7D'),fill_value=0)
    rng=np.random.default_rng(20261109)
    starts=rng.integers(0,len(weekly),(5000,(len(weekly)+3)//4))
    draws=((starts[:,:,None]+np.arange(4))%len(weekly)).reshape(5000,-1)[:,:len(weekly)]
    sampled=weekly['sum'].to_numpy()[draws].sum(axis=1)/weekly['count'].to_numpy()[draws].sum(axis=1)
    return (100*np.quantile(sampled,[.025,.975])).tolist(),int(weekly['count'].gt(0).sum())


def main():
    paths=wallet.read_paths()
    paths=paths[paths.tp_r.isin([0,2])].copy()
    covered=leader.leadership().dropna().index
    paths=paths[pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(covered)].copy()
    gross=paths.side*(paths.exit_price-paths.entry_price)
    fees=wallet.TAKER*paths.entry_price+paths.exit_fee_rate*paths.exit_price
    paths['contribution']=(gross-fees)/paths.entry_price*np.where(paths.tp_r.eq(2),.75,.25)
    events=paths.groupby(['is_control','parent_id','event_id']).agg(
        net=('contribution','sum'),entry=('entry_ms','min'),last=('exit_bar_ms','max')).reset_index()
    real=events[~events.is_control].set_index('parent_id')
    ctl=events[events.is_control].set_index('parent_id')
    pairs=real.add_suffix('_signal').join(ctl.add_suffix('_control'),how='inner')
    if len(pairs)!=10816 or not pairs.entry_control.gt(pairs.entry_signal).all():
        raise ValueError('Changed diagnostics cohort or control timing')
    funded=pd.read_csv(leader.EXP/'funded_1.0.csv.gz')
    if not np.allclose(real.loc[funded.parent_id].net,funded.net_return):
        raise ValueError('Equal-notional return differs from actual funded paired-leg fills')
    metrics={}
    for label,start,end in [('all','2020-01-01','2026-11-09'),('dev','2020-01-01','2025-01-01'),
                           ('2025','2025-01-01','2026-01-01'),('2026','2026-01-01','2026-11-09')]:
        lo,hi=[pd.Timestamp(day,tz='UTC').value//1_000_000 for day in (start,end)]
        sample=pairs[(pairs.entry_signal>=lo)&(pairs.last_signal<hi)&(pairs.last_control<hi)]
        difference=sample.net_signal-sample.net_control
        bounds,weeks=draws_by_week(difference,sample.entry_signal)
        window=funded[(funded.entry_ms>=lo)&(funded.exit_bar_ms<hi)]
        metrics[label]=dict(n_pairs=len(sample),nonempty_signal_weeks=weeks,
            mean_net_signal_pct=100*sample.net_signal.mean(),mean_net_control_pct=100*sample.net_control.mean(),
            difference_pp=100*difference.mean(),paired_difference_95pct_interval_pp=bounds,
            funded_top5_profit_usdt=window.cash_pnl.nlargest(5).sum(),sum_funded_closed_pnl_usdt=window.cash_pnl.sum())
    result=dict(frozen_signal_exit='75% at 2R, 25% V1.7 runner',periods=metrics,
        interval='5000 circular resamples of four NY weeks; no adjustment for prior strategy selection; individual event returns, not account NAV.',
        sample='Only pairs with both trades complete in the period. Parent timestamps determine week membership.')
    (leader.EXP/'paired_uncertainty.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
