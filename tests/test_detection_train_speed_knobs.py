"""Speed-knob parsing for detection train (no GPU required)."""
from __future__ import annotations

from src.detection.train import DEFAULT_CACHE, DEFAULT_WORKERS, SAFE_AUG, _parse_cache


def test_defaults_are_speed_oriented() -> None:
    assert DEFAULT_WORKERS >= 4
    assert DEFAULT_CACHE == "disk"
    # iron rule: temporal / color-breaking augs stay off
    assert SAFE_AUG["fliplr"] == 0.0
    assert SAFE_AUG["flipud"] == 0.0
    assert SAFE_AUG["mosaic"] == 0.0
    assert SAFE_AUG["mixup"] == 0.0
    assert SAFE_AUG["hsv_h"] == 0.0


def test_parse_cache() -> None:
    assert _parse_cache("false") is False
    assert _parse_cache("off") is False
    assert _parse_cache("disk") == "disk"
    assert _parse_cache("true") == "disk"
    assert _parse_cache("ram") == "ram"
