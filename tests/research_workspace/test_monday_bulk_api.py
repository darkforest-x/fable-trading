"""The retired Monday campaign cannot be started or fill old orders."""
import json
import sqlite3
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace import paper_desk as book
from yoyo.research_workspace import paper_desk_api as api
from yoyo.research_workspace.monday_bulk_retirement import retire_monday_bulk
from yoyo.research_workspace.paper_desk_limits import LimitOrders
from yoyo.research_workspace.api import same_origin


HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


def _seed_campaign(desk, account):
    at = book.clock_ms()
    with desk.connect() as db:
        db.execute(
            "INSERT INTO monday_bulk_configs(account_id,settings_json,enabled,version,generation,armed_week,updated_ms) "
            "VALUES(?,?,?,?,?,?,?)",
            (account, json.dumps({"notional_usdt": "10", "leverage": 1}), 1, 7, 4, "2026-10-19", at),
        )
        db.execute(
            "INSERT INTO limit_orders(id,account_id,action,symbol,side,quantity,limit_price,leverage,note,position_id,"
            "status,created_ms,updated_ms,quote_id,trigger_quote_id,fill_id,reason,take_profit,stop_loss,fee_rate,"
            "bulk_kind,bulk_week,bulk_generation,bulk_expires_ms,bulk_level) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("b" * 32, account, "open", "ETH-USDT-SWAP", "short", "1", "110", 1, "bulk", None,
             "pending", at, at, "c" * 32, None, None, "awaiting touch", None, None, "0.0002",
             "monday_1618", "2026-10-19", 4, at + 60_000, "110"),
        )


def _install(tmp_path):
    app = FastAPI()
    router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    app.include_router(router)
    return app


def test_api_initialization_retires_bulk_and_preserves_manual_activity(tmp_path):
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account("Manual practice", "1000", "manual-account-request-0001")["id"]
    quote = desk.save_quote(dict(symbol="BTC-USDT-SWAP", bid="100", ask="101",
                                 source_ms=book.clock_ms(), received_ms=book.clock_ms()))
    position = desk.open(account, "long", "1", quote["id"], "manual position",
                         "manual-open-request-0001", {})["position"]
    manual_order = LimitOrders(desk).place(
        "open", account, "1", "90", quote["id"], "manual-limit-request-0001",
        {quote["symbol"]: quote}, "long", 1,
    )["order"]
    _seed_campaign(desk, account)

    app = _install(tmp_path)
    installed_desk = app.state.paper_desk
    with installed_desk.connect() as db:
        config = db.execute("SELECT enabled,version,generation FROM monday_bulk_configs WHERE account_id=?",
                            (account,)).fetchone()
        bulk_order = db.execute("SELECT status,reason FROM limit_orders WHERE id=?", ("b" * 32,)).fetchone()
        untouched_manual = db.execute("SELECT status,bulk_kind FROM limit_orders WHERE id=?",
                                      (manual_order["id"],)).fetchone()
        kept_position = db.execute("SELECT remaining FROM positions WHERE id=?", (position["id"],)).fetchone()
        fills = db.execute("SELECT count(*) FROM fills WHERE account_id=?", (account,)).fetchone()[0]

    assert tuple(config) == (0, 8, 5)
    assert bulk_order["status"] == "cancelled"
    assert "停用" in bulk_order["reason"]
    assert tuple(untouched_manual) == ("pending", None)
    assert kept_position["remaining"] == "1"
    assert fills == 1
    assert not hasattr(app.state, "paper_monday_bulk")

    route_paths = {route.path for route in app.routes}
    assert not any("monday-bulk" in path for path in route_paths)
    assert "/api/research/paper-desk/accounts/{account_id}/open" in route_paths
    assert "/api/research/paper-desk/accounts/{account_id}/limit-open" in route_paths

    with TestClient(app, headers=HEADERS) as client:
        response = client.get(f"/api/research/paper-desk/accounts/{account}/monday-bulk")
        assert response.status_code == 404

    second = retire_monday_bulk(installed_desk)
    assert second == {"configs_retired": 0, "orders_cancelled": 0}
    with installed_desk.connect() as db:
        current = db.execute("SELECT version,generation FROM monday_bulk_configs WHERE account_id=?",
                             (account,)).fetchone()
    assert tuple(current) == (8, 5)


def test_retirement_rolls_back_config_when_order_cancellation_fails(tmp_path):
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account("Atomic retirement", "1000", "atomic-account-request-0001")["id"]
    _seed_campaign(desk, account)
    with desk.connect() as db:
        db.execute(
            "CREATE TRIGGER reject_bulk_cancel BEFORE UPDATE OF status ON limit_orders "
            "WHEN OLD.bulk_kind='monday_1618' BEGIN SELECT RAISE(ABORT,'forced failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced failure"):
        retire_monday_bulk(desk)

    with desk.connect() as db:
        config = db.execute("SELECT enabled,version,generation FROM monday_bulk_configs WHERE account_id=?",
                            (account,)).fetchone()
        order = db.execute("SELECT status FROM limit_orders WHERE id=?", ("b" * 32,)).fetchone()
    assert tuple(config) == (1, 7, 4)
    assert order["status"] == "pending"
