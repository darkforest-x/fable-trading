"""Narrow Alibaba Cloud Model Studio Qwen-VL adapter for chart research.

Requests contain a candidate image followed by optional reference images. The
OpenAI-compatible endpoint is selected from a fixed Beijing/Singapore allowlist;
the API key must belong to that region. Replies use JSON Object mode where the
selected models support it and are always validated locally with
``CurrentDecision``. No tools, retries, redirects, or trading integration are
used.

Official contracts:
* https://help.aliyun.com/en/model-studio/qwen-vl-compatible-with-openai
* https://help.aliyun.com/en/model-studio/compatibility-of-openai-with-dashscope
* https://help.aliyun.com/en/model-studio/qwen3-vl-plus
* https://help.aliyun.com/en/model-studio/qwen-vl-max
* https://help.aliyun.com/en/model-studio/qwen-structured-output
* https://help.aliyun.com/en/model-studio/vision
* https://help.aliyun.com/en/model-studio/error-code
"""
from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any

import httpx

from yoyo.vision_research.schemas import ImageInput
from .zhipu import (
    CONNECT_TIMEOUT_SECONDS,
    POOL_TIMEOUT_SECONDS,
    READ_TIMEOUT_SECONDS,
    WRITE_TIMEOUT_SECONDS,
    ZhipuClient,
    ZhipuError,
)


PROVIDER = "qwen"
DEFAULT_QWEN_MODEL = "qwen3-vl-plus"
QWEN_MODELS = frozenset({"qwen3-vl-plus", "qwen-vl-max"})
REGION_ENDPOINTS = MappingProxyType({
    "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
})
PROMPT_VERSION = "spike-vision-qwen-v1-project-rules"
MAX_OUTPUT_TOKENS = 4096
CONNECTION_TEST_MAX_TOKENS = 128


class QwenError(ZhipuError):
    """Qwen failure using the shared safe error shape."""


# Provider codes admitted to diagnostics are exact codes listed by Alibaba's
# error guide. Arbitrary provider text and unrecognized codes are discarded.
_PROVIDER_ERRORS: dict[str, tuple[str, str]] = {
    "InvalidApiKey": ("authentication_failed", "通义千问 API Key 无效或与所选地域不匹配。"),
    "invalid_api_key": ("authentication_failed", "通义千问 API Key 无效或与所选地域不匹配。"),
    "AccessDenied.Unpurchased": ("permission_denied", "当前百炼账户无权访问指定模型。"),
    "access_denied": ("permission_denied", "当前百炼账户无权访问指定模型。"),
    "Model.AccessDenied": ("permission_denied", "当前百炼账户无权访问指定模型。"),
    "ModelNotFound": ("model_not_found", "指定的通义千问模型不存在或当前地域不可用。"),
    "model_not_found": ("model_not_found", "指定的通义千问模型不存在或当前地域不可用。"),
    "model_not_supported": ("unsupported_model_operation", "指定的模型不支持此接口。"),
    "InvalidParameter": ("invalid_request", "百炼拒绝了请求参数，请检查图片与模型设置。"),
    "invalid_request_error": ("invalid_request", "百炼拒绝了请求参数，请检查图片与模型设置。"),
    "InternalError.Algo.InvalidParameter": ("invalid_request", "百炼无法处理本次请求参数。"),
    "InvalidParameter.NotSupportEnableThinking": ("invalid_request", "所选模型不支持本次思考模式参数。"),
    "DataInspectionFailed": ("safety_blocked", "百炼安全策略拦截了本次图片审阅。"),
    "data_inspection_failed": ("safety_blocked", "百炼安全策略拦截了本次图片审阅。"),
    "Throttling": ("rate_limited", "百炼请求达到速率限制，请稍后再试。"),
    "Throttling.RateQuota": ("rate_limited", "百炼请求达到速率限制，请稍后再试。"),
    "Throttling.BurstRate": ("rate_limited", "百炼请求速率短时过高，请稍后再试。"),
    "limit_requests": ("rate_limited", "百炼请求达到速率限制，请稍后再试。"),
    "limit_burst_rate": ("rate_limited", "百炼请求速率短时过高，请稍后再试。"),
    "Throttling.AllocationQuota": ("quota_exceeded", "百炼模型调用额度已达到上限。"),
    "insufficient_quota": ("quota_exceeded", "百炼模型调用额度已达到上限。"),
    "Arrearage": ("payment_required", "阿里云账户存在欠费，请检查账户状态。"),
    "isv.OUT_OF_SERVICE": ("payment_required", "阿里云账户余额不足，百炼服务当前已暂停。"),
}

_HTTP_ERRORS: dict[int, tuple[str, str]] = {
    400: ("invalid_request", "百炼拒绝了请求参数，请检查图片与模型设置。"),
    401: ("authentication_failed", "通义千问 API Key 无效或与所选地域不匹配。"),
    402: ("payment_required", "阿里云账户余额不足或存在欠费。"),
    403: ("permission_denied", "当前百炼账户无权访问指定模型或接口。"),
    404: ("model_not_found", "指定的通义千问模型或接口不存在。"),
    408: ("timeout", "百炼服务返回请求超时（HTTP 408）；结果未知，没有自动重试。"),
    409: ("conflict", "百炼服务因请求冲突中止了处理。"),
    413: ("input_too_large", "百炼拒绝了过大的请求，请减少图片大小或参考图数量。"),
    415: ("unsupported_image_type", "百炼不支持本次请求的图片格式。"),
    422: ("invalid_request", "百炼无法处理请求内容，请检查图片和接口参数。"),
    429: ("rate_limited", "百炼达到速率限制，请稍后再试。"),
}


