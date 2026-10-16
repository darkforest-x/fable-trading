"""Explicit local full-universe acceptance run before enabling owner schedules.

Uses the real worker lock, model, public source and observation book. Only
research schedules change. Existing observations and trading books remain.
Raw measurements and the prior schedule configuration are retained locally.
"""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import statistics
import threading
import time
import urllib.request
import uuid
from .store import Book,now
from .models import DEFAULT_MODEL,TIMEFRAMES,freeze_spec
from .patrol import Patrol,next_due,GRACE_MS
from .market import Market
from .inference import Engine
from .resources import Governor


def main(runtime,output,enable=False):
    output.mkdir(parents=True,exist_ok=True);book=Book(runtime)
    with (book.runtime/'worker.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        os.nice(10)
        prior=book.schedules();(output/'previous_schedules.json').write_text(json.dumps(prior,ensure_ascii=False,indent=2))
        if any(j['status'] in ('running','queued') for j in book.jobs(100)):
            raise RuntimeError('先等待已有检测完成，不能与验收争抢队列')
        for s in prior:
            if s['enabled']:book.schedule(dict(s,enabled=False))
        plans=[];base=now()//900000*900000-900000
        for tf,interval in [('5m',15),('15m',15),('1H',240),('1Dutc',1440)]:
            step=TIMEFRAMES[tf];end=base//step*step
            s=dict(id=uuid.uuid4().hex,spec=freeze_spec(dict(model_id=DEFAULT_MODEL,timeframe=tf,mode='latest',scope='all',symbols=[])),
                   enabled=False,validation=True,incremental=True,interval_minutes=interval,created_ms=now(),
                   next_due_ms=next_due(now(),interval),baseline_after_ms=end-max(3,interval*60000//step)*step,last_job_id=None)
            book.schedule(s);plans.append(s)
        (output/'plans.json').write_text(json.dumps(plans,ensure_ascii=False,indent=2))
        market=Market(runtime=runtime,book=book);engine=Engine();patrol=Patrol(book,market,engine);governor=Governor()
        records=[];accepted=False
        def run_group(name,selected,cutoff):
            ids=[]
            for s in selected:
                step=TIMEFRAMES[s['spec']['timeframe']];end=cutoff//step*step
                spec=dict(s['spec'],incremental=True,cursor_key=s['id'],baseline_after_ms=s['baseline_after_ms'],
                          interval_minutes=s['interval_minutes'],start_ms=end-240*step,end_ms=end)
                j=book.submit(spec,'validation-'+uuid.uuid4().hex,s['id']);ids.append(j['id'])
                s['last_job_id']=j['id'];book.schedule(s)
            samples=[];stop=threading.Event()
            def sample():
                while not stop.is_set():
                    started=time.monotonic()
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:8766/api/yolo-detect/jobs?limit=1',timeout=5) as response:response.read()
                        samples.append(time.monotonic()-started)
                    except Exception:samples.append(5.)
                    stop.wait(3)
            watcher=threading.Thread(target=sample,daemon=True);watcher.start()
            started=time.monotonic();last=0;pauses=0
            try:
                while any(book.get(i)['status'] in ('queued','running') for i in ids):
                    if time.monotonic()-started>3600:raise RuntimeError('验收一轮超时，未启用高频计划')
                    reason=governor.pause_reason()
                    if reason:
                        pauses+=1;book.meta('worker',dict(pid=os.getpid(),state='cooling',reason=reason,heartbeat_ms=now()));time.sleep(5)
                    else:
                        if not patrol.tick():time.sleep(.5)
                        else:time.sleep(.08)
                    if time.monotonic()-last>30:
                        last=time.monotonic();print(json.dumps(dict(stage=name,elapsed_s=round(last-started,1),jobs=[dict(id=i,status=book.get(i)['status'],progress=book.get(i)['progress']) for i in ids]),ensure_ascii=False),flush=True)
            finally:stop.set();watcher.join(timeout=6)
            jobs=[book.get(i) for i in ids]
            with closing(book.connect()) as db:
                stored=db.execute('SELECT sum(length(payload)) FROM chart_blobs').fetchone()[0] or 0
            result=dict(stage=name,elapsed_s=round(time.monotonic()-started,3),api_p95_s=round(sorted(samples)[min(len(samples)-1,int(len(samples)*.95))],3) if samples else None,
                        memory_pauses=pauses,chart_blob_bytes=stored,jobs=[dict(id=j['id'],status=j['status'],progress=j['progress']) for j in jobs])
            records.append(result);(output/'measurements.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
            print(json.dumps(result,ensure_ascii=False),flush=True);return result
        try:
            cold=run_group('cold-short',plans[:2],base)
            warm=run_group('warm-short',plans[:2],base+900000)
            long=run_group('long-bootstrap',plans[2:],base+900000)
            catchup=run_group('current-short',plans[:2],now()//900000*900000)
            groups=[cold,warm,long,catchup]
            good=lambda g:all(j['status'] in ('completed','partial') and not j['progress'].get('failed',0) for j in g['jobs'])
            accepted=all(good(g) for g in groups) and warm['elapsed_s']<600 and warm['api_p95_s'] is not None and warm['api_p95_s']<1 and warm['memory_pauses']==0
            (output/'acceptance.json').write_text(json.dumps(dict(accepted=accepted,enabled=accepted and enable,criterion='warm 5m+15m <600s, API p95 <1s, no memory pauses, no transient failed coins in all four groups'),indent=2))
            if accepted and enable:
                for s in plans:
                    # Cursor timestamps, not activation time, define next pending work.
                    last=book.get(s['last_job_id']) if s.get('last_job_id') else None
                    pending=last and last['progress'].get('remaining_endpoints',0)
                    book.schedule(dict(s,enabled=True,validation=False,next_due_ms=now() if pending else next_due(now(),s['interval_minutes'])))
                for s in prior:
                    if s['enabled'] and s['spec']['timeframe']=='15m' and s['spec']['scope']=='all' and s['interval_minutes']==60:book.remove_schedule(s['id'])
                    else:book.schedule(s)
            else:
                for s in plans:book.schedule(dict(s,validation=False,error='容量验收未通过或仅测量，尚未启用'))
                for s in prior:book.schedule(s)
            print(json.dumps(dict(accepted=accepted,enabled=accepted and enable),ensure_ascii=False),flush=True)
        finally:
            if not accepted:
                for s in prior:book.schedule(s)
            book.meta('worker',dict(pid=os.getpid(),heartbeat_ms=now(),state='stopped'))
            book.meta('control',dict(stop_worker=False,maintenance_until_ms=0))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--enable-on-pass',action='store_true');a=p.parse_args()
    main(a.runtime,a.output,a.enable_on_pass)
