"""Chart identity, same-origin navigation gate and failure recovery; no real UI."""
from types import SimpleNamespace
import traceback
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from yoyo.monitor import tradingview


@pytest.mark.parametrize("symbol,expected", [("BTC-USDT-SWAP", "OKX:BTCUSDT.P"),
    ("ETH-USD-SWAP", "OKX:ETHUSD.P"), ("BTC-USDC-SWAP", "OKX:BTCUSDC.P")])
@pytest.mark.parametrize("timeframe,interval", [("15m", "15"), ("30m", "30"), ("1H", "60"), ("4H", "240")])
def test_exact_chart_identity(symbol, expected, timeframe, interval):
    url = urlparse(tradingview.chart_url(symbol, timeframe))
    assert url.scheme == "https" and url.netloc == "www.tradingview.com" and url.path == "/chart/"
    assert parse_qs(url.query) == {"symbol": [expected], "interval": [interval]}


@pytest.mark.parametrize("symbol,timeframe", [("BTC-USDT-SWAP&symbol=BAD", "1H"),
    ('BTC-USDT-SWAP\"', "1H"), ("https://evil.test", "1H"), ("BTC-USDT-SWAP", "5m"), ("", "4H")])
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


def test_automation_timeout_does_not_misdiagnose_permissions(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr="private UI context (-1712)"))
    with pytest.raises(tradingview.DesktopOpenError, match="调用 TradingView 超时") as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "private UI" not in str(error.value)
    assert "caffeinate" not in str(error.value)
    assert "权限" not in str(error.value)
    assert "系统设置" not in str(error.value)
    assert not tradingview._OPEN_LOCK.locked()


def test_process_timeout_releases_click_lock(monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    def timeout(*args, **kwargs):
        raise tradingview.subprocess.TimeoutExpired("osascript", 25)
    monkeypatch.setattr(tradingview.subprocess, "run", timeout)
    with pytest.raises(tradingview.DesktopOpenError, match="超时"):
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert not tradingview._OPEN_LOCK.locked()


@pytest.mark.parametrize("stage,expected", [
    ("activate", "界面尚未就绪"),
    ("accessibility", "界面尚未就绪"),
    ("window_ready", "界面尚未就绪"),
    ("menu_button", "无法读取 TradingView 的打开链接菜单"),
    ("menu_items", "无法读取 TradingView 的打开链接菜单"),
    ("dispatch", "界面尚未就绪"),
    ("clipboard", "无法读取或临时设置剪贴板"),
    ("layout", "图表布局配置不可用"),
])
def test_stage_diagnostics_are_precise_and_log_only_controlled_metadata(stage, expected, monkeypatch, caplog):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    stderr = ("/private/secret-file:123: execution error: private window title; "
              f"SPIKE_STAGE={stage};SPIKE_CODE=-2700;SPIKE_REASON=SPIKE_AX_UNAVAILABLE "
              "(-2700)\nprivate clipboard text")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=1, stdout="private stdout", stderr=stderr))
    with pytest.raises(tradingview.DesktopOpenError, match=expected) as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert error.value.status_code == 503
    assert not tradingview._OPEN_LOCK.locked()
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage() == (
        f"TradingView open failed: stage={stage} code=-2700 reason=SPIKE_AX_UNAVAILABLE")
    assert "private" not in str(error.value) + caplog.text


@pytest.mark.parametrize("stage,reason,expected", [
    ("layout", "SPIKE_LAYOUT_UNAVAILABLE", "图表布局配置不可用"),
    ("layout", "SPIKE_INVALID_URL", "图表布局配置不可用"),
    ("dispatch", "SPIKE_CLIPBOARD_CHANGED", "剪贴板内容已被其他操作更改"),
    ("window_ready", "SPIKE_DEADLINE", "调用 TradingView 超时"),
    ("menu_button", "SPIKE_MENU_UNAVAILABLE", "无法读取 TradingView 的打开链接菜单"),
    ("menu_items", "SPIKE_MENU_UNAVAILABLE", "无法读取 TradingView 的打开链接菜单"),
])
def test_allowlisted_script_reasons_have_distinct_recovery_messages(stage, reason, expected, monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr=f"SPIKE_STAGE={stage};SPIKE_CODE=-2700;SPIKE_REASON={reason}"))
    with pytest.raises(tradingview.DesktopOpenError, match=expected) as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "系统设置" not in str(error.value)
    assert "caffeinate" not in str(error.value)
    assert not tradingview._OPEN_LOCK.locked()


