"""Fund the identical strategy after reverting only its largest shared retrospective exit change."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_efficient_trend as study

EXP = study.EXP


def main():
    dest = EXP/'largest_event_sensitivity.json'
    if dest.exists():
        raise FileExistsError('Frozen single-event sensitivity already evaluated')
    attr = json.loads((EXP/'incremental_returns.json').read_text())
    outputs = {}
    for venue in ['OKX','Binance']:
        largest = attr[venue]['changed_returns'][0]
        if largest['symbol']!='ADA' or not largest['entry_utc'].startswith('2020-11-26'):
            raise ValueError('Sensitivity requires the same largest ADA move on both venues')
        paths = study.thirds.okx_paths() if venue=='OKX' else study.binance.assembled(set(study.binance.patch.affected_tickers()))
        paths = paths[~paths.is_control]
        choices = pd.read_csv(EXP/f'decisions_{venue}.csv.gz',keep_default_na=False)
        choices = choices[~choices.is_control].copy()
        hit = choices.event_id.eq(largest['event_id'])
        if hit.sum()!=1 or not choices.loc[hit,'choice'].eq('full_runner').all():
            raise ValueError('Expected exactly one original full-runner choice')
        choices.loc[hit,'choice']='partial_2_3'
        revised = study.rotation._rotated_paths(paths,choices)
        metrics,trades,_ = study.market.fund(venue,revised,0.)
        candidate = pd.read_csv(EXP/f'funded_{venue}.csv.gz',index_col='event_id')
        if set(candidate.index)!=set(trades.index):
            raise ValueError('Diagnostic cannot change the signal or portfolio roster')
        other = trades.index[trades.index!=largest['event_id']]
        if not np.allclose(candidate.loc[other,'net_return'],trades.loc[other,'net_return'],atol=1e-9):
            raise ValueError('A second trade changed its causal price return')
        outputs[venue] = dict(reverted=largest['event_id'],symbol=largest['symbol'],entry=largest['entry_utc'],metrics=metrics)
    outputs['interpretation']='After-the-fact sensitivity only. The original 12H ADA signal remains funded, with the baseline 2R exit instead of a full runner on that single episode. All subsequent quantities are recomputed in the same shared wallet.'
    dest.write_text(json.dumps(outputs,indent=2,allow_nan=False)+'\n')
    print({v:outputs[v]['metrics']['all']['final_usdt'] for v in ['OKX','Binance']})


if __name__=='__main__':
    main()
