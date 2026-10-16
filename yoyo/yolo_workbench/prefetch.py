"""Bounded public input lookahead; one model owner, two shared-rate IO readers.

Only frozen inputs are prepared. No result or coverage cursor is written here.
Future errors return to the normal patrol retry path. Four retained inputs cap
memory, and the public client shares its rate limiter across all readers.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
from .market import Market
from .models import TIMEFRAMES
from .store import now

MAX_ENDPOINTS=12


def bounds(spec,symbol,cursor):
    step=TIMEFRAMES[spec['timeframe']]
    after=cursor if cursor is not None else spec['baseline_after_ms']
    listed=spec.get('listed_ms',{}).get(symbol,0)
    eligible=((listed+step-1)//step+1240)*step if listed else 0
    if cursor is None:after=max(after,eligible-step)
    return after,min(spec['end_ms'],after+MAX_ENDPOINTS*step),eligible


class Prefetch:
    def __init__(self,book,market):
        self.book=book;self.market=market;self.local=threading.local()
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='patrol-input')
        self.pending={};self.stop=threading.Event()

    def read(self,job,symbol,after,end):
        if not hasattr(self.local,'market'):
            self.local.market=Market(self.market.client,runtime=self.market.runtime,book=self.book)
        market=self.local.market;tf=job['spec']['timeframe']
        rows=market.candles(symbol,tf,end,end-240*TIMEFRAMES[tf],lambda:self.stop.is_set() or self.book.cancelled(job['id']))
        return rows,market.sources.get((symbol,tf),'OKX public candles')

    def candles(self,job,symbol,after,end):
        # Discard cancelled work without retaining an unbounded future cache.
        for key,future in list(self.pending.items()):
            if self.book.cancelled(key[0]):
                future.cancel();self.pending.pop(key)
        with closing(self.book.connect()) as db:
            units=db.execute("""SELECT u.symbol,c.through_ms FROM patrol_units u
                LEFT JOIN patrol_cursors c ON c.schedule_id=? AND c.symbol=u.symbol
                WHERE u.job_id=? AND u.status='pending' AND u.retry_ms<=?
                ORDER BY u.rowid LIMIT 2""",(job['spec']['cursor_key'],job['id'],now())).fetchall()
        for unit in units:
            a,e,eligible=bounds(job['spec'],unit['symbol'],unit['through_ms'])
            key=(job['id'],unit['symbol'],a,e)
            if eligible>job['spec']['end_ms'] or e<=a or key in self.pending:continue
            if len(self.pending)>=4:break
            self.pending[key]=self.pool.submit(self.read,job,unit['symbol'],a,e)
        key=(job['id'],symbol,after,end);future=self.pending.get(key)
        if future is None:return self.read(job,symbol,after,end)
        try:return future.result()
        finally:self.pending.pop(key,None)

    def close(self):
        self.stop.set()
        for future in self.pending.values():future.cancel()
        self.pool.shutdown(wait=True,cancel_futures=True)
