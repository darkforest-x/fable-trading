import json

from yoyo.copier.orders_card import build_orders_card, is_pending_order

WOODS, ARTHUR, CHARTPRIME, UNROUTED = "1226095564073205780", "1131521990814089276", "988830102957736027", "42"


def card(channel_id, total=0.0, positions=0, pnl=0.0):
    return {"channel_id": channel_id, "channel_name": channel_id, "exchange": "Gate", "leverage": 5,
            "account_ok": True, "account_total_usdt": total, "account_unrealized_pnl": pnl,
            "account_positions": positions, "open_signals": 1, "executed": 1}


def order(oid, channel_id, status="live", gate_status=None, left=None, plan=None, **extra):
    response = {}
    if gate_status is not None or left is not None:
        response = {"gate": {"order": {"status": gate_status, "left": left}}}
    return {"id": oid, "channel_id": channel_id, "status": status, "inst_id": "BTC-USDT-SWAP", "side": "buy",
            "response_json": json.dumps({"response": response, "plan": plan or {}}), **extra}


def test_pending_requires_live_status_and_resting_quantity():
    assert is_pending_order(order(1, WOODS, gate_status="open"))
    assert is_pending_order(order(1, WOODS, gate_status="", left=3))
    assert not is_pending_order(order(1, WOODS, gate_status="finished", left=3))
    assert not is_pending_order(order(1, WOODS, status="dry_run", gate_status="open"))
    assert not is_pending_order({"status": "live", "response_json": "not json"})


def test_only_routed_channels_are_shown_richest_first():
    result = build_orders_card([card(WOODS, 10), card(ARTHUR, 50), card(UNROUTED, 999)], [],
                               [WOODS, ARTHUR], {WOODS: True})
    assert [c["channel_id"] for c in result["channels"]] == [ARTHUR, WOODS]
    assert result["channels"][0]["name"] == "Arthur行情分析"
    assert result["channels"][1]["monitor_enabled"] is True


def test_position_row_uses_latest_filled_live_order_as_reference():
    orders = [
        order(3, CHARTPRIME, gate_status="open"),  # still resting -> pending, not the reference
        order(2, CHARTPRIME, gate_status="finished", plan={"notional_usdt": 120, "margin_usdt": 6},
              sl_trigger="90", tp_trigger="110"),
        order(1, CHARTPRIME, gate_status="finished"),
    ]
    result = build_orders_card([card(CHARTPRIME, 30, positions=1, pnl=-1.5)], orders, [CHARTPRIME])
    assert result["position_count"] == 1
    [row] = result["positions"]
    assert (row["notional_usdt"], row["margin_usdt"], row["sl_trigger"]) == (120.0, 6.0, "90")
    assert row["unrealized_pnl"] == -1.5
    assert [o["id"] for o in result["pending"]] == [3]
    # Live rows of a channel that holds a position belong to the position, not history.
    assert result["history"] == []


def test_history_keeps_closed_rows_and_live_rows_of_flat_channels():
    orders = [order(2, WOODS, status="dry_run"), order(1, WOODS, gate_status="finished"),
              order(9, UNROUTED, status="dry_run")]
    result = build_orders_card([card(WOODS)], orders, [WOODS])
    assert [o["id"] for o in result["history"]] == [2, 1]
    assert result["history"][1]["local_record"] is True
    assert result["positions"] == []


def test_switched_off_routed_channel_is_hidden():
    result = build_orders_card([card(WOODS), card(CHARTPRIME, 99)], [], [WOODS, CHARTPRIME], {CHARTPRIME: False})
    assert [c["channel_id"] for c in result["channels"]] == [WOODS]
