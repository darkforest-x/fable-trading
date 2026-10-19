"""Replaceable, fail-closed adapter boundary for the local signal console.

The dashboard process never imports Ultralytics or LightGBM. An adapter is a
whitelisted implementation selected by environment, and inference runs in a
short-lived subprocess. Adding a future model means adding another adapter (or
overriding the explicit compatible artifact paths), not rewriting the React UI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

from yoyo.console.replay import (
    ADAPTER_ID,
    PROJECT_ROOT,
    ReplayError,
    describe_replay,
    normalize_symbol,
    replay_image_output_path,
)

DEFAULT_ADAPTER_ID = ADAPTER_ID
TIMEOUT_SECONDS = 90


class AdapterError(RuntimeError):
    """The configured adapter is unknown or its worker contract failed."""


class ModelAdapter(Protocol):
    """Stable boundary consumed by the console API and React client."""

    adapter_id: str

    def describe(self) -> dict[str, Any]: ...

    def infer(self, symbol: str) -> dict[str, Any]: ...

    def image_path(self, symbol: str) -> Path: ...


class LatestResearchReplayAdapter:
    """Latest research L1/L2 pair served only on aligned pre-holdout inputs."""

    adapter_id = DEFAULT_ADAPTER_ID

    def describe(self) -> dict[str, Any]:
        return describe_replay()

    def infer(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        python = PROJECT_ROOT / ".venv" / "bin" / "python"
        executable = python if python.is_file() else Path(sys.executable)
        env = {
            **os.environ,
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "PYTHONPATH": str(PROJECT_ROOT),
        }
        proc = subprocess.run(
            [str(executable), "-m", "yoyo.console.worker", "--symbol", normalized],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        if not lines:
            tail = (proc.stderr or "").strip()[-800:]
            raise AdapterError(f"model worker produced no JSON: {tail}")
        try:
            payload = json.loads(lines[-1])
        except ValueError as exc:
            raise AdapterError(f"model worker returned invalid JSON: {lines[-1][:240]}") from exc
        if proc.returncode != 0 or not payload.get("ok"):
            raise AdapterError(str(payload.get("detail") or proc.stderr or "model worker failed"))
        result = dict(payload["result"])
        result["image_url"] = f"/api/model/replay-image/{normalized}"
        return result

    def image_path(self, symbol: str) -> Path:
        return replay_image_output_path(symbol)


_ADAPTER_FACTORIES = {DEFAULT_ADAPTER_ID: LatestResearchReplayAdapter}
_busy = threading.Lock()
_cache: dict[tuple[str, str], dict[str, Any]] = {}


def configured_adapter_id() -> str:
    return os.environ.get("FABLE_SIGNAL_CONSOLE_ADAPTER", DEFAULT_ADAPTER_ID).strip()


def get_adapter() -> ModelAdapter:
    adapter_id = configured_adapter_id()
    factory = _ADAPTER_FACTORIES.get(adapter_id)
    if factory is None:
        raise AdapterError(
            f"unknown FABLE_SIGNAL_CONSOLE_ADAPTER={adapter_id!r}; "
            f"allowed: {', '.join(sorted(_ADAPTER_FACTORIES))}"
        )
    return factory()


def adapter_status_payload() -> dict[str, Any]:
    try:
        return {"ok": True, "adapter": get_adapter().describe()}
    except (AdapterError, ReplayError, OSError, ValueError) as exc:
        return {"ok": False, "detail": str(exc)}


def inference_payload(symbol: str, *, force: bool = False) -> dict[str, Any]:
    """Run or reuse one immutable replay result; concurrent calls fail friendly."""
    try:
        adapter = get_adapter()
        normalized = normalize_symbol(symbol)
    except (AdapterError, ReplayError) as exc:
        return {"ok": False, "detail": str(exc)}
    key = (adapter.adapter_id, normalized)
    if not force and key in _cache:
        return {"ok": True, "cached": True, "result": _cache[key]}
    if not _busy.acquire(blocking=False):
        return {"ok": False, "busy": True, "detail": "另一个模型回放正在运行，请稍后重试"}
    try:
        result = adapter.infer(normalized)
        _cache[key] = result
        return {"ok": True, "cached": False, "result": result}
    except (AdapterError, ReplayError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": str(exc)}
    finally:
        _busy.release()


def replay_image_path(symbol: str) -> Path:
    return get_adapter().image_path(symbol)
