"""Loopback SPIKE visual reviews, with no scanner or execution hooks.

FastAPI serves static UI and bounded JSON uploads on one origin. Credentials
are saved in owner-authorized private local settings, never in the research ledger.
Sources: https://fastapi.tiangolo.com/tutorial/static-files/
https://www.starlette.io/threadpool/ and https://docs.bigmodel.cn/api-reference/模型-api/对话补全
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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import VERSION
from .automatic import AutomaticReviews, POST_SIGNAL_BARS
from .defaults import install_default_references
from .zhipu import ZhipuClient, ZhipuError
from .providers import PROVIDERS, client_identity, profile_id, prompt_version, provider_for_model, region_for
from .images import MAX_TOTAL_IMAGE_BYTES, image_from_bytes, image_from_data_url
from .schemas import (AnalyzeRequest, ConfigRequest, DEFAULT_CRITERIA, DEFAULT_MODEL,
                      ReferencesRequest, ReviewRequest)
from .source import SourceError, SpikeSource
from .settings import LocalSettings
from .store import ReferenceRevisionConflict, ResearchStore, utc_now

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-spike-gemini-vision-20260923-v1"
DEFAULT_RUNTIME = ROOT / "experiments" / "active" / EXPERIMENT_ID / "runtime"
MAX_BODY_BYTES = 18 * 1024 * 1024
CHART_CAPTURE_RENDER_VERSION = "tradingview-lightweight-charts-4.2.0"


class SnapshotConflict(Exception):
    """Raised when the chart rows shown to the user no longer match the request."""


def current_review_context(provenance, chart=None, viewport=None):
    """Locate the observation's right edge using only its immutable snapshot.

    Reads provenance times and the final candle's opening time. No prices or
    future rows are added. Uploaded images have an explicitly unverified time.
    """
    context = {"assessment_scope": "current_right_edge",
               "time_boundary": provenance.get("time_boundary", "unverified_upload"),
               "timezone": "Asia/Shanghai"}
    for name in ("symbol", "timeframe", "last_bar_closed"):
        if name in provenance:
            context[name] = provenance[name]
    times = {"observed_at": provenance.get("observed_at_ms"),
             "spike_signal_at": provenance.get("signal_bar_close_ms", provenance.get("bar_close_ms"))}
    if chart and chart.get("candles"):
        times["rightmost_bar_open_at"] = chart["candles"][-1]["t"]
    for name, value in times.items():
        if isinstance(value, (int, float)):
            context[name] = datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).isoformat()
    if viewport is not None:
        context["browser_declared_viewport"] = viewport.model_dump(by_alias=True)
    return context


def validate_model(model: str) -> str:
    model = model.strip()
    provider_for_model(model)
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


def create_app(runtime: Optional[Path] = None, source=None, provider_factory=ZhipuClient,
               seed_defaults: bool = False, automatic_worker: bool = False, replay_history=None, replay_cases=None, qwen_factory=None):
    @asynccontextmanager
    async def lifespan(app):
        # https://fastapi.tiangolo.com/advanced/events/
        if automatic_worker:
            automatic.start()
        try:
            yield
        finally:
            if automatic_worker:
                await run_in_threadpool(automatic.stop)

    app = FastAPI(title="SPIKE Vision Lab", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    runtime = Path(runtime or os.environ.get("VISION_RESEARCH_RUNTIME", DEFAULT_RUNTIME))
    store = ResearchStore(runtime)
    if seed_defaults:
        install_default_references(store)
    settings = LocalSettings(runtime)
    saved = settings.load()
    spike = source or SpikeSource(os.environ.get("SPIKE_READONLY_URL", "http://127.0.0.1:8766"))
    # Legacy unknown-provider keys are never migrated to a new destination.
    profiles = {}
    def local_profile(value):
        provider_id = value["provider"]
        region = region_for(provider_id, value.get("region"))
        model = validate_model(value.get("model") or PROVIDERS[provider_id]["default_model"])
        if provider_for_model(model) != provider_id:
            raise ValueError("保存的供应商与模型不匹配")
        return {"provider": provider_id, "region": region, "model": model,
                "api_key": value.get("api_key", ""),
                "credential_source": "local_config" if value.get("api_key") else "none"}
    for identity, value in saved.get("profiles", {}).items():
        item = local_profile(value)
        if identity != profile_id(item["provider"], item["region"]):
            raise ValueError("保存的密钥地域与配置不匹配")
        profiles[identity] = item
    saved_provider = saved.get("provider", "gemini")
    if saved_provider in PROVIDERS:
        item = local_profile(saved)
        profiles[profile_id(item["provider"], item["region"])] = item
    else:
        item = None

    def configured_profile(provider_id, region):
        identity = profile_id(provider_id, region)
        saved_profile = profiles.get(identity, {})
        if saved_profile.get("api_key"):
            return dict(saved_profile)
        if provider_id == "qwen":
            env_region = os.environ.get("DASHSCOPE_REGION", "beijing")
            key = os.environ.get("DASHSCOPE_API_KEY", "") if env_region == region else ""
            model = saved_profile.get("model") or os.environ.get("QWEN_MODEL") or PROVIDERS[provider_id]["default_model"]
        else:
            key = os.environ.get("ZHIPU_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or ""
            model = saved_profile.get("model") or os.environ.get("ZHIPU_MODEL") or DEFAULT_MODEL
        model = validate_model(model)
        if provider_for_model(model) != provider_id:
            raise ValueError("环境配置的供应商与模型不匹配")
        return {"provider": provider_id, "region": region, "model": model, "api_key": key.strip(),
                "credential_source": "environment" if key.strip() else "none"}

    initial_provider = saved_provider if item else "qwen" if os.environ.get("DASHSCOPE_API_KEY") else "zhipu"
    initial_region = item["region"] if item else region_for(initial_provider,
        os.environ.get("DASHSCOPE_REGION", "beijing") if initial_provider == "qwen" else None)
    config = configured_profile(initial_provider, initial_region)
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
            provider_id, region = config["provider"], config["region"]
            public = {"model": config["model"], "api_key_configured": bool(config["api_key"]),
                      "credential_source": config["credential_source"], "provider": provider_id,
                      "region": region, "provider_name": PROVIDERS[provider_id]["name"],
                      "endpoint": PROVIDERS[provider_id]["endpoints"][region]}
        return dict(public, providers=[dict(spec, id=name) for name, spec in PROVIDERS.items()],
                    app_name="SPIKE Vision Lab", version=VERSION, spike=spike.status(),
                    production_eligible=False, training_eligible=False, default_criteria=DEFAULT_CRITERIA)

    @app.get("/api/status")
    def get_status():
        spike.list_signals()
        return status()

    @app.get("/api/signals")
    def signals():
        payload = spike.list_signals()
        jobs = {item["signal_id"]: item for item in automatic.snapshot()["items"]}
        return {**payload, "items": [{**item, "ai_review": jobs.get(item["id"])}
                                     for item in payload.get("items", [])]}

    @app.get("/api/automatic")
    def automatic_status():
        return automatic.snapshot()

    @app.post("/api/automatic")
    async def configure_automatic(request: Request):
        payload = await read_json(request, 4096)
        if set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
            raise HTTPException(400, "请提供自动识别开关状态")
        automatic.set_enabled(payload["enabled"])
        if payload["enabled"]:
            await run_in_threadpool(automatic.discover_once)
        return automatic.snapshot()

    @app.get("/api/signals/{signal_id}/image")
    def signal_image(signal_id: str):
        try:
            image, provenance = spike.signal_image(signal_id)
        except SourceError as exc:
            raise HTTPException(409, str(exc))
        return Response(image.data, media_type="image/png",
                        headers={"X-Image-SHA256": image.sha256,
                                 "X-Visible-End-Ms": str(provenance["visible_end_ms"])})

    @app.get("/api/signals/{signal_id}/chart")
    def signal_chart(signal_id: str, mode: Literal["signal_close", "live"] = "signal_close",
                     post_signal_bars: int = Query(default=12, ge=1, le=96)):
        try:
            if mode == "live":
                return spike.live_chart(signal_id, post_signal_bars)
            return spike.signal_chart(signal_id)
        except SourceError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/api/references")
    def references():
        return store.get_references()

    @app.put("/api/references")
    async def replace_references(request: Request):
        payload = await read_json(request)
        try:
            body = ReferencesRequest.model_validate(payload)
        except ValidationError:
            raise HTTPException(400, "请检查参考图格式、数量和版本号")
        items, images, names, hashes = [], [], set(), set()
        try:
            for submitted in body.references:
                image = image_from_data_url(submitted.data_url, submitted.name)
                normalized_name = image.name.strip()
                if not normalized_name or normalized_name.casefold() in names:
                    raise ValueError("参考图名称不能为空且不能重复")
                if image.sha256 in hashes:
                    raise ValueError("不能重复保存相同的参考图片")
                names.add(normalized_name.casefold())
                hashes.add(image.sha256)
                images.append(image)
            if sum(len(image.data) for image in images) > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError("参考图合计不能超过 12 MB")
            for image in images:
                items.append({"name": image.name, "sha256": image.sha256,
                              "image_url": store.put_image(image)})
            return store.replace_references(items, body.expected_revision)
        except ReferenceRevisionConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/api/config")
    async def configure(request: Request):
        payload = await read_json(request, 4096)
        try:
            update = ConfigRequest.model_validate(payload)
            key = update.api_key.strip() if update.api_key is not None else None
            if key is not None and (len(key) < 20 or not re.fullmatch(r"[A-Za-z0-9_.-]+", key)):
                raise ValueError("API Key 格式不正确")
        except (ValidationError, ValueError):
            raise HTTPException(400, "模型或 Key 格式不正确，请检查输入")
        with config_lock:
            try:
                target_provider = update.provider or (provider_for_model(update.model.strip())
                    if update.model else config["provider"])
                target_region = region_for(target_provider, update.region or (
                    config["region"] if target_provider == config["provider"] else None))
                target = configured_profile(target_provider, target_region)
                model = validate_model(update.model or target["model"])
                if provider_for_model(model) != target_provider:
                    raise ValueError("模型不属于所选供应商")
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            chosen_key = key if key is not None else target["api_key"]
            target.update(model=model, api_key=chosen_key,
                          credential_source="local_config" if chosen_key else "none")
            next_profiles = {**profiles, profile_id(target_provider, target_region): target}
            persisted = {identity: {field: value[field] for field in ("provider", "region", "model", "api_key")}
                         for identity, value in next_profiles.items()}
            try:
                settings.save(chosen_key, model, provider=target_provider, region=target_region, profiles=persisted)
            except RuntimeError as exc:
                raise HTTPException(500, str(exc))
            profiles.clear()
            profiles.update(next_profiles)
            config.update(target)
        return status()

    def provider(model=None):
        with config_lock:
            current = dict(config)
        chosen_model = validate_model(model or current["model"])
        provider_id = provider_for_model(chosen_model)
        if provider_id != current["provider"]:
            raise HTTPException(409, "该记录的模型属于另一供应商，请先在模型设置中切换，再执行识别")
        if not current["api_key"]:
            raise HTTPException(503, f"请先在模型设置中配置{PROVIDERS[provider_id]['name']}当前地域的 API Key")
        if provider_id == "qwen":
            from .qwen import QwenClient
            factory = qwen_factory or QwenClient
            return factory(api_key=current["api_key"], model=chosen_model, region=current["region"])
        return provider_factory(api_key=current["api_key"], model=chosen_model)

    def capture_exchange(client, kind, run_id=None):
        if not hasattr(client, "trace_callback"):
            return None
        exchange_id = uuid.uuid4().hex
        metadata = {"id": exchange_id, "created_at": utc_now(), "kind": kind,
                    "run_id": run_id, "model": client.model, **client_identity(client), "status": "running"}
        client.trace_callback = lambda exchange: store.save_exchange({**metadata, **exchange})
        return exchange_id

    @app.post("/api/connection-test")
    async def connection_test(request: Request):
        await read_json(request, 4096)
        client = provider()
        if not inference_lock.acquire(blocking=False):
            client.close()
            raise HTTPException(409, "当前有识别请求运行中，请稍后再试")
        exchange_id = capture_exchange(client, "connection_test")
        try:
            result = await run_in_threadpool(client.check_connection)
            result.update(client_identity(client))
            result["api_exchange_id"] = store.finish_exchange(exchange_id, "completed")
            return result
        except ZhipuError as exc:
            saved_id = store.finish_exchange(exchange_id, "failed", str(exc))
            return JSONResponse({"ok": False, "model": client.model, **client_identity(client), "message": str(exc),
                                 "error_details": exc.diagnostics(), "api_exchange_id": saved_id}, status_code=502)
        except Exception:
            message = "连接测试异常；结果未知，没有自动重试"
            saved_id = store.finish_exchange(exchange_id, "failed", message)
            return JSONResponse({"ok": False, "message": message,
                                 "api_exchange_id": saved_id}, status_code=502)
        finally:
            client.close()
            inference_lock.release()

    def analyze_sync(body: AnalyzeRequest, client, references, reference_revision,
                     reference_source, chart_snapshot, prepared=None, run_id=None):
        if prepared is not None:
            image, provenance = prepared
            symbol, timeframe = provenance["symbol"], provenance["timeframe"]
            image_source = "spike_automatic"
        elif body.signal_id:
            if body.chart_capture_data_url:
                chart = chart_snapshot
                if chart is None:
                    raise SourceError("缺少候选图表快照，请重新载入候选")
                provenance = dict(chart["provenance"])
                image = image_from_data_url(body.chart_capture_data_url,
                                            provenance["symbol"] + "-" + provenance["timeframe"] + "-capture.png")
                provenance.update(chart_snapshot_id=chart.get("snapshot_id"),
                                  chart_sha256=chart["chart_sha256"],
                                  chart_candles=chart["candles"],
                                  render_version=CHART_CAPTURE_RENDER_VERSION,
                                  pixel_origin="browser_capture",
                                  pixel_attestation="unverified",
                                  capture_pixels_attested_to_ohlc=False)
                if body.chart_viewport is not None:
                    provenance["browser_declared_viewport"] = body.chart_viewport.model_dump(by_alias=True)
                symbol, timeframe, image_source = provenance["symbol"], provenance["timeframe"], "spike_capture"
            else:
                if body.expected_chart_sha256:
                    if chart_snapshot is None or chart_snapshot["chart_sha256"] != body.expected_chart_sha256:
                        raise SnapshotConflict("候选图表数据已变化，请重新载入后再识别")
                image, provenance = spike.signal_image(body.signal_id)
                symbol, timeframe = provenance["symbol"], provenance["timeframe"]
                image_source = "spike"
        else:
            image = image_from_data_url(body.image_data_url, body.image_name or "upload.png")
            provenance = {"time_boundary": "unverified_upload", "bar_count": None}
            symbol, timeframe, image_source = "", "", "upload"
        if body.expected_image_sha256 and image.sha256 != body.expected_image_sha256:
            raise ValueError("图表已变化，请重新载入后再识别，避免发送与预览不同的图片")
        if sum(len(item.data) for item in [image, *references]) > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("待判图和参考图合计不能超过 12 MB")
        if image.sha256 in {item.sha256 for item in references}:
            raise ValueError("待判图不能同时作为参考图")
        review_context = current_review_context(provenance, chart_snapshot, body.chart_viewport)
        record = {
            "id": run_id or uuid.uuid4().hex, "created_at": utc_now(), "status": "running", "model": client.model,
            **client_identity(client),
            "automatic": prepared is not None, "signal_id": body.signal_id,
            "symbol": symbol, "timeframe": timeframe, "source": image_source,
            "image_url": store.put_image(image), "image_name": image.name, "image_sha256": image.sha256,
            "image_width": image.width, "image_height": image.height,
            "criteria": body.criteria, "criteria_sha256": hashlib.sha256(body.criteria.encode()).hexdigest(),
            "prompt_version": prompt_version(client.model), "schema_version": 2, "provenance": provenance,
            "analysis_scope": "current_right_edge", "review_context": review_context,
            "references": [{"name": item.name, "sha256": item.sha256, "image_url": store.put_image(item)} for item in references],
            "reference_source": reference_source, "reference_revision": reference_revision,
            "decision": None, "usage": {}, "latency_ms": None, "error": None,
            "error_details": None, "review": None,
            "review_history": [], "training_eligible": False, "production_eligible": False,
        }
        record["api_exchange_id"] = capture_exchange(client, "recognition", record["id"])
        store.save(record)
        started = time.perf_counter()
        try:
            result = client.analyze(image=image, references=references, criteria=body.criteria,
                                    context=review_context)
            record.update(result, status="completed", completed_at=utc_now())
        except ZhipuError as exc:
            record.update(status="failed", error=str(exc), error_details=exc.diagnostics(),
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          completed_at=utc_now())
        except Exception:
            record.update(status="failed", error="识别请求异常；结果未知，未自动重试",
                          error_details={"code": "internal_error", "http_status": None, "provider_code": None},
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          completed_at=utc_now())
        record["api_exchange_id"] = store.finish_exchange(record["api_exchange_id"], record["status"],
                                                         record.get("error"))
        store.save(record)
        return record

    def saved_references():
        snapshot = store.get_references()
        images = []
        for saved in snapshot["items"]:
            name = saved.get("image_url", "").rsplit("/", 1)[-1]
            if name != saved.get("sha256", "") + ".png":
                raise ValueError("已保存的参考图记录损坏，请重新保存参考图")
            path = store.image_path(name)
            image = image_from_bytes(path.read_bytes(), saved.get("name", "reference"))
            if image.sha256 != saved["sha256"]:
                raise ValueError("已保存的参考图内容不匹配，请重新保存参考图")
            images.append(image)
        return images, snapshot["revision"]

    def automatically_review(signal, run_id):
        from .auto_chart import RENDER_VERSION, render_live_chart

        # A manual review may have finished while this signal was queued.
        for prior in store.list():
            if (prior.get("provenance", {}).get("id") == signal["id"]
                    and prior.get("status") == "completed"
                    and prior.get("analysis_scope") == "current_right_edge"
                    and prior.get("provenance", {}).get("time_boundary") == "live_observation"):
                return prior
        chart = spike.live_chart(signal["id"], POST_SIGNAL_BARS)
        provenance = chart["provenance"]
        if (not provenance.get("recognition_eligible") or provenance.get("source_stale")
                or provenance.get("id") != signal["id"]):
            raise SourceError("盘口快照过期或不对应当前信号，未调用模型。")
        image = render_live_chart(chart)
        # Validate again after rendering; never send a stale queued snapshot.
        spike.chart_snapshot(signal["id"], chart["snapshot_id"])
        provenance = dict(provenance, chart_snapshot_id=chart["snapshot_id"],
                          chart_sha256=chart["chart_sha256"], chart_candles=chart["candles"],
                          render_version=RENDER_VERSION, pixel_origin="server_render",
                          pixel_attestation="server_render_from_snapshot",
                          capture_pixels_attested_to_ohlc=False)
        references, revision = saved_references()
        client = provider()
        try:
            body = AnalyzeRequest(signal_id=signal["id"], criteria=DEFAULT_CRITERIA)
            return analyze_sync(body, client, references, revision, "global", chart,
                                prepared=(image, provenance), run_id=run_id)
        finally:
            client.close()

    def has_credentials():
        with config_lock:
            return bool(config["api_key"])

    automatic = AutomaticReviews(store, spike, automatically_review, inference_lock,
                                 has_credentials, enabled_default=automatic_worker,
                                 clock=getattr(spike, "clock", None))
    app.state.automatic = automatic

    @app.post("/api/analyze")
    async def analyze(request: Request):
        payload = await read_json(request)
        try:
            body = AnalyzeRequest.model_validate(payload)
            if body.model:
                body.model = validate_model(body.model)
        except (ValidationError, ValueError):
            raise HTTPException(400, "请选择一张待判图，检查模型、形态规则和参考图格式")
        chart_snapshot = None
        if body.chart_snapshot_id and body.chart_viewport is None:
            raise HTTPException(409, "盘口识别需要最新的图表视口信息，请刷新页面后重新识别")
        if body.signal_id and (body.chart_capture_data_url or body.expected_chart_sha256):
            try:
                if body.chart_snapshot_id:
                    chart_snapshot = await run_in_threadpool(spike.chart_snapshot, body.signal_id, body.chart_snapshot_id)
                else:
                    chart_snapshot = await run_in_threadpool(spike.signal_chart, body.signal_id)
            except SourceError as exc:
                raise HTTPException(409, str(exc))
            if (body.expected_chart_sha256 and
                    chart_snapshot["chart_sha256"] != body.expected_chart_sha256):
                raise HTTPException(409, "候选图表数据已变化，请重新载入后再截取")
            if body.chart_viewport is not None:
                rows = chart_snapshot["candles"]
                if (body.chart_viewport.bar_count != len(rows)
                        or body.chart_viewport.last_bar_open_ms != rows[-1]["t"]):
                    raise HTTPException(409, "截图视口与盘口快照不一致，请回到最新盘口后重新识别")
        try:
            if body.references is None:
                selected_references, reference_revision = saved_references()
                if (body.reference_revision is not None and
                        body.reference_revision != reference_revision):
                    raise ReferenceRevisionConflict("参考图版本已更新，请重新载入后再识别")
                reference_source = "global"
            else:
                selected_references = [image_from_data_url(item.data_url, item.name)
                                       for item in body.references]
                reference_revision = None
                reference_source = "request"
            if sum(len(image.data) for image in selected_references) > MAX_TOTAL_IMAGE_BYTES:
                raise ValueError("参考图合计不能超过 12 MB")
        except ReferenceRevisionConflict as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        client = provider(body.model)
        if not inference_lock.acquire(blocking=False):
            client.close()
            raise HTTPException(409, "已有识别请求运行中，请等待结果，避免重复计费")
        try:
            return await run_in_threadpool(analyze_sync, body, client, selected_references,
                                           reference_revision, reference_source, chart_snapshot)
        except (ValueError, SourceError) as exc:
            raise HTTPException(400, str(exc))
        except SnapshotConflict as exc:
            raise HTTPException(409, str(exc))
        finally:
            client.close()
            inference_lock.release()

    @app.get("/api/runs")
    def runs():
        return {"items": store.list()}

    @app.get("/api/exchanges")
    def exchanges():
        return {"items": store.list_exchanges()}

    @app.get("/api/exchanges/{exchange_id}")
    def get_exchange(exchange_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", exchange_id):
            raise HTTPException(404, "API 原始记录不存在")
        record = store.get_exchange(exchange_id)
        if record is None:
            raise HTTPException(404, "API 原始记录不存在；旧请求没有保存原始正文")
        return record

    @app.get("/api/exchanges/{exchange_id}/export")
    def export_exchange(exchange_id: str):
        return Response(json.dumps(get_exchange(exchange_id), ensure_ascii=False, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="spike-api-{exchange_id}.json"'})

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

    from .replay import install_replay_routes
    def replay_model_selection():
        with config_lock:
            return {key: config[key] for key in ("model", "region")}

    install_replay_routes(app, store, provider, inference_lock, read_json, capture_exchange,
                          current_review_context, replay_model_selection, history=replay_history, cases=replay_cases)

    @app.get("/")
    def unified_workspace():
        return RedirectResponse("http://127.0.0.1:8766/#vision", status_code=307)

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="workbench")
    return app


def main():
    parser = argparse.ArgumentParser(description="Start the local SPIKE vision workbench")
    parser.add_argument("--port", type=int, default=8771)
    parser.add_argument("--runtime", type=Path, default=None)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(runtime=args.runtime, seed_defaults=True, automatic_worker=True), host="127.0.0.1", port=args.port,
                access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
