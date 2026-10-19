"""Crash/retry and concurrent-order evidence against a fake exchange only."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from yoyo.discipline_app.execution_journal import ExecutionJournal
from yoyo.discipline_app.okx_execution import ExchangeUncertain
from yoyo.discipline_app.policy import Rejected


def test_timeout_and_restart_never_send_again(tmp_path):
    path=tmp_path/'live.sqlite3'
    journal=ExecutionJournal(path)
    identity='test-identity-001'
    journal.prepare(identity,'/api/v5/trade/order',{'clOrdId':'fixed'})
    calls=[]
    class Exchange:
        def post(self,path,body):
            calls.append(body)
            raise ExchangeUncertain('timeout')
    with pytest.raises(ExchangeUncertain):
        journal.dispatch(identity,Exchange())
    restarted=ExecutionJournal(path)
    with pytest.raises(ExchangeUncertain):
        restarted.dispatch(identity,Exchange())
    assert len(calls)==1
    assert restarted.unresolved()[0]['id']==identity
    restarted.resolve(identity,'acknowledged',[{'ordId':'actual-exchange-receipt'}])
    assert restarted.dispatch(identity,Exchange())==[{'ordId':'actual-exchange-receipt'}]
    assert len(calls)==1


def test_concurrent_dispatch_claims_one_attempt(tmp_path):
    journal=ExecutionJournal(tmp_path/'live.sqlite3')
    identity='test-identity-002'
    journal.prepare(identity,'/api/v5/trade/order',{'clOrdId':'fixed'})
    barrier=threading.Barrier(6)
    calls=[]
    class Exchange:
        def post(self,path,body):
            calls.append(body)
            return [{'ordId':'one'}]
    def submit(_):
        barrier.wait()
        try:
            return journal.dispatch(identity,Exchange())
        except ExchangeUncertain:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(submit,range(6)))
    assert len(calls)==1
    assert any(result==[{'ordId':'one'}] for result in results)
    with pytest.raises(Rejected,match='不同参数'):
        journal.prepare(identity,'/api/v5/trade/order',{'clOrdId':'changed'})
