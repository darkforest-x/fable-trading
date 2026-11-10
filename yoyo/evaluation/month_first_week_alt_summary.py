"""Read the actual VectorBT fills once; all averages weight independent trades equally."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation.month_first_week_alt_entries import CONFIG, DEST, RULES


def group_stats(g):
    net = g['net_return_pct']
    wins = net > 0
    losses = net < 0
    mfe = g['mfe_pct']
    return dict(n=len(g), win_pct=100*wins.mean(), mean_net_pct=net.mean(),
                median_net_pct=net.median(), profit_factor=net[wins].sum() / -net[losses].sum()
                if losses.any() else np.nan, median_mfe_pct=mfe.median(),
                mfe_2_or_more=int((mfe>=2).sum()), lost_after_mfe2=int((losses & (mfe>=2)).sum()),
                losses=int(losses.sum()), mean_stress_pct=g['stress_net_pct'].mean(),
                ambiguous_dual=int(g['ambiguous_sl_tp'].sum()))


def month_interval(g):
    monthly = g.groupby('month').delta.agg(['sum','count'])
    weights = np.random.default_rng(CONFIG['seed']).multinomial(len(monthly),
              np.full(len(monthly),1/len(monthly)),size=4000)
    return np.quantile((weights@monthly['sum'])/(weights@monthly['count']), [.025,.975]).tolist()


def summarize(trades):
    events = pd.read_csv(DEST/'events.csv.gz')
    months = pd.read_csv(DEST/'months.csv.gz')
    decisions = pd.read_csv(DEST/'candidates.csv.gz',parse_dates=['entry_time','touch'])
    sources = pd.read_csv(DEST/'sources.csv.gz')
    fills = decisions.merge(trades, on='trade_id', how='left', validate='1:1',suffixes=('','_engine'))
    assert set(decisions.trade_id)==set(trades.trade_id)
    fills['entry_time']=pd.to_datetime(fills.entry_time,utc=True)
    fills['exit_time']=pd.to_datetime(fills.exit_time,utc=True)
    fills['period']='cross_split'
    cut=pd.Timestamp(CONFIG['split_utc'])
    fills.loc[fills.entry_time >= cut, 'period']='later'
    fills.loc[(fills.entry_time < cut) & (fills.exit_time < cut), 'period']='early'
    # Isolated equal entry notionals; cost sensitivity only, no altered order triggers.
    is_target=fills.exit_reason.eq('target')
    fills['stress_net_pct']=fills.net_return_pct-.05*(1+(~is_target)*(fills.exit_price/fills.entry_fill))
    fills['net_pnl_per_100_usdt']=fills.net_return_pct
    fills['peak_gross_pnl_per_100_usdt']=fills.mfe_pct
    fills['peak_gross_pnl_per_100_usdt_upper_bound']=fills.mfe_pct_optimistic_upper_bound
    fills['trough_gross_pnl_per_100_usdt']=-fills.mae_pct.abs()
    keep=list(decisions.columns)+[k for k in trades.columns if k not in decisions.columns]+[
        'period','stress_net_pct','net_pnl_per_100_usdt','peak_gross_pnl_per_100_usdt','peak_gross_pnl_per_100_usdt_upper_bound','trough_gross_pnl_per_100_usdt']
    fills[keep].to_csv(DEST/'position_ledger.csv.gz',index=False)
    headings={'symbol':'合约','month':'首周月份','side':'触价侧','entry_rule':'入场规则','status':'状态',
        'touch':'首次触价小时UTC','entry_time':'开仓小时UTC','entry_fill':'开仓价','stop_price':'止损价',
        'target_price':'目标价','exit_time':'退出小时UTC','exit_reason':'退出原因','exit_price':'退出价',
        'net_pnl_per_100_usdt':'每100U已实现净盈亏U','mfe_pct':'最大浮盈已确认下界%',
        'mfe_pct_optimistic_upper_bound':'最大浮盈可能上界%','mfe_time':'浮盈峰值小时UTC',
        'mae_pct':'最大浮亏幅度%','mae_time':'浮亏峰值小时UTC','unrealized_net_pct':'未平仓期末净浮盈亏%',
        'ambiguous_sl_tp':'同小时双触','fees_pct':'手续费占初始名义%'}
    readable=fills.loc[~fills.is_control,list(headings)].copy()
    readable['side']=readable.side.map({'upper':'上轨做空','lower':'下轨做多'})
    readable['entry_rule']=readable.entry_rule.map(dict(zip(RULES,['触价后直接进','触价小时收内','触价小时收外',
        '等一根小时线收回','等连续两根小时线收回','等4小时收回','等日线收回'],strict=True)))
    readable.sort_values(['entry_time','symbol','entry_rule']).rename(columns=headings).to_csv(
        DEST/'逐笔浮盈与盈亏.csv',index=False,float_format='%.8g',encoding='utf-8-sig')
    done=fills[fills.status.eq('closed')]
    actual=done[~done.is_control].copy()
    assert actual.net_return_pct.notna().all()
    rows=[]
    for period, group in [('all',actual),('early',actual[actual.period=='early']),('later',actual[actual.period=='later'])]:
        for rule, frame in group.groupby('entry_rule'):
            for side, subset in [('both',frame),*list(frame.groupby('side'))]:
                rows.append(dict(period=period,entry_rule=rule,side=side,**group_stats(subset)))
    stats=pd.DataFrame(rows)
    stats.to_csv(DEST/'entry_metrics.csv',index=False)
    symbols=[]
    for (symbol,rule,side),g in actual.groupby(['symbol','entry_rule','side']):
        symbols.append(dict(symbol=symbol,entry_rule=rule,side=side,**group_stats(g)))
    pd.DataFrame(symbols).to_csv(DEST/'symbol_metrics.csv.gz',index=False)
    pairs=actual.merge(done[done.is_control][['paired_id','net_return_pct','period']],
                       on='paired_id',suffixes=('','_random'),validate='1:1')
    pairs['delta']=pairs.net_return_pct-pairs.net_return_pct_random
    comparisons=[]
    for period in ['all','early','later']:
        sub=pairs if period=='all' else pairs[(pairs.period==period)&(pairs.period_random==period)]
        for rule,g in sub.groupby('entry_rule'):
            ci=month_interval(g)
            comparisons.append(dict(period=period,entry_rule=rule,n=len(g),mean_signal_pct=g.net_return_pct.mean(),
                mean_random_pct=g.net_return_pct_random.mean(),delta_pp=g.delta.mean(),
                month_bootstrap_ci_low_pp=ci[0],month_bootstrap_ci_high_pp=ci[1]))
    pd.DataFrame(comparisons).to_csv(DEST/'random_comparison.csv',index=False)
    sides=[]
    for side,g in events.groupby('side'):
        full=g.dropna(subset=['inward_close_168h_pct'])
        target=full.high if side=='upper' else full.low
        sides.append(dict(side=side,touches=len(g),n_168h=len(full),
            reached_edge=int(full.reached_near_168h.sum()),inward_end=int(full.inward_close_168h_pct.gt(0).sum()),
            n_24h=int(g.inward_24h_pct.notna().sum()),inward_1pct_24h=int(g.inward_24h_pct.ge(1).sum()),
            median_target_distance_pct=100*(target/full.level-1).abs().median()))
    symbol_rates=events.pivot_table(index='symbol',columns='side',values='reached_near_168h',aggfunc=['count','mean'])
    both_sides=symbol_rates[symbol_rates['count'].min(axis=1,skipna=False)>=5]['mean']
    pick=stats.query("period=='early' and side=='both'").nlargest(1,'mean_net_pct').iloc[0].entry_rule
    later=stats[(stats.period=='later') & (stats.side=='both') & (stats.entry_rule==pick)].iloc[0].to_dict()
    summary=dict(source_contracts=len(sources),complete_month_contracts=months[months.complete].symbol.nunique(),
        touch_contracts=events.symbol.nunique(),hours=int(sources.hours.sum()),valid_coin_months=int(months.complete.sum()),
        touched_months=int(months.has_touch.sum()),first_touches=len(events),side_stats=sides,
        first_month=months[months.complete].month.min(),last_month=months[months.complete].month.max(),
        symbols_min_five_per_side=len(both_sides),upper_reach_rate_higher_symbols=int((both_sides.upper>both_sides.lower).sum()),
        early_pick=pick,pick_later=later,marked_capital_per_independent_trade=100,
        actual_status=fills[~fills.is_control].status.value_counts().to_dict(),
        actual_exit_reasons=actual.exit_reason.value_counts().to_dict(),
        actual_controls=len(pairs), candidate_rules=RULES)
    (DEST/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return summary


if __name__ == '__main__':
    print(json.dumps(summarize(pd.read_csv(DEST/'trades.csv.gz')), indent=2))
