import asyncio
import tempfile
from pathlib import Path

from yoyo.copier.notifications.position_monitor import check_positions_once, position_snapshot
from yoyo.copier.store.sqlite import Database


class FakeOkx:
    def __init__(self, rounds):
        self.rounds = list(rounds)

    def get_positions(self):
        if not self.rounds:
            return []
        return self.rounds.pop(0)

    def get_open_orders(self):
        return []

    def get_open_triggers(self):
        return []

    def cancel_trigger_order(self, order_id):
        return {"ok": True, "order_id": order_id}


class FakeFailedPositions(FakeOkx):
    def __init__(self, error="timeout"):
        super().__init__([[]])
        self.last_positions_error = error


class FakeGateWithTriggers(FakeOkx):
    def __init__(self, positions, open_orders, triggers):
        super().__init__([positions])
        self.open_orders = open_orders
        self.triggers = triggers
        self.cancelled = []

    def get_open_orders(self):
        return self.open_orders

    def get_open_triggers(self):
        return self.triggers

    def cancel_trigger_order(self, order_id):
        self.cancelled.append(order_id)
        return {"ok": True, "order_id": order_id}


def pos(inst="BTC-USDT-SWAP", side="net", size="1", avg="68000", upl="0", notional="68000"):
    return {
        "instId": inst,
        "posSide": side,
        "pos": size,
        "notionalUsd": notional,
        "avgPx": avg,
        "upl": upl,
    }


def test_position_snapshot_normalizes_okx_rows():
    snap = position_snapshot([pos(size="2", upl="12.5")])

    row = snap["BTC-USDT-SWAP:net"]
    assert row["inst_id"] == "BTC-USDT-SWAP"
    assert row["pos"] == "2"
    assert row["notional_usdt"] == "68000"
    assert row["upl"] == "12.5"


def test_position_monitor_notifies_open_change_and_close(monkeypatch):
    sent = []

    async def fake_send(db, title, lines):
        sent.append({"title": title, "lines": lines})
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.notifications.position_monitor.send_telegram_notification", fake_send)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        client = FakeOkx([
            [],
            [pos(size="1")],
            [pos(size="0.5")],
            [],
        ])

        first = asyncio.run(check_positions_once(db, client))
        opened = asyncio.run(check_positions_once(db, client))
        changed = asyncio.run(check_positions_once(db, client))
        closed = asyncio.run(check_positions_once(db, client))

    assert first["seeded"] == 1
    assert opened["opened"] == 1
    assert changed["changed"] == 1
    assert closed["closed"] == 1
    assert [item["title"] for item in sent] == [
        "OKX 持仓已出现",
        "OKX 部分止盈/减仓",
        "OKX 持仓已消失",
    ]
    assert all("数量" not in line for item in sent for line in item["lines"])
    assert any("实际仓位" in line for item in sent for line in item["lines"])
    closed_notice = sent[-1]
    assert not any("未实现盈亏" in line for line in closed_notice["lines"])
    assert "最终盈亏: 待成交账本确认" in closed_notice["lines"]
    reduce_notice = sent[1]
    assert any("减少仓位" in line for line in reduce_notice["lines"])
    assert any("当前浮盈" in line for line in reduce_notice["lines"])


def test_position_monitor_skips_failed_empty_read_without_closing_snapshot(monkeypatch):
    sent = []

    async def fake_send(db, title, lines):
        sent.append({"title": title, "lines": lines})
        return {"ok": True}

    monkeypatch.setattr("yoyo.copier.notifications.position_monitor.send_telegram_notification", fake_send)

    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        seeded = asyncio.run(check_positions_once(db, FakeOkx([[pos(size="1")]]), "gate_feiyang"))
        skipped = asyncio.run(check_positions_once(db, FakeFailedPositions(), "gate_feiyang"))

        snapshot = db.get_setting("exchange_position_monitor_snapshot_gate_feiyang")
        with db._connect() as conn:
            logs = [dict(row) for row in conn.execute("SELECT event FROM audit_logs").fetchall()]

    assert seeded["seeded"] == 1
    assert skipped["skipped"] == 1
    assert skipped["closed"] == 0
    assert "BTC-USDT-SWAP:net" in (snapshot or "")
    assert sent == []
    assert any(row["event"] == "exchange_position_monitor_skip" for row in logs)


def test_position_monitor_cancels_orphan_triggers_only():
    with tempfile.TemporaryDirectory() as td:
        db = Database(Path(td) / "t.db")
        client = FakeGateWithTriggers(
            positions=[],
            open_orders=[{"instId": "XPL-USDT-SWAP"}],
            triggers=[
                {"instId": "DOGE-USDT-SWAP", "ordId": "doge-sl"},
                {"instId": "XPL-USDT-SWAP", "ordId": "xpl-sl"},
            ],
        )

        result = asyncio.run(check_positions_once(db, client, "gate_mia"))

    assert result["orphan_triggers"] == 1
    assert client.cancelled == ["doge-sl"]
