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
import argparse
import gzip

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v10_4_study import v9_facts
from yoyo.evaluation.spike_v12_local_touch import auxiliary_touch_events, LocalTouchParams
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-v12-held-break-display-20260920-v1')
POOL = Path('data/altcoin_trends_20260909_v1/history_full_verified/frozen_pool')


def audit_one(params):
    path = Path('experiments/active/exp-spike-v112-one-line-audit-20260920-v1/input.json.gz')
    payload = json.loads(gzip.decompress(path.read_bytes()))
    raw = pd.DataFrame(payload['15m']['candles'])
    raw.index = pd.to_datetime(raw.t,unit='ms',utc=True)
    raw = raw.rename(columns={'o':'open','h':'high','l':'low','c':'close','v':'volume'})
    facts = v9_facts(raw[['open','high','low','close','volume']],15,'ONE',payload['tick'])
    f = facts['frame']
    def replay(n):
        z = f.iloc[:n]
        return auxiliary_touch_events(z.open,z.high,z.low,z.close,z.atr,can_run=facts['can_run'][:n],tick=payload['tick'],params=params)
    full = replay(len(f))
    target = [r for r in full.trace['lines'] if (r['ax'],r['bx'],r['cx'])==(1066,1182,1189)]
    checks=[]
    for n in (1192,1217):
        short=replay(n)
        np.testing.assert_array_equal(full.break_event[:n],short.break_event)
        assert short.events == [x for x in full.events if x['i']<n]
        checks.append(n)
    return {'input':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'target_lines':target,'prefix_checks':checks}


def main():
    assert _committed((Path(__file__), EXP/'PROJECT_PLAN.md')), 'Commit builder first'
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy-rebound', action='store_true')
    args = parser.parse_args()
    out_dir = EXP / ('ab_legacy' if args.legacy_rebound else 'ab_b_atr')
    out_dir.mkdir(exist_ok=True)
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
        aux = auxiliary_touch_events(frame.open,frame.high,frame.low,frame.close,frame.atr,can_run=facts['can_run'],tick=tick,params=LocalTouchParams(rebound_at_b=not args.legacy_rebound))
        def stamp(i, close=False):
            return (f.index[int(i)] + pd.Timedelta(minutes=15 if close else 0)).tz_convert('Asia/Shanghai').isoformat()
        lines = pd.DataFrame(aux.trace['lines'])
        for name in ('ax','bx','cx','born_i','break_i'):
            lines[name+'_bj'] = [stamp(i, name in ('born_i','break_i')) if pd.notna(i) else None for i in lines[name]]
        lines.to_csv(out_dir/f'{asset}_auxiliary_lines.csv',index=False)
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
    result['rebound_at_b'] = not args.legacy_rebound
    result['one_regression'] = audit_one(LocalTouchParams(rebound_at_b=not args.legacy_rebound))
    (out_dir/'screenshot_cases.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')


if __name__ == '__main__':
    main()
