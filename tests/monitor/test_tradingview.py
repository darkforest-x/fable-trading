"""Chart identity, same-origin navigation gate and failure recovery; no real UI."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from yoyo.monitor import tradingview


@pytest.mark.parametrize("symbol,expected", [("BTC-USDT-SWAP", "OKX:BTCUSDT.P"),
    ("ETH-USD-SWAP", "OKX:ETHUSD.P"), ("BTC-USDC-SWAP", "OKX:BTCUSDC.P")])
@pytest.mark.parametrize("timeframe,interval", [("15m", "15"), ("1H", "60"), ("4H", "240")])
def test_exact_chart_identity(symbol, expected, timeframe, interval):
    url = urlparse(tradingview.chart_url(symbol, timeframe))
    assert url.scheme == "https" and url.netloc == "www.tradingview.com" and url.path == "/chart/"
    assert parse_qs(url.query) == {"symbol": [expected], "interval": [interval]}


@pytest.mark.parametrize("symbol,timeframe", [("BTC-USDT-SWAP&symbol=BAD", "1H"),
    ('BTC-USDT-SWAP\"', "1H"), ("https://evil.test", "1H"), ("BTC-USDT-SWAP", "30m"), ("", "4H")])
def test_invalid_navigation_never_launches(symbol, timeframe, monkeypatch):
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k: pytest.fail("invalid navigation launched UI"))
    with pytest.raises(tradingview.DesktopOpenError) as error:
        tradingview.open_chart(symbol, timeframe)
    assert error.value.status_code == 400


def test_bridge_uses_data_arguments_and_recovers_after_permission_failure(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    calls = []
    responses = iter([SimpleNamespace(returncode=1, stdout="", stderr="private context -1743"),
                      SimpleNamespace(returncode=0, stdout="requested\n", stderr="")])
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)
    monkeypatch.setattr(tradingview.subprocess, "run", run)
    with pytest.raises(tradingview.DesktopOpenError, match="macOS") as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "private context" not in str(error.value)
    assert tradingview.open_chart("ETH-USDT-SWAP", "4H")["requested"] is True
    assert calls[1][0] == ["/usr/bin/osascript", str(tradingview.SCRIPT), tradingview.chart_url("ETH-USDT-SWAP", "4H")]
    assert calls[1][1].get("shell", False) is False


def test_bridge_rejects_concurrent_clicks_without_launch(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k: pytest.fail("concurrent UI launch"))
    with tradingview._OPEN_LOCK:
        with pytest.raises(tradingview.DesktopOpenError) as error:
            tradingview.open_chart("BTC-USDT-SWAP", "1H")
        assert error.value.status_code == 409


def test_same_origin_post_gate_and_error_contract(tmp_path, monkeypatch):
    from yoyo.monitor import server
    app = server.create_app(runtime=tmp_path, start_monitor=False)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/tradingview/open")
    assert route.methods == {"POST"}
    calls = []
    monkeypatch.setattr(server, "open_chart", lambda s, t: calls.append((s, t)) or {"requested": True})
    payload = server.DesktopChartRequest(symbol="BTC-USDT-SWAP", timeframe="15m")
    def request(origin="http://127.0.0.1:8766", action="open-tradingview", site="same-origin"):
        return Request({"type": "http", "method": "POST", "scheme": "http", "path": "/api/tradingview/open",
            "server": ("127.0.0.1", 8766), "headers": [(b"host", b"127.0.0.1:8766"),
                (b"origin", origin.encode()), (b"x-spike-action", action.encode()), (b"sec-fetch-site", site.encode())]})
    for req in (request(origin="https://evil.test"), request(origin=""), request(action=""), request(site="cross-site")):
        with pytest.raises(HTTPException) as error:
            route.endpoint(payload, req)
        assert error.value.status_code == 403
    assert not calls
    assert route.endpoint(payload, request()) == {"requested": True}
    assert calls == [("BTC-USDT-SWAP", "15m")]
    def fail(*args):
        raise tradingview.DesktopOpenError("busy", 409)
    monkeypatch.setattr(server, "open_chart", fail)
    with pytest.raises(HTTPException) as error:
        route.endpoint(payload, request())
    assert error.value.status_code == 409 and error.value.detail == "busy"


def test_automation_timeout_guides_service_permission_without_raw_output(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr="private UI context (-1712)"))
    with pytest.raises(tradingview.DesktopOpenError, match="caffeinate") as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "private UI" not in str(error.value)
    assert not tradingview._OPEN_LOCK.locked()


def test_process_timeout_releases_click_lock(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    def timeout(*args, **kwargs):
        raise tradingview.subprocess.TimeoutExpired("osascript", 25)
    monkeypatch.setattr(tradingview.subprocess, "run", timeout)
    with pytest.raises(tradingview.DesktopOpenError, match="超时"):
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert not tradingview._OPEN_LOCK.locked()
