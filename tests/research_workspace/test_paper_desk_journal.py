"""Position journal persistence, lifecycle isolation and concurrent edit contracts."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace import paper_desk as book, paper_desk_api as api
from yoyo.research_workspace.api import same_origin
from yoyo.research_workspace.paper_desk_journal import PositionJournal, FIELDS

CONTENT = dict(entry_reason='突破后回踩\n量能确认', analysis='15 分钟结构', plan='失效条件：跌回区间', review='等待复盘')
HEADERS = {'origin': 'http://testserver', 'sec-fetch-site': 'same-origin'}


@pytest.fixture
def env(tmp_path):
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account('Journal QA', '10000', 'account-request-0001')['id']
    q = desk.save_quote(dict(symbol='BTC-USDT-SWAP', bid='100', ask='100',
                            source_ms=book.clock_ms(), received_ms=book.clock_ms()))
    opened = desk.open(account, 'long', '2', q['id'], 'Legacy rationale', 'open-request-0001', {}, 10)
    return SimpleNamespace(desk=desk, journal=PositionJournal(desk), account=account, quote=q, position=opened['position']['id'])


def test_read_is_lazy_and_preserves_existing_note_without_market(env):
    before = env.desk.export(env.account)
    result = env.journal.get(env.position)
    assert result['journal'] == dict(position_id=env.position, entry_reason='Legacy rationale',
                                     analysis='', plan='', review='', version=0, created_ms=None, updated_ms=None)
    assert result['account_name'] == 'Journal QA'
    assert env.desk.export(env.account) == before
    with env.desk.connect() as db:
        assert db.execute('SELECT count(*) FROM position_journals').fetchone()[0] == 0


def test_save_survives_restart_and_does_not_change_financial_rows(env):
    before = env.desk.export(env.account)
    saved = env.journal.save(env.position, 0, CONTENT)
    restarted = book.PaperDesk(env.desk.path.parent)
    assert PositionJournal(restarted).get(env.position) == saved
    assert saved['journal']['version'] == 1
    assert saved['journal']['updated_ms'] >= saved['journal']['created_ms'] > 0
    exported = restarted.export(env.account)
    assert exported['journals'] == [saved['journal']]
    exported['journals'] = []
    assert exported == before
    assert exported['positions'][0]['note'] == 'Legacy rationale'
    assert 'journals' not in restarted.snapshot({}, env.account, True)


def test_log_follows_adds_partial_close_and_closed_lifecycle_but_not_reentry(env):
    saved = env.journal.save(env.position, 0, CONTENT)['journal']
    added = env.desk.open(env.account, 'long', '1', env.quote['id'], '', 'open-request-0002', {env.quote['symbol']: env.quote}, 10)
    assert added['position']['id'] == env.position
    env.desk.close(env.position, '1', env.quote['id'], 'close-request-0001')
    assert env.journal.get(env.position)['journal'] == saved
    env.desk.close(env.position, '2', env.quote['id'], 'close-request-0002')
    closed = env.journal.get(env.position)
    assert closed['position']['closed_ms'] is not None
    assert closed['journal'] == saved
    updated = env.journal.save(env.position, 1, dict(CONTENT, review='平仓后复盘'))
    assert updated['journal']['review'] == '平仓后复盘'
    reopened = env.desk.open(env.account, 'long', '1', env.quote['id'], '', 'open-request-0003', {}, 10)
    assert reopened['position']['id'] != env.position
    assert env.journal.get(reopened['position']['id'])['journal']['version'] == 0
    other = env.desk.create_account('Other', '1000', 'account-request-0002')['id']
    assert env.desk.export(other)['journals'] == []


def test_concurrent_edits_reject_lost_update_and_identical_retry_is_safe(env):
    def save(text):
        try:
            return env.journal.save(env.position, 0, dict(CONTENT, analysis=text))
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['first tab', 'second tab']))
    winners = [r for r in results if r]
    assert len(winners) == 1
    content = {key: winners[0]['journal'][key] for key in FIELDS}
    assert env.journal.save(env.position, 0, content) == winners[0]
    with pytest.raises(ValueError, match='其他页面更新'):
        env.journal.save(env.position, 0, dict(content, review='stale'))
    assert env.journal.get(env.position) == winners[0]
    assert env.journal.save(env.position, 1, dict(content, review='merged'))['journal']['version'] == 2


@pytest.mark.parametrize('version,content', [(-1, CONTENT), (True, CONTENT), (0, {}),
    (0, dict(CONTENT, analysis='x'*10001)), (0, dict(CONTENT, review=None))])
def test_invalid_updates_leave_no_rows(env, version, content):
    with pytest.raises(ValueError):
        env.journal.save(env.position, version, content)
    assert env.desk.export(env.account)['journals'] == []


def test_missing_position_cannot_create_orphan(env):
    with pytest.raises(KeyError):
        env.journal.get('missing')
    with pytest.raises(KeyError):
        env.journal.save('missing', 0, CONTENT)
    assert env.desk.export(env.account)['journals'] == []


def test_journal_api_without_market_origin_validation_conflict_and_export(env):
    app = FastAPI()
    router = APIRouter(prefix='/api/research', dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=env.desk.path.parent))
    class NoMarket:
        def get(self, *args, **kwargs):
            raise AssertionError('Journals must never fetch market data')
    app.state.paper_desk_quotes = NoMarket()
    app.include_router(router)
    path = '/api/research/paper-desk/positions/'+env.position+'/journal'
    with TestClient(app) as client:
        assert client.get(path).json()['journal']['entry_reason'] == 'Legacy rationale'
        payload = dict(version=0, **CONTENT)
        assert client.post(path, json=payload).status_code == 403
        assert client.post(path, json=dict(payload, review='x'*10001), headers=HEADERS).status_code == 422
        assert client.post(path, json=dict(payload, version=True), headers=HEADERS).status_code == 422
        assert client.post(path, json=dict(payload, unexpected=True), headers=HEADERS).status_code == 422
        response = client.post(path, json=payload, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert client.post(path, json=payload, headers=HEADERS).json() == response.json()
        conflict = client.post(path, json=dict(payload, review='different'), headers=HEADERS)
        assert conflict.status_code == 409
        assert client.get(path).json() == response.json()
        assert client.get(path.replace(env.position, 'missing')).status_code == 404
        assert client.post(path.replace(env.position, 'missing'), json=payload, headers=HEADERS).status_code == 404
        exported = client.get('/api/research/paper-desk/accounts/'+env.account+'/export').json()
        assert exported['journals'] == [response.json()['journal']]
