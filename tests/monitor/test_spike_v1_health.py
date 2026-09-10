"""Lightweight liveness contract for the SPIKE V1 monitor."""
from __future__ import annotations

import pytest


NOW = 400 * 60 * 60 * 1000


@pytest.mark.parametrize("status,errors,age_ms,model_status,ok", [
    ("idle", 0, 1_000, "ready", True),
    ("degraded", 1, 1_000, "ready", False),
    ("idle", 0, 20 * 60_000, "ready", False),
    ("starting", 0, 1_000, "ready", False),
    ("idle", 0, 1_000, "loading", False),
])
def test_v1_health_reads_only_scan_and_model_state(tmp_path, monkeypatch, status, errors, age_ms, model_status, ok):
    from yoyo.monitor.server import create_app

    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    scan = {"status": status, "total": 3, "errors": errors, "finished_at_ms": NOW - age_ms}
    calls = []

    monkeypatch.setattr(monitor.client, "clock", lambda: NOW)
    monkeypatch.setattr(monitor.store, "get_meta", lambda key, default=None: calls.append(key) or (scan if key == "scan" else default))
    monkeypatch.setattr(monitor.model_gate, "status", lambda: {"status": model_status})
    monkeypatch.setattr(monitor, "status", lambda: (_ for _ in ()).throw(AssertionError("health must not build full status")))

    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/healthz")
    result = endpoint()

    assert result["service_alive"] is True
    assert result["ok"] is ok
    assert result["market_ready"] is (status not in ("error", "starting") and 3 > errors and age_ms < 20 * 60_000)
    assert result["model_ready"] is (model_status == "ready")
    assert calls == ["scan", "v1:model_gate"]


def test_health_hides_previous_worker_scan_generation(tmp_path, monkeypatch):
    from yoyo.monitor.server import create_app

    app = create_app(runtime=tmp_path, start_monitor=False)
    monitor = app.state.monitor
    monitor.scan_generation = "current-worker"
    previous = {"status": "idle", "generation": "previous-worker", "completed": 1434,
                "total": 1434, "errors": 0, "finished_at_ms": NOW}
    monkeypatch.setattr(monitor.client, "clock", lambda: NOW)
    monkeypatch.setattr(monitor.store, "get_meta", lambda key, default=None:
                        previous if key == "scan" else {"status": "ready"})
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/healthz")

    result = endpoint()

    assert result["scan"]["status"] == "starting"
    assert result["scan"]["stale_previous_run"] is True
    assert result["scan"]["generation"] == "current-worker"
    assert result["market_ready"] is False
    assert result["ok"] is False
