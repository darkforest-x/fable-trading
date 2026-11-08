"""Hold half of starting capital per sleeve, with distinct realized-funding cash flows.

Use the frozen BTC fills and 6h ALT basket. All marks use member closes available at
the recorded time. Apply the source study's adverse ordering at funding boundaries.
"""
from collections import defaultdict

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_funding as funding
from yoyo.evaluation import monday_breadth_opt_payoffs as prices
from yoyo.evaluation.monday_breadth_extension_pairs import EXP, PARENT
from yoyo.evaluation.monday_breadth_opt_exit_study import member_bridge
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p

OUT = EXP / 'sleeves'
RULES = ('medium_time', 'dual_source_specific')


def funded_marks(alt, btc):
    members = alt.merge(member_bridge(), on=['minutes', 'known']).assign(sleeve='ALT')
    direct = btc.drop_duplicates(['decision', 'exit_time', 'policy']).assign(symbol='BTCUSDT', sleeve='BTC')
    legs = pd.concat([members, direct], ignore_index=True)
    legs['key'] = list(zip(legs.sleeve, legs.decision, legs.exit_time))
    legs = legs.drop_duplicates(['key', 'symbol'])
    counts = legs.groupby('key').symbol.size()
    paths = {}
    endpoint_cost = defaultdict(float)
    for symbol, group in legs.groupby('symbol'):
        q, _ = prices.load_full_ohlc(symbol, group.decision.min(), group.exit_time.max(), repair_missing=False)
        months = sorted(set(group.decision.dt.strftime('%Y-%m')) | set(group.exit_time.dt.strftime('%Y-%m')))
        history = funding.rates(symbol, months=months, local_only=True)
        if history.empty:
            raise ValueError(f'Missing funding data for {symbol}')
        settlements = history.realized_rate * history.mark_price
        for trade in group.itertuples():
            start, end = trade.decision, trade.exit_time
            marks = pd.date_range(start, end, freq='5min')
            entry = q.open.at[start]
            px = np.r_[entry, q.close.reindex(marks[:-1]).to_numpy()] / entry
            settle = settlements.loc[start:end].copy()
            at_boundary = settle.index.isin([start, end])
            settle.loc[at_boundary] = settle.loc[at_boundary].clip(lower=0)
            if not np.isfinite(px).all() or not np.isfinite(settle).all():
                raise ValueError(f'Unobserved prices or funding on {symbol} {start}')
            cash = settle.reindex(marks, fill_value=0).cumsum().to_numpy() / entry
            n = counts.at[trade.key]
            if trade.key not in paths:
                paths[trade.key] = pd.Series(0., index=marks)
            paths[trade.key] += (px - .0005 - cash) / n
            endpoint_cost[trade.key] += 100 * cash[-1] / n
    return paths, endpoint_cost


def curve(group, asset, paths, costs, grid):
    equity = 1.
    values = pd.Series(np.nan, index=grid)
    values.iloc[0] = equity
    for trade in group.sort_values('decision').itertuples():
        key = (asset, trade.decision, trade.exit_time)
        np.testing.assert_allclose(costs[key], trade.net_pct - trade.funded_pct, atol=1e-7)
        marks = paths[key]
        values.loc[marks.index] = equity * marks
        equity *= 1 + trade.funded_pct / 100
        values.loc[trade.exit_time] = equity
    return values.ffill()


def compound(group, field='net_pct', slip=0):
    return (1 + (group[field] - slip) / 100).prod()


def calendar_summary(name, legs):
    results = []
    for year, subset in legs.groupby(legs.decision.dt.year):
        perf = subset.groupby('asset').net_pct.agg(lambda x: (1+x/100).prod()-1)
        results.append(dict(strategy=name,year=year,distinct=subset.decision.nunique(),legs=len(subset),
                            btc_fee_growth=100*perf.at['BTC'],alt_fee_growth=100*perf.at['ALT'],
                            equal_sleeves_fee_growth=100*perf.mean()))
    return results


