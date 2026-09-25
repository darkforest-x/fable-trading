"""Preserve the parent signal engines and exercise native-probe oracles."""
from pathlib import Path

from yoyo.evaluation.spike_v130_delivery import CORE, LAYER, OUTPUT, PARENT, render
from yoyo.evaluation.spike_v130_probe import build_core_probe, build_layer_probe, cases


def test_parent_detection_and_reference_engine_are_preserved():
    parent, made = PARENT.read_text(), render()
    begin, end = "f_risk(", "// ───────────── 自动下降线参数"
    assert made.split(begin, 1)[1].split(end, 1)[0] == parent.split(begin, 1)[1].split(end, 1)[0]
    begin, end = "var int v112BoxUsed", "// BEGIN V128 ROLL_HINTS"
    assert made.split(begin, 1)[1].split(end, 1)[0] == parent.split(begin, 1)[1].split(end, 1)[0]


def test_historical_trend_regions_are_preserved():
    parent, made = PARENT.read_text(), render()
    begin, end = "type V10SignalDrawing", "var array<V10SignalDrawing> v10SignalHistory"
    assert made.split(begin, 1)[1].split(end, 1)[0] == parent.split(begin, 1)[1].split(end, 1)[0]
    assert made.count("polyline.new(") == parent.count("polyline.new(")


def test_render_is_exact_versioned_source_and_default_retest():
    made = render()
    assert OUTPUT != PARENT
    assert 'shorttitle="SPIKE V13.0"' in made
    assert 'input.string("回踩确认", "信号模式"' in made
    assert made.endswith(LAYER.read_text())
    assert CORE.read_text() in made
    if OUTPUT.exists():
        assert OUTPUT.read_text() == made


def test_native_core_probe_oracle_covers_both_directions_and_failures():
    probe, expected = build_core_probe()
    assert len(expected) == len(cases()) == 20
    assert {v["code"] for v in expected} == {1, 2, 3, 4, 5, 6}
    normal = {v["name"]: v for v in expected}
    for side in (1, -1):
        assert normal[f"{side}:normal"]["confirmation_i"] == 5
        assert normal[f"{side}:limit5"]["confirmation_i"] == 5
        assert normal[f"{side}:limit4"]["code"] == 2
    assert CORE.read_text() in probe
    assert "runtime.error" in probe


def test_native_lifecycle_probe_runs_delivered_state_not_a_python_port():
    source = build_layer_probe()
    assert "v130Entry := qa_open" in source
    assert "qa_low <= v130Protection" in source
    assert 'runtime.error("lifecycle occupied confirmation must expire")' in source
    assert 'runtime.error("lifecycle entry-bar stop")' in source
    assert 'runtime.error("lifecycle raw reverse next open")' in source
    assert CORE.read_text() in source


def test_only_legacy_alerts_are_gated_in_retest_mode():
    text = render()
    parent_alerts = text.split("// BEGIN V130 RETEST CORE", 1)[0]
    for line in parent_alerts.splitlines():
        if line.startswith("alertcondition("):
            assert line.startswith("alertcondition(not v130Retest and ")
    assert 'alertcondition(v130Retest and v130Confirmed,' in text
