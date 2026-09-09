"""The display client may dispatch only validated chart identities locally."""
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import subprocess

import pytest

from yoyo.monitor.tradingview import DesktopOpenError
from yoyo.monitor import windows_tradingview as win
from yoyo.monitor.desktop_tunnel import tunnel_config


@pytest.fixture
def setup(tmp_path, monkeypatch):
    runtime = tmp_path / "Spike"
    runtime.mkdir()
    layout = runtime / "tradingview-layout.txt"
    layout.write_text("https://cn.tradingview.com/chart/Example123/\n")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    monkeypatch.setattr(win.sys, "platform", "win32")
    return layout


@pytest.mark.parametrize("period,interval", [("5m", "5"), ("15m", "15"), ("30m", "30"), ("1H", "60"), ("4H", "240"), ("1Dutc", "1D")])
@pytest.mark.parametrize("symbol,tv", [("BTC-USDT-SWAP", "BTCUSDT.P"), ("ETH-USD-SWAP", "ETHUSD.P"), ("ETH-USDC-SWAP", "ETHUSDC.P")])
def test_identity_survives_layout_binding(setup, period, interval, symbol, tv):
    url = urlsplit(win.bound_chart_url(symbol, period, setup))
    assert url.netloc == "cn.tradingview.com"
    assert url.path == "/chart/Example123/"
    assert parse_qs(url.query) == {"symbol": ["OKX:" + tv], "interval": [interval]}


@pytest.mark.parametrize("bad", ["https://tradingview.com.evil/chart/a/", "http://tradingview.com/chart/a/", "https://evil@tradingview.com/chart/a/", "https://tradingview.com/chart/a/?symbol=evil", "https://tradingview.com/chart/../", "https://tradingview.com/chart/a/\nhttps://evil", "https://tradingview.com/chart/a/\"", b"\xff"])
def test_bad_layout_cannot_launch(setup, monkeypatch, bad):
    setup.write_bytes(bad if isinstance(bad, bytes) else bad.encode())
    monkeypatch.setattr(win.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(DesktopOpenError):
        win.open_chart("BTC-USDT-SWAP", "1H")


def test_missing_layout_is_actionable(setup):
    setup.unlink()
    with pytest.raises(DesktopOpenError, match="布局配置"):
        win.open_chart("BTC-USDT-SWAP", "1H")


@pytest.mark.parametrize("symbol,period", [("BTC-USDT-SWAP;calc", "1H"), ("BTC-USDT-SWAP", "10m"), ("BTC-USDT-SWAP", "60&x=1")])
def test_invalid_request_cannot_launch(setup, monkeypatch, symbol, period):
    monkeypatch.setattr(win.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    with pytest.raises(DesktopOpenError) as caught:
        win.open_chart(symbol, period)
    assert caught.value.status_code == 400


def test_dispatch_is_one_url_argument_not_shell_code(setup, monkeypatch):
    calls = []
    monkeypatch.setattr(win.subprocess, "run", lambda argv, **kw: (calls.append((argv, kw)) or SimpleNamespace(returncode=0, stdout="requested\n", stderr="")))
    assert win.open_chart("ETH-USDT-SWAP", "4H") == {"requested": True, "symbol": "ETH-USDT-SWAP", "timeframe": "4H", "target": "windows"}
    argv, kw = calls[0]
    assert argv[-2] == str(win.SCRIPT)
    assert argv[-1] == "https://cn.tradingview.com/chart/Example123/?symbol=OKX%3AETHUSDT.P&interval=240"
    assert not kw.get("shell")
    assert kw["timeout"] == 20
    assert not win._OPEN_LOCK.locked()


@pytest.mark.parametrize("result", [SimpleNamespace(returncode=1, stdout="", stderr="secret unknown error"), SimpleNamespace(returncode=0, stdout="untrusted", stderr=""), SimpleNamespace(returncode=1, stdout="", stderr="SPIKE_INTERACTIVE_SESSION_REQUIRED"), SimpleNamespace(returncode=1, stdout="", stderr="SPIKE_TV_NOT_INSTALLED"), subprocess.TimeoutExpired("secret", 20), OSError("secret")])
def test_fixed_errors_and_lock_cleanup(setup, monkeypatch, result):
    def run(*a, **k):
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(win.subprocess, "run", run)
    with pytest.raises(DesktopOpenError) as caught:
        win.open_chart("ETH-USDT-SWAP", "1H")
    assert "secret" not in str(caught.value)
    assert not win._OPEN_LOCK.locked()


def test_parallel_requests_do_not_launch_two_charts(setup, monkeypatch):
    monkeypatch.setattr(win.subprocess, "run", lambda *a, **k: pytest.fail("must not launch"))
    win._OPEN_LOCK.acquire()
    try:
        with pytest.raises(DesktopOpenError) as caught:
            win.open_chart("BTC-USDT-SWAP", "1H")
        assert caught.value.status_code == 409
    finally:
        win._OPEN_LOCK.release()


def test_tunnel_remains_loopback_and_verifies_host_key(tmp_path):
    config = tunnel_config("Administrator@192.168.1.2", tmp_path)
    argv = config["ProgramArguments"]
    assert argv[argv.index("-R") + 1] == "127.0.0.1:8767:127.0.0.1:8766"
    assert "StrictHostKeyChecking=yes" in argv
    assert "ExitOnForwardFailure=yes" in argv
    assert "ServerAliveInterval=15" in argv
    assert config["KeepAlive"] is True


@pytest.mark.parametrize("remote", ["-oProxyCommand=sh", "-v@host", "user@host;cmd", "user@host\n-oFoo", "ssh://user@host"])
def test_tunnel_rejects_options_as_destination(tmp_path, remote):
    with pytest.raises(ValueError):
        tunnel_config(remote, tmp_path)
