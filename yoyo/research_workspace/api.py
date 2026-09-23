"""Same-origin research APIs for the existing monitor, with independent jobs.

No scanner or model imports: catalog reads and queued offline work must not
increase the signal pulse workload. HTTPX's async client is used only for the
fixed loopback vision service: https://www.python-httpx.org/async/.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .catalog import Catalog
from .store import WorkspaceStore, now

ROOT = Path(__file__).resolve().parents[2]
PARENT = "exp-spike-v128-recent-20260923-v1"
VISION = ROOT / "yoyo/vision_research/static"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Note(StrictModel):
    expected_revision: int = Field(default=0, ge=0)
    stage: str = Field(default="hypothesis", pattern="^(hypothesis|review|inconclusive|rejected|archived)$")
    notes: str = Field(default="", max_length=12000)
    experiment_ids: list[str] = Field(default_factory=list, max_length=40)


class Factor(Note):
    name: str = Field(min_length=2, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    definition: str = Field(min_length=5, max_length=4000)
    causality: str = Field(min_length=5, max_length=2000)


class Experiment(StrictModel):
    title: str = Field(min_length=2, max_length=150)
    question: str = Field(min_length=5, max_length=4000)
    single_variable: str = Field(min_length=3, max_length=4000)
    factor_ids: list[str] = Field(default_factory=list, max_length=40)


class RunRequest(StrictModel):
    recipe: str = Field(pattern="^(spike-v128-frozen|verify-evidence)$")
    experiment_id: str = Field(min_length=1, max_length=180)
    symbols: list[str] = Field(default_factory=lambda: ["ETHUSDT"], min_length=1, max_length=10)


def same_origin(request: Request):
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if (request.headers.get("origin") != expected
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(403, "请从本机统一工作台提交。")


def register_experiment(root, store, payload):
    """Append one active preregistration without rewriting historical YAML."""
    exp_id = "exp-workspace-" + uuid.uuid4().hex[:16]
    record = dict(experiment_id=exp_id, source_repo="darkforest-x/fable-trading", status="active",
                  question=payload.question, single_variable=payload.single_variable,
                  title=payload.title, factor_ids=payload.factor_ids, artifacts=[],
                  training_eligible=False, production_eligible=False, holdout_consumed=False,
                  reuse_allowed=False, notes="工作台预登记；尚未运行或验证。", created_at=now())
    registry = Path(root) / "experiments/registry.yaml"
    with (store.runtime / "registry.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = registry.read_bytes()
        parsed = yaml.safe_load(old)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("experiments"), list):
            raise ValueError("实验注册表结构不正确，未写入。")
        folder = Path(root) / "experiments/active" / exp_id
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "workspace_spec.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        extra = yaml.safe_dump([record], allow_unicode=True, sort_keys=False)
        text = old + b"\n\n" + "\n".join("  " + line for line in extra.splitlines()).encode() + b"\n"
        temp = registry.with_name(".workspace-registry-" + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_bytes(text)
            # Refuse to replace a concurrent external editor's changes.
            if registry.read_bytes() != old:
                raise ValueError("注册表同时有更新，请刷新后重试。预登记草稿已保留。")
            os.replace(temp, registry)
        finally:
            temp.unlink(missing_ok=True)
    return record


def start_worker(store, root):
    with (store.runtime / "worker.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-m", "yoyo.research_workspace.worker", "--root", str(root),
                          "--runtime", str(store.runtime)], cwd=root, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True)


def install(app, runtime, root=ROOT, launch_worker=True, vision_transport=None):
    root = Path(root).resolve()
    catalog = Catalog(root)
    store = WorkspaceStore(Path(runtime) / "research_workspace")
    app.state.research_store = store
    api = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])

    def find_experiment(exp_id):
        try:
            return catalog.experiment(exp_id)
        except KeyError:
            raise HTTPException(404, "实验不存在")

    def all_factors():
        entries = {f["id"]: f for f in catalog.factors()}
        for key, value in store.notes("factor").items():
            entries[key] = dict(entries.get(key, {"id": key, "status": "hypothesis"}), **value,
                                training_eligible=False, production_eligible=False)
        return list(entries.values())

    def validate_links(ids, available):
        if any(x not in available for x in ids):
            raise HTTPException(400, "关联记录不存在，请刷新后重新选择。")

    @api.get("/overview")
    def overview():
        exps, factors, jobs = catalog.experiments(), all_factors(), store.jobs()
        counts = {}
        for row in exps:
            key = row.get("status", "unknown")
            counts[key] = counts.get(key, 0) + 1
        return {"experiment_count": len(exps), "factor_count": len(factors), "statuses": counts,
                "jobs": jobs[:8], "recent": exps[:8], "sources": [
                    {"name": "Qlib · 实验与运行", "url": "https://qlib.readthedocs.io/en/stable/component/recorder.html"},
                    {"name": "FreqUI · 回测与对比", "url": "https://www.freqtrade.io/en/stable/freq-ui/"},
                    {"name": "MLflow · 参数、指标与产物", "url": "https://mlflow.org/docs/latest/ml/tracking/"}],
                "objective": "降低交易频率，提高扣费胜率与实际兑现的高 R", "cost_bp": 20,
                "training_eligible": False, "production_eligible": False}

    @api.get("/factors")
    def factors():
        return {"items": all_factors()}

    @api.post("/factors", status_code=201)
    def create_factor(payload: Factor):
        validate_links(payload.experiment_ids, {x["experiment_id"] for x in catalog.experiments()})
        key = "factor-" + uuid.uuid4().hex[:16]
        if payload.expected_revision:
            raise HTTPException(400, "新因子版本必须从 0 开始")
        value = store.save_note("factor", key, payload.model_dump(exclude={"expected_revision"}))
        return dict(value, id=key)

    @api.put("/factors/{factor_id}")
    def update_factor(factor_id: str, payload: Note):
        if factor_id not in {x["id"] for x in all_factors()}:
            raise HTTPException(404, "因子不存在")
        validate_links(payload.experiment_ids, {x["experiment_id"] for x in catalog.experiments()})
        previous = store.notes("factor").get(factor_id, {})
        value = {k: v for k, v in previous.items() if k not in {"revision", "updated_at"}}
        value.update(payload.model_dump(exclude={"expected_revision"}))
        try:
            return store.save_note("factor", factor_id, value, payload.expected_revision)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @api.get("/experiments")
    def experiments():
        notes = store.notes("experiment")
        return {"items": [dict(x, annotation=notes.get(x["experiment_id"])) for x in catalog.experiments()]}

    @api.post("/experiments", status_code=201)
    def create_experiment(payload: Experiment):
        validate_links(payload.factor_ids, {x["id"] for x in all_factors()})
        return register_experiment(root, store, payload)

    @api.get("/experiments/{exp_id}")
    def evidence(exp_id: str):
        find_experiment(exp_id)
        return dict(catalog.evidence(exp_id), annotation=store.notes("experiment").get(exp_id),
                    history=store.history("experiment", exp_id))

    @api.put("/experiments/{exp_id}/note")
    def experiment_note(exp_id: str, payload: Note):
        find_experiment(exp_id)
        try:
            return store.save_note("experiment", exp_id, payload.model_dump(exclude={"expected_revision"}), payload.expected_revision)
        except ValueError as error:
            raise HTTPException(409, str(error))

    @api.get("/recipes")
    def recipes():
        manifest = root / "data/research/spike_v128_recent_20260923/manifest.json"
        symbols = []
        if manifest.is_file():
            symbols = sorted(str(x["symbol"]) for x in json.loads(manifest.read_text()).get("streams", []))
        return {"items": [
            {"id": "spike-v128-frozen", "name": "SPIKE V12.8 冻结规则重放", "experiment_id": PARENT,
             "description": "已有两月快照；15m / 1h，原退出、20bp 成本和匹配随机对照。最多 10 个合约，逐个运行。",
             "available": bool(symbols), "symbols": symbols, "start": "2026-07-23T00:00:00Z", "end": "2026-09-23T04:00:00Z"},
            {"id": "verify-evidence", "name": "现有证据快照与哈希核验", "available": True,
             "description": "保存本次可读取的配置、表格与哈希；不重算交易，不代表策略通过。"}]}

    @api.get("/jobs")
    def jobs():
        return {"items": store.jobs()}

    @api.post("/jobs", status_code=202)
    def create_job(payload: RunRequest):
        find_experiment(payload.experiment_id)
        if payload.recipe == "spike-v128-frozen":
            recipe = recipes()["items"][0]
            if payload.experiment_id != PARENT or not recipe["available"]:
                raise HTTPException(400, "此重放适配器仅支持已登记的 V12.8 两月冻结实验。")
            if len(set(payload.symbols)) != len(payload.symbols) or any(x not in recipe["symbols"] for x in payload.symbols):
                raise HTTPException(400, "合约必须来自冻结数据清单，且不能重复。")
        spec = payload.model_dump()
        spec.update(cost_bp=20, training_eligible=False, production_eligible=False)
        output_root = root / "experiments/active" / payload.experiment_id / "workspace_runs"
        try:
            job = store.create_job(payload.experiment_id, payload.recipe, spec, output_root)
        except ValueError as error:
            raise HTTPException(409, str(error))
        if launch_worker:
            start_worker(store, root)
        return job

    def get_job(job_id):
        try:
            return store.job(job_id)
        except KeyError:
            raise HTTPException(404, "任务不存在")

    @api.get("/jobs/{job_id}")
    def job_detail(job_id: str):
        job = get_job(job_id)
        path = Path(job["output"])
        result = path / "result.json"
        log = path / "run.log"
        tail = ""
        if log.is_file():
            with log.open("rb") as stream:
                stream.seek(max(0, log.stat().st_size - 12000))
                tail = stream.read().decode("utf-8", "replace")
        return dict(job, result=json.loads(result.read_text()) if result.is_file() else None, log=tail)

    @api.post("/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        get_job(job_id)
        return store.cancel(job_id)

    @api.get("/file")
    def evidence_file(experiment_id: str, path: str):
        find_experiment(experiment_id)
        allowed = {x["path"] for x in catalog.evidence(experiment_id).get("files", []) if x.get("downloadable", True)}
        if path not in allowed:
            raise HTTPException(404, "文件未列入本实验的可读证据。")
        target = (root / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "文件不可读")
        return FileResponse(target, filename=target.name)

    app.include_router(api)

    @app.api_route("/api/vision/{path:path}", methods=["GET", "POST", "PUT"], dependencies=[Depends(same_origin)])
    async def vision_proxy(path: str, request: Request):
        # Fixed service and strict origin-form paths, never a caller-chosen URL.
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or any(x in {"", ".", ".."} for x in path.split("/")):
            raise HTTPException(404, "视觉接口不存在")
        if path.split("/")[0] not in {"status", "signals", "automatic", "references", "config", "connection-test", "analyze", "runs", "exchanges", "images", "replay"}:
            raise HTTPException(404, "视觉接口不存在")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 18 * 1024 * 1024:
                raise HTTPException(413, "图片请求过大")
            body.extend(chunk)
        headers = {"origin": "http://127.0.0.1:8771", "sec-fetch-site": "same-origin"}
        if request.headers.get("content-type"):
            headers["content-type"] = request.headers["content-type"]
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(240, connect=3), transport=vision_transport) as client:
                response = await client.request(request.method, "http://127.0.0.1:8771/api/" + path,
                                                params=request.query_params, content=bytes(body), headers=headers)
        except httpx.HTTPError:
            raise HTTPException(503, "视觉研究服务暂不可用，请在运行状态中检查 8771 后台服务。")
        # Only image links generated by the vision service need namespacing;
        # original model conversation bodies must remain byte-faithful.
        content = response.content
        if response.headers.get("content-type", "").startswith("application/json") and not path.endswith("/export"):
            def links(value):
                if isinstance(value, dict):
                    return {k: "/api/vision" + v[4:] if k == "image_url" and isinstance(v, str) and v.startswith("/api/images/") else links(v) for k, v in value.items()}
                if isinstance(value, list):
                    return [links(x) for x in value]
                return value
            try:
                content = json.dumps(links(response.json()), ensure_ascii=False).encode()
            except ValueError:
                pass
        forwarded = {k: v for k, v in response.headers.items() if k in {"content-type", "content-disposition", "x-image-sha256", "x-visible-end-ms"}}
        return Response(content, status_code=response.status_code, headers=forwarded)

    app.mount("/vision-assets", StaticFiles(directory=VISION), name="vision-assets")
