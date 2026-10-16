"""Single-process background queue: no imports of execution/notification code.

A process lock permits only one worker per workbench book. Scheduled runs skip
missed occurrences and never overlap. A crash preserves partial results and
marks the old job interrupted; the UI never presents it as complete.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path
from .models import ROOT, TIMEFRAMES
from .store import Book, now


def start(runtime):
    book=Book(runtime)
    with (book.runtime/'worker.log').open('ab') as log:
        subprocess.Popen([sys.executable,'-m','yoyo.yolo_workbench.worker','--runtime',str(runtime)],
                         cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)


def due_schedules(book,stamp):
    for schedule in book.schedules():
        if not schedule['enabled'] or schedule['next_due_ms']>stamp:continue
        old=book.get(schedule['last_job_id']) if schedule.get('last_job_id') else None
        if old and old['status'] in ('queued','running'):
            schedule['skipped_overlap']=schedule.get('skipped_overlap',0)+1
        else:
            try:
                job=book.submit(schedule['spec'],f"{schedule['id']}:{schedule['next_due_ms']}",schedule['id'])
                schedule['last_job_id']=job['id'];schedule['error']=None
            except ValueError as exc:schedule['error']=str(exc)
        schedule['next_due_ms']=stamp+schedule['interval_minutes']*60000
        # Do not resurrect a schedule deleted by the UI during this tick.
        with closing(book.connect()) as db,db:
            from .store import encode
            db.execute('UPDATE schedules SET payload=? WHERE id=?',(encode(schedule),schedule['id']))


def execute(book,job,market,engine):
    from .market import Cancelled
    identity=job['id'];spec=dict(job['spec']);completed=0;failed=0;total=0
    cancel=lambda:book.cancelled(identity)
    def progress(symbol,done,windows):
        book.meta('worker',dict(pid=os.getpid(),heartbeat_ms=now(),state='running',job_id=identity))
        book.update(identity,progress=dict(completed=completed,total=total,failed=failed,symbol=symbol,
                                          windows_done=done,windows_total=windows))
    try:
        universe=market.universe();known={r['symbol'] for r in universe}
        symbols=[r['symbol'] for r in universe] if spec['scope']=='all' else spec['symbols']
        if not set(symbols)<=known:raise ValueError('所选币种不在当前 OKX 加密永续池中')
        stamp=market.client.clock();step=TIMEFRAMES[spec['timeframe']]
        if spec['mode']=='latest':spec.update(end_ms=stamp//step*step,start_ms=stamp//step*step-240*step)
        spec.update(universe=symbols,universe_observed_ms=stamp)
        book.update(identity,spec=spec);total=len(symbols)
        for symbol in symbols:
            if cancel():raise Cancelled()
            progress(symbol,0,0)
            try:
                rows=market.candles(symbol,spec['timeframe'],spec['end_ms'],spec['start_ms'],cancel)
                result=engine.scan(rows,spec,symbol,lambda done,n:progress(symbol,done,n),cancel)
                result.update(observed_ms=now(),source='OKX public history-candles',universe_observed_ms=stamp)
            except Cancelled:raise
            except Exception as exc:
                failed+=1;result=dict(symbol=symbol,error=str(exc)[:500],observed_ms=now())
            book.result(identity,symbol,result);completed+=1;progress(symbol,0,0)
        status='failed' if failed==total else 'partial' if failed else 'completed'
        book.update(identity,status='cancelled' if cancel() else status)
    except Cancelled:book.update(identity,status='cancelled')
    except Exception as exc:book.update(identity,status='failed',error=str(exc)[:500])


def run(runtime):
    book=Book(runtime)
    with (book.runtime/'worker.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        book.recover()
        from .market import Market
        from .inference import Engine
        market=Market();engine=Engine();last_work=now()
        while True:
            stamp=now();book.meta('worker',dict(pid=os.getpid(),heartbeat_ms=stamp,state='idle'))
            due_schedules(book,stamp)
            job=book.claim()
            if job:execute(book,job,market,engine);last_work=now();continue
            if stamp-last_work>600000 and not any(s['enabled'] for s in book.schedules()):break
            time.sleep(2)
        book.meta('worker',dict(pid=os.getpid(),heartbeat_ms=now(),state='stopped'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True)
    run(parser.parse_args().runtime)
