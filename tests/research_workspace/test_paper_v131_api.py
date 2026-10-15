"""The 15m allow-list and public category snapshot are frozen per run."""
from test_paper_api import client, HEADERS
from yoyo.research_workspace import paper_api, paper_v131, strategies


def payload():
    return dict(strategy_id="spike-v131-retest", symbol_scope="okx_all_usdt",
                timeframes=["15m"], request_id="v131-registration-123456")


def test_v131_creation_retry_freezes_categories_and_timeframe(client, monkeypatch):
    c, store, _ = client
    monkeypatch.setattr(paper_api, "catalog", strategies.catalog)
    calls = []
    def snapshot():
        calls.append(1)
        return {"observed_ms":123, "instruments":{"BTC-USDT-SWAP":{"asset_class":"crypto"}}}
    monkeypatch.setattr(paper_v131, "instrument_snapshot", snapshot)
    result = c.post('/api/research/paper/runs', json=payload(), headers=HEADERS)
    assert result.status_code == 201, result.text
    value = result.json()
    assert value['spec']['instrument_snapshot']['observed_ms'] == 123
    assert value['spec']['cost_bp'] == 20 and value['spec']['funding_included'] is False
    retry = c.post('/api/research/paper/runs', json=payload(), headers=HEADERS)
    assert retry.json()['id'] == value['id'] and len(calls) == 1
    bad = dict(payload(), timeframes=['1H'])
    assert c.post('/api/research/paper/runs', json=bad, headers=HEADERS).status_code == 400
    assert len(store.runs()) == 1


def test_category_source_failure_does_not_create_unclassified_run(client, monkeypatch):
    c, store, _ = client
    monkeypatch.setattr(paper_api, "catalog", strategies.catalog)
    def fail(): raise ValueError('OKX asset metadata unavailable')
    monkeypatch.setattr(paper_v131, "instrument_snapshot", fail)
    assert c.post('/api/research/paper/runs', json=payload(), headers=HEADERS).status_code == 409
    assert not store.runs()
