"""The status CLI must never disclose raw launchctl environment or errors."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from yoyo.monitor import manage


RAW = """gui/501/com.fable.impulse-monitor = {
\tactive count = 1
\tenvironment = {
\t\tUNRELATED_API_KEY => fake-super-secret
\t\tstate = fake-state-secret
\t\tpid = 876543210
\t\truns = 876543211
\t}
\tstate = running
\truns = 4
\tpid = 321
\tstdout path = /tmp/untrusted-secret-path
\tstderr path = /tmp/untrusted-secret-error
}
"""


def fake_result(stdout=RAW, stderr="fake-stderr-secret", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_status_is_a_strict_whitelist_and_ignores_environment(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return fake_result()

    monkeypatch.setattr(manage.subprocess, "run", run)
    result = manage.status("gui/501/" + manage.LABEL)
    assert set(result) == {"label", "state", "pid", "runs", "config_path", "stdout_log", "stderr_log"}
    assert result["label"] == manage.LABEL
    assert result["state"] == "running" and result["pid"] == 321 and result["runs"] == 4
    assert result["config_path"] == str(manage.PLIST)
    assert result["stdout_log"].endswith("Library/Logs/Fable/ImpulseMonitor/service.log")
    assert result["stderr_log"].endswith("Library/Logs/Fable/ImpulseMonitor/service-error.log")
    assert "secret" not in json.dumps(result)
    assert "87654321" not in json.dumps(result)
    assert calls == [(["launchctl", "print", "gui/501/" + manage.LABEL],
                     {"capture_output": True, "text": True, "check": False, "timeout": 10})]


def test_cli_prints_only_sanitized_json(monkeypatch, capsys):
    monkeypatch.setattr(manage.sys, "argv", ["manage", "status"])
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **kw: fake_result())
    manage.main()
    out, err = capsys.readouterr()
    parsed = json.loads(out)
    assert parsed["pid"] == 321 and parsed["state"] == "running"
    assert err == ""
    assert "environment" not in out and "secret" not in out and "active count" not in out


@pytest.mark.parametrize("value", ["fake-secret", "running fake-secret", "{fake-secret}"])
def test_unknown_state_and_noninteger_counts_are_never_echoed(monkeypatch, value):
    raw = f"job = {{\n    state = {value}\n    pid = fake-secret\n    runs = 9 fake-secret\n}}"
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **kw: fake_result(raw))
    result = manage.status("gui/501/" + manage.LABEL)
    assert result["state"] == "unknown"
    assert result["pid"] is None and result["runs"] is None
    assert "secret" not in json.dumps(result)


def test_failed_launchctl_discards_both_streams_and_returns_failure(monkeypatch, capsys):
    monkeypatch.setattr(manage.sys, "argv", ["manage", "status"])
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **kw: fake_result(returncode=3))
    with pytest.raises(SystemExit) as exc:
        manage.main()
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert json.loads(out)["state"] == "unavailable"
    assert "secret" not in out and err == ""


@pytest.mark.parametrize("failure", [
    OSError("fake-secret system error"),
    subprocess.TimeoutExpired("launchctl", 10, output="fake-secret partial output"),
])
def test_launchctl_exceptions_are_sanitized(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(manage.subprocess, "run", fail)
    result = manage.status("gui/501/" + manage.LABEL)
    assert result["state"] == "unavailable"
    assert "secret" not in json.dumps(result)


def test_status_without_fields_is_unknown_not_running(monkeypatch):
    monkeypatch.setattr(manage.subprocess, "run", lambda *a, **kw: fake_result("job = {}"))
    result = manage.status("gui/501/" + manage.LABEL)
    assert result["state"] == "unknown" and result["pid"] is None