def _provider_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    for value in (error.get("code"), error.get("type")):
        if isinstance(value, str) and value in _PROVIDER_ERRORS:
            return value
    return None


def _request_error(response: httpx.Response) -> QwenError:
    """Map HTTP/provider errors without returning response text or secrets."""
    status = response.status_code
    provider_code = _provider_code(response)
    if 300 <= status < 400:
        code, message = "redirect_rejected", "百炼返回了重定向；为保护 API Key，本请求已停止。"
    elif status >= 500:
        code, message = "provider_unavailable", "百炼服务暂时不可用，请稍后再试。"
    else:
        code, message = _HTTP_ERRORS.get(
            status, ("provider_error", "百炼请求失败；请按错误码检查服务状态。")
        )
    if provider_code is not None and not 300 <= status < 400:
        code, message = _PROVIDER_ERRORS[provider_code]
    diagnostic = f"HTTP {status}" + (f" · {provider_code}" if provider_code else "")
    return QwenError(code, f"{message}（{diagnostic}）", http_status=status,
                      provider_code=provider_code)


class QwenClient(ZhipuClient):
    """One-request Qwen Vision REST boundary compatible with the review UI."""

    provider = PROVIDER

    def __init__(self, api_key: str, model: str = DEFAULT_QWEN_MODEL,
                 region: str = "beijing", transport: Any = None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise QwenError("missing_api_key", "请先配置有效的通义千问 API Key。")
        if type(model) is not str or model not in QWEN_MODELS:
            raise QwenError("invalid_model", "通义千问视觉模型仅支持 qwen3-vl-plus 与 qwen-vl-max。")
        if type(region) is not str or region not in REGION_ENDPOINTS:
            raise QwenError("invalid_region", "通义千问地域仅支持 beijing 或 singapore。")

        self.model = model
        self.region = region
        self.prompt_version = PROMPT_VERSION
        self._api_key = api_key.strip()
        self.trace_callback = None
        self.last_exchange = None
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS,
                write=WRITE_TIMEOUT_SECONDS, pool=POOL_TIMEOUT_SECONDS,
            ),
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            transport=transport,
        )

    def _endpoint_url(self) -> str:
        return REGION_ENDPOINTS[self.region]

    def _request_error(self, response: httpx.Response) -> QwenError:
        return _request_error(response)

    def _thinking_options(self) -> dict[str, Any]:
        # qwen3-vl-plus is a hybrid model; the legacy qwen-vl-max is not.
        return {"enable_thinking": False} if self.model == "qwen3-vl-plus" else {}

    def _output_token_budget(self) -> int:
        # 4K leaves ample room for the bounded decision while staying below
        # both supported models' documented completion limits.
        return MAX_OUTPUT_TOKENS

    def _connection_token_budget(self) -> int:
        return CONNECTION_TEST_MAX_TOKENS

    def _json_mode_enabled(self) -> bool:
        return True

    @staticmethod
    def _as_qwen_error(error: ZhipuError) -> QwenError:
        if isinstance(error, QwenError):
            return error
        message = error.message.replace("智谱", "通义千问")
        return QwenError(
            error.code, message, http_status=error.http_status,
            provider_code=error.provider_code, timeout_phase=error.timeout_phase,
            output_details=error.output_details,
        )

    def _send(self, json_body: dict[str, Any]) -> httpx.Response:
        try:
            return super()._send(json_body)
        except ZhipuError as exc:
            error = self._as_qwen_error(exc)
            if self.last_exchange is not None and self.last_exchange.get("error"):
                self.last_exchange["error"] = str(error)
                if self.trace_callback:
                    self.trace_callback(self.last_exchange)
            raise error from exc

    def check_connection(self) -> dict[str, Any]:
        try:
            result = super().check_connection()
        except ZhipuError as exc:
            raise self._as_qwen_error(exc) from exc
        result["message"] = (
            "百炼 API Key 与模型文本调用已验证；此测试会消耗少量 token，尚未验证图片审阅。"
        )
        return result

    def analyze(self, image: ImageInput, references: list[ImageInput] | None = None,
                criteria: str = "", context: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return super().analyze(image, references, criteria, context)
        except ZhipuError as exc:
            raise self._as_qwen_error(exc) from exc

    def analyze_comparison(self, *, input_mode: str, image: ImageInput | None,
                           market_packet: dict[str, Any], criteria: str,
                           context: dict[str, Any]) -> dict[str, Any]:
        try:
            return super().analyze_comparison(
                input_mode=input_mode, image=image, market_packet=market_packet,
                criteria=criteria, context=context,
            )
        except ZhipuError as exc:
            raise self._as_qwen_error(exc) from exc