@pytest.mark.parametrize("code", [-1719, -1728])
@pytest.mark.parametrize("stage", ["window_ready", "menu_button", "menu_items", "dispatch"])
def test_invalid_window_or_element_index_is_not_a_permission_denial(code, stage, monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr=f"private window index SPIKE_STAGE={stage};SPIKE_CODE={code};SPIKE_REASON=SPIKE_AX_UNAVAILABLE ({code})"))
    with pytest.raises(tradingview.DesktopOpenError, match="界面尚未就绪或窗口已发生变化") as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "系统设置" not in str(error.value)
    assert "自动化" not in str(error.value)
    assert not tradingview._OPEN_LOCK.locked()


@pytest.mark.parametrize("code", [-1743, -25211])
@pytest.mark.parametrize("stage", sorted(tradingview._STAGES))
def test_actual_macos_permission_codes_remain_permission_errors(code, stage, monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr=f"SPIKE_STAGE={stage};SPIKE_CODE={code};SPIKE_REASON=SPIKE_AX_UNAVAILABLE"))
    with pytest.raises(tradingview.DesktopOpenError, match="辅助功能／自动化"):
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert not tradingview._OPEN_LOCK.locked()


@pytest.mark.parametrize("stage", sorted(tradingview._STAGES))
def test_call_timeout_at_every_stage_never_requests_permission_changes(stage, monkeypatch):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="", stderr=f"SPIKE_STAGE={stage};SPIKE_CODE=-1712;SPIKE_REASON=SPIKE_AX_UNAVAILABLE"))
    with pytest.raises(tradingview.DesktopOpenError, match="调用 TradingView 超时") as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert "caffeinate" not in str(error.value)
    assert "系统设置" not in str(error.value)
    assert not tradingview._OPEN_LOCK.locked()


@pytest.mark.parametrize("stderr,expected_code", [
    ("private invalid index (-1719)", -1719),
    ("private denied (-25211)", -25211),
    ("private SPIKE_STAGE=private_stage;SPIKE_CODE=-2700;SPIKE_REASON=PRIVATE_SECRET", -2700),
    ("private SPIKE_STAGE=private_stage;SPIKE_CODE=secret;SPIKE_REASON=PRIVATE_SECRET", None),
    ("private quote -17430", None),
    ("private window title -1743 followed by actual error (-2700)", None),
])
def test_unstructured_or_unknown_diagnostics_never_leak_private_values(stderr, expected_code, monkeypatch, caplog):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
        SimpleNamespace(returncode=1, stdout="private stdout", stderr=stderr))
    with pytest.raises(tradingview.DesktopOpenError) as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert caplog.records[0].getMessage() == (
        f"TradingView open failed: stage=unknown code={expected_code if expected_code is not None else 'unknown'} reason=unknown")
    assert "private" not in caplog.text + str(error.value)
    assert "PRIVATE_SECRET" not in caplog.text + str(error.value)
    if expected_code != -25211:
        assert "系统设置" not in str(error.value)


@pytest.mark.parametrize("failure,expected", [
    (tradingview.subprocess.TimeoutExpired("private invocation", 25,
                                        output="private stdout", stderr="private clipboard"), "调用 TradingView 超时"),
    (OSError("private OS failure"), "界面尚未就绪"),
])
def test_process_failures_hide_exception_data_and_release_the_lock(failure, expected, monkeypatch, caplog):
    monkeypatch.setattr(tradingview.sys, "platform", "darwin")
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(tradingview.subprocess, "run", fail)
    with pytest.raises(tradingview.DesktopOpenError, match=expected) as error:
        tradingview.open_chart("BTC-USDT-SWAP", "1H")
    assert not tradingview._OPEN_LOCK.locked()
    assert "private" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__ is True
    formatted = "".join(traceback.format_exception(type(error.value), error.value, error.value.__traceback__))
    assert "private invocation" not in formatted
    assert "private OS failure" not in formatted
    monkeypatch.setattr(tradingview.subprocess, "run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="requested\n", stderr=""))
    assert tradingview.open_chart("BTC-USDT-SWAP", "1H") == {
        "requested": True, "symbol": "BTC-USDT-SWAP", "timeframe": "1H"}
