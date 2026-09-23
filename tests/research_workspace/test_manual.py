"""Manual decisions stay versioned, separate from simulated and actual orders."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace.api import same_origin
from yoyo.research_workspace.manual import ManualWorkspace
from yoyo.research_workspace.manual_api import install
from yoyo.research_workspace.store import WorkspaceStore


HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
BASE = "/api/research/manual"


@pytest.fixture
def workspace(tmp_path):
    app = FastAPI()
    store = WorkspaceStore(tmp_path)
    router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    install(router, app, store)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store


def post(client, path, payload, code=201):
    response = client.post(BASE + path, json=payload, headers=HEADERS)
    assert response.status_code == code, response.text
    return response.json()


def playbook(client):
    return post(client, "/playbooks", dict(name="My setup", status="ready", setup="MA launch",
                context="Review context", entry_rule="Closed bar", invalidation_rule="Structure fails",
                exit_rule="Recorded exit", avoid_rule="No chase", management_rule="Monitor protection"))


def ticket(client, **kwargs):
    payload = dict(symbol="BTC-USDT-SWAP", side="long", timeframe="1H", mode="real")
    payload.update(kwargs)
    return post(client, "/tickets", payload)


def event(client, record, action, code=200, **kwargs):
    return post(client, f"/tickets/{record['id']}/events",
                dict(expected_revision=record["revision"], action=action, **kwargs), code)


def past(minutes=1):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def opened(client, **kwargs):
    record = ticket(client, **kwargs)
    return event(client, record, "open", occurred_at=past(2), entry_price=100,
                 initial_stop=90, quantity=2, protection_status="confirmed")


def test_empty_no_risk_defaults_no_execution(workspace):
    client, store = workspace
    data = client.get(BASE).json()
    assert data["tickets"] == []
    assert all(x["status"] == "draft" for x in data["templates"])
    assert "0.25%" not in str(data)
    assert data["summary"]["real"]["net_pnl_usdt"] is None
    assert data["capabilities"]["order_execution"] is False
    assert data["capabilities"]["automatic_position_sync"] is False
    assert store.jobs() == []


def test_origin_validation_and_unknown_fields(workspace):
    client, _ = workspace
    assert client.post(BASE + "/playbooks", json={"name": "Draft"}).status_code == 403
    assert post(client, "/playbooks", {"name": "Draft", "production_eligible": True}, 422)
    assert post(client, "/tickets", dict(symbol="BTCUSD", side="long", timeframe="1H", mode="real"), 422)
    assert post(client, "/sessions", dict(date="2026-09-24", title="Today", risk_budget_usdt=0), 422)
    assert post(client, "/playbooks", dict(name="Draft", status="ready"), 409)


def test_frozen_plan_snapshots_and_revision_conflicts(workspace):
    client, store = workspace
    rules = playbook(client)
    session = post(client, "/sessions", dict(date="2026-09-24", title="Today", availability="Evening only"))
    plan = dict(symbol="BTC-USDT-SWAP", side="long", timeframe="1H", mode="real", status="planned",
                playbook_id=rules["id"], playbook_revision=1, session_id=session["id"], thesis="Observed structure",
                trigger="Close above", invalidation="Below floor", exit_plan="Rule one",
                planned_entry=100, planned_stop=90, risk_budget_usdt=10)
    record = post(client, "/tickets", plan)
    # A later rule revision cannot alter the selected historical rule.
    changes = {k: v for k, v in rules.items() if k not in {"id", "revision", "created_at", "updated_at"}}
    changes.update(expected_revision=1, exit_rule="Changed exit")
    assert client.put(BASE + "/playbooks/" + rules["id"], json=changes, headers=HEADERS).status_code == 200
    assert client.put(BASE + "/playbooks/" + rules["id"], json=changes, headers=HEADERS).status_code == 409
    post(client, "/tickets", plan, 409)
    opened_record = event(client, record, "open", occurred_at=past(2), entry_price=101, initial_stop=90, quantity=2)
    assert opened_record["plan_snapshot"]["planned_entry"] == 100
    assert opened_record["plan_snapshot"]["playbook_snapshot"]["exit_rule"] == "Recorded exit"
    assert opened_record["execution"]["initial_risk_usdt"] == 22
    assert opened_record["execution"]["price_risk_budget_exceeded"] is True
    assert opened_record["execution"]["backfilled"] is True
    assert opened_record["execution"]["unplanned"] is False
    assert client.put(BASE + "/tickets/" + record["id"], json=dict(plan, expected_revision=2), headers=HEADERS).status_code == 409
    event(client, record, "open", code=409, occurred_at=past(), entry_price=100, quantity=2)
    managed = event(client, opened_record, "manage", notes="Moved protection after a partial fill", protection_status="confirmed")
    assert managed["execution"]["initial_stop"] == 90
    assert managed["execution"]["initial_risk_usdt"] == 22
    assert managed["management"][0]["notes"] == "Moved protection after a partial fill"
    final = event(client, managed, "close", occurred_at=past(), net_pnl_usdt=11)
    assert final["outcome"]["net_r"] == .5
    assert final["execution"]["initial_risk_usdt"] == 22
    restored = ManualWorkspace(WorkspaceStore(store.runtime)).get("ticket", record["id"])
    assert restored["plan_snapshot"] == opened_record["plan_snapshot"]
    assert restored["status"] == "closed"
    assert store.jobs() == []


def test_missing_stop_pnl_zero_and_separate_practice(workspace):
    client, _ = workspace
    unknown = ticket(client)
    unknown = event(client, unknown, "open", occurred_at=past(2), entry_price=100, quantity=2)
    assert unknown["execution"]["initial_stop"] is None
    assert unknown["execution"]["initial_risk_usdt"] is None
    assert unknown["execution"]["price_risk_budget_exceeded"] is None
    unknown = event(client, unknown, "close", occurred_at=past())
    assert unknown["outcome"]["net_r"] is None
    real = event(client, opened(client), "close", occurred_at=past(), net_pnl_usdt=0)
    practice = event(client, opened(client, mode="practice"), "close", occurred_at=past(), net_pnl_usdt=100)
    assert real["outcome"]["net_r"] == 0
    assert practice["outcome"]["net_r"] == 5
    summary = client.get(BASE).json()["summary"]
    assert summary["real"]["closed"] == 2
    assert summary["real"]["known_pnl"] == 1 and summary["real"]["unknown_pnl"] == 1
    assert summary["real"]["net_pnl_usdt"] == 0
    assert summary["practice"]["net_pnl_usdt"] == 100
    assert summary["real"]["cohorts"]["prospective"]["net_pnl_usdt"] is None
    assert summary["real"]["cohorts"]["retrospective"]["closed"] == 2
    assert summary["real"]["evidence_status"] == "self_reported_unreconciled"
    reconciled = event(client, unknown, "reconcile", net_pnl_usdt=-5, notes="Exchange statement")
    assert reconciled["outcome"]["net_r"] is None
    history = client.get(BASE + "/tickets/" + unknown["id"]).json()["history"]
    assert history[0]["outcome"]["net_pnl_usdt"] == -5
    assert history[1]["outcome"]["net_pnl_usdt"] is None


def test_state_and_time_boundaries(workspace):
    client, _ = workspace
    record = ticket(client)
    event(client, record, "close", code=409, occurred_at=past())
    event(client, record, "review", code=409, notes="Early", lesson="Early")
    event(client, record, "skip", code=422, occurred_at=past(), notes="Cannot silently discard supplied time")
    event(client, record, "open", code=422, occurred_at=past(), entry_price=100, quantity=2, net_pnl_usdt=999)
    event(client, record, "open", code=422, occurred_at="2026-01-01T00:00:00", entry_price=100, quantity=2)
    event(client, record, "open", code=409,
          occurred_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), entry_price=100, quantity=2)
    event(client, record, "open", code=409, occurred_at=past(), entry_price=100, initial_stop=101, quantity=2)
    active = event(client, record, "open", occurred_at=past(2), entry_price=100, quantity=2)
    event(client, active, "close", code=409, occurred_at=past(3))
    event(client, active, "skip", code=409, notes="Cannot discard a recorded fill")
    skipped = event(client, ticket(client), "skip", notes="Chasing entry")
    reviewed = event(client, skipped, "review", notes="Missed a rule", lesson="Check earlier", adherence="deviated")
    assert reviewed["review"]["adherence"] == "deviated"
    event(client, skipped, "review", code=409, notes="Stale form", lesson="Preserve newer review")
    event(client, reviewed, "open", code=409, occurred_at=past(), entry_price=100, quantity=2)


def test_planned_cannot_omit_rules_or_budget(workspace):
    client, _ = workspace
    for extra in [{"status": "planned"}, {"playbook_revision": 1}, {"planned_entry": 100, "planned_stop": 100}]:
        post(client, "/tickets", dict(symbol="ETH-USDT-SWAP", side="long", timeframe="1H", mode="practice", **extra), 409)
    short = ticket(client, side="short", planned_entry=100, planned_stop=110)
    short = event(client, short, "open", occurred_at=past(2), entry_price=100, initial_stop=110, quantity=3)
    closed = event(client, short, "close", occurred_at=past(), net_pnl_usdt=-15)
    assert closed["outcome"]["net_r"] == -.5


def test_prior_record_and_retrospective_results_remain_separate(workspace):
    client, _ = workspace
    record = ticket(client)
    record = event(client, record, "open", occurred_at=datetime.now(timezone.utc).isoformat(), entry_price=100, quantity=2)
    assert record["execution"]["backfilled"] is False
    event(client, record, "close", occurred_at=datetime.now(timezone.utc).isoformat(), net_pnl_usdt=-3)
    event(client, opened(client), "close", occurred_at=past(), net_pnl_usdt=100)
    summary = client.get(BASE).json()["summary"]["real"]
    assert summary["cohorts"]["prospective"]["net_pnl_usdt"] == -3
    assert summary["cohorts"]["retrospective"]["net_pnl_usdt"] == 100
    assert summary["known_pnl"] == 2
