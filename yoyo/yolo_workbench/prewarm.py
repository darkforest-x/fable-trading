"""Bounded public-IO bootstrap for a specified existing observation job.

Two IO readers share one <=3 requests/second client. There is no model/GPU
work, no signal output and no cursor advancement. Checkpoints stay bounded.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import threading
from .market import Market
from .store import Book
from yoyo.monitor.okx import OKX


def prewarm(runtime,identity):
    os.nice(15);book=Book(runtime);job=book.get(identity)
    if not job or not job['spec'].get('universe'):raise ValueError('需要已冻结币种池的任务')
    spec=job['spec'];client=OKX(rate=3);client.synchronize();local=threading.local()
    def read(symbol):
        try:
            cached=book.input_cache(symbol,spec['timeframe'])
            step=__import__('yoyo.yolo_workbench.models',fromlist=['TIMEFRAMES']).TIMEFRAMES[spec['timeframe']]
            if cached and cached[-1]['t']+step>=spec['end_ms']:return symbol,'cached'
            if not hasattr(local,'market'):local.market=Market(client,runtime=runtime,book=book)
            local.market.candles(symbol,spec['timeframe'],spec['end_ms'],spec['start_ms'])
            return symbol,'ready'
        except Exception as exc:return symbol,str(exc)[:200]
    # Start at the opposite end from the GPU worker to avoid duplicate fetches.
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i,(symbol,status) in enumerate(pool.map(read,reversed(spec['universe']))):
            print(json.dumps(dict(completed=i+1,symbol=symbol,status=status),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--job',required=True);a=parser.parse_args();prewarm(a.runtime,a.job)
