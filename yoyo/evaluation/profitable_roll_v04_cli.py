"""Owner-selected two-add research entry point, selected 2026-09-21.

Preserve the frozen V0.3 research CLI and accounting engine. This V0.4 profile
only selects its existing cap-two arm: one initial entry plus at most two
successful adds. Structure protection continues after the cap is reached.
No exchange request or account operation is performed.
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.profitable_roll_v03 import replay_roll


def read_bars(path):
    frame=pd.read_csv(path)
    key=next((name for name in ['open_time','time','timestamp','ts'] if name in frame),None)
    if key is None:raise ValueError('CSV needs open_time/time/timestamp/ts column')
    raw=frame.pop(key)
    frame.index=pd.to_datetime(raw,unit='ms',utc=True) if pd.api.types.is_numeric_dtype(raw) else pd.to_datetime(raw,utc=True)
    return frame


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prices',type=Path,required=True);p.add_argument('--marks',type=Path)
    p.add_argument('--funding',type=Path,help='JSON object:epoch-ms settlement timestamp to rate')
    p.add_argument('--tiers',type=Path,required=True,help='JSON list of max_quantity/mmr/max_leverage tiers')
    p.add_argument('--entry',required=True,help='Exact bar-open ISO8601 timestamp with timezone')
    p.add_argument('--stop',required=True,type=float);p.add_argument('--requested-quantity',required=True,type=float)
    p.add_argument('--tick',required=True,type=float);p.add_argument('--quantity-step',required=True,type=float)
    p.add_argument('--min-quantity',required=True,type=float);p.add_argument('--min-notional',type=float,default=0)
    p.add_argument('--max-order-quantity',type=float);p.add_argument('--capital',type=float,default=100)
    p.add_argument('--leverage',type=float,default=40);p.add_argument('--fee',type=float,default=.001)
    p.add_argument('--max-adds',type=int,choices=[2],default=2,
                   help='Selected V0.4 policy: at most two successful adds (default: 2)')
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    frame=read_bars(a.prices);entry=pd.Timestamp(a.entry)
    if entry.tzinfo is None:raise ValueError('--entry must include a timezone')
    i=int(frame.index.get_indexer([entry.tz_convert('UTC')])[0])
    if i<0:raise ValueError('Entry timestamp is absent from price CSV')
    result,events=replay_roll(frame,entry_i=i,initial_stop=a.stop,tick=a.tick,
        quantity_step=a.quantity_step,min_quantity=a.min_quantity,min_notional=a.min_notional,
        max_order_quantity=a.max_order_quantity,initial_quantity_requested=a.requested_quantity,
        tiers=json.loads(a.tiers.read_text()),capital=a.capital,leverage=a.leverage,fee=a.fee,
        max_adds=a.max_adds,
        mark_frame=None if a.marks is None else read_bars(a.marks),
        funding_rates=None if a.funding is None else json.loads(a.funding.read_text()))
    result.update(strategy_profile='profitable_roll_v04_two_adds',max_adds=2)
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(events).to_csv(a.output/'events.csv',index=False)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
