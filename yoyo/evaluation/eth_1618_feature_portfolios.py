"""Reuse validated VectorBT cached-fill adapter for new ETH admission columns.

No new OHLC or trade outcome simulation. Existing baseline, unscored and
single-feature native selections are copied from the prior study. Signal
entry maker2bp, target maker2bp, other exits taker5bp, unchanged.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_1618_features import EXP, PRIOR, ROOT, config, read, dump
from yoyo.evaluation.eth_score_calibration_vbt import cached_portfolios


def main():
    cfg = config(); pred = read('predictions.csv.gz')
    complete = pred[pred.complete].copy(); output = []; audits = []
    resume = (EXP/'selected_trades.csv.gz').exists()
    if resume:
        output = [read('selected_trades.csv.gz')]
        audits = json.loads((EXP/'engine_audit.json').read_text())
    for (target, fold), group in ([] if resume else complete.groupby(['target', 'fold'])):
        reference = group[group.arm.eq('original_model')].copy()
        masks = np.column_stack([group[group.arm.eq(arm)].set_index('trade_id').loc[reference.trade_id].admit.to_numpy(bool)
                                 for arm in cfg['arms']])
        result, audit = cached_portfolios(reference, masks, cfg['arms'])
        for arm, rows in result.items():
            scores = group[group.arm.eq(arm)].set_index('trade_id').score
            rows['score'] = rows.trade_id.map(scores)
            rows['admit'] = True
            output.append(rows)
        audits.append(dict(target=target, fold=fold, **audit))
    old = pd.read_csv(PRIOR/'selected_trades.csv.gz', float_precision='round_trip')
    old = old[old.symbol.eq('ETH')].copy()
    for original, name in ([] if resume else [('fresh_tree_net_noordinal', 'original_model'), ('single_reward', 'single_reward')]):
        rows = old[old.arm.eq(original)].copy()
        allowed = set(complete.trade_id)
        rows = rows[rows.trade_id.isin(allowed)]
        rows['arm'] = name; rows['vbt_net_r'] = rows.net_r
        output.append(rows)
    # v2 saved scored selections only. Reuse the later calibration study's
    # unscored test selections for Q2 onward; Q1 needs only one new control
    # admission column per target, still using unchanged native fills.
    if not any(part.arm.eq('unscored').any() for part in output):
        cal = pd.read_csv(ROOT/'experiments/active/exp-eth-1618-score-calibration-20261031-v1/selected_trades.csv.gz', float_precision='round_trip')
        reused = cal[cal.arm.eq('unscored')].copy()
        output.append(reused)
        first = complete[complete.arm.eq('original_model')&complete.fold.eq('2025Q1')]
        for target, group in first.groupby('target'):
            result, audit = cached_portfolios(group,np.ones((len(group),1),bool),['unscored'])
            output.append(result['unscored'])
            audits.append(dict(target=target,fold='2025Q1',stage='missing_unscored_control',**audit))
    result = pd.concat(output, ignore_index=True)
    result.to_csv(EXP/'selected_trades.csv.gz', index=False)
    pred[~pred.complete][['trade_id','event_id','target','fold','week','side','ordinal','submission_time','arm','score','admit']].to_csv(EXP/'current_week.csv', index=False)
    dump(EXP/'engine_audit.json', audits)
    dump(EXP/'engine_receipt.json', dict(engine='VectorBT 1.2.0 cached native fill admission', new_native_positions=0,
         OHLC_replays=0, new_policy_runs=24, missing_control_runs=3, unchanged_control_runs_reused=True,
         max_native_net_r_error=max(r['max_native_net_r_error'] for r in audits),
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()))
    print(result.groupby(['target','arm']).net_r.agg(['count','sum']).to_string(), flush=True)


if __name__ == '__main__':
    main()
