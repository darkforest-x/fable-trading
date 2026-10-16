"""Activation requires actual continuous coverage, not cached job summaries."""
from contextlib import closing
import json
import pytest
from yoyo.yolo_workbench.store import Book,encode
from yoyo.yolo_workbench.patrol import Patrol,enqueue_due,GRACE_MS
from yoyo.yolo_workbench.finalize import verify
from test_patrol import plan,MarketStub,Detector,finish,END


def trial(tmp_path):
    book=Book(tmp_path)
    plans=[plan(book,tf,tf,interval) for tf,interval in [('5m',15),('15m',15),('1H',240),('1Dutc',1440)]]
    enqueue_due(book,END+GRACE_MS)
    finish(Patrol(book,MarketStub(),Detector()))
    jobs=book.jobs()
    records=[dict(stage='warm-short-prefetch',elapsed_s=400,api_p95_s=.3,memory_pauses=0,
                  jobs=[dict(id=j['id']) for j in jobs if j['spec']['timeframe'] in ('5m','15m')]),
             dict(stage='long-bootstrap',jobs=[dict(id=j['id']) for j in jobs if j['spec']['timeframe'] not in ('5m','15m')])]
    return book,plans,records


def test_full_trial_passes_only_with_saved_contiguous_evidence(tmp_path):
    book,plans,records=trial(tmp_path)
    receipt=verify(book,plans,records)
    assert receipt['accepted'] and receipt['verified_coin_periods']==12
    assert all(c['total']==c['covered']==3 for c in receipt['coverage'].values())
    with closing(book.connect()) as db,db:
        db.execute("UPDATE patrol_cursors SET through_ms=through_ms-300000 WHERE schedule_id='5m' AND symbol='A'")
    with pytest.raises(ValueError,match='missing endpoint coverage'):verify(book,plans,records)


def test_rejects_capacity_and_unresolved_failure(tmp_path):
    book,plans,records=trial(tmp_path);records[0]['elapsed_s']=601
    with pytest.raises(ValueError,match='capacity'):verify(book,plans,records)
    records[0]['elapsed_s']=400
    job=book.get(records[0]['jobs'][0]['id'])
    book.update(job['id'],status='partial',progress=dict(job['progress'],failed=1))
    with pytest.raises(ValueError,match='unresolved'):verify(book,plans,records)


def test_rejects_saved_endpoint_gap_despite_current_cursor(tmp_path):
    book,plans,records=trial(tmp_path)
    with closing(book.connect()) as db,db:
        row=db.execute("SELECT r.job_id,r.payload FROM results r JOIN jobs j ON j.id=r.job_id WHERE j.schedule_id='5m' AND r.symbol='A'").fetchone()
        value=json.loads(row['payload']);value['coverage_after_ms']+=300000;value['endpoints_scored']-=1
        db.execute("UPDATE results SET payload=? WHERE job_id=? AND symbol='A'",(encode(value),row['job_id']))
    with pytest.raises(ValueError,match='eligible baseline'):verify(book,plans,records)


def test_explicit_history_insufficiency_is_not_counted_as_coverage(tmp_path):
    book,plans,records=trial(tmp_path)
    job=next(j for j in book.jobs() if j['spec']['timeframe']=='1Dutc')
    book.update(job['id'],spec=dict(job['spec'],listed_ms={'A':job['spec']['end_ms']-10*86400000}))
    with closing(book.connect()) as db,db:
        db.execute("DELETE FROM patrol_cursors WHERE schedule_id='1Dutc' AND symbol='A'")
        db.execute("UPDATE results SET payload=? WHERE job_id=? AND symbol='A'",(encode(dict(symbol='A',availability='insufficient_history')),job['id']))
    receipt=verify(book,plans,records)
    assert receipt['coverage']['1Dutc']['covered']==2 and receipt['coverage']['1Dutc']['unavailable']==1


def test_repaired_failure_is_accepted_without_rewriting_failed_evidence(tmp_path):
    book,plans,records=trial(tmp_path)
    source=next(j for j in book.jobs() if j['spec']['timeframe']=='1Dutc')
    failed=book.submit(source['spec'],'failed-trial',source['schedule_id'])
    book.result(failed['id'],'A',dict(symbol='A',error='network',retry_pending=True))
    book.update(failed['id'],status='partial',progress=dict(completed=3,total=3,failed=1))
    records.insert(0,dict(stage='failed-trial',jobs=[dict(id=failed['id'])]))
    receipt=verify(book,plans,records)
    assert receipt['repaired_failures']==1 and book.get(failed['id'])['progress']['failed']==1
