from yoyo.copier.gate.client import GateClient
from yoyo.copier.okx.client import OkxClient


def test_gate_open_orders_are_normalized(monkeypatch):
    client = GateClient(api_key="key", secret_key="secret")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: [
            {"id": 123, "contract": "ETH_USDT", "size": 46, "price": "1634.65"}
        ],
    )

    orders = client.get_open_orders()

    assert orders[0]["instId"] == "ETH-USDT-SWAP"
    assert orders[0]["ordId"] == "123"
    assert orders[0]["side"] == "buy"
    assert orders[0]["sz"] == "46.0"


def test_okx_open_orders_returns_data():
    class FakeTrade:
        def get_order_list(self, **kwargs):
            return {"code": "0", "data": [{"ordId": "456"}]}

    client = OkxClient()
    client.trade = FakeTrade()

    assert client.get_open_orders() == [{"ordId": "456"}]


def test_gate_open_triggers_are_normalized(monkeypatch):
    client = GateClient(api_key="key", secret_key="secret")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: [
            {
                "id": 789,
                "initial": {"contract": "ETH_USDT"},
                "trigger": {"price": "1596"},
            }
        ],
    )

    triggers = client.get_open_triggers()

    assert triggers[0]["instId"] == "ETH-USDT-SWAP"
    assert triggers[0]["triggerPx"] == "1596"
