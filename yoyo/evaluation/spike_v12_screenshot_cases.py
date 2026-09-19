"""Bounded OKX screenshot audit, using frozen historical OHLC and causal ATR.

Reads Aug 1..26 2026 for warmup and geometry; focuses on Aug 19 HYPE and
Aug 24 BTC. Features use open/high/low/close/volume through the decision bar.
This is the auxiliary-family reference, not merged Pine parity or a backtest.
The original input path and hash are recorded; canonical market data is read-only.
"""
from pathlib import Path
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v10_4_study import v9_facts
from yoyo.evaluation.spike_v12_local_touch import auxiliary_touch_events
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v12-held-break-display-20260920-v1')
POOL = Path('data/altcoin_trends_20260909_v1/history_full_verified/frozen_pool')


def main():
    assert _committed((Path(__file__), EXP/'PROJECT_PLAN.md')), 'Commit builder first'
    result = {'scope': 'auxiliary family only; no merged Pine parity',
              'source_commit': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
              'cases': []}
    for asset, tick, day in [('BTC', .1, '2026-08-24'), ('HYPE', .001, '2026-08-19')]:
        path = POOL/f'{asset}_USDT_SWAP_15m.csv'
        f = pd.read_csv(path)
        f.index = pd.to_datetime(f.ts, unit='ms', utc=True)
        f = f.loc['2026-08-01':'2026-08-26', ['open','high','low','close','volume']].copy()
        facts = v9_facts(f, 15, asset, tick)
        frame = facts['frame']
        aux = auxiliary_touch_events(frame.open,frame.high,frame.low,frame.close,frame.atr,can_run=facts['can_run'],tick=tick)
        def stamp(i, close=False):
            return (f.index[int(i)] + pd.Timedelta(minutes=15 if close else 0)).tz_convert('Asia/Shanghai').isoformat()
        lines = pd.DataFrame(aux.trace['lines'])
        for name in ('ax','bx','cx','born_i','break_i'):
            lines[name+'_bj'] = [stamp(i, name in ('born_i','break_i')) if pd.notna(i) else None for i in lines[name]]
        lines.to_csv(EXP/f'{asset}_auxiliary_lines.csv',index=False)
        start = pd.Timestamp(day,tz='Asia/Shanghai')
        idx = np.flatnonzero((frame.index >= start) & (frame.index < start+pd.Timedelta(days=1)))
        long_signals = np.asarray(facts['v9_long'])
        signals = [{'i':int(i),'close_bj':stamp(i,True),'close':float(frame.close.iloc[i])} for i in idx if long_signals[i]]
        target = lines[(pd.to_datetime(lines.break_i_bj,utc=True) >= start) & (pd.to_datetime(lines.break_i_bj,utc=True) < start+pd.Timedelta(days=1))]
        selected = target.to_dict('records')
        for line in selected:
            held_checks = []
            for signal in signals:
                if signal['i'] < line['break_i']:
                    continue
                xs = np.arange(int(line['break_i'])+1, signal['i']+1)
                ys = line['ap'] + (line['bp']-line['ap']) * (xs-line['ax']) / (line['bx']-line['ax'])
                failed = xs[frame.close.to_numpy()[xs] <= ys]
                held_checks.append({'v9_close_bj':signal['close_bj'],'elapsed_bars':signal['i']-int(line['break_i']), 'continuous_close_above':not len(failed), 'first_failure_close_bj':stamp(failed[0],True) if len(failed) else None})
            line['hold_until_v9'] = held_checks
        case = {'asset':asset,'exchange':'OKX','input':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'tick_assumption':tick,'bars':len(frame),'start':stamp(0),'end':stamp(len(frame)-1),'target_day_bj':day,'target_breaks':selected,'v9_final_longs':signals,'all_aux_breaks':len(aux.events)}
        result['cases'].append(case)
        print(json.dumps(case,ensure_ascii=False,default=str), flush=True)
    (EXP/'screenshot_cases.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')


if __name__ == '__main__':
    main()
