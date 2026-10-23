"""Inspection close routes authorize only one owned lot, using synthetic accounts."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_manual_commands import active
from test_manual_live import h
from yoyo.okx_execution import manual_api
from yoyo.okx_execution.manual_commands import consume

HEADERS = {"Origin": "https://workspace.example", "X-Fable-Manual": "owner-confirmation"}
BASE = "/api/manual-live/close"


@pytest.fixture
def client(active, monkeypatch):
    monkeypatch.setattr(manual_api, "worker_lock", lambda _: True)
    app = FastAPI()
    manual_api.install(app, active["book"], service=active["service"])
    with TestClient(app, base_url="https://workspace.example") as c:
        yield c


def draft(client, active):
    return client.post(BASE+"/preview", json={"lot_id": active["store"].trades()[0]["id"]}, headers=HEADERS)


def test_close_preview_and_confirm_queue_only_with_explicit_owner_ack(client, active):
    before = deepcopy(active["exchange"].posts)
    response = draft(client, active)
    assert response.status_code == 200
    value = response.json()
    assert value["account_scope"] == "yolo_manual" and value["action"] == "close"
    assert "account_uid" not in value and response.headers["cache-control"] == "no-store"
    url = BASE+"/"+value["id"]+"/confirm"
    assert client.post(url, json={"acknowledgement": "CONFIRM-LIVE-ORDER"}, headers=HEADERS).status_code == 422
    assert client.post(url, json={"acknowledgement": "CONFIRM-LIVE-CLOSE"}).status_code == 403
    assert client.post(url, json={"acknowledgement": "CONFIRM-LIVE-CLOSE"}, headers={**HEADERS, "Origin": "https://other.example"}).status_code == 403
    payload = {"acknowledgement": "CONFIRM-LIVE-CLOSE"}
    assert client.post(url, json=payload, headers=HEADERS).json()["state"] == "confirmed"
    active["now"][0] += 60_001
    assert client.post(url, json=payload, headers=HEADERS).json()["state"] == "confirmed"
    assert client.get(BASE+"/"+value["id"]).json()["state"] == "confirmed"
    assert active["exchange"].posts == before


def test_stale_worker_and_injected_size_fail_closed(client, active):
    lot = active["store"].trades()[0]["id"]
    assert client.post(BASE+"/preview", json={"lot_id": lot, "size": "100"}, headers=HEADERS).status_code == 422
    active["now"][0] += 60_001
    assert draft(client, active).status_code == 409


def test_confirmation_expires_and_worker_rechecks_changed_position(client, active):
    value = draft(client, active).json()
    active["now"][0] += 60_001
    active["store"].set_meta("heartbeat_ms", active["now"][0])
    payload = {"acknowledgement": "CONFIRM-LIVE-CLOSE"}
    assert client.post(BASE+"/"+value["id"]+"/confirm", json=payload, headers=HEADERS).status_code == 409
    value = draft(client, active).json()
    assert client.post(BASE+"/"+value["id"]+"/confirm", json=payload, headers=HEADERS).status_code == 200
    before = deepcopy(active["exchange"].posts)
    active["exchange"].positions[0]["pos"] = "1"
    consume(active["engine"], active["engine"].commands)
    assert client.get(BASE+"/"+value["id"]).json()["state"] == "rejected"
    assert active["exchange"].posts == before


def test_worker_closes_only_confirmed_lot_and_reports_done_after_fill(client, active):
    exchange, engine = active["exchange"], active["engine"]
    value = draft(client, active).json()
    calls = []

    def post(path, body):
        calls.append((path, deepcopy(body)))
        if path.endswith("/cancel-algos"):
            exchange.algo["state"] = "canceled"
            return [{"sCode": "0", "algoId": "oco1"}]
        assert path.endswith("/order") and "attachAlgoOrds" not in body
        exchange.positions = []
        exchange.order = dict(body, state="filled", accFillSz=body["sz"], avgPx="101", fillTime=str(engine.clock()))
        return [{"sCode": "0", "ordId": "close1"}]

    exchange.post = post
    client.post(BASE+"/"+value["id"]+"/confirm", json={"acknowledgement": "CONFIRM-LIVE-CLOSE"}, headers=HEADERS)
    consume(engine, engine.commands)
    for _ in range(4):
        engine.manage(active["store"].trades()[0])
        consume(engine, engine.commands)
    assert client.get(BASE+"/"+value["id"]).json()["state"] == "done"
    assert len(calls) == 2
    assert calls[0][1] == [{"instId": value["symbol"], "algoId": value["algo_id"]}]
    assert calls[1][1]["sz"] == value["size"]
    assert calls[1][1]["side"] == "sell" and calls[1][1]["posSide"] == "long"
    assert active["store"].trades()[0]["exit_reason"] == "owner_confirmed_close"
