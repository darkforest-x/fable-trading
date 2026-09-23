"""Loopback SPIKE/Gemini research workbench, with no scanner or execution hooks.

FastAPI serves static UI and bounded JSON uploads on one origin. Credentials
stay in process memory or inherited environment, never in the research ledger.
Sources: https://fastapi.tiangolo.com/tutorial/static-files/
https://www.starlette.io/threadpool/ and https://ai.google.dev/gemini-api/docs/get-started
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import VERSION
from .gemini import GeminiClient, GeminiError
from .images import MAX_TOTAL_IMAGE_BYTES, image_from_data_url
from .schemas import AnalyzeRequest, ConfigRequest, DEFAULT_CRITERIA, DEFAULT_MODEL, ReviewRequest
from .source import SourceError, SpikeSource
from .store import ResearchStore, utc_now

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-spike-gemini-vision-20260923-v1"
DEFAULT_RUNTIME = ROOT / "experiments" / "active" / EXPERIMENT_ID / "runtime"
MAX_BODY_BYTES = 18 * 1024 * 1024


def validate_model(model: str) -> str:
    model = model.strip()
    if not re.fullmatch(r"gemini-[A-Za-z0-9._-]{1,90}", model):
        raise ValueError("请填写 Gemini 模型 ID，例如 gemini-3.8-flash")
    return model


async def read_json(request: Request, limit: int = MAX_BODY_BYTES):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "请使用 JSON 请求")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "请求过大，请减少图片数量或大小")
        body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "JSON 请求格式不正确")
    if not isinstance(value, dict):
        raise HTTPException(400, "请求必须是 JSON 对象")
    return value


def create_app(runtime: Optional[Path] = None, source=None, provider_factory=GeminiClient):
    app = FastAPI(title="SPIKE Vision Lab", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    store = ResearchStore(Path(runtime or os.environ.get("VISION_RESEARCH_RUNTIME", DEFAULT_RUNTIME)))
    spike = source or SpikeSource(os.environ.get("SPIKE_READONLY_URL", "http://127.0.0.1:8766"))
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    config = {"api_key": env_key.strip(), "model": validate_model(os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)),
              "credential_source": "environment" if env_key.strip() else "none"}
    inference_lock = threading.Lock()
    config_lock = threading.Lock()
    app.state.store, app.state.source = store, spike

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                    return JSONResponse({"detail": "仅接受本机工作台的同源请求"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def status():
        with config_lock:
            public = {"model": config["model"], "api_key_configured": bool(config["api_key"]),
                      "credential_source": config["credential_source"]}
        return dict(public, app_name="SPIKE Vision Lab", version=VERSION, spike=spike.status(),
                    production_eligible=False, training_eligible=False, default_criteria=DEFAULT_CRITERIA)

    @app.get("/api/status")
    def get_status():
        spike.list_signals()
        return status()

    @app.get("/api/signals")
    def signals():
        return spike.list_signals()

    @app.get("/api/signals/{signal_id}/image")
    def signal_image(signal_id: str):
        try:
            image, provenance = spike.signal_image(signal_id)
        except SourceError as exc:
            raise HTTPException(409, str(exc))
        return Response(image.data, media_type="image/png",
                        headers={"X-Image-SHA256": image.sha256,
                                 "X-Visible-End-Ms": str(provenance["visible_end_ms"])})

    @app.post("/api/config")
    async def configure(request: Request):
        payload = await read_json(request, 4096)
        try:
            update = ConfigRequest.model_validate(payload)
            model = validate_model(update.model)
            key = update.api_key.strip() if update.api_key is not None else None
            if key is not None and (len(key) < 20 or not re.fullmatch(r"[A-Za-z0-9_-]+", key)):
                raise ValueError("API Key 格式不正确")
        except (ValidationError, ValueError):
            raise HTTPException(400, "模型或 Key 格式不正确，请检查输入")
        with config_lock:
            config["model"] = model
            if key is not None:
                config.update(api_key=key, credential_source="session")
        return status()

    def provider(model=None):
        with config_lock:
            key = config["api_key"]
            chosen_model = model or config["model"]
        if not key:
            raise HTTPException(503, "请先在模型设置中配置 Gemini API Key")
        return provider_factory(api_key=key, model=chosen_model)

    @app.post("/api/connection-test")
    async def connection_test(request: Request):
        await read_json(request, 4096)
        client = provider()
        if not inference_lock.acquire(blocking=False):
            client.close()
            raise HTTPException(409, "当前有识别请求运行中，请稍后再试")
        try:
            return await run_in_threadpool(client.check_connection)
        except GeminiError as exc:
            return JSONResponse({"ok": False, "model": status()["model"], "message": str(exc),
                                 "error_details": exc.diagnostics()}, status_code=502)
        finally:
            client.close()
            inference_lock.release()

    def analyze_sync(body: AnalyzeRequest, client):
        if body.signal_id:
            image, provenance = spike.signal_image(body.signal_id)
            symbol, timeframe = provenance["symbol"], provenance["timeframe"]
            image_source = "spike"
        else:
            image = image_from_data_url(body.image_data_url, body.image_name or "upload.png")
            provenance = {"time_boundary": "unverified_upload", "bar_count": None}
            symbol, timeframe, image_source = "", "", "upload"
        if body.expected_image_sha256 and image.sha256 != body.expected_image_sha256:
            raise ValueError("图表已变化，请重新载入后再识别，避免发送与预览不同的图片")
        references = [image_from_data_url(item.data_url, item.name) for item in body.references]
        if sum(len(item.data) for item in [image, *references]) > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("待判图和参考图合计不能超过 12 MB")
        if image.sha256 in {item.sha256 for item in references}:
            raise ValueError("待判图不能同时作为参考图")
        record = {
            "id": uuid.uuid4().hex, "created_at": utc_now(), "status": "running", "model": client.model,
            "symbol": symbol, "timeframe": timeframe, "source": image_source,
            "image_url": store.put_image(image), "image_name": image.name, "image_sha256": image.sha256,
            "image_width": image.width, "image_height": image.height,
            "criteria": body.criteria, "criteria_sha256": hashlib.sha256(body.criteria.encode()).hexdigest(),
            "prompt_version": "spike-vision-v1", "schema_version": 1, "provenance": provenance,
            "references": [{"name": item.name, "sha256": item.sha256, "image_url": store.put_image(item)} for item in references],
            "decision": None, "usage": {}, "latency_ms": None, "error": None,
            "error_details": None, "review": None,
            "review_history": [], "training_eligible": False, "production_eligible": False,
        }
        store.save(record)
        started = time.perf_counter()
        try:
            result = client.analyze(image=image, references=references, criteria=body.criteria)
            record.update(result, status="completed", completed_at=utc_now())
        except GeminiError as exc:
            record.update(status="failed", error=str(exc), error_details=exc.diagnostics(),
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          completed_at=utc_now())
        except Exception:
            record.update(status="failed", error="识别请求异常；结果未知，未自动重试",
                          error_details={"code": "internal_error", "http_status": None, "provider_code": None},
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          completed_at=utc_now())
        store.save(record)
        return record

    @app.post("/api/analyze")
    async def analyze(request: Request):
        payload = await read_json(request)
        try:
            body = AnalyzeRequest.model_validate(payload)
            if body.model:
                body.model = validate_model(body.model)
        except (ValidationError, ValueError):
            raise HTTPException(400, "请选择一张待判图，检查模型与形态规则，参考图最多四张")
        client = provider(body.model)
        if not inference_lock.acquire(blocking=False):
            client.close()
            raise HTTPException(409, "已有识别请求运行中，请等待结果，避免重复计费")
        try:
            return await run_in_threadpool(analyze_sync, body, client)
        except (ValueError, SourceError) as exc:
            raise HTTPException(400, str(exc))
        finally:
            client.close()
            inference_lock.release()

    @app.get("/api/runs")
    def runs():
        return {"items": store.list()}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        record = store.get(run_id)
        if record is None:
            raise HTTPException(404, "识别记录不存在")
        return record

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str):
        record = get_run(run_id)
        return Response(json.dumps(record, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="vision-' + record["id"] + '.json"'})

    @app.post("/api/runs/{run_id}/review")
    async def review(run_id: str, request: Request):
        payload = await read_json(request, 24_000)
        try:
            body = ReviewRequest.model_validate(payload)
            return store.review(run_id, body.verdict, body.note)
        except ValidationError:
            raise HTTPException(400, "请提供有效的人工复核结论")
        except KeyError:
            raise HTTPException(404, "识别记录不存在")
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/images/{name}")
    def saved_image(name: str):
        try:
            path = store.image_path(name)
        except FileNotFoundError:
            raise HTTPException(404, "图片不存在")
        return FileResponse(path, media_type="image/png")

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="workbench")
    return app


def main():
    parser = argparse.ArgumentParser(description="Start the local SPIKE/Gemini vision workbench")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--runtime", type=Path, default=None)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(runtime=args.runtime), host="127.0.0.1", port=args.port,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
