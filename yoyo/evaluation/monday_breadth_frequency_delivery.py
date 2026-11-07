"""Combine frozen per-rule and single-account frequency, costs and matched controls.

All statistics here reuse native trade ledgers. An account's controls are the
matched returns for its accepted alerts, not an average across its four rules.
"""

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_breadth_frequency_long import EXP
from yoyo.evaluation.monday_breadth_opt_delivery import CONF, FOLLOW, count_label
from yoyo.evaluation.monday_breadth_opt_selection import OUT as ORIGINAL
from yoyo.evaluation.monday_breadth_opt_match_exits import paired_month_p


def controls_for_accounts():
    book = pd.read_parquet(EXP/'audit/fixed/all_rule_and_account_events.parquet')
    book = book[book.candidate_id.str.startswith('account_') & book.epoch.eq(3)]
    pairs = pd.read_parquet(EXP/'controls/fixed_paired_events.parquet')
    pairs = pairs.rename(columns={'candidate_id':'source_id'})
    keys = ['source_id','decision']
    cols = ['eligible_coverage','coverage_pct','matched_actual_pct','matched_random_pct','paired_excess_pp']
    result = book[['candidate_id',*keys]].merge(pairs[[*keys,*cols]], on=keys,
                                               how='left', validate='many_to_one', indicator=True)
    if result._merge.ne('both').any():
        raise ValueError('A source alert is missing from the control coverage audit')
    result.drop(columns='_merge').to_csv(EXP/'account_matched_2026.csv', index=False)
    rows = []
    for cid, group in result.groupby('candidate_id'):
        matched = group[group.eligible_coverage].dropna(subset=['matched_actual_pct','matched_random_pct'])
        differences = matched.set_index('decision').paired_excess_pp
        rows.append(dict(candidate_id=cid, n_paired_events=len(matched),
                         n_2026_actual_events=len(group),
                         eligible_months=matched.decision.dt.strftime('%Y-%m').nunique(),
                         mean_matched_actual_pct=matched.matched_actual_pct.mean(),
                         mean_matched_random_pct=matched.matched_random_pct.mean(),
                         mean_paired_edge_pp=differences.mean(),
                         p_month_sign=paired_month_p(differences)))
    return pd.DataFrame(rows)


def rule_text(cid, fixed):
    if cid.startswith('account_'):
        return '四条同档信号共用一个账户，空仓取第一笔；同时触发按 BTC、ETH、SOL、山寨排序'
    if cid=='ETH_flat_36':
        return '10分钟≥2币且占过去28天触价币≥1%，后120分钟新增≤初始且BTC涨≥0.25%；6%止损，涨5%后1小时EMA50跟踪，最多6天'
    row = fixed.loc[cid]
    breadth = count_label(row.breadth)
    if row.breadth.startswith('share_'):
        breadth = '至少2币，且'+breadth
    confirm = CONF[row.confirmation]
    if row.confirmation.startswith('reclaim_'):
        confirm = '初始可交易山寨币中'+confirm
    return (f'{row.minutes}分钟初始首触{breadth}，其后等{row.wait_min}分钟，新增币条件{FOLLOW[row.diffusion]}，'
            f'确认条件{confirm}，持有{row.hold_hours}小时后全部平仓')


def constituent_outcomes():
    """Summarize member horizons at the actual altcoin-rule entry times.

    The peak column averages each member's individual six-day high. The later
    observations are labels and cannot change the earlier six-hour fills.
    """
    rules=pd.read_csv(EXP/'fixed/locked_rules.csv').query("asset=='ALT'")
    events=pd.read_parquet(EXP/'audit/fixed/costed_events.parquet')
    results=[]
    for row in rules.itertuples():
        source=ORIGINAL/'payoffs'/f'event_outcomes_minutes{row.minutes}_wait{row.wait_min}.csv.gz'
        fields=['known','hold_hours','members_expected_n','endpoint_n','path_n','mean_mfe_pct',
                'member_net_win_pct','basket_net_return_pct','hit10_pct']
        values=pd.read_csv(source,usecols=fields,parse_dates=['known'])
        original=events[events.candidate_id.eq(row.candidate_id)][['candidate_id','decision','known','net_pct','funded_pct']]
        for h in sorted({row.hold_hours,144}):
            part=original.merge(values[values.hold_hours.eq(h)],on='known',validate='one_to_one')
            if len(part)!=len(original):raise ValueError('Missing ALT observation clock')
            if h==row.hold_hours and not np.allclose(part.basket_net_return_pct,part.net_pct):
                raise ValueError('ALT native outcomes do not match the selected trades')
            part.loc[part.endpoint_n.ne(part.members_expected_n),['member_net_win_pct','basket_net_return_pct']]=np.nan
            part.loc[part.path_n.ne(part.members_expected_n),['mean_mfe_pct','hit10_pct']]=np.nan
            results.append(part)
    pd.concat(results).to_csv(EXP/'alt_constituent_horizons_2026.csv',index=False)


