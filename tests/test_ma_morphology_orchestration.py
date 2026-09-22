"""Pure regression checks for fail-closed morphology orchestration helpers."""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace
import pytest

from scripts.research.run_ma_morphology_redo import (
    EXP,
    _launch_batch_bytes,
    _powershell_wmi,
    _remote_json_subprocess_code,
    _stage_paths,
)


def test_remote_success_code_emits_child_json_outside_returncode_guard() -> None:
    code = _remote_json_subprocess_code(["fake-child"])
    emitted: list[dict[str, bool]] = []

    class FakeSubprocess:
        @staticmethod
        def run(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=0, stdout=b'{"preflight": true}', stderr=b"")

    exec(code, {"subprocess": FakeSubprocess, "json": json, "emit": emitted.append})
    assert emitted == [{"preflight": True}]
    assert "if p.returncode:\n    raise" in code
    assert "\nemit(" in code


def test_stage_includes_frozen_launch_contract_even_when_not_self_hashed() -> None:
    paths = _stage_paths({"files": {"scripts/windows/complete_ma_morphology_redo.py": "a" * 64}})
    assert EXP / "launch_contract.json" in paths
    assert any(path.as_posix().endswith("complete_ma_morphology_redo.py") for path in paths)


def test_fixed_batch_sets_repo_cwd_and_preserves_exit_code_with_crlf() -> None:
    batch = _launch_batch_bytes()
    text = batch.decode("ascii")
    assert b"\r\n" in batch
    assert 'set "PYTHONPATH=C:\\fable"' in text
    assert "cd /d C:\\fable" in text
    assert "scripts.windows.complete_ma_morphology_redo" in text
    assert "wmi_exit_code.txt" in text
    assert "exit /b %FABLE_TRAIN_RC%" in text


def test_wmi_command_uses_utf16le_literal_quoting_and_repo_cwd() -> None:
    command = 'cmd.exe /d /c "C:\\fable\\experiments\\owner\'s\\launch_wmi.cmd"'
    encoded = _powershell_wmi(command)
    text = base64.b64decode(encoded).decode("utf-16le")
    assert "Invoke-CimMethod" in text
    assert "Win32_Process" in text
    assert "CurrentDirectory='C:\\fable'" in text
    assert "CommandLine='cmd.exe /d /c \"C:\\fable\\experiments\\owner''s\\launch_wmi.cmd\"'" in text
    assert "list2cmdline" not in text


def test_completion_continuation_collects_once_without_relaunch(tmp_path, monkeypatch):
    from scripts.research import run_ma_morphology_redo as r
    states=iter([{'job':{'status':'running'}},{'job':{'status':'completed'}}])
    called=[]
    monkeypatch.setattr(r,'EXP',tmp_path)
    monkeypatch.setattr(r,'snapshot',lambda:next(states))
    monkeypatch.setattr(r,'collect',lambda:called.append('collected'))
    monkeypatch.setattr(r,'start',lambda:pytest.fail('must not relaunch'))
    monkeypatch.setattr(r.time,'sleep',lambda _:None)
    r.finish()
    assert called==['collected']
    assert json.loads((tmp_path/'completion_transfer_status.json').read_text())['status']=='completed'


def test_completion_continuation_stops_on_remote_failure(tmp_path, monkeypatch):
    from scripts.research import run_ma_morphology_redo as r
    monkeypatch.setattr(r,'EXP',tmp_path)
    monkeypatch.setattr(r,'snapshot',lambda:{'job':{'status':'failed','error':'CUDA error'}})
    monkeypatch.setattr(r,'collect',lambda:pytest.fail('must not collect incomplete job'))
    with pytest.raises(r.OrchestrationError,match='Remote job failed'):r.finish()
    assert json.loads((tmp_path/'completion_transfer_status.json').read_text())['status']=='remote_failed'
