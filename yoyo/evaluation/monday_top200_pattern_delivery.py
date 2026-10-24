"""Reconcile and deliver the already-computed winner audit and admission study.

No new execution. A post-hoc exclusion of the two most represented winner
weeks is a concentration diagnostic, never a deployable admission condition.
The market-wide stop streak uses deterministic timestamp/symbol/ID ordering;
simultaneous-bar true ordering is unavailable and it is not account drawdown.
"""
from __future__ import annotations
import json
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_top200_patterns import (
    EXP, TOP, DELIVERY, CUT, FILTERS, LABELS, FAR, old,
    ledgers, metrics, finite, dump,
)


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__]).returncode:
        raise RuntimeError('Commit delivery builder before producing evidence')
    m=pd.read_csv(EXP/'metrics.csv',float_precision='round_trip')
    x=pd.read_csv(EXP/'top200-features.csv',float_precision='round_trip')
    s=pd.read_csv(EXP/'all-signal-features-outcomes.csv.gz',float_precision='round_trip')
    f,stamps=ledgers()
    frozen=json.loads((EXP/'coverage.json').read_text())['ledgers']
    assert stamps==frozen
    # Validation masks must not rewrite any frozen financial field.
    keys=['uid','trade_id'];cols=['entry','stop','target_price','exit_price','net_bp','net_r','gross_r','closed']
    a=f[f.population.eq('signal')].set_index(keys).sort_index()
    b=s.set_index(keys).sort_index()
    assert a.index.equals(b.index)
    for c in cols:
        np.testing.assert_allclose(a[c].astype(float),b[c].astype(float),equal_nan=True,rtol=0,atol=0)
    # Original aggregate baselines must reproduce, including censored positions.
    checks=[]
    prior=pd.read_csv(FAR/'metrics.csv')
    for base,config in [('body1h3-near','near-n3'),('body1h3-far','far-n3')]:
        for group in ['BTC','ETH','ALTCOINS']:
            for period in ['all','earlier','later']:
                p=prior[(prior.config_id==config)&(prior.group==group)&(prior.period==period)].iloc[0]
                q=m[(m.base==base)&(m['filter']=='baseline')&(m.group==group)&(m.period==period)].iloc[0]
                for c in ['signals','closed','censored','win_rate','mean_r','total_r','paired','paired_signal_r','paired_random_r']:
                    np.testing.assert_allclose(p[c],q[c],rtol=1e-11,atol=1e-11,equal_nan=True)
                checks.append(dict(base=base,group=group,period=period))
    # Compare render CSV MA observations to independently reread full histories
    # wherever the selected entry belongs to one of the fixed baselines.
    joined=x.merge(s,on=['symbol','entry_time','side'],suffixes=('_top','_all'))
    for c in ['entry_ma_favorable_count','entry_six_ma_directional_slopes','entry_ma_width_pct']:
        np.testing.assert_allclose(joined[c+'_top'],joined[c+'_all'],rtol=1e-9,atol=1e-9)
    top_weeks=x.week.value_counts().head(2).index.tolist();slices=[]
    for base,g in f.groupby('base'):
        candidates=s[s.base.eq(base)]
        for key in FILTERS:
            keep=candidates.loc[candidates[key],'uid']
            t=g[g.uid.isin(keep)&~g.week.isin(top_weeks)]
            for period in ['all','earlier','later']:
                z=t if period=='all' else t[(t.entry_time<CUT if period=='earlier' else t.entry_time>=CUT)&~t.cross_cut]
                slices.append(dict(base=base,filter=key,period=period,excluded_pin_weeks=','.join(top_weeks),**metrics(z)))
    pd.DataFrame(slices).to_csv(EXP/'concentration-sensitivity.csv',index=False)
    cn=pd.DataFrame(slices).rename(columns={'base':'基线','filter':'入场条件','period':'时段','excluded_pin_weeks':'剔除触价周',
        'closed':'已平仓','win_rate':'胜率','mean_r':'平均净R','payoff':'实际盈亏比',
        'paired_signal_r':'配对策略净R','paired_random_r':'配对随机净R'})
    cn['入场条件']=cn['入场条件'].map(LABELS)
    cn.to_csv(DELIVERY/'03_去除两大集中行情周的敏感性.csv',index=False,encoding='utf-8-sig')
    # Expand descriptive features without fitting a signal or changing filters.
    holding=(pd.to_datetime(x.exit_interval_end)-pd.to_datetime(x.entry_time)).dt.total_seconds()/3600
    details=dict(rows=len(x),symbol_week_side_groups=int(x.cluster.nunique()),market_weeks=int(x.week.nunique()),
        top_two_weeks=top_weeks,top_two_week_rows=int(x.week.isin(top_weeks).sum()),
        entry_against_all_six=int(x.entry_ma_favorable_count.eq(0).sum()),
        all_six_slopes_against_trade=int(x.entry_six_ma_directional_slopes.eq(0).sum()),
        mae_gt5pct_full_interior=int(x.interior_mae_pct.gt(5).sum()),
        mae_gt10pct_full_interior=int(x.interior_mae_pct.gt(10).sum()),
        holding_hours_median=float(holding.median()),holding_hours_p90=float(holding.quantile(.9)),
        post_entry_recovery_observed=int(x.first_post_entry_six_ma_recovery.notna().sum()),
        entry_ma_favorable_count=x.entry_ma_favorable_count.value_counts().sort_index().to_dict(),
        by_source=x.source_key.value_counts().to_dict())
    dump(EXP/'concentration.json',finite(details))
    validation=dict(frozen_financial_fields_unchanged_rows=len(a),baseline_metric_cells_checked=len(checks),
        baseline_checks=checks,local_review_vs_full_source_comparisons=len(joined),
        per_trade_chinese_rows=len(pd.read_csv(DELIVERY/'01_逐笔K线成交量均线分析.csv')),
        all_new_filter_rows_subset_of_baselines=True,additional_simulations=0,
        visual_scope='Original delivery six representative charts visually inspected;200 row-by-row analyses are quantitative, not a claim of200manual image inspections.',
        limitations=['Three predeclared admission-only ideas across three fixed original strategies; no wait-for-later MA entry is simulated.',
                     'Winner-derived patterns, already explored historical periods, current-venue costs on Binance data; no untouched validation or live eligibility.',
                     'Matched random uses same admitted signal geometry; absolute strategy means and strict paired means have different denominators.',
                     'Full population includes629altcoins with eligible signals plusBTC/ETH; one of630available altcoins has no entries in these three baselines.',
                     'Longest market stop run sorts equal exit timestamps by symbol/ID and is not a tick-exact ordering or funded-account statistic.',
                     'MAE/MFE in top200 review exclude both endpoint candles and represent conservative lower bounds, not exact intrabar excursions.'])
    dump(EXP/'validation.json',validation)
    # Companion evidence archive; the175MB image archive remains immutable.
    archive=TOP/'前200笔_逐笔分析与框架验证.zip'
    payload=[DELIVERY/n for n in ['01_逐笔K线成交量均线分析.csv','02_三类入场条件框架验证.csv','03_去除两大集中行情周的敏感性.csv']]
    payload += [EXP/n for n in ['config.json','summary.json','validation.json','top200-features.csv','top200-summary.json',
                                'concentration.json','metrics.csv','year-metrics.csv','winner-loser-features.csv','concentration-sensitivity.csv']]
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in payload:z.write(p,p.name)
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    dump(EXP/'delivery.json',dict(folder=str(DELIVERY),archive=str(archive),archive_bytes=archive.stat().st_size,
        files=[str(p) for p in payload],reproduce=['.venv/bin/python -m yoyo.evaluation.monday_top200_patterns review',
        '.venv/bin/python -m yoyo.evaluation.monday_top200_patterns collect --workers 3',
        '.venv/bin/python -m yoyo.evaluation.monday_top200_patterns summarize',
        '.venv/bin/python -m yoyo.evaluation.monday_top200_pattern_delivery'],
        training_eligible=False,production_eligible=False))
    print(json.dumps(finite(dict(concentration=details,validation=validation)),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
