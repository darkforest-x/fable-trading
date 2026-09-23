"""Validate research images and remove file metadata before provider transmission.

Images are never silently resized: dense candle geometry must stay observable.
Source: https://pillow.readthedocs.io/en/stable/reference/Image.html
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from .schemas import ImageInput

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 6_000_000
DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$")


def image_from_bytes(data: bytes, name: str = "chart.png") -> ImageInput:
    """Decode actual format, orient pixels, and encode a metadata-free PNG."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("图片为空或超过 8 MB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("仅支持 PNG、JPEG 和 WEBP 图片")
                if getattr(source, "n_frames", 1) != 1:
                    raise ValueError("请使用静态图片")
                width, height = source.size
                if width < 32 or height < 32 or width * height > MAX_PIXELS:
                    raise ValueError("图片须至少 32×32，且不超过 600 万像素")
                source.load()
                oriented = ImageOps.exif_transpose(source).convert("RGBA")
                clean = Image.new("RGB", oriented.size, "white")
                clean.paste(oriented, mask=oriented.getchannel("A"))
                buffer = io.BytesIO()
                clean.save(buffer, format="PNG")
                pixels = buffer.getvalue()
                width, height = clean.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无法解码或尺寸过大") from exc
    if len(pixels) > MAX_IMAGE_BYTES:
        raise ValueError("无损处理后的图片超过 8 MB，请裁去无关区域")
    clean_name = re.sub(r"[\x00-\x1f/\\]", "_", name)[:160] or "chart.png"
    return ImageInput(clean_name, "image/png", pixels,
                      hashlib.sha256(pixels).hexdigest(), width, height)


def image_from_data_url(value: str, name: str = "chart.png") -> ImageInput:
    match = DATA_URL.fullmatch(value)
    if not match:
        raise ValueError("图片必须是 PNG、JPEG 或 WEBP 的 Base64 数据")
    if len(match.group(2)) > (MAX_IMAGE_BYTES * 4 // 3) + 4:
        raise ValueError("单张图片不能超过 8 MB")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("图片数据不完整") from exc
    return image_from_bytes(data, name)
