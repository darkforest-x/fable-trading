import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yoyo.copier.api.app import create_app
from yoyo.copier.discord_channels import channel_names, clean_title, register_web_channel, trade_blocked_channels
from yoyo.copier.monitor_switches import channel_enabled
from yoyo.copier.store.sqlite import Database

GUILD, ROUTED, KNOWN_OFF, NEW = "1004707886657699901", "988830102957736027", "1226097931904352378", "1400000000000000001"


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as td:
        database = Database(Path(td) / "t.db")
        database.set_setting("monitor_switches", json.dumps({"channels": {ROUTED: True, KNOWN_OFF: False}, "features": {}}))
        database.set_setting("exchange_channel_map", json.dumps({ROUTED: "okx"}))
        database.set_setting("blocked_trade_channels", "[]")
        yield database


def test_new_server_channel_is_monitored_but_not_traded(db):
    assert register_web_channel(db, NEW, GUILD, "Discord | #mr-profit | kolunite会员")
    assert channel_enabled(db, NEW)
    assert NEW in trade_blocked_channels(db)
    assert channel_names(db)[NEW] == "#mr-profit | kolunite会员"
    assert not register_web_channel(db, NEW, GUILD, "Discord | #mr-profit | kolunite会员")


def test_owner_switched_off_channel_stays_off(db):
    assert not register_web_channel(db, KNOWN_OFF, GUILD, "Tareeq")
    assert not channel_enabled(db, KNOWN_OFF)


def test_routed_channel_keeps_trading_and_other_channels_untouched(db):
    assert not register_web_channel(db, ROUTED, GUILD, "ChartPrime")
    assert ROUTED not in trade_blocked_channels(db)
    assert channel_enabled(db, ROUTED)


@pytest.mark.parametrize("channel, guild", [(NEW, ""), (NEW, "@me"), ("discord-web", GUILD)])
def test_direct_messages_and_unknown_ids_are_never_registered(db, channel, guild):
    assert not register_web_channel(db, channel, guild, "x")
    assert not channel_enabled(db, NEW)


def test_without_an_allow_list_nothing_is_switched_off(db):
    db.set_setting("monitor_switches", json.dumps({"features": {}}))
    assert not register_web_channel(db, NEW, GUILD, "x")
    assert channel_enabled(db, ROUTED) and channel_enabled(db, NEW)
    assert NEW in trade_blocked_channels(db)


def test_title_cleanup():
    assert clean_title("(3) Discord | #eth-advanced | kolunite会员") == "#eth-advanced | kolunite会员"
    assert clean_title("#daniel | kolunite会员 - Discord") == "#daniel | kolunite会员"


def test_ingest_registers_then_records_without_trading(db, monkeypatch):
    calls = []

    async def fake_process(self, message_id, text, author=None, allow_execute=True):
        calls.append((message_id, allow_execute))

    monkeypatch.setattr("yoyo.copier.router.action_router.ActionRouter.process_message", fake_process)
    with TestClient(create_app(db), base_url="http://127.0.0.1:8080") as client:
        response = client.post("/api/messages/ingest", json={
            "content": "BTC long 60000", "channel_id": NEW, "guild_id": GUILD, "channel_name": "Discord | #daniel | kolunite会员",
            "external_id": "discord_web_new_1", "source": "discord-web"})
    body = response.json()
    assert body["channel_registered"] is True and body["message_id"]
    assert calls and NEW in trade_blocked_channels(db)


def test_old_extension_without_guild_id_does_not_register(db):
    with TestClient(create_app(db), base_url="http://127.0.0.1:8080") as client:
        body = client.post("/api/messages/ingest", json={
            "content": "hi", "channel_id": NEW, "external_id": "discord_web_new_2", "source": "discord-web"}).json()
    assert body["channel_registered"] is False and body["message_id"] is None


def test_watchlist_replaces_the_monitored_set_and_routes_to_paper(db):
    from yoyo.copier.discord_channels import apply_watchlist
    from yoyo.copier.exchange import get_channel_exchange_map

    result = apply_watchlist(db, GUILD, ["eth-advanced", "Tareeq", "比特币飞扬"], {KNOWN_OFF: "Tareeq"})
    assert channel_enabled(db, KNOWN_OFF) and not channel_enabled(db, ROUTED)
    assert get_channel_exchange_map(db)[KNOWN_OFF] == "paper"
    assert get_channel_exchange_map(db)[ROUTED] == "okx"  # kept for restore, but switched off
    assert json.loads(db.get_setting("exchange_channel_map_before_watchlist")) == {ROUTED: "okx"}
    assert ROUTED in result["switched_off"]


def test_watchlist_registers_listed_tabs_and_ignores_the_rest(db):
    from yoyo.copier.discord_channels import apply_watchlist
    from yoyo.copier.exchange import get_channel_exchange_map

    apply_watchlist(db, GUILD, ["eth-advanced", "michele行情分析", "Eliz"], {})
    assert register_web_channel(db, NEW, GUILD, "(2) Discord | #eth-advanced | kolunite会员")
    assert channel_enabled(db, NEW) and get_channel_exchange_map(db)[NEW] == "paper"
    assert NEW not in trade_blocked_channels(db)
    assert register_web_channel(db, "1400000000000000002", GUILD, "Discord | #🏹｜michele行情分析 | kolunite会员")
    assert register_web_channel(db, "1400000000000000003", GUILD, "Discord | 🎯Eliz | kolunite会员")
    chat = "1400000000000000004"
    assert not register_web_channel(db, chat, GUILD, "Discord | #会员聊天 | kolunite会员")
    assert not channel_enabled(db, chat)
    assert channel_names(db)[chat] == "#会员聊天 | kolunite会员"  # remembered, not monitored
    assert not register_web_channel(db, "1400000000000000005", "1999999999999999999", "Discord | #eth-advanced | other")


def test_watchlist_rejects_known_ids_outside_the_list(db):
    from yoyo.copier.discord_channels import apply_watchlist

    with pytest.raises(ValueError):
        apply_watchlist(db, GUILD, ["eth-advanced"], {ROUTED: "ChartPrime"})


def _snowflake(ms_ago: float) -> str:
    import time
    return str((int(time.time() * 1000 - ms_ago - 1420070400000)) << 22)


@pytest.mark.parametrize("minutes_old, executes", [(1, True), (180, False)])
def test_late_replayed_messages_are_recorded_but_never_executed(db, monkeypatch, minutes_old, executes):
    calls = []

    async def fake_process(self, message_id, text, author=None, allow_execute=True):
        calls.append(allow_execute)

    monkeypatch.setattr("yoyo.copier.router.action_router.ActionRouter.process_message", fake_process)
    with TestClient(create_app(db), base_url="http://127.0.0.1:8080") as client:
        body = client.post("/api/messages/ingest", json={
            "content": "BTC long", "channel_id": ROUTED, "guild_id": GUILD,
            "external_id": f"discord_web_{ROUTED}_{_snowflake(minutes_old * 60000)}", "source": "discord-web"}).json()
    assert body["message_id"] and calls == [executes]
    assert body["late"] is (not executes)
