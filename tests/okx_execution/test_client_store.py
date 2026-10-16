"""Credentials, signature, ambiguous response and durable intent checks."""
import base64
from dataclasses import replace
import hashlib
import hmac
import json
import pytest

from yoyo.okx_execution.client import Client, APIError, TransportError, signature
from yoyo.okx_execution.config import Config, private_directory, read_private, write_private
from yoyo.okx_execution.store import Store, identifier


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return json.dumps(self.payload).encode()


class Opener:
    def __init__(self, payload): self.payload, self.requests = payload, []
    def open(self, request, timeout):
        self.requests.append(request)
        if isinstance(self.payload, Exception): raise self.payload
        return Response(self.payload)


def test_signature_exact_query_and_body():
    path, body = "/api/v5/trade/order?a=1&b=2", '{"sz":"1"}'
    assert signature("secret", "stamp", "POST", path, body) == base64.b64encode(
        hmac.new(b"secret", ("stampPOST" + path + body).encode(), hashlib.sha256).digest()).decode()


@pytest.mark.parametrize("payload,exception", [
    ({"code": "0", "data": [{"sCode": "51008", "sMsg": "DO NOT PRINT THIS"}]}, APIError),
    ({"code": "50100", "data": []}, APIError),
    (TimeoutError("SECRET"), TransportError),
    ({"code": "0", "data": {}}, TransportError)])
def test_failure_is_not_empty_and_not_retried(payload, exception):
    opener = Opener(payload)
    client = Client(Config(), dict(api_key="key", secret="secret", passphrase="phrase", demo=False), writes=True, opener=opener)
    with pytest.raises(exception) as error:
        client.post("/api/v5/trade/order", {"sz": "1"})
    assert len(opener.requests) == 1
    assert "SECRET" not in str(error.value) and "DO NOT PRINT" not in str(error.value)


def test_readonly_rejects_writes_before_network_and_demo_matches_credentials():
    opener = Opener({"code": "0", "data": []})
    client = Client(Config(), opener=opener)
    with pytest.raises(PermissionError): client.post("/api/v5/trade/order", {})
    assert not opener.requests
    with pytest.raises(ValueError): Client(Config(), {"demo": True})
    client = Client(replace(Config(), demo=True), opener=opener)
    client.get("/api/v5/public/time")
    assert opener.requests[0].get_header("X-simulated-trading") == "1"


def test_private_files_no_replacement_and_no_symlink(tmp_path):
    runtime = private_directory(tmp_path / "runtime")
    path = runtime / "credentials.json"
    write_private(path, {"secret": "fixture"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_private(path)["secret"] == "fixture"
    with pytest.raises(FileExistsError): write_private(path, {})
    link = runtime / "link"
    link.symlink_to(path)
    with pytest.raises(ValueError): read_private(link)


def test_restart_keeps_intent_and_single_writer(tmp_path):
    store = Store(tmp_path)
    trade = dict(id="signal", state="entry_pending", entry_id=identifier("signal", "entry"))
    store.save(trade)
    assert Store(tmp_path).trades() == [trade]
    with store.lock():
        with pytest.raises(BlockingIOError):
            with Store(tmp_path).lock(): pass
    assert len(identifier("signal", "entry")) == 32
    assert identifier("signal", "entry") != identifier("signal", "exit")
