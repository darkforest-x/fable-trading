"""Paper campaign API requires explicit sizing and never uses exchange writes."""
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace.paper_desk_api import install
from yoyo.research_workspace.api import same_origin


@pytest.fixture
def env(tmp_path):
    app = FastAPI()
    api = APIRouter(prefix='/api/research', dependencies=[Depends(same_origin)])
    install(api, app, SimpleNamespace(runtime=tmp_path))
    app.include_router(api)
    desk = app.state.paper_desk
    account = desk.create_account('Synthetic batch API', '1000', 'bulk-api-account-001')['id']
    client = TestClient(app, headers={'origin':'http://testserver', 'sec-fetch-site':'same-origin'})
    return SimpleNamespace(app=app, c=client, desk=desk, account=account,
                           url=f'/api/research/paper-desk/accounts/{account}/monday-bulk')


def settings():
    return dict(notional_usdt='10', leverage=1, direction='both', mode='once',
                take_profit='none', stop_loss_percent=None)


def test_default_disabled_and_missing_accounts(env):
    response = env.c.get(env.url)
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] is False
    assert response.json()['version'] == 0
    assert env.desk.export(env.account)['orders'] == []
    assert env.c.get(env.url.replace(env.account, 'missing-account')).status_code == 404


def test_sizing_is_required_and_extra_execution_fields_rejected(env):
    assert env.c.post(env.url+'/preview', json={}).status_code == 422
    for extra in [dict(live=True), dict(api_key='not-a-key'), dict(notional_usdt='')]:
        assert env.c.post(env.url+'/preview', json={**settings(), **extra}).status_code == 422
    assert env.c.post(env.url+'/configure', json=dict(
        settings=settings(), enabled=True, version=0, request_id='too-short',
    )).status_code == 422
    assert env.desk.export(env.account)['orders'] == []


def test_campaign_mutation_inherits_same_origin_guard(env):
    response = env.c.post(env.url+'/configure', headers={'origin':'https://other.example'},
                          json=dict(settings=settings(), enabled=True, version=0,
                                    request_id='bulk-cross-origin-001'))
    assert response.status_code == 403
    assert env.c.get(env.url).json()['enabled'] is False


def test_missing_monday_service_is_explicit_and_does_not_create_orders(env):
    response = env.c.post(env.url+'/preview', json=settings())
    assert response.status_code == 409
    assert '周一' in response.text
    assert env.desk.export(env.account)['orders'] == []


def test_configuration_replay_and_stop_without_sizing(env):
    payload = dict(settings=settings(), enabled=True, version=0,
                   request_id='bulk-api-enable-001')
    first = env.c.post(env.url+'/configure', json=payload)
    assert first.status_code == 200, first.text
    assert first.json()['enabled'] is True
    assert env.c.post(env.url+'/configure', json=payload).json() == first.json()
    conflict = env.c.post(env.url+'/configure', json={**payload, 'request_id':'bulk-api-enable-002'})
    assert conflict.status_code == 409
    stopped = env.c.post(env.url+'/configure', json=dict(
        enabled=False, version=first.json()['version'], request_id='bulk-api-disable-001',
    ))
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()['enabled'] is False
    assert env.desk.export(env.account)['orders'] == []
