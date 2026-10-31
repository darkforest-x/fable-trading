"""One native execution pass per new fixed-policy order; never a custom fill engine.

Nautilus 1.231.0, HEDGING independent positions, explicit monotone OHLC segments.
Signals are maker limits; random completed-close controls are taker markets.
Targets maker, initial stops and NY expiry taker. Slippage/funding omitted and
reported, independent risk units are not compounded account returns.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_eth_repeat_data import EXP, config, dump, read_frame
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def execute(symbol, suffix='native', entries=None, path='OHLC'):
    cfg = config()
    out = EXP/f'{symbol}_{suffix}'
    receipt = out.with_suffix('.json')
    if receipt.exists():
        print(f'Reusing completed {receipt.name}',flush=True)
        return
    frame = read_frame(symbol)
    if entries is None:
        entries = pd.read_csv(EXP/f'{symbol}_entries.csv.gz',float_precision='round_trip')
    entries.to_csv(EXP/f'{symbol}_{suffix}_orders.csv.gz', index=False)
    policy = ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=0,
                        fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'],
                        intrabar_path=path,monotone_segments=True)
    rows,fills,audit = run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),entries,policy,
                                  tick=.1 if symbol=='BTC' else .01,symbol=symbol,
                                  entry_order_mode='limit',execution_minutes=5,batch_bars=24000)
    rows.to_csv(EXP/f'{symbol}_{suffix}_trades.csv.gz',index=False)
    fills.to_csv(EXP/f'{symbol}_{suffix}_fills.csv.gz',index=False)
    audit['builder_commit'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    audit['config'] = cfg
    dump(receipt,audit)
    print(symbol,suffix,dict(orders=len(entries),trades=len(rows),fills=len(fills),ambiguous=int(rows.ambiguous.sum())),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol',choices=['BTC','ETH'],required=True)
    parser.add_argument('--continuation',action='store_true')
    parser.add_argument('--sensitivity',action='store_true')
    args=parser.parse_args()
    if args.continuation:
        entries=pd.read_csv(EXP/f'{args.symbol}_continuation_orders.csv.gz',float_precision='round_trip')
        if len(entries):
            execute(args.symbol,'close_now',entries)
    elif args.sensitivity:
        rows=pd.read_csv(EXP/f'{args.symbol}_native_trades.csv.gz',float_precision='round_trip')
        orders=pd.read_csv(EXP/f'{args.symbol}_entries.csv.gz',float_precision='round_trip')
        # Entry candle ordering can affect same-bar exits even without both full barriers.
        ids=rows.loc[rows.ambiguous.eq(1)|rows.exit_i.eq(rows.entry_i),'trade_id']
        selected=orders[orders.trade_id.isin(ids)]
        if len(selected):
            execute(args.symbol,'path_sensitivity',selected,'OLHC')
    else:
        execute(args.symbol)


if __name__=='__main__':
    main()