def run():
    constituent_outcomes()
    ctrl = pd.read_csv(EXP/'controls/combined_primary_validity.csv')
    ctrl = pd.concat([ctrl,controls_for_accounts()],ignore_index=True).set_index('candidate_id')
    if ctrl.index.duplicated().any() or len(ctrl)!=11:
        raise ValueError('Expected nine asset and two account hypotheses')
    ps = ctrl.p_month_sign.dropna().sort_values()
    ctrl['holm_all_11'] = np.nan
    ctrl.loc[ps.index,'holm_all_11'] = np.maximum.accumulate(
        ps.to_numpy()*(len(ctrl)-np.arange(len(ps)))).clip(0,1)

    fixed = pd.read_csv(EXP/'fixed/locked_rules.csv').set_index('candidate_id')
    long = pd.read_csv(EXP/'long/locked_rules.csv').set_index('candidate_id')
    perf = pd.concat([pd.read_csv(EXP/'audit'/f/'performance.csv',dtype={'period':str}) for f in ['fixed','long']])
    monthly = pd.concat([pd.read_csv(EXP/'audit'/f/'frequency_monthly_2026.csv') for f in ['fixed','long']])
    monthly.to_csv(EXP/'monthly_opportunities_2026.csv',index=False)
    results = []
    for cid, part in perf.groupby('candidate_id',sort=True):
        perf_by_period = part.set_index('period')
        all_time, v, t = (perf_by_period.loc[k] for k in ['all','2025','2026'])
        m = monthly[monthly.candidate_id.eq(cid)]
        c = ctrl.loc[cid]
        if int(m.n.sum())!=t.n or c.n_2026_actual_events!=t.n or len(m)!=10:
            raise ValueError(f'Count mismatch for {cid}')
        if cid in fixed.index:
            quality = '通过2025年筛选' if fixed.loc[cid,'eligible'] else '仅频率达标'
        elif cid in long.index:
            quality = '通过动态退出组2025年筛选' if long.loc[cid,'eligible'] else '仅频率达标'
        else:
            quality = '预定单账户组合，各分支保留自己的信号间隔'
        results.append({'候选':cid,'研究状态':quality,'规则':rule_text(cid,fixed),
                        '全历史实计笔数':int(all_time.n),'全历史扣手续费每笔均收%':all_time['mean'],
                        '2025笔数':int(v.n),'2025扣手续费每笔均收%':v['mean'],
                        '2025逐笔平仓结算回撤%':v.dd,'2025截尾均值减1.28SE%':v.lcb,
                        '2026截至10月27日笔数':int(t.n),'2026活跃月份':int(t.months),
                        '2026每月最少':int(m.n.min()),'2026每月最多':int(m.n.max()),
                        '2026相邻入场最长间隔天':t.largest_gap_days,
                        '2026只扣手续费每笔均收%':t['mean'],'2026已核资金费笔数':int(t.funded_n),
                        '2026含资金费胜率%':t.funded_win,'2026含资金费每笔均收%':t.funded_mean,
                        '2026五分钟收盘盯市回撤_含手续费未扣资金费%':t.marked_close_dd_before_funding,
                        '2026额外往返0.20个百分点后每笔均收%':t.funded_stress,
                        '2026去最佳月含资金费每笔均收%':t.funded_ex_best_month,
                        '随机可配对事件':int(c.n_paired_events),'随机可配对月份':int(c.eligible_months),
                        '配对覆盖率%':100*c.n_paired_events/t.n,
                        '配对内信号每笔均收%':c.mean_matched_actual_pct,
                        '配对内随机每笔均收%':c.mean_matched_random_pct,
                        '信号比随机高_百分点':c.mean_paired_edge_pp,
                        '按月置换p':c.p_month_sign,'11项Holm校正p':c.holm_all_11})
    pd.DataFrame(results).to_csv(EXP/'frequency_results.csv',index=False,encoding='utf-8-sig')


if __name__=='__main__':
    run()
