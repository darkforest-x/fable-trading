"""Evaluate all frozen ETH feature/training arms, including failures and controls.

Weekly dependence, target multiplicity, changed trade sets and old versus
recent periods are explicit. Original raw score>0 remains every trade gate;
within-quarter top-decile metrics are descriptive, never policy selection.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_1618_features import EXP, PRIOR, config, read, dump
from yoyo.evaluation.btc_eth_repeat_fit import metrics
from yoyo.evaluation.major_1618_v2_evaluate import diagnose, fold_week_permutation, policy_difference, holm


def main():
    cfg = config(); pred = read('predictions.csv.gz'); pred = pred[pred.complete].copy()
    chosen = read('selected_trades.csv.gz'); chosen['net_r'] = chosen.vbt_net_r
    oldpred = pd.read_csv(PRIOR/'ETH_predictions.csv.gz', float_precision='round_trip')
    oldpred = oldpred[oldpred.complete & oldpred.arm.eq('single_reward')]
    receipts = json.loads((EXP/'model_receipts.json').read_text())
    rows = []; details = []; tests = []; years = []; quarters = []; sides = []; gains = []; concentration = []
    for target, cohort in pred[pred.arm.eq('original_model')].groupby('target'):
        baseline = chosen[chosen.target.eq(target)&chosen.arm.eq('original_model')]
        for arm in ['original_model','unscored','single_reward']+cfg['arms']:
            selection = chosen[chosen.target.eq(target)&chosen.arm.eq(arm)]
            stats = metrics(selection)
            scoredata = pred[pred.target.eq(target)&pred.arm.eq(arm)]
            if arm == 'single_reward': scoredata = oldpred[oldpred.target.eq(target)]
            diagnostic = diagnose(scoredata) if not scoredata.empty else {}
            diff = policy_difference(selection,baseline,cohort.week)
            removed = baseline[~baseline.trade_id.isin(selection.trade_id)]
            added = selection[~selection.trade_id.isin(baseline.trade_id)]
            decomposition = dict(added_n=len(added),added_net_r=float(added.net_r.sum()),
                                 removed_n=len(removed),removed_net_r=float(removed.net_r.sum()),
                                 retained_n=int(selection.trade_id.isin(baseline.trade_id).sum()))
            assert abs(decomposition['added_net_r']-decomposition['removed_net_r']-diff['delta_total_r'])<1e-8
            row = dict(target=target,arm=arm,candidate_n=len(cohort),candidate_weeks=int(cohort.week.nunique()),
                       **{k:v for k,v in stats.items() if not isinstance(v,(list,dict))},
                       auc=diagnostic.get('target_auc'),delta_total_r=diff['delta_total_r'],**decomposition)
            if arm == 'single_reward': row['unavailable_candidates']=len(cohort)-len(scoredata)
            rows.append(row)
            detail = dict(target=target,arm=arm,selected=stats,diagnostics=diagnostic,versus_original=diff,
                          decomposition=decomposition)
            if arm in cfg['arms']:
                detail['rank_permutation_p'] = fold_week_permutation(scoredata,'net_r')
                detail['matched_week_p'] = stats.get('week_p')
                tests.append(detail)
            details.append(detail)
            for year, part in selection.groupby(selection.week.str[:4]):
                years.append(dict(target=target,arm=arm,year=year,**metrics(part)))
            for side, part in selection.groupby('side'):
                sides.append(dict(target=target,arm=arm,side=int(side),**metrics(part)))
            for fold, part in cohort.groupby('fold'):
                quarters.append(dict(target=target,arm=arm,fold=fold,**metrics(selection[selection.fold.eq(fold)])))
            weekly = selection.groupby('week').net_r.sum().sort_values(ascending=False)
            concentration.append(dict(target=target,arm=arm,net_r=float(selection.net_r.sum()),
                without_best_week=float(selection.net_r.sum()-weekly.iloc[:1].sum()),
                without_best_three_weeks=float(selection.net_r.sum()-weekly.iloc[:3].sum())))
    holm(tests)
    improvements = [dict(matched_week_p=r['versus_original']['improvement_week_p']) for r in tests]
    holm(improvements)
    for r, improvement in zip(tests, improvements):
        r['improvement_holm_p'] = improvement['holm_p']
        netci = r['selected'].get('net_bp_ci95',[None,None])
        deltaci = r['versus_original']['delta_mean_week_ci95']
        r['historical_evidence_gate'] = bool(netci[0] is not None and netci[0]>0 and deltaci[0]>0
                                           and r['holm_p']<.01 and r['improvement_holm_p']<.01)
        r['production_eligible'] = False
    for r in receipts:
        total = sum(r['feature_gain'])
        for feature,gain in zip(r['features'],r['feature_gain']):
            gains.append(dict(target=r['target'],fold=r['fold'],arm=r['arm'],feature=feature,gain=gain,
                              fraction=gain/total if total else 0,new_feature=feature.startswith('g_')))
    for name, records in [('metrics',rows),('years',years),('quarters',quarters),('sides',sides),('feature_gain',gains),('concentration',concentration)]:
        pd.DataFrame(records).to_csv(EXP/f'{name}.csv',index=False)
    dump(EXP/'diagnostics.json',details)
    baseline = pred[pred.arm.eq('original_model')&pred.target.eq('near')]
    dump(EXP/'summary.json',dict(config=cfg,results=rows,policy_tests=tests,concentration=concentration,
        coverage=dict(events_per_target=len(baseline),candidate_weeks=int(baseline.week.nunique()),
                      first_week=str(baseline.week.min()),last_week=str(baseline.week.max()),folds=int(baseline.fold.nunique()),
                      label_positive_rate=float(baseline.label.mean())),
        model_receipt=json.loads((EXP/'fit_receipt.json').read_text()),
        engine_receipt=json.loads((EXP/'engine_receipt.json').read_text()),
        limitations=['Known historical data; no untouched confirmation or automatic test-winner promotion.',
                     'Only225-548training candidates per baseline fold; NY weeks, not touch counts, are dependence units.',
                     'Three feature groups and three training choices tested separately; no combined model tested.',
                     'Independent R at original stop risk, not account ROI. No extra slippage/funding.',
                     'Original matched controls use market entry/taker fees; signals use native maker entry.',
                     'The earliest3training events lack7day BTC warmup; missing values retained, no sample removal/backfill.',
                     'Current incomplete week is inference only, never training/evaluation.'],
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(pd.DataFrame(rows)[['target','arm','n','net_r','net_bp','random_bp','delta_total_r']].to_string(index=False),flush=True)


if __name__ == '__main__':
    main()
