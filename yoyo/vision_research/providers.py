"""Allowlisted VLM identities and regional credential boundaries.

Provider, model and region identify one credential destination. No arbitrary
base URL is accepted. The documented public OpenAI-compatible endpoints are:
https://help.aliyun.com/zh/model-studio/vision
https://help.aliyun.com/zh/model-studio/get-api-key
"""
from __future__ import annotations

from .schemas import DEFAULT_MODEL, VISION_MODELS

PROVIDERS = {
    "qwen": {
        "name": "千问", "default_model": "qwen3-vl-plus",
        "models": ["qwen3-vl-plus", "qwen-vl-max"], "default_region": "beijing",
        "regions": {"beijing": "中国内地（北京）", "singapore": "国际（新加坡）"},
        "endpoints": {
            "beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "singapore": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
        },
        "key_help_url": "https://help.aliyun.com/zh/model-studio/get-api-key",
    },
    "zhipu": {
        "name": "智谱", "default_model": DEFAULT_MODEL,
        "models": sorted(VISION_MODELS), "default_region": "default",
        "regions": {"default": "BigModel 官方服务"},
        "endpoints": {"default": "https://open.bigmodel.cn/api/paas/v4/chat/completions"},
        "key_help_url": "https://open.bigmodel.cn/usercenter/proj-mgmt/apikeys",
    },
}


def provider_for_model(model: str) -> str:
    for name, spec in PROVIDERS.items():
        if model in spec["models"]:
            return name
    raise ValueError("请选择支持的视觉模型，例如 qwen3-vl-plus 或 glm-5.3-flash")


def region_for(provider: str, region: str | None = None) -> str:
    if provider not in PROVIDERS:
        raise ValueError("不支持的模型供应商")
    value = region or PROVIDERS[provider]["default_region"]
    if value not in PROVIDERS[provider]["regions"]:
        raise ValueError("请选择与 API Key 对应的服务地域")
    return value


def profile_id(provider: str, region: str) -> str:
    return f"{provider}:{region_for(provider, region)}"


def prompt_version(model: str) -> str:
    if provider_for_model(model) == "qwen":
        from .qwen import PROMPT_VERSION
    else:
        from .zhipu import PROMPT_VERSION
    return PROMPT_VERSION


def client_identity(client) -> dict:
    """Include metadata only; never copy client credentials into research runs."""
    provider = provider_for_model(client.model)
    return {"provider": provider, "provider_region": region_for(provider, getattr(client, "region", None))}
