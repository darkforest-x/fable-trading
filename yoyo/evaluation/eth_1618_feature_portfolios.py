"""Reuse validated VectorBT cached-fill adapter for new ETH admission columns.

No new OHLC or trade outcome simulation. Existing baseline, unscored and
single-feature native selections are copied from the prior study. Signal
entry maker2bp, target maker2bp, other exits taker5bp, unchanged.
"""
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_1618_features import EXP, PRIOR, config, read, dump
from yoyo.evaluation.eth_score_calibration_vbt import cached_portfolios


def main():
    cfg = config(); pred = read('predictions.csv.gz')
    complete = pred[pred.complete].copy(); output = []; audits = []
    for (target, fold), group in complete.groupby(['target', 'fold']):
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
    for original, name in [('fresh_tree_net_noordinal', 'original_model'), ('unscored', 'unscored'), ('single_reward', 'single_reward')]:
        rows = old[old.arm.eq(original)].copy()
        allowed = set(complete.trade_id)
        rows = rows[rows.trade_id.isin(allowed)]
        rows['arm'] = name; rows['vbt_net_r'] = rows.net_r
        output.append(rows)
    result = pd.concat(output, ignore_index=True)
    result.to_csv(EXP/'selected_trades.csv.gz', index=False)
    pred[~pred.complete][['trade_id','event_id','target','fold','week','side','ordinal','submission_time','arm','score','admit']].to_csv(EXP/'current_week.csv', index=False)
    dump(EXP/'engine_audit.json', audits)
    dump(EXP/'engine_receipt.json', dict(engine='VectorBT 1.2.0 cached native fill admission', new_native_positions=0,
         OHLC_replays=0, new_policy_runs=len(audits), unchanged_control_runs_reused=True,
         max_native_net_r_error=max(r['max_native_net_r_error'] for r in audits),
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()))
    print(result.groupby(['target','arm']).net_r.agg(['count','sum']).to_string(), flush=True)


if __name__ == '__main__':
    main()
