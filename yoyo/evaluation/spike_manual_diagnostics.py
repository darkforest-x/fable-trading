"""Per-trade explanatory joins for the frozen September 25 manual study.

Source: owner asks how simultaneous timeframe signals and 4R exits affect a
repeatable manual process. This module does not change an entry or exit.
Cross-timeframe context uses the last ordinary signal confirmed at or before
the decision close. It is a past event, not a claim that its position is alive.
Strata are exploratory descriptions, not separately replayed trading systems.
Outcome-based examples and tail deletion are explicitly ex-post diagnostics.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_manual_system_study as study
from yoyo.evaluation import spike_v128_recent_report as stats
from yoyo.evaluation.spike_v8_six_filters import _committed


def contexts(trades, candidates):
    """Backward-asof only: candidates may be known simultaneously at close."""
    result=trades.copy()
    events=candidates.loc[candidates.arm.eq('ordinary')].copy()
    events['signal_close']=pd.to_datetime(events.signal_close,utc=True)
    for minutes in (5,15,60):
        parts=[]
        for symbol,g in result.groupby('symbol',sort=False):
            source=events.loc[events.symbol.eq(symbol)&events.timeframe_min.eq(minutes),['signal_close','side']].sort_values('signal_close')
            source=source.rename(columns={'signal_close':f'last_{minutes}m_signal_close','side':f'last_{minutes}m_side'})
            left=g[['trade_key','signal_close']].sort_values('signal_close')
            joined=pd.merge_asof(left,source,left_on='signal_close',right_on=f'last_{minutes}m_signal_close',direction='backward',allow_exact_matches=True)
            joined[f'last_{minutes}m_age_hours']=(joined.signal_close-joined[f'last_{minutes}m_signal_close']).dt.total_seconds()/3600
            if joined[f'last_{minutes}m_age_hours'].dropna().lt(0).any():raise ValueError('Future event join')
            parts.append(joined.drop(columns='signal_close'))
        result=result.merge(pd.concat(parts),on='trade_key',validate='one_to_one')
        result[f'last_{minutes}m_aligned']=result.side.eq(result[f'last_{minutes}m_side']) & result[f'last_{minutes}m_side'].notna()
    return result


def max_loss_streak(values):
    streak=longest=0
    for value in values:
        streak=streak+1 if value<0 else 0
        longest=max(longest,streak)
    return longest


def main():
    own=Path(__file__);test=Path('tests/evaluation/test_spike_manual_diagnostics.py')
    if not _committed((own,test)):raise ValueError('Commit diagnostic builder before construction')
    root=study.EXP/'summary_v1';receipt=json.loads((root/'receipt.json').read_text())
    for name,sha in receipt['files'].items():
        if study.p.digest(Path(name))!=sha:raise ValueError('Changed input summary')
    trades=pd.read_csv(root/'all_trades.csv.gz');candidates=pd.read_csv(root/'all_candidates.csv.gz')
    for col in ('signal_close','entry_time','exit_time'):
        trades[col]=pd.to_datetime(trades[col],utc=True)
    paired=pd.read_csv(root/'paired_four_r.csv.gz')
    base=contexts(trades.loc[trades.policy.eq('baseline')],candidates)
    fields=['trade_key','take4_net_r','take4_exit_time','take4_censored','take4_net_bp']
    base=base.merge(paired[fields],on='trade_key',validate='one_to_one')
    base['same_entry_take4_delta_r']=base.take4_net_r-base.net_r
    base['entry_decision_note']=np.where(base.arm.eq('ordinary'),
        '原规则：已确认普通信号且该账本空仓，下一开盘入场；盈利结果不是入场条件。',
        '原规则：已确认多头联合信号且该账本空仓，下一开盘入场；联合线原生平台值尚未对齐。')
    base['slope_rule_note']=np.where(base.htf_slope_aligned,
        '信号当时通过上级已完成SMA60斜率同向门；仍须按该政策自己的仓位占用决定是否成交。',
        '信号当时被上级已完成SMA60斜率同向门拒绝；事后盈亏不能证明该过滤有效。')
    def note(row):
        if row.censored:return '期末/数据边界未闭合，不计为输赢。'
        if row.first4_observation=='ambiguous':return '止损根也触及4R，OHLC无法确定先后；4R目标按止损先处理。'
        if row.first4_observation=='not_reached':return '本笔持仓没有可确认的4R触达；不要从离场后的走势计算浮盈。'
        delta=row.same_entry_take4_delta_r
        return f'曾可确认触达毛4R；同入场预挂4R目标净结果{row.take4_net_r:.3f}R，相对原退出变化{delta:+.3f}R；此比较在事后才能知道。'
    base['four_r_review_note']=base.apply(note,axis=1)
    out=study.EXP/'diagnostics_v1';out.mkdir(parents=True,exist_ok=True)
    rows=[];conditional=[];strata=[];examples=[]
    for keys,g in base.loc[~base.censored].groupby(['symbol','timeframe_min','arm']):
        common=dict(zip(['symbol','timeframe_min','arm'],keys))
        for fold in ('all','earlier','later'):
            f=g if fold=='all' else g.loc[g.fold.eq(fold)]
            if f.empty:continue
            matched=f.loc[f.matched].copy();matched['excess_r']=matched.net_r-matched.control_net_r
            inference=stats.block_inference(matched.excess_r.to_numpy(),matched.utc_week.to_numpy(),seed=925129)
            descending=f.net_r.sort_values(ascending=False)
            rows.append(dict(common,fold=fold,n=len(f),median_fee_r=float(f.fee_r.median()),
                max_consecutive_losses=max_loss_streak(f.sort_values('entry_time').net_r),
                net_sum_r=float(f.net_r.sum()),net_sum_r_without_best1=float(descending.iloc[1:].sum()),
                net_sum_r_without_best3=float(descending.iloc[3:].sum()),
                random_mean_net_r=float(matched.control_net_r.mean()),mean_excess_r=float(matched.excess_r.mean()),
                excess_r_ci_low=inference['ci_low_bp'],excess_r_ci_high=inference['ci_high_bp'],
                excess_r_p=inference['p_one_sided'],matched_n=len(matched),
                known4=int(f.first4_observation.eq('known').sum()),ambiguous4=int(f.first4_observation.eq('ambiguous').sum())))
            touched=f.loc[f.first4_observation.eq('known')]
            conditional.append(dict(common,fold=fold,n=len(touched),net_loss=int(touched.net_r.lt(0).sum()),
                benefited_4r=int(touched.same_entry_take4_delta_r.gt(1e-9).sum()),
                harmed_4r=int(touched.same_entry_take4_delta_r.lt(-1e-9).sum()),
                baseline_mean_net_r=float(touched.net_r.mean()),take4_mean_net_r=float(touched.take4_net_r.mean()),
                baseline_median_net_r=float(touched.net_r.median()),delta_mean_r=float(touched.same_entry_take4_delta_r.mean())))
            for feature in ('htf_slope_aligned','last_15m_aligned','last_60m_aligned'):
                for flag,sub in f.groupby(feature):
                    matched_sub=sub.loc[sub.matched]
                    strata.append(dict(common,fold=fold,feature=feature,value=bool(flag),n=len(sub),
                        win_rate=float(sub.net_r.gt(0).mean()),mean_net_r=float(sub.net_r.mean()),
                        random_net_r=float(matched_sub.control_net_r.mean()),
                        excess_r=float((matched_sub.net_r-matched_sub.control_net_r).mean()),
                        interpretation='Post-hoc strata only; not a serial admission-policy replay.'))
        eligible=g.loc[g.first4_observation.eq('known')]
        selected=[('best_original_outcome',g.net_r.idxmax())]
        if len(eligible):selected += [('largest_4r_rescue',eligible.same_entry_take4_delta_r.idxmax()),('largest_4r_sacrifice',eligible.same_entry_take4_delta_r.idxmin())]
        stopped=g.loc[g.exit_reason.eq('initial_stop')]
        if len(stopped):selected.append(('typical_initial_stop',(stopped.net_r-stopped.net_r.median()).abs().idxmin()))
        for reason,index in selected:examples.append(dict(g.loc[index],selection_reason=reason))
    evidence=pd.DataFrame(rows)
    mask=evidence.fold.eq('later');evidence.loc[mask,'later_p_holm_12']=study.holm(evidence.loc[mask,'excess_r_p'])
    tables={'risk_and_tail.csv':evidence,'conditional_four_r.csv':pd.DataFrame(conditional),
        'exploratory_entry_strata.csv':pd.DataFrame(strata),'examples.csv':pd.DataFrame(examples),
        '逐笔决策复盘.csv':base}
    for name,frame in tables.items():frame.to_csv(out/name,index=False,encoding='utf-8-sig')
    # These p-values concern fixed early-period feature ranking, not a fitted model.
    # Sign-flip entire UTC weeks rather than treating correlated trades as independent.
    rank_rows=[]
    for keys,g in trades.loc[~trades.censored].groupby(['symbol','timeframe_min','arm','policy']):
        earlier=g.loc[g.fold.eq('earlier')];later=g.loc[g.fold.eq('later')].copy()
        score=earlier.side*earlier.htf_slope/earlier.signal_price
        cutoff=score.quantile(.9);later['selected']=later.side*later.htf_slope/later.signal_price>=cutoff
        contrasts=[];weeks=[]
        for week,f in later.groupby('utc_week'):
            top=f.loc[f.selected,'net_bp'];rest=f.loc[~f.selected,'net_bp']
            if len(top) and len(rest):contrasts.append(float(top.mean()-rest.mean()));weeks.append(week)
        inference=stats.block_inference(contrasts,weeks,seed=925129)
        rank_rows.append(dict(zip(['symbol','timeframe_min','arm','policy'],keys),
            informative_weeks=len(weeks),top_minus_rest_week_mean_bp=inference['mean_excess_bp'],
            week_sign_permutation_p=inference['p_one_sided'],
            null='Symmetric weekly top-minus-rest contrast; sparse weeks excluded; no predictive independence claim.'))
    rank=pd.DataFrame(rank_rows);rank['p_holm_36']=study.holm(rank.week_sign_permutation_p)
    rank.to_csv(out/'single_feature_week_permutation.csv',index=False)
    study.p.dump(out/'receipt.json',{'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'source_sha256':study.p.digest(own),'test_sha256':study.p.digest(test),'input_receipt_sha256':study.p.digest(root/'receipt.json'),
        'rows':len(base),'files':{x.name:study.p.digest(x) for x in out.iterdir() if x.is_file() and x.name!='receipt.json'},
        'all_chart_manually_reviewed':False,'post_hoc_strata_are_not_trade_rules':True,'production_eligible':False})
    print(json.dumps({'diagnostics':str(out),'baseline_rows':len(base)}))


if __name__=='__main__':main()
