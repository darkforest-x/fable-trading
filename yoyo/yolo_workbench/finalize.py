"""Verify completed capacity trials and repaired coverage before activation.

This explicit local command preserves failed trial receipts. Recovery is
accepted only from committed endpoint evidence, never by clearing an error.
It waits for the calibration owner, then holds the same single-worker lock.
"""
import argparse
from contextlib import closing
import fcntl
import json
from pathlib import Path
import time
from .models import TIMEFRAMES
from .patrol import next_due,GRACE_MS
from .prefetch import bounds
from .store import Book,now


def verify(book,plans,records):
    warm=[r for r in records if r['stage']=='warm-short-prefetch'][-1]
    if not (warm['elapsed_s']<600 and warm['api_p95_s'] is not None and warm['api_p95_s']<1 and warm['memory_pauses']==0):
        raise ValueError('steady capacity acceptance failed')
    keys={p['id'] for p in plans};jobs={};latest={}
    for record in records:
        for item in record['jobs']:
            job=book.get(item['id'])
            if not job or job.get('schedule_id') not in keys:raise ValueError('trial does not belong to selected plans')
            if not job['spec'].get('incremental') or job['spec'].get('scope')!='all':raise ValueError('trial is not full-market incremental')
            if job['spec'].get('cursor_key')!=job['schedule_id']:raise ValueError('trial cursor does not belong to plan')
            if not job['spec'].get('universe'):raise ValueError('trial universe is empty')
            jobs[job['id']]=job
            if job['status'] not in ('completed','partial'):raise ValueError('trial is not complete')
            progress=book.current_progress(job)
            if progress.get('failed'):raise ValueError('unresolved trial failures')
            latest[job['spec']['timeframe']]=job
    recovered=sum(book.current_progress(j).get('recovered',0) for j in jobs.values())
    if {jobs[j['id']]['spec']['timeframe'] for j in warm['jobs']}!={'5m','15m'}:raise ValueError('warm trial is missing a short timeframe')
    if set(latest)!={'5m','15m','1H','1Dutc'}:raise ValueError('missing timeframe acceptance')
    counts={};last={};ranges=0
    with closing(book.connect()) as db:
        db.execute('BEGIN')
        for tf,job in latest.items():
            spec=job['spec'];covered=unavailable=0
            for symbol in spec['universe']:
                row=db.execute('SELECT through_ms FROM patrol_cursors WHERE schedule_id=? AND symbol=?',(spec['cursor_key'],symbol)).fetchone()
                if row and row[0]>=spec['end_ms']:covered+=1;continue
                row=db.execute("SELECT json_extract(payload,'$.availability') FROM results WHERE job_id=? AND symbol=?",(job['id'],symbol)).fetchone()
                if row and row[0]=='insufficient_history' and bounds(spec,symbol,None)[2]>spec['end_ms']:
                    unavailable+=1;continue
                raise ValueError('missing endpoint coverage: '+tf+' '+symbol)
            counts[tf]=dict(total=len(spec['universe']),covered=covered,unavailable=unavailable,through_ms=spec['end_ms'])
        rows=db.execute("""SELECT j.schedule_id,r.symbol,r.payload,j.spec FROM results r
            JOIN jobs j ON j.id=r.job_id WHERE j.schedule_id IN (SELECT value FROM json_each(?))
            AND json_extract(r.payload,'$.coverage_through_ms') IS NOT NULL
            ORDER BY j.schedule_id,r.symbol,json_extract(r.payload,'$.coverage_after_ms')""",(json.dumps(sorted(keys)),))
        for sid,symbol,raw,raw_spec in rows:
            value=json.loads(raw);a=value['coverage_after_ms'];e=value['coverage_through_ms'];step=TIMEFRAMES[value['timeframe']];key=(sid,symbol)
            if e<=a or (e-a)%step or (e-a)//step!=value['endpoints_scored']:raise ValueError('endpoint count mismatch')
            if key not in last and a!=bounds(json.loads(raw_spec),symbol,None)[0]:raise ValueError('coverage does not start at eligible baseline')
            if key in last and last[key]!=a:raise ValueError('coverage gap or duplicate')
            last[key]=e;ranges+=1
        for key,through in last.items():
            cursor=db.execute('SELECT through_ms FROM patrol_cursors WHERE schedule_id=? AND symbol=?',key).fetchone()
            if not cursor or cursor[0]!=through:raise ValueError('cursor lacks matching saved evidence')
    return dict(accepted=True,warm_elapsed_s=warm['elapsed_s'],api_p95_s=warm['api_p95_s'],memory_pauses=warm['memory_pauses'],
                repaired_failures=recovered,coverage=counts,verified_result_ranges=ranges,verified_coin_periods=len(last))


def main(runtime,output):
    # Calibration restores its fallback on a rejected initial network pass.
    # Hold supervision only after that receipt exists; never interrupt a scan.
    while not (output/'acceptance.json').exists():time.sleep(.2)
    book=Book(runtime)
    with (book.runtime/'worker.lock').open('a+') as lock:
        # The calibration finally block can clear control just before releasing
        # its lock. Reassert the hold until ownership transfers; never rely on
        # observing a transient heartbeat state or kill an active inference.
        while True:
            book.meta('control',dict(stop_worker=True,maintenance_until_ms=now()+7200000))
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:time.sleep(.2)
        try:
            saved=json.loads((output/'plans.json').read_text());ids={s['id'] for s in saved}
            plans=[s for s in book.schedules() if s['id'] in ids]
            expected={'5m':15,'15m':15,'1H':240,'1Dutc':1440}
            if len(plans)!=4 or {p['spec']['timeframe']:p['interval_minutes'] for p in plans}!=expected or any(p['spec']['scope']!='all' for p in plans):
                raise ValueError('owner plan selection changed; activation withheld')
            records=json.loads((output/'measurements.json').read_text());receipt=verify(book,plans,records)
            stamp=now()
            for s in plans:
                tf=s['spec']['timeframe'];latest=receipt['coverage'][tf]['through_ms']
                behind=tf in ('5m','15m') and latest<(stamp-GRACE_MS)//900000*900000
                book.schedule(dict(s,enabled=True,validation=False,error=None,next_due_ms=stamp if behind else next_due(stamp,s['interval_minutes'])))
            prior=json.loads((output/'previous_schedules.json').read_text())
            obsolete={s['id'] for s in prior if s['enabled'] and s['spec']['timeframe']=='15m' and s['spec']['scope']=='all' and s['interval_minutes']==60}
            for job in book.jobs(100):
                if job.get('schedule_id') in obsolete and job['status'] in ('queued','running'):book.cancel(job['id'])
            for identity in obsolete:book.remove_schedule(identity)
            receipt.update(enabled=True,activated_ms=stamp,plan_ids=sorted(ids))
            (output/'activation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2));print(json.dumps(receipt,ensure_ascii=False),flush=True)
        finally:book.meta('control',dict(stop_worker=False,maintenance_until_ms=0))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);a=parser.parse_args()
    main(a.runtime,a.output)
