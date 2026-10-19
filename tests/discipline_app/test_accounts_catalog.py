"""Account identity isolation, secret-safe errors, and dynamic contract coverage."""
import hashlib
import json
import time
from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from yoyo.discipline_app.accounts import Accounts
from yoyo.discipline_app.api import create_app
from yoyo.discipline_app.book import Book
from yoyo.discipline_app.live_book import LiveBook
from yoyo.discipline_app.market import parse_instruments
from yoyo.discipline_app.policy import Policy, Rejected
from test_live_book import Exchange, quote, ticket, live

TOKEN='account-isolation-test-token-at-least-32-characters'

class IdentityExchange(Exchange):
    def __init__(self, uid):
        super().__init__(time.time)
        self.uid=uid
    def get(self,path,**params):
        rows=super().get(path,**params)
        if path.endswith('/config'): rows[0]['uid']=self.uid
        return rows

class Probe:
    def __init__(self, credentials): self.uid=credentials.key
    def inspect(self):
        return dict(account_mode='2',position_mode='long_short_mode',
                    account_reference=hashlib.sha256(self.uid.encode()).hexdigest()[:16])
    def close(self): pass

@pytest.fixture
def manager(tmp_path):
    primary=LiveBook(tmp_path/'live.sqlite3',IdentityExchange('first'))
    quote(primary);assert primary.reconcile()
    return Accounts(primary,tmp_path,lambda c:IdentityExchange(c.key),Probe)

def test_enrollment_never_trades_and_restart_keeps_ledger_identity(manager):
    primary=manager.primary
    with primary.transaction() as (_,s):
        s['daily']['entries']=2;s['cooldown_until']=time.time()+86400;s['loss_streak']=2
    added=manager.add('备用账户','second','secret-test','phrase-test')
    second=manager.resolve(added['id']); assert second.reconcile()
    assert primary.client.writes==second.client.writes==[]
    assert manager.resolve('primary').snapshot()['daily']['entries']==2
    assert second.snapshot()['daily']['entries']==0
    restored=Accounts(primary,manager.runtime,lambda c:IdentityExchange(c.key),Probe)
    assert restored.resolve(added['id']).path==second.path
    assert restored.resolve('primary').snapshot()['loss_streak']==2
    assert restored.resolve('primary').snapshot()['cooldown_until']>time.time()
    public=json.dumps(restored.list())
    for secret in ['secret-test','phrase-test']: assert secret not in public
    assert (manager.runtime/'accounts.json').stat().st_mode & 0o077==0
    assert (second.path.parent/'credentials.json').stat().st_mode & 0o077==0

def test_duplicate_uid_cannot_create_fresh_risk_budget(manager):
    with pytest.raises(Rejected,match='已经添加'):
        manager.add('renamed','first','other-key','phrase')
    assert len(manager.books)==1

def test_account_selection_is_per_request_and_unknown_never_defaults(manager):
    added=manager.add('备用账户','second','secret-test','phrase-test')
    second=manager.resolve(added['id']);second.client.equity='250';second.reconcile()
    app=create_app(manager,TOKEN,market_enabled=False)
    with TestClient(app) as client:
        auth={'Authorization':'Bearer '+TOKEN}
        one=client.get('/v1/state',headers={**auth,'X-Spike-Account':'primary'}).json()
        two=client.get('/v1/state',headers={**auth,'X-Spike-Account':added['id']}).json()
        assert one['equity']=='1000' and two['equity']=='250'
        assert two['account_id']==added['id']
        assert client.get('/v1/state',headers={**auth,'X-Spike-Account':'unknown'}).status_code==409
        assert client.post('/v1/preview',headers=auth,json=ticket()).status_code==409
        response=client.post('/v1/accounts',headers=auth,json={'name':'a','key':'SECRET-DO-NOT-ECHO','secret':None,'passphrase':'private'})
        assert response.status_code==422
        assert 'SECRET-DO-NOT-ECHO' not in response.text and 'private' not in response.text
        assert client.get('/v1/accounts').status_code==401
    assert manager.primary.client.writes==second.client.writes==[]

def instrument(coin='DOGE',**changes):
    return dict(dict(instId=coin+'-USDT-SWAP',state='live',instType='SWAP',ctType='linear',
        settleCcy='USDT',ctValCcy=coin,ctVal='100',ctMult='',lotSz='1',minSz='1',tickSz='.00001',lever='50',groupId='5'),**changes)

def test_catalog_additions_are_dynamic_and_units_are_base_currency():
    rows=[instrument(c) for c in ['DOGE','PEPE','NEWCOIN']]
    rows += [instrument('INVERSE',ctType='inverse'),instrument('USDC',settleCcy='USDC'),
             instrument('EXPIRED',state='suspend'),instrument('BAD',ctValCcy='USD'),instrument('INVALID',tickSz='')]
    result=parse_instruments(rows)
    assert set(result)=={c+'-USDT-SWAP' for c in ['DOGE','PEPE','NEWCOIN']}
    assert result['DOGE-USDT-SWAP']['step']=='100'
    assert result['DOGE-USDT-SWAP']['max_leverage']==50
    assert result['PEPE-USDT-SWAP']['logo_url'].endswith('/pepe.png')

def test_delisted_contract_is_removed_and_cannot_preview(manager):
    book=manager.primary
    before=book.snapshot(); kept={k:v for k,v in before['instruments'].items() if not k.startswith('BTC-')}
    book.update_market(kept,{k:v for k,v in before['quotes'].items() if k in kept})
    assert 'BTC-USDT-SWAP' not in book.snapshot()['instruments']
    with pytest.raises(Rejected): book.preview(ticket())

def test_immediate_owner_policy_keeps_counters_and_cooldown(tmp_path):
    book=Book(tmp_path/'paper.sqlite3');book.configure(replace(Policy(),risk_fraction='.02',margin_mode='isolated',major_exposure_multiple=None,alt_exposure_multiple=None))
    book.configure(Policy())
    with book.transaction() as (_,state):
        state['daily']={'key':'test','entries':2,'start_equity':'1000','locked_reason':'loss'}
        state['loss_streak']=3;state['cooldown_until']=time.time()+86400
    with book.transaction() as (_,state): before={k:state[k] for k in ['daily','loss_streak','cooldown_until']}
    book.activate_policy_now(Policy())
    with book.transaction() as (_,state):
        assert all(state[k]==v for k,v in before.items())
        assert state['policy_proposal'] is None
        assert state['policy']['margin_mode']=='cross'

def test_first_enrollment_works_at_zero_monotonic_time(manager,monkeypatch):
    monkeypatch.setattr('yoyo.discipline_app.accounts.time.monotonic',lambda:0.0)
    result=manager.add('first attempt','new-zero','secret','phrase')
    assert result['id'] in manager.books

def test_second_close_nonce_waits_for_first_market_close(live):
    from test_live_book import enter
    import uuid
    book,exchange,_=live
    _,identity,_=enter(book)
    book.close(uuid.uuid4().hex,identity)
    closing=next(o for o in exchange.orders.values() if not o.get('attachAlgoOrds'))
    closing['state']='live'
    before=len(exchange.writes)
    with pytest.raises(Rejected,match='上一笔平仓'):
        book.close(uuid.uuid4().hex,identity)
    assert len(exchange.writes)==before
