"""Worker GET connections reuse transport, never a receipt or a write retry."""
from io import BytesIO
import json
import pytest
from yoyo.okx_execution.client import TransportError
from yoyo.okx_execution.config import Config
from yoyo.okx_execution.manual_client import ManualClient

class Reads:
    def __init__(self):self.count=0
    def open(self,request,timeout):
        assert request.get_method()=='GET'
        self.count+=1
        return BytesIO(json.dumps(dict(code='0',data=[dict(sequence=self.count)])).encode())
    def close(self):self.closed=True
class Writes:
    def __init__(self):self.count=0
    def open(self,request,timeout):
        assert request.get_method()=='POST'
        self.count+=1
        raise TimeoutError()

def test_get_after_ambiguous_post_is_fresh_and_post_is_never_retried():
    reads,writes=Reads(),Writes()
    client=ManualClient(Config(),dict(api_key='test',secret='test',passphrase='test',demo=False),writes=True,opener=writes,read_opener=reads)
    client.pace=lambda _:None;client.reader.pace=lambda _:None
    assert client.get('/api/v5/account/positions')[0]['sequence']==1
    with pytest.raises(TransportError):client.post('/api/v5/trade/order',{'ordType':'market'})
    assert client.get('/api/v5/account/positions')[0]['sequence']==2
    assert writes.count==1 and reads.count==2
    client.close();assert reads.closed

def test_parallel_phase_cannot_leak_receipts_across_writes():
    reads,writes=Reads(),Writes()
    client=ManualClient(Config(),dict(api_key='test',secret='test',passphrase='test',demo=False),writes=True,opener=writes,read_opener=reads)
    client.pace=lambda _:None;client.reader.pace=lambda _:None
    path='/api/v5/account/positions'
    try:
        with client.read_scope([(path,{})]) as scope:
            assert scope.get(path)[0]['sequence']==1
            assert scope.get(path)[0]['sequence']==1
            with pytest.raises(PermissionError):scope.post('/api/v5/trade/order',{})
        with pytest.raises(TransportError):client.post('/api/v5/trade/order',{})
        with client.read_scope([(path,{})]) as scope:
            assert scope.get(path)[0]['sequence']==2
        assert writes.count==1
    finally:client.close()
