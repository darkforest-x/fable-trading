"""Resolve the ambiguous lifetime OKX drawdown and conditionally transfer the frozen limit geometry."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binding_risk_target as study
from yoyo.evaluation import cloud_close_drawdown_bounds as dd
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_volatility_expansion as market

EXP = study.EXP


def refine():
    equity = pd.read_csv(EXP/'hourly.csv.gz', index_col='hour_end_utc', parse_dates=True)
    old = dd.max_drawdown(equity)
    f = pd.read_csv(EXP/'funded.csv.gz',usecols=['event_id','notional','cash_pnl'])
    legs = wallet.read_paths()
    legs = legs[legs.tp_r.isin([0,2]) & legs.event_id.isin(f.event_id)]
    changed = pd.read_csv(EXP/'changed_limits.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    changed = changed[changed.event_id.isin(f.event_id)]
    legs = pd.concat([legs[~(legs.tp_r.eq(2) & legs.event_id.isin(changed.event_id))],changed],ignore_index=True)
    legs = legs.merge(f,on='event_id',validate='many_to_one')
    if len(legs)!=2*len(f) or legs.duplicated(['event_id','tp_r']).any():
        raise ValueError('Missing binding-risk funded leg')
    legs['signed_size'] = legs.notional / legs.entry_price * np.where(legs.tp_r.eq(2),2/3,1/3) * legs.side
    pnl = legs.signed_size*(legs.exit_price-legs.entry_price)-legs.signed_size.abs()*(legs.entry_price*wallet.TAKER+legs.exit_price*legs.exit_fee_rate)
    if not np.allclose(pnl.groupby(legs.event_id).sum(),f.set_index('event_id').cash_pnl.sort_index(),atol=1e-7):
        raise ValueError('Native positions do not match funded cash')
    native5 = pd.read_csv(dd.INPUTS/'quotes.csv',dtype={'symbol':str},keep_default_na=False)
    native1 = pd.read_csv(wallet.ROOT/'data/research/cloud_close_drawdown_bounds_1m_20261109/quotes.csv',dtype={'symbol':str},keep_default_na=False)
    records = []
    for step,quotes,times in [(300_000,native5,[dd.PEAK,*dd.TROUGHS]),
                              (60_000,native1,[dd.PEAK,pd.Timestamp('2025-11-12T14:00Z')])]:
        for stamp in times:
            end = stamp+pd.Timedelta(hours=1)
            fine = dd.hour_envelope(stamp,equity.loc[end],legs,quotes,step)
            if step==60_000 and (fine['active_symbols']!=fine['observed_symbols'] or fine['min_observed_close'] is None):
                raise ValueError('Unobserved minute-level joint price')
            equity.loc[end,['lower_nav','upper_nav']]=[fine['refined_lower'],fine['refined_upper']]
            records.append(dict(step_ms=step,**fine))
    late = legs[~legs.exit_phase.eq('open')]
    fees = (late.signed_size.abs()*late.exit_price*late.exit_fee_rate).groupby(pd.to_datetime(late.exit_bar_ms+wallet.HOUR,unit='ms',utc=True)).sum()
    coarse = equity.index.difference(pd.Index([r['hour_end'] for r in records]))
    equity.loc[coarse,'lower_nav']-=fees.reindex(coarse,fill_value=0)
    entry = (legs.signed_size.abs()*legs.entry_price*wallet.TAKER).groupby(pd.to_datetime(legs.entry_ms+wallet.HOUR,unit='ms',utc=True)).sum()
    equity['upper_nav'] = np.maximum(equity.upper_nav,equity.open_nav+entry.reindex(equity.index,fill_value=0))
    seen = np.r_[1000.,equity[['open_nav','open_exit_nav','nav']].to_numpy().ravel()]
    lower = max(100*(1-(seen/np.maximum.accumulate(seen)).min()),100*(1-records[-1]['min_observed_close']/records[-2]['max_observed_close']))
    upper = dd.max_drawdown(equity)
    if lower>upper or upper>old+.01:
        raise ValueError('Refined drawdown bounds disagree')
    pd.DataFrame(records).to_csv(EXP/'okx_refined_marks.csv',index=False)
    return dict(terminal_usdt=float(equity.nav.iloc[-1]),hourly_upper_pct=old,refined_upper_pct=upper,observed_lower_pct=lower)


def main():
    summary = json.loads((EXP/'summary.json').read_text())
    if (EXP/'validation.json').exists():
        raise FileExistsError('Frozen validation already exists')
    refined = refine()
    original = pd.read_csv(EXP/'account_metrics.csv')
    full = original[(original.policy.eq('nearer_protection')) & original.period.eq('all') & original.scenario.eq('actual')].iloc[0].to_dict()
    full['dd_bound_pct']=refined['refined_upper_pct']
    qualifies = second.meets(full)
    result = dict(OKX=refined,okx_full_qualifies_with_refinement=qualifies,binance_transfer=False)
    if summary['dev_pass'] and qualifies and full['final_usdt']>original[(original.policy.eq('fixed_stop_risk')) & original.period.eq('all')].final_usdt.item():
        base = market.binance.assembled(set(market.binance.patch.affected_tickers()))
        paths,audit = study.reroute(base,'Binance')
        audit.to_csv(EXP/'geometry_Binance.csv.gz',index=False)
        paths[paths.tp_r.eq(2)&paths.event_id.isin(audit.loc[audit.changed,'event_id'])].to_csv(EXP/'changed_limits_Binance.csv.gz',index=False)
        real,controls = paths[~paths.is_control],paths[paths.is_control]
        records=[]
        for label,pool,slip in [('actual',real,0.),('matched',real[real.parent_id.isin(controls.parent_id)],0.),('controls',controls,0.),('stress',real,.0005)]:
            metrics,trades,equity=market.fund('Binance',pool,slip)
            records.extend(dict(venue='Binance',scenario=label,period=p,**m) for p,m in metrics.items())
            if label=='actual':
                trades.to_csv(EXP/'funded_Binance.csv.gz')
                equity.to_csv(EXP/'hourly_Binance.csv.gz',index_label='hour_end_utc')
                result.update(binance_transfer=True,binance_changed_real=int((audit.changed & ~audit.is_control).sum()),
                              binance_dev_pass=second.meets(metrics['dev']),binance_full_pass=second.meets(metrics['all']))
        pd.DataFrame(records).to_csv(EXP/'binance_metrics.csv',index=False)
    (EXP/'validation.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(result)


if __name__=='__main__':
    main()
