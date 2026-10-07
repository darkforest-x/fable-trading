"""The old copier API answered every origin; the migrated one must not."""
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yoyo.copier.api.app import allowed_origin, create_app
from yoyo.copier.store.sqlite import Database


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        db.set_setting("dry_run", "true")
        with TestClient(create_app(db), base_url="http://127.0.0.1:8080") as c:
            c.db = db
            yield c


def test_origin_rules():
    own = {"http://127.0.0.1:8080"}
    assert allowed_origin(None, own)
    assert allowed_origin("chrome-extension://abcdef", own)
    assert allowed_origin("http://127.0.0.1:8080", own)
    assert not allowed_origin("https://evil.example", own)
    assert not allowed_origin("http://127.0.0.1:8766", own)


def test_foreign_page_cannot_switch_to_live(client):
    response = client.post("/api/control/dry-run", json={"enabled": False},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert client.db.get_setting("dry_run") == "true"


def test_foreign_page_cannot_inject_a_signal(client):
    response = client.post("/api/messages/ingest", json={"content": "BTC long", "external_id": "x1"},
                           headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert not client.db.message_exists("x1")


def test_no_cors_grant_for_foreign_reads(client):
    response = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_foreign_host_header_is_rejected(client):
    assert client.get("/api/health", headers={"Host": "attacker.example"}).status_code == 400


def test_same_service_origin_may_write(client):
    response = client.post("/api/control/kill-switch", json={"enabled": True},
                           headers={"Origin": "http://127.0.0.1:8080"})
    assert response.status_code == 200
    assert client.db.get_setting("kill_switch") == "true"


def test_root_points_to_workbench_and_capture_page_is_served(client):
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 302 and root.headers["location"].endswith("#copier")
    page = client.get("/capture/orders-card")
    assert page.status_code == 200 and "当前持仓与订单" in page.text


def test_orders_card_endpoint_reads_local_state(client):
    body = client.get("/api/dashboard/orders-card").json()
    assert body["dry_run"] is True
    assert {"channels", "positions", "pending", "history", "position_count"} <= set(body)
