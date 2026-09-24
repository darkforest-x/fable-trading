"""Composition must preserve lineage and reject silently unused asset bindings."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from test_api_store import _fixture_root
from yoyo.data.dataset_catalog import dataset_id
from yoyo.research_workspace import api


HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}
DATASET = dataset_id("data/research/spike_v128_recent_20260923")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = _fixture_root(tmp_path / "repo")
    static = tmp_path / "static"
    static.mkdir()
    monkeypatch.setattr(api, "VISION", static)
    monkeypatch.setattr(api.DatasetCatalog, "overview", lambda self: {"items": [
        {"id": DATASET, "name": "Frozen dataset"}, {"id": "dataset-other", "name": "Other dataset"}]})
    app = FastAPI()
    api.install(app, tmp_path / "runtime", root=root, launch_worker=False)
    with TestClient(app) as client:
        yield client, app, root


def definition(**overrides):
    return dict(name="Frozen rules study", route="rules", dataset_ids=[DATASET], factor_ids=[], model_ids=[],
                strategy_id="spike-v128", experiment_id=api.PARENT, notes="", stage="research", expected_revision=0,
                **overrides)


def create(client, **overrides):
    payload = definition()
    payload.update(overrides)
    result = client.post("/api/research/pipelines", json=payload, headers=HEADERS)
    assert result.status_code == 201, result.text
    return result.json()


def run(client, plan, **overrides):
    payload = dict(expected_revision=plan["revision"], mode="backtest", symbols=["BTCUSDT"],
                   request_id="isolated-request-12345678")
    payload.update(overrides)
    return client.post(f"/api/research/pipelines/{plan['id']}/runs", json=payload, headers=HEADERS)


def test_six_layers_parallel_routes_and_registered_models(workspace):
    client, _, _ = workspace
    data = client.get("/api/research/platform").json()
    assert [x["id"] for x in data["business_lines"]] == ["personal", "research", "systematic"]
    assert client.get("/api/research/manual").json()["capabilities"]["order_execution"] is False
    assert [x["id"] for x in data["layers"]] == ["data", "features", "models", "strategies", "evaluation", "forward"]
    assert {x["id"] for x in data["layers"][2]["components"]} == {"yolo", "vlm", "lightgbm"}
    assert {x["id"] for x in data["pipelines"]} == {"rules", "yolo_lgbm", "vlm"}
    assert data["summary"]["datasets"] == 2
    models = client.get("/api/research/models").json()
    assert models["gates"]["training"]["allowed"] is False
    assert models["gates"]["production"]["allowed"] is False


def test_pipeline_versions_and_frozen_job_lineage(workspace):
    client, app, _ = workspace
    plan = create(client)
    assert plan["validation"]["backtest"]["allowed"]
    response = run(client, plan)
    assert response.status_code == 202, response.text
    job = response.json()["run"]
    saved = job["spec"]["pipeline"]
    assert saved["snapshot"]["id"] == plan["id"]
    assert saved["snapshot"]["revision"] == 1
    assert saved["snapshot"]["dataset_ids"] == [DATASET]
    assert len(saved["sha256"]) == 64
    assert job["spec"]["cost_bp"] == 20
    edited = definition()
    edited.update(expected_revision=1, notes="second version")
    assert client.put(f"/api/research/pipelines/{plan['id']}", json=edited, headers=HEADERS).status_code == 200
    assert run(client, plan).status_code == 409
    assert app.state.research_store.job(job["id"])["spec"]["pipeline"] == saved
    detail = client.get(f"/api/research/pipelines/{plan['id']}").json()
    assert [x["revision"] for x in detail["history"]] == [2, 1]


@pytest.mark.parametrize("override", [
    {"route": "vlm"}, {"route": "yolo_lgbm"}, {"dataset_ids": ["dataset-other"]},
    {"factor_ids": ["l2.ma_spread_pct"]}, {"stage": "archived"},
    {"strategy_id": "spike-v128-joint"}, {"experiment_id": "exp-fixture-v1"},
])
def test_unsupported_combinations_are_saved_but_never_executed(workspace, override):
    client, app, _ = workspace
    plan = create(client, **override)
    assert not plan["validation"]["backtest"]["allowed"]
    assert run(client, plan).status_code == 409
    assert app.state.research_store.jobs() == []
    assert app.state.paper_store.runs() == []


def test_reference_validation_origin_and_privileged_fields(workspace):
    client, _, _ = workspace
    assert client.post("/api/research/pipelines", json=definition()).status_code == 403
    for field, value in [("model_ids", ["nonexistent"]), ("strategy_id", "nonexistent")]:
        data = definition()
        data[field] = value
        assert client.post("/api/research/pipelines", json=data, headers=HEADERS).status_code == 409
    for field, value in [("production_eligible", True), ("cost_bp", 0), ("command", "python train.py")]:
        assert client.post("/api/research/pipelines", json=dict(definition(), **{field: value}), headers=HEADERS).status_code == 422


def test_direct_job_cannot_forge_pipeline_snapshot_or_mismatch_binding(workspace):
    client, app, _ = workspace
    plan = create(client)
    assert run(client, plan, timeframes=["4H"]).status_code == 409
    payload = dict(recipe="verify-evidence", experiment_id=api.PARENT,
                   pipeline_ref={"id": plan["id"], "revision": 1})
    assert client.post("/api/research/jobs", json=payload, headers=HEADERS).status_code == 409
    payload["recipe"] = "spike-v128-frozen"
    payload["pipeline"] = {"fake": "snapshot"}
    assert client.post("/api/research/jobs", json=payload, headers=HEADERS).status_code == 422
    assert not app.state.research_store.jobs()


def test_paper_plan_uses_live_source_and_preserves_composition(workspace, monkeypatch):
    from yoyo.research_workspace import paper_api, paper_source
    client, app, _ = workspace
    manifest = {"hash": "fixture", "files": {}, "commit": "a" * 40}
    monkeypatch.setattr(paper_api, "source_manifest", lambda root: manifest)

    class Source:
        def __init__(self, runtime):
            pass

        def checkpoint(self, symbol, tf, at):
            return {"candles": [{"t": at - 900000}], "tick": .1}

        def events(self, plugin, at, symbols, timeframes):
            assert symbols is None
            return []

    monkeypatch.setattr(paper_source, "MonitorSource", Source)
    plan = create(client, dataset_ids=[])
    assert plan["validation"]["paper"]["allowed"]
    result = run(client, plan, mode="paper", symbols=["BTC-USDT-SWAP"])
    assert result.status_code == 202, result.text
    paper = result.json()["run"]
    assert paper["spec"]["pipeline"]["snapshot"]["id"] == plan["id"]
    assert paper["spec"]["pipeline"]["mode"] == "paper"
    assert paper["spec"]["data_source"]["kind"] == "existing_monitor_closed_checkpoints"
    assert paper["spec"]["production_eligible"] is False
    retried = run(client, plan, mode="paper", symbols=["BTC-USDT-SWAP"])
    assert retried.status_code == 202, retried.text
    assert retried.json()["run"]["id"] == paper["id"]
    # Caller cannot attach a plan for another strategy to a direct run request.
    bad = dict(strategy_id="spike-v128-joint", symbols=["BTC-USDT-SWAP"], timeframes=["15m"],
               request_id="isolated-request-other", pipeline_ref={"id": plan["id"], "revision": 1})
    assert client.post("/api/research/paper/runs", json=bad, headers=HEADERS).status_code == 409
    assert len(app.state.paper_store.runs()) == 1
    full = run(client, plan, mode="paper", symbols=None, symbol_scope="okx_all_usdt",
               request_id="isolated-full-market-12345")
    assert full.status_code == 202, full.text
    assert full.json()["run"]["spec"]["symbol_scope"] == "okx_all_usdt"
    assert full.json()["run"]["spec"]["symbols"] is None


def test_backtest_cannot_inherit_the_full_market_paper_default(workspace):
    client, app, _ = workspace
    plan = create(client)
    for values in ({"symbols": None}, {"symbol_scope": "okx_all_usdt"}):
        result = run(client, plan, **values)
        assert result.status_code == 409
    assert not app.state.research_store.jobs()


def test_model_annotation_routes_are_versioned_and_no_promotion(workspace):
    client, _, _ = workspace
    items = client.get("/api/research/models").json()["items"]
    assert items
    path = "/api/research/models/" + items[0]["id"]
    assert client.put(path, json={"stage": "research", "notes": "fixture"}, headers=HEADERS).status_code == 200
    assert client.put(path, json={"stage": "archived"}, headers=HEADERS).status_code == 409
    assert client.put(path, json={"stage": "production"}, headers=HEADERS).status_code == 422
    assert client.post(path + "/audit", headers=HEADERS).status_code == 200
    result = client.get(path).json()
    assert not result["production_eligible"] and not result["training_eligible"]
