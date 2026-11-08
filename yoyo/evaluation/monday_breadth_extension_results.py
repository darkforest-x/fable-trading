"""Index the primary 2026 backtest and matched controls across all tested alternatives."""
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_breadth_extension_pairs import EXP,PARENT


def read(path):
    return pd.read_csv(EXP/path)


def rows_from(frame, family, source, columns, *, note=None):
    rows = frame[list(columns)].rename(columns=columns).copy()
    rows['family'] = family
    rows['source'] = source
    rows['cost_and_scope'] = note
    return rows


def run():
    rows=[]
    p=read('pairs/periods.csv').query('epoch==3').merge(read('pairs/controls.csv'),on='pair')
    rows.append(rows_from(p,'shared_pairs','pairs/periods.csv',{
        'pair':'rule','n':'events','funded_win':'funded_win_pct','funded_mean':'funded_mean_pct','funded_growth':'funded_growth_pct',
        'funded_growth_stress':'stressed_growth_pct','marked_close_dd_before_funding':'dd_5m_before_funding_pct',
        'matched':'matched_events','edge':'matched_edge_pp','p':'monthly_p','p_holm_six':'family_holm_p'},
        note='single full-capital; six alternatives; controls compare fees-only'))
    d=read('dual/periods.csv').query('period=="3"').merge(read('dual/controls.csv').drop(columns='n'),on='strategy')
    rows.append(rows_from(d,'BTC_exit','dual/periods.csv',{
        'strategy':'rule','n':'events','win':'funded_win_pct','mean':'funded_mean_pct','growth':'funded_growth_pct',
        'stress_growth':'stressed_growth_pct','marked_dd':'dd_5m_before_funding_pct','pairs':'matched_events',
        'edge':'matched_edge_pp','p':'monthly_p','holm':'family_holm_p'},note='single BTC; matched funded'))
    s=read('sleeves/accounts_2026.csv').merge(read('sleeves/controls.csv'),on='strategy')
    s['matched']=s.btc_matched+s.alt_matched
    rows.append(rows_from(s,'two_sleeves','sleeves/accounts_2026.csv',{
        'strategy':'rule','distinct':'events','full_distinct':'full_history_entries','profitable_legs_pct':'funded_win_pct',
        'funded_growth':'funded_growth_pct','marked_funded_dd':'dd_5m_funded_pct','stressed_growth':'stressed_growth_pct',
        'matched':'matched_events','edge_pp':'matched_edge_pp','monthly_sign_p':'monthly_p','september_btc_funded_points':'sept_btc_points'},
        note='equal starting capital; win counts individual signal executions; controls sleeve-average event effects before funding'))
    s=read('partial/periods.csv').query('period=="3"').merge(read('partial/accounts_2026.csv'),on='exit_policy')
    main=read('sleeves/accounts_2026.csv').set_index('strategy').loc['dual_source_specific']
    s['events']=main.distinct
    s['all_events']=main.full_distinct
    s['win']=main.profitable_legs_pct
    ctrl=read('sleeves/controls.csv').set_index('strategy').loc['dual_source_specific']
    partial_ctrl=read('partial/controls.csv')
    if not partial_ctrl.covered_case_clocks.eq(0).all():
        raise ValueError('Recompute the matched partial-sleeve effects on new case support')
    s['matched']=ctrl.btc_matched+ctrl.alt_matched
    s['edge']=ctrl.edge_pp
    s['p']=ctrl.monthly_sign_p
    rows.append(rows_from(s,'two_sleeves_case_exit','partial/accounts_2026.csv',{
        'exit_policy':'rule','events':'events','all_events':'full_history_entries','win':'funded_win_pct','combined_funded_growth':'funded_growth_pct',
        'combined_funded_marked_dd':'dd_5m_funded_pct','combined_stressed_growth':'stressed_growth_pct','matched':'matched_events',
        'edge':'matched_edge_pp','p':'monthly_p','partial_sept_net_points':'sept_btc_points'},note='same eligible random support as source-specific sleeves; zero matched 2026 case entries'))
    for suffix in ('','below/'):
        folder='btc/'+suffix
        b=read(folder+'rule_summary.csv')
        holm=next(c for c in b if c.startswith('p_holm_'))
        rows.append(rows_from(b,'BTC_regime' if not suffix else 'BTC_regime_below',folder+'rule_summary.csv',{
            'rule':'rule','2026_accepted_n':'events','n_accepted_all_history':'full_history_entries','2026_funded_accepted_mean_net_pct':'funded_mean_pct',
            '2026_funded_accepted_win_pct':'funded_win_pct','2026_funded_accepted_growth_pct':'funded_growth_pct',
            '2026_funded_accepted_max_drawdown_pct':'settled_funded_dd_pct','matched_events_n':'matched_events',
            'mean_paired_edge_funded_pp':'matched_edge_pp','p_month':'monthly_p',holm:'family_holm_p'},note='unchanged original BTC alert clock; funded controls also meet EMA rule'))
    r=read('risk/accounts.csv')
    risk=r.query('scope=="all_fee" and extra_roundtrip_pp==0')[['strategy','sleeve_deployment','max_dd_pct']].rename(columns={'max_dd_pct':'long_dd'})
    r=r.query('scope=="2026_funded"').merge(risk,on=['strategy','sleeve_deployment'])
    r0=r[r.extra_roundtrip_pp.eq(0)].merge(r[r.extra_roundtrip_pp.eq(.2)][['strategy','sleeve_deployment','growth_pct']],
                                           on=['strategy','sleeve_deployment'],suffixes=('','_stress'))
    r0['size_rule']=r0.strategy+' @ '+r0.sleeve_deployment.astype(str)
    rows.append(rows_from(r0,'cash_reserve','risk/accounts.csv',{'size_rule':'rule','distinct':'events','growth_pct':'funded_growth_pct',
        'max_dd_pct':'dd_5m_funded_pct','long_dd':'all_history_5m_fee_only_dd_pct','growth_pct_stress':'stressed_growth_pct'},
        note='post hoc sizing sensitivity; percentage of each sleeve used per signal; all-history drawdown before funding'))
    integ=read('risk/integrated_sensitivity.csv').query('subset=="all_2026"').iloc[0]
    ctrl=read('risk/integrated_controls.csv').iloc[0]
    equity=rows[-1]
    ii=equity.rule.str.startswith('below_plus_case')
    equity.loc[ii,'full_history_entries']=read('risk/integrated_years.csv').distinct.sum()
    equity.loc[ii,'funded_win_pct']=integ.win_pct
    equity.loc[ii,'matched_events']=ctrl.matched
    equity.loc[ii,'matched_edge_pp']=ctrl.edge_pp
    equity.loc[ii,'monthly_p']=ctrl.p_month
    equity.loc[ii,'sept_btc_points']=read('partial/periods.csv').query('period=="3" and exit_policy=="half_then_6d"').partial_sept_net_points.item()
    # A matched-event effect is per position notional, independent of the capital fraction used.
    ev=read('alt/event_results.csv.gz')
    controls=read('alt/control_paired_events.csv')
    chosen=read('alt/selection.csv').set_index('strategy')
    for rule,g in ev[ev.epoch.eq(3)].groupby('strategy'):
        qualified=g[g.funding_member_coverage_pct.ge(99.999)&g.funded_net_pct.notna()].sort_values('decision')
        full=len(qualified)==len(g)
        paired=controls[(controls.strategy==rule)&controls.basis.eq('funded_net')&controls.eligible_80pct].copy()
        q=qualified.funded_net_pct
        rows.append(pd.DataFrame([dict(family='ALT_exit',rule=rule,source='alt/event_results.csv.gz',events=len(g),
            funded_n=len(q),funded_win_pct=100*q.gt(0).mean(),funded_mean_pct=q.mean(),
            funded_growth_pct=100*((1+q/100).prod()-1) if full else np.nan,
            stressed_growth_pct=100*((1+(q-.2)/100).prod()-1) if full else np.nan,matched_events=len(paired),
            matched_edge_pp=paired.paired_delta_pp.mean(),monthly_p=chosen.at[rule,'p_month_2026_vs_random_funded_net'] if rule in chosen.index else np.nan,
            family_holm_p=chosen.at[rule,'p_holm_2026_vs_random_funded_net'] if rule in chosen.index else np.nan,
            cost_and_scope='full-member funded actual results; compound only with complete event coverage; random requires >=80% matched members')]))
    result=pd.concat(rows,ignore_index=True)
    order=['family','rule','events','full_history_entries','funded_n','funded_win_pct','funded_mean_pct','funded_growth_pct',
           'stressed_growth_pct','dd_5m_funded_pct','dd_5m_before_funding_pct','settled_funded_dd_pct',
           'all_history_5m_fee_only_dd_pct','matched_events','matched_edge_pp','monthly_p','family_holm_p',
           'sept_btc_points','source','cost_and_scope']
    if result.duplicated(['family','rule']).any():raise ValueError('Duplicate result row')
    result[order].to_csv(EXP/'results.csv',index=False)


if __name__=='__main__':
    run()
