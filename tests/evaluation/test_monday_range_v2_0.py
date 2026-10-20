"""Guard the owner-corrected V2.0 composition against dropping V1.11 features.

This is a source contract, not a Pine interpreter. Only pane-routing, the
status/version label and Fanshen plot formatting/identifier prefixes may differ from the two
original bodies. Native compilation and value parity are verified separately.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PINE = ROOT / "yoyo/evaluation/pine"
SOURCE = PINE / "monday_range_v2_0.pine"
FANSHEN_MARKER = "// === Fanshen v1.1: unchanged formulas, namespaced identifiers ===============\n"
LEX = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|\b[A-Za-z_][A-Za-z_0-9]*\b')


def test_v20_preserves_the_entire_v111_range_body():
    source = SOURCE.read_text()
    parent = (PINE / "monday_range_v1_11.pine").read_text()
    body = source[source.index("string timeGroup ="):].split("\n" + FANSHEN_MARKER)[0]
    restored = body.replace(", force_overlay=true", "").replace(
        '"V2.0 · 区间基底 v1.11 · "', '"v1.11 · "'
    )
    assert restored == parent[parent.index("string timeGroup ="):]
    assert body.count(", force_overlay=true") == 11
    assert "table.new(position.bottom_left, 1, 3, border_width=0, force_overlay=true)" in body


def test_v20_preserves_fanshen_signals_plots_and_alerts():
    body = SOURCE.read_text().split(FANSHEN_MARKER)[1]
    parent = (PINE / "owner_fanshen_v1_1.pine").read_text().split("\n", 2)[2]
    restored = LEX.sub(
        lambda m: m.group()[3:] if m.group().startswith("fs_") else m.group(), body
    )
    assert restored.replace(", format=format.price, precision=2", "") == parent


def test_v20_keeps_single_pane_declaration_and_closed_15m_requests():
    source = SOURCE.read_text()
    assert source.count("\nindicator(") == 1
    assert 'shorttitle="区间翻身 V2.0", overlay=false' in source
    assert 'timeframe=""' not in source and 'timeframe_gaps=' not in source
    assert source.count("request.security(") == 2
    assert '"15", confirmedKeyEngine(), lookahead=barmerge.lookahead_on' in source
    assert 'latestKey.closeAt <= observedAt ? latestKey : previousKey' in source


def test_symbol_price_precision_is_not_replaced_by_oscillator_precision():
    source = SOURCE.read_text()
    declaration = source.split("indicator(", 1)[1].split("\n\n", 1)[0]
    assert "precision=" not in declaration and "format=" not in declaration
    fanshen = source.split(FANSHEN_MARKER)[1]
    plots = [line for line in fanshen.splitlines() if line.startswith(("plot(", "plotshape(", "plotchar("))]
    assert len(plots) == 10
    assert all(", format=format.price, precision=2)" in line for line in plots)
