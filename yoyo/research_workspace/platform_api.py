"""Same-origin framework composition and model management endpoints.

All mutations are local research annotations or calls into existing bounded
engines. No endpoint trains, loads arbitrary model code, promotes or trades.
"""
from typing import Literal

from fastapi import HTTPException
from pydantic import Field

from yoyo.contracts.research import PipelineDefinition, PipelineRef, PipelineRun, ResearchContract
from .model_catalog import ModelCatalog
from .platform import ResearchPlatform


class ModelNote(ResearchContract):
    expected_revision: int = Field(default=0, ge=0)
    stage: Literal["research", "rejected", "archived"] = "research"
    notes: str = Field(default="", max_length=12000)


def install(api, app, root, catalog, datasets, store, factors, recipes, create_job, paper):
    models = ModelCatalog(root, catalog, store)
    platform = ResearchPlatform(catalog, datasets, models, store, factors, paper["strategies"], recipes, app.state.paper_store)
    app.state.research_platform = platform

    def invoke(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as error:
            raise HTTPException(404, str(error))
        except ValueError as error:
            raise HTTPException(409, str(error))

    @api.get("/platform")
    def overview():
        return platform.overview()

    @api.get("/models")
    def model_list():
        return models.overview()

    @api.get("/models/{model_id}")
    def model_detail(model_id: str):
        return invoke(models.get, model_id)

    @api.post("/models/{model_id}/audit")
    def model_audit(model_id: str):
        return invoke(models.audit, model_id)

    @api.put("/models/{model_id}")
    def model_note(model_id: str, payload: ModelNote):
        return invoke(models.annotate, model_id, payload.stage, payload.notes, payload.expected_revision)

    @api.get("/pipelines")
    def pipelines():
        return platform.pipelines()

    @api.get("/pipelines/{pipeline_id}")
    def pipeline_detail(pipeline_id: str):
        result = invoke(platform.get, pipeline_id)
        result["history"] = store.history("pipeline", pipeline_id)
        return result

    @api.post("/pipelines", status_code=201)
    def create_pipeline(payload: PipelineDefinition):
        return invoke(platform.save, payload)

    @api.put("/pipelines/{pipeline_id}")
    def update_pipeline(pipeline_id: str, payload: PipelineDefinition):
        return invoke(platform.save, payload, pipeline_id)

    @api.post("/pipelines/{pipeline_id}/runs", status_code=202)
    def run_pipeline(pipeline_id: str, payload: PipelineRun):
        from .api import RunRequest as BacktestRequest
        from .paper_api import RunRequest as PaperRequest

        plan = invoke(platform.get, pipeline_id)
        if plan["revision"] != payload.expected_revision:
            raise HTTPException(409, "研究流程版本已变化，请刷新后新建运行。")
        if not plan["validation"][payload.mode]["allowed"]:
            raise HTTPException(409, plan["validation"][payload.mode]["reason"])
        try:
            ref = PipelineRef(id=pipeline_id, revision=payload.expected_revision)
            if payload.mode == "backtest":
                if len(payload.symbols) > 10:
                    raise ValueError("单次回测最多 10 个合约。")
                if payload.timeframes is not None and sorted(payload.timeframes) != ["15m", "1H"]:
                    raise ValueError("原冻结回测固定同时运行 15m / 1H，不能覆盖周期。")
                request = BacktestRequest(recipe="spike-v128-frozen", experiment_id=plan["experiment_id"],
                                          symbols=payload.symbols, pipeline_ref=ref)
                # create_job validates and freezes the plan again immediately
                # before storing a job, so direct API callers share this gate.
                result = create_job(request)
            else:
                request = PaperRequest(strategy_id=plan["strategy_id"], symbols=payload.symbols,
                                       timeframes=payload.timeframes or ["15m"], request_id=payload.request_id, pipeline_ref=ref)
                result = paper["create"](request)
        except ValueError as error:
            raise HTTPException(409, str(error))
        return dict(kind=payload.mode, run=result)
