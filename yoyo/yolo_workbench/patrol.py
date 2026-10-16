"""Durable endpoint coverage with cooperative, short-period-first scheduling.

One GPU owner processes one coin at a time. Committed result and coverage
cursor advance atomically. Failed inputs never advance coverage. Catchup is
bounded per visit, but retains the remaining endpoints for subsequent jobs.
No execution pulse, training contract or canonical OHLC store is touched.
"""
from contextlib import closing
import json
import time
from .store import encode, now
from .models import TIMEFRAMES
from .storage import pack
from .prefetch import Prefetch,bounds,MAX_ENDPOINTS

GRACE_MS=60000


def next_due(stamp,interval_minutes):
    interval=interval_minutes*60000
    return (max(0,stamp-GRACE_MS)//interval+1)*interval+GRACE_MS


def enqueue_due(book,stamp):
    for schedule in book.schedules():
        if not schedule.get('incremental') or not schedule['enabled'] or schedule['next_due_ms']>stamp:continue
        old=book.get(schedule['last_job_id']) if schedule.get('last_job_id') else None
        if old and old['status'] in ('queued','running'):continue
        step=TIMEFRAMES[schedule['spec']['timeframe']]
        cutoff=(stamp-GRACE_MS)//step*step
        spec=dict(schedule['spec'],incremental=True,end_ms=cutoff,start_ms=cutoff-240*step,
                  cursor_key=schedule['id'],baseline_after_ms=schedule['baseline_after_ms'],
                  interval_minutes=schedule['interval_minutes'])
        try:job=book.submit(spec,f"{schedule['id']}:{cutoff}:{schedule.get('generation',0)}",schedule['id'])
        except ValueError:continue
        # Catchup generations may share a cutoff, but never repeat a committed endpoint.
        with closing(book.connect()) as db,db:
            row=db.execute('SELECT payload FROM schedules WHERE id=?',(schedule['id'],)).fetchone()
            if row:
                latest=json.loads(row[0]);latest.update(last_job_id=job['id'],next_due_ms=next_due(stamp,latest['interval_minutes']),generation=latest.get('generation',0)+1,error=None)
                db.execute('UPDATE schedules SET payload=? WHERE id=?',(encode(latest),schedule['id']))


class Patrol:
    def __init__(self,book,market,engine,prefetch=False):
        self.book=book;self.market=market;self.engine=engine
        self.prefetch=Prefetch(book,market) if prefetch else None

    def close(self):
        if self.prefetch:self.prefetch.close()

    def choose(self):
        with closing(self.book.connect()) as db:
            row=db.execute("""SELECT * FROM jobs WHERE status IN ('queued','running')
                AND json_extract(spec,'$.incremental')=1 AND (cancel=1
                OR NOT EXISTS(SELECT 1 FROM patrol_units WHERE job_id=jobs.id)
                OR NOT EXISTS(SELECT 1 FROM patrol_units WHERE job_id=jobs.id AND status='pending')
                OR EXISTS(SELECT 1 FROM patrol_units WHERE job_id=jobs.id AND status='pending' AND retry_ms<=?))
                ORDER BY CASE WHEN json_extract(spec,'$.timeframe') IN ('5m','15m') THEN 0 ELSE 1 END,
                updated_ms,created_ms,id LIMIT 1""",(now(),)).fetchone()
            return self.book.job(row)

    def initialize(self,job):
        if job['spec'].get('universe') is not None:return job
        universe=self.market.universe();known={r['symbol']:r for r in universe};spec=dict(job['spec'])
        symbols=list(known) if spec['scope']=='all' else spec['symbols']
        if not set(symbols)<=set(known):raise ValueError('所选币种不在当前 OKX 加密永续池中')
        spec.update(universe=symbols,listed_ms={s:known[s].get('listed_ms',0) for s in symbols},universe_observed_ms=self.market.client.clock())
        with closing(self.book.connect()) as db,db:
            db.executemany("INSERT OR IGNORE INTO patrol_units(job_id,symbol,status) VALUES (?,?,'pending')",[(job['id'],s) for s in symbols])
            db.execute("UPDATE jobs SET status='running',spec=?,progress=?,updated_ms=? WHERE id=?",
                       (encode(spec),encode(dict(completed=0,total=len(symbols),failed=0,unavailable=0,started_ms=now())),now(),job['id']))
        return self.book.get(job['id'])

    def commit(self,job,symbol,payload,through=None):
        """The only coverage advancement path: result + cursor + unit, one commit."""
        with closing(self.book.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if self.book.cancelled(job['id']):return
            exists=db.execute('SELECT 1 FROM results WHERE job_id=? AND symbol=?',(job['id'],symbol)).fetchone()
            if not exists:
                db.execute('INSERT INTO results VALUES (?,?,?)',(job['id'],symbol,encode(pack(db,payload))))
                if through is not None:
                    db.execute('INSERT INTO patrol_cursors VALUES (?,?,?) ON CONFLICT(schedule_id,symbol) DO UPDATE SET through_ms=MAX(through_ms,excluded.through_ms)',
                               (job['spec']['cursor_key'],symbol,through))
            db.execute("UPDATE patrol_units SET status='done' WHERE job_id=? AND symbol=?",(job['id'],symbol))

    def progress(self,job,symbol='',seconds=0):
        with closing(self.book.connect()) as db:
            total,done=db.execute("SELECT count(*),COALESCE(sum(status='done'),0) FROM patrol_units WHERE job_id=?",(job['id'],)).fetchone()
            failed,unavailable,backlog,endpoints=db.execute("""SELECT
                COALESCE(sum(json_extract(payload,'$.error') IS NOT NULL AND COALESCE(json_extract(payload,'$.availability'),'')!='insufficient_history'),0),
                COALESCE(sum(json_extract(payload,'$.availability')='insufficient_history'),0),
                COALESCE(sum(COALESCE(json_extract(payload,'$.remaining_endpoints'),0)),0),
                COALESCE(sum(COALESCE(json_extract(payload,'$.endpoints_scored'),0)),0)
                FROM results WHERE job_id=?""",(job['id'],)).fetchone()
        previous=self.book.get(job['id'])['progress'];started=previous.get('started_ms',job['created_ms'])
        progress=dict(completed=done,total=total,failed=failed,unavailable=unavailable,pending=total-done,
                      remaining_endpoints=backlog,endpoints_scored=endpoints,symbol=symbol,started_ms=started,
                      elapsed_seconds=round((now()-started)/1000,2),last_coin_seconds=round(seconds,3))
        status=('partial' if failed or unavailable else 'completed') if total==done else 'running'
        self.book.update(job['id'],status=status,progress=progress)
        if status in ('partial','completed') and backlog and job.get('schedule_id'):
            with closing(self.book.connect()) as db,db:
                row=db.execute('SELECT payload FROM schedules WHERE id=?',(job['schedule_id'],)).fetchone()
                if row:
                    s=json.loads(row[0]);s['next_due_ms']=now();db.execute('UPDATE schedules SET payload=? WHERE id=?',(encode(s),s['id']))

    def tick(self):
        job=self.choose()
        if not job:return False
        identity=job['id']
        if job['cancel']:
            self.book.update(identity,status='cancelled');return True
        try:job=self.initialize(job)
        except Exception as exc:
            self.book.update(identity,status='failed',error=str(exc)[:500]);return True
        spec=job['spec'];step=TIMEFRAMES[spec['timeframe']]
        with closing(self.book.connect()) as db:
            unit=db.execute("SELECT * FROM patrol_units WHERE job_id=? AND status='pending' AND retry_ms<=? ORDER BY rowid LIMIT 1",(identity,now())).fetchone()
            if not unit:
                self.progress(job);return True
            symbol=unit['symbol']
            cursor=db.execute('SELECT through_ms FROM patrol_cursors WHERE schedule_id=? AND symbol=?',(spec['cursor_key'],symbol)).fetchone()
        start=time.monotonic();after,end,eligible=bounds(spec,symbol,cursor[0] if cursor else None)
        # A coin cannot have valid inputs before its listing plus the frozen warmup.
        if eligible>spec['end_ms']:
            self.commit(job,symbol,dict(symbol=symbol,timeframe=spec['timeframe'],observed_ms=now(),
                error='上市历史不足 1,240 根，尚不能按原模型输入检测',availability='insufficient_history',eligible_from_ms=eligible))
        else:
            if end<=after:
                with closing(self.book.connect()) as db,db:db.execute("UPDATE patrol_units SET status='done' WHERE job_id=? AND symbol=?",(identity,symbol))
            else:
                sub=dict(spec,scan_after_ms=after,end_ms=end,start_ms=end-240*step)
                cancel=lambda:self.book.cancelled(identity)
                def pulse(done,total):
                    self.book.meta('worker',dict(pid=__import__('os').getpid(),heartbeat_ms=now(),state='running',job_id=identity,symbol=symbol,windows_done=done,windows_total=total))
                    # Small batches leave room for interactive work on the shared GPU.
                    time.sleep(.02)
                try:
                    if self.prefetch:rows,source=self.prefetch.candles(job,symbol,after,end)
                    else:
                        rows=self.market.candles(symbol,spec['timeframe'],end,sub['start_ms'],cancel)
                        source=self.market.sources.get((symbol,spec['timeframe']),'OKX public candles')
                    result=self.engine.scan(rows,sub,symbol,pulse,cancel)
                    result.update(observed_ms=now(),source=source,
                        incremental=True,coverage_after_ms=after,coverage_through_ms=end,remaining_endpoints=(spec['end_ms']-end)//step,
                        universe_observed_ms=spec['universe_observed_ms'])
                    self.commit(job,symbol,result,end)
                except Exception as exc:
                    if cancel():self.book.update(identity,status='cancelled');return True
                    attempts=unit['attempts']+1
                    if attempts<3:
                        with closing(self.book.connect()) as db,db:
                            db.execute('UPDATE patrol_units SET attempts=?,retry_ms=? WHERE job_id=? AND symbol=?',(attempts,now()+15000*attempts,identity,symbol))
                    else:self.commit(job,symbol,dict(symbol=symbol,timeframe=spec['timeframe'],observed_ms=now(),error=str(exc)[:500],coverage_after_ms=after,retry_pending=True))
        self.progress(job,symbol,time.monotonic()-start)
        return True
