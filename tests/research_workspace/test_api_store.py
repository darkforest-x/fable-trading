"""Isolated HTTP/store contract tests for the research workspace API."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from yoyo.research_workspace import api as api_module
from yoyo.research_workspace.api import PARENT, install
from yoyo.research_workspace.store import WorkspaceStore


def _fixture_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "experiments" / "active" / "exp-fixture-v1").mkdir(parents=True)
    (path / "experiments" / "active" / PARENT).mkdir(parents=True)
    (path / "artifacts").mkdir(parents=True)
    registry = (
        "# Keep this historical comment byte-for-byte.\n"
        "schema_version: 1\n"
        "experiments:\n"
        "  - experiment_id: exp-fixture-v1\n"
        "    status: inconclusive\n"
        "    question: Isolated fixture only?\n"
        "    artifacts: []\n"
        "    training_eligible: false\n"
        "    production_eligible: false\n"
        f"  - experiment_id: {PARENT}\n"
        "    status: active\n"
        "    question: Frozen replay parent fixture\n"
        "    artifacts: []\n"
        "    training_eligible: false\n"
        "    production_eligible: false\n"
    )
    (path / "experiments" / "registry.yaml").write_bytes(registry.encode("utf-8"))
    (path / "artifacts" / "registry.yaml").write_text("schema_version: 1\nartifacts: []\n", encoding="utf-8")
    feature_path = path / "yoyo" / "layers" / "l2_judgment" / "features.py"
    feature_path.parent.mkdir(parents=True)
    feature_path.write_text('FEATURE_COLUMNS = ["ma_spread_pct"]\n', encoding="utf-8")
    manifest = path / "data" / "research" / "spike_v128_recent_20260923" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"streams": [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]}), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path / "repo")
    runtime = tmp_path / "runtime"
    static = tmp_path / "vision-assets"
    static.mkdir()
    monkeypatch.setattr(api_module, "VISION", static)
    monkeypatch.setattr(api_module.subprocess, "check_output", lambda *a, **k: "a" * 40)

    def make_client(vision_transport=None):
        app = FastAPI()
        install(app, runtime=runtime, root=root, launch_worker=False, vision_transport=vision_transport)
        return TestClient(app)

    with make_client() as client:
        yield {"client": client, "make_client": make_client, "root": root, "runtime": runtime}


def _same_origin_headers(**extra):
    return {"origin": "http://testserver", "sec-fetch-site": "same-origin", **extra}


def _experiment_payload():
    return {
        "title": "Fixture research question",
        "question": "Does this isolated fixture change a measurable result?",
        "single_variable": "Change only one fixture variable.",
        "factor_ids": [],
    }


def test_cross_origin_cannot_write_experiment(workspace):
    registry = workspace["root"] / "experiments" / "registry.yaml"
    before = registry.read_bytes()
    response = workspace["client"].post(
        "/api/research/experiments",
        json=_experiment_payload(),
        headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert registry.read_bytes() == before
    assert {p.name for p in (workspace["root"] / "experiments" / "active").iterdir()} == {PARENT, "exp-fixture-v1"}


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/research/factors", {
            "name": "Injected factor", "category": "test", "definition": "A valid definition.",
            "causality": "A valid causal boundary.", "training_eligible": True,
        }),
        ("/api/research/jobs", {
            "recipe": "verify-evidence", "experiment_id": "exp-fixture-v1", "shell_command": "rm -rf /",
        }),
        ("/api/research/experiments", {**_experiment_payload(), "production_eligible": True}),
    ],
)
def test_unknown_or_privileged_fields_are_rejected(workspace, path, payload):
    response = workspace["client"].post(path, json=payload, headers=_same_origin_headers())
    assert response.status_code == 422


def test_factor_revision_conflict_history_and_reopen_persistence(workspace):
    client = workspace["client"]
    created = client.post(
        "/api/research/factors",
        json={
            "name": "Fixture factor",
            "category": "structure",
            "definition": "An isolated structural feature.",
            "causality": "Only bars through the decision candle are used.",
            "experiment_ids": ["exp-fixture-v1"],
        },
        headers=_same_origin_headers(),
    )
    assert created.status_code == 201
    factor_id = created.json()["id"]
    assert created.json()["revision"] == 1

    update = client.put(
        f"/api/research/factors/{factor_id}",
        json={"expected_revision": 1, "stage": "review", "notes": "Revision two."},
        headers=_same_origin_headers(),
    )
    assert update.status_code == 200
    assert update.json()["revision"] == 2

    conflict = client.put(
        f"/api/research/factors/{factor_id}",
        json={"expected_revision": 1, "stage": "rejected", "notes": "Stale write."},
        headers=_same_origin_headers(),
    )
    assert conflict.status_code == 409

    reopened = WorkspaceStore(workspace["runtime"] / "research_workspace")
    history = reopened.history("factor", factor_id)
    assert [record["revision"] for record in history] == [2, 1]
    assert history[0]["stage"] == "review"
    assert reopened.notes("factor")[factor_id]["notes"] == "Revision two."


def test_experiment_append_preserves_original_registry_bytes_and_stays_ineligible(workspace):
    client = workspace["client"]
    registry = workspace["root"] / "experiments" / "registry.yaml"
    original = registry.read_bytes() + b"# Preserve this exact terminal comment and spacing.  \n\n"
    registry.write_bytes(original)
    response = client.post("/api/research/experiments", json=_experiment_payload(), headers=_same_origin_headers())
    assert response.status_code == 201

    updated = registry.read_bytes()
    parsed = yaml.safe_load(updated)
    created = next(row for row in parsed["experiments"] if row["experiment_id"] == response.json()["experiment_id"])
    assert created["status"] == "active"
    assert created["training_eligible"] is False
    assert created["production_eligible"] is False
    from yoyo.contracts.artifacts import ExperimentRecord
    assert ExperimentRecord.from_mapping(created).source_commit == "a" * 40
    spec = workspace["root"] / "experiments" / "active" / created["experiment_id"] / "workspace_spec.json"
    assert json.loads(spec.read_text(encoding="utf-8"))["production_eligible"] is False
    assert updated.startswith(original)


def test_file_route_serves_catalogued_files_and_rejects_unlisted_paths(workspace):
    exp_dir = workspace["root"] / "experiments" / "active" / "exp-fixture-v1"
    config = exp_dir / "config.json"
    config.write_text('{"mode":"fixture"}', encoding="utf-8")
    params = {"experiment_id": "exp-fixture-v1", "path": "experiments/active/exp-fixture-v1/config.json"}
    allowed = workspace["client"].get("/api/research/file", params=params)
    assert allowed.status_code == 200
    assert allowed.json() == {"mode": "fixture"}

    config.write_text('{"mode":"fixture","apiKey":"never-download"}', encoding="utf-8")
    assert workspace["client"].get("/api/research/file", params=params).status_code == 404
    detail = workspace["client"].get("/api/research/experiments/exp-fixture-v1").json()
    assert detail["config"][params["path"]] == {"mode": "fixture"}

    params["path"] = "../../outside.json"
    denied = workspace["client"].get("/api/research/file", params=params)
    assert denied.status_code == 404


def test_fixed_recipe_rejects_arbitrary_symbols_and_experiments(workspace):
    client = workspace["client"]
    bad_symbol = client.post(
        "/api/research/jobs",
        json={"recipe": "spike-v128-frozen", "experiment_id": PARENT, "symbols": ["DOGEUSDT"]},
        headers=_same_origin_headers(),
    )
    assert bad_symbol.status_code == 400

    bad_experiment = client.post(
        "/api/research/jobs",
        json={"recipe": "spike-v128-frozen", "experiment_id": "exp-fixture-v1", "symbols": ["BTCUSDT"]},
        headers=_same_origin_headers(),
    )
    assert bad_experiment.status_code == 400


def test_queue_cancel_and_job_survive_store_reopen(workspace):
    created = workspace["client"].post(
        "/api/research/jobs",
        json={"recipe": "verify-evidence", "experiment_id": "exp-fixture-v1"},
        headers=_same_origin_headers(),
    )
    assert created.status_code == 202
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"

    cancelled = workspace["client"].post(f"/api/research/jobs/{job_id}/cancel", headers=_same_origin_headers())
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested"] is True

    reopened = WorkspaceStore(workspace["runtime"] / "research_workspace")
    persisted = reopened.job(job_id)
    assert persisted["status"] == "cancelled"
    assert persisted["cancel_requested"] is True


def test_vision_proxy_preserves_upstream_error_and_only_rewrites_image_urls(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path / "repo")
    static = tmp_path / "vision-assets"
    static.mkdir()
    monkeypatch.setattr(api_module, "VISION", static)
    seen = []
    upstream_payload = {
        "image_url": "/api/images/chart.png",
        "conversation": [
            {"role": "assistant", "content": "The literal /api/images/chart.png stays in the transcript."},
            {"role": "assistant", "content": "A full remote URL stays as text: https://example.test/api/images/chart.png"},
        ],
        "nested": {"image_url": "https://example.test/chart.png"},
    }

    def handler(request):
        seen.append(request)
        return httpx.Response(409, json=upstream_payload)

    app = FastAPI()
    install(app, runtime=tmp_path / "runtime", root=root, launch_worker=False,
            vision_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.post(
            "/api/vision/analyze",
            json={"prompt": "fixture"},
            headers=_same_origin_headers(),
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["image_url"] == "/api/vision/images/chart.png"
    assert payload["conversation"] == upstream_payload["conversation"]
    assert payload["nested"]["image_url"] == "https://example.test/chart.png"
    assert seen[0].url == "http://127.0.0.1:8771/api/analyze"


def test_vision_proxy_serves_png_image_route_for_64_hex_id(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path / "repo")
    static = tmp_path / "vision-assets"
    static.mkdir()
    monkeypatch.setattr(api_module, "VISION", static)
    image_id = "0123456789abcdef" * 4
    image_bytes = b"\x89PNG\r\n\x1a\nfixture"
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=image_bytes, headers={"content-type": "image/png"})

    app = FastAPI()
    install(app, runtime=tmp_path / "runtime", root=root, launch_worker=False,
            vision_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.get(f"/api/vision/images/{image_id}.png")

    assert response.status_code == 200
    assert response.content == image_bytes
    assert response.headers["content-type"] == "image/png"
    assert len(seen) == 1
    assert str(seen[0].url) == f"http://127.0.0.1:8771/api/images/{image_id}.png"


@pytest.mark.parametrize(
    "path",
    [
        # httpx normalizes a literal ../ that leaves /api/vision before the
        # request reaches ASGI, so it must resolve to a non-proxy route.
        "/api/vision/../status",
        # Percent-encoded dot segments retain the route prefix through httpx;
        # Starlette decodes them and the proxy's per-segment guard must reject.
        "/api/vision/images/%2e%2e/status",
        # A double-encoded traversal must also fail closed (the percent sign is
        # not an allowed proxy path character after one URL decode).
        "/api/vision/%252e%252e/status",
    ],
)
def test_vision_proxy_rejects_traversal_paths_without_forwarding(tmp_path, monkeypatch, path):
    root = _fixture_root(tmp_path / "repo")
    static = tmp_path / "vision-assets"
    static.mkdir()
    monkeypatch.setattr(api_module, "VISION", static)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"unexpected_forward": str(request.url)})

    app = FastAPI()
    install(app, runtime=tmp_path / "runtime", root=root, launch_worker=False,
            vision_transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert seen == []


def test_vision_proxy_rejects_body_over_18_mib_before_upstream(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path / "repo")
    static = tmp_path / "vision-assets"
    static.mkdir()
    monkeypatch.setattr(api_module, "VISION", static)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    app = FastAPI()
    install(app, runtime=tmp_path / "runtime", root=root, launch_worker=False,
            vision_transport=httpx.MockTransport(handler))
    body = b"x" * (18 * 1024 * 1024 + 1)
    with TestClient(app) as client:
        response = client.post(
            "/api/vision/analyze",
            content=body,
            headers=_same_origin_headers(**{"content-type": "application/octet-stream"}),
        )
    assert response.status_code == 413
    assert seen == []