def matched_summary(name, legs, fixed, native, matched):
    alt = fixed[fixed.candidate_id.eq('freq36_ALT') & fixed.eligible_coverage]
    alt = alt.rename(columns={'matched_actual_pct':'actual','matched_random_pct':'random'})
    if name=='medium_time':
        btc = fixed[fixed.candidate_id.eq('freq36_BTC') & fixed.eligible_coverage]
        btc = btc.rename(columns={'matched_actual_pct':'actual','matched_random_pct':'random'})
    else:
        origins = legs[legs.asset.eq('BTC')][['decision','source','policy','net_pct']]
        draws = matched.merge(origins,on=['decision','source'])
        draws = draws.merge(native,left_on=['random_entry','policy'],right_on=['entry_time','policy'],
                            suffixes=('_event','_control'),validate='many_to_one')
        btc = draws.groupby('decision',as_index=False).agg(actual=('net_pct_event','first'),random=('net_pct_control','mean'))
    groups = [btc, alt]
    # Equal weight to each sleeve's mean matched event, never impute unmatched trades.
    edge = pd.concat([(g.set_index('decision').actual - g.set_index('decision').random)/(2*len(g)) for g in groups])
    return dict(strategy=name,btc_matched=len(btc),alt_matched=len(alt),total=len(legs),actual=sum(g.actual.mean() for g in groups)/2,
                random=sum(g.random.mean() for g in groups)/2,edge_pp=edge.sum(),monthly_sign_p=paired_month_p(edge))


def run():
    OUT.mkdir(exist_ok=True)
    original = pd.read_parquet(PARENT/'audit/fixed/all_rule_and_account_events.parquet')
    alt = original[original.candidate_id.eq('freq36_ALT')].copy()
    btc = pd.read_parquet(EXP/'dual/events.parquet').assign(asset='BTC')
    btc = btc[btc.strategy.isin(RULES)]
    a26, b26 = alt[alt.epoch.eq(3)], btc[btc.epoch.eq(3)]
    paths, costs = funded_marks(a26, b26)
    idx = pd.date_range(pd.Timestamp('2026-01-01',tz='UTC'),max(a26.exit_time.max(),b26.exit_time.max()),freq='5min')
    ac = curve(a26,'ALT',paths,costs,idx)
    fixed = pd.read_parquet(PARENT/'controls/fixed_paired_events.parquet')
    native = pd.read_parquet(EXP/'dual/native_fills.parquet')[['entry_time','policy','net_pct']]
    base = pd.read_parquet(PARENT/'controls/fixed_matches.parquet',filters=[('candidate_id','=','freq36_BTC')])
    draws = base.dropna(subset=['random_entry','net_pct'])[['decision','random_entry']].assign(source='medium')
    case = pd.read_parquet(PARENT/'btc_target/matched_draws_2026.parquet')[['decision','random_entry']].assign(source='case')
    draws = pd.concat([draws,case],ignore_index=True)
    annual, rows, controls, monthly = [], [], [], []
    curves = {'ALT':ac}
    for name in RULES:
        b = b26[b26.strategy.eq(name)]
        both = pd.concat([a26,b],ignore_index=True)
        bc = curve(b,'BTC',paths,costs,idx)
        eq = (ac+bc)/2
        curves[name] = eq
        curves[name+'_BTC'] = bc
        np.testing.assert_allclose(eq.iloc[-1],(compound(b,'funded_pct')+compound(a26,'funded_pct'))/2)
        counts = both.groupby(both.decision.dt.strftime('%Y-%m')).decision.nunique()
        monthly.extend(dict(strategy=name,month=month,n=int(n)) for month,n in counts.items())
        all_legs = pd.concat([alt,btc[btc.strategy.eq(name)]],ignore_index=True)
        annual.extend(calendar_summary(name,all_legs))
        sep = b[b.decision.between('2026-09-15','2026-09-18')]
        assert len(sep)==1
        rows.append(dict(strategy=name,full_distinct=all_legs.decision.nunique(),distinct=both.decision.nunique(),legs=len(both),
                         btc=len(b),alt=len(a26),profitable_legs_pct=100*both.funded_pct.gt(0).mean(),
                         min_month=counts.min(),max_month=counts.max(),funded_growth=100*(eq.iloc[-1]-1),
                         marked_funded_dd=100*(1-eq/eq.cummax().clip(lower=1)).max(),
                         btc_marked_funded_dd=100*(1-bc/bc.cummax().clip(lower=1)).max(),
                         alt_marked_funded_dd=100*(1-ac/ac.cummax().clip(lower=1)).max(),
                         stressed_growth=100*((compound(b,'funded_pct',.2)+compound(a26,'funded_pct',.2))/2-1),
                         september_btc_funded_points=sep.iloc[0].funded_points))
        controls.append(matched_summary(name,both,fixed,native,draws))
    equity = pd.DataFrame(curves)
    equity.to_parquet(OUT/'equity_5m.parquet')
    equity.resample('1D').last().to_csv(OUT/'daily_funded.csv',index_label='utc_day')
    for file,records in [('periods',annual),('accounts_2026',rows),('controls',controls),('monthly',monthly)]:
        pd.DataFrame(records).to_csv(OUT/(file+'.csv'),index=False)


if __name__=='__main__':
    run()
