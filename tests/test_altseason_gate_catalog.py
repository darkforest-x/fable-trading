"""Regression for the actual rejected Gate catalog query shape."""
from yoyo.data.altseason_gate_catalog import bootstrap


def test_bootstrap_uses_observed_no_query_and_freezes_it(tmp_path):
    class Client:
        venue = "gate"
        root = tmp_path
        calls = []

        def get(self, path, params):
            self.calls.append((path, params))
            return [{"name": "ABC_USDT", "type": "direct", "contract_type": "", "quanto_multiplier": "1", "status": "delisted"}], {"fetched_at": "fixed", "request": {"params": params}, "body_sha256": "probe"}

    client = Client()
    result = bootstrap(client)
    assert client.calls == [("/futures/usdt/contracts", {})]
    assert result["markets"][0]["status"] == "delisted"
    assert result["bootstrap"]["query"] == {}
    assert bootstrap(client) == result and len(client.calls) == 1
