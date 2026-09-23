"""Reference boxes must exclude launch and preserve per-event provenance."""

import json

import pytest

from yoyo.vision_research.reference_pack import SPEC, validate_bounds


@pytest.mark.parametrize("change", [
    {"first_launch_offset": -2},
    {"core_start_offset": -4, "core_end_offset": -2},
    {"window_end_i": 98},
    {"window_start_i": 96},
])
def test_rejects_launch_inside_box_or_invalid_core(change):
    item = {"source_anchor_i": 100, "window_start_i": 90, "window_end_i": 102,
            "core_start_offset": -5, "core_end_offset": -2, "first_launch_offset": -1}
    with pytest.raises(ValueError):
        validate_bounds({**item, **change})


def test_frozen_decisions_tighten_short_cores_without_adding_launch():
    items = json.loads(SPEC.read_text())["items"]
    for item in items:
        first, last, launch = validate_bounds(item)
        assert launch == last + 1
        assert len(item["expected_source_prefix_sha256"]) == 64
    changed = {item["symbol"]: item for item in items if "previous_core_start_offset" in item}
    assert set(changed) == {"FIL_USDT_SWAP", "TRUST_USDT_SWAP"}
    for item in changed.values():
        assert item["core_start_offset"] == item["previous_core_start_offset"] + 1
        assert item["core_end_offset"] == item["previous_core_end_offset"]
