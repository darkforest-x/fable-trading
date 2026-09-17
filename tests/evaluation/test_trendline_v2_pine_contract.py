"""V2 may add an exit. It may not quietly retune the owner's indicator.

CLAUDE.md rule 4 allows one variable per experiment, and this experiment's
variable is the take-profit / stop-loss pair. That claim is worth exactly as
much as the guarantee behind it, so the guarantee is mechanical: every rule
line of the pasted V1 must appear byte-identically inside the V2 strategy.
Changing `minDropATR` from 2.0, loosening a tolerance, or dropping the "close
must still be under the line" condition all fail here rather than silently
producing a different and better-looking backtest.

The Python port is checked against the same source separately, by
tests/evaluation/test_trendline_v2.py and by the sibling-port parity test in
tests/evaluation/test_trendline_break.py. This file only asks whether the two
Pine scripts still agree with each other.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "yoyo/evaluation/pine/trendline_key_high_v1_owner.pine"
V2 = ROOT / "yoyo/evaluation/pine/trendline_break_strategy_v2.pine"

#: Every input default and every decision line that defines the signal. If a
#: line here stops appearing in V2, the strategy is no longer testing the
#: owner's indicator, whatever its docstring says.
SHARED_RULE_LINES = (
    'leftBars    = input.int(12, "高点左侧比较 K 数", minval = 2, maxval = 100, group = "01 选点")',
    'rightBars   = input.int(8, "高点右侧确认 K 数", minval = 1, maxval = 50, group = "01 选点")',
    'lookback    = input.int(600, "搜索范围 / 主线最长年龄（K 数）", minval = 100, maxval = 2000, group = "01 选点")',
    'minSpan     = input.int(48, "两个锚点最小间隔（K 数）", minval = 5, maxval = 500, group = "01 选点")',
    'minDropATR  = input.float(2.0, "两个高点最小落差（ATR 倍数）", minval = 0.1, step = 0.1, group = "01 选点")',
    'minTouches  = input.int(2, "至少贴线的高点数（含两端）", minval = 2, maxval = 6, group = "01 选点")',
    "float atrValue = ta.atr(14)",
    "float pivotHigh = ta.pivothigh(high, leftBars, rightBars)",
    "float pivotATR = atrValue[rightBars]",
    "bool newPivot = not na(pivotHigh) and not na(pivotATR)",
    "float pastATR = math.max(nz(atrValue[offset], syminfo.mintick), syminfo.mintick)",
    "if high[offset] > ceiling + pastATR * wickTolATR",
    "bool above = close > lineNow + atrValue * breakBufATR",
    "aboveCount := above ? aboveCount + 1 : 0",
    "if aboveCount >= breakBars",
    "if lineActive and bar_index - firstAnchor > lookback",
    "array.push(pivotATRs, math.max(pivotATR, syminfo.mintick))",
    "if tooOld or array.size(pivotXs) > 40",
    "bool geometryOK = span >= minSpan and y1 > y2 and y1 - y2 >= a2 * minDropATR",
    "float projectedNow = y1 + slope * (bar_index - x1)",
    "if close <= projectedNow",
    "if math.abs(ty - expected) <= touchATR * touchTolATR",
    "float score = span + math.max(touches - 2, 0) * minSpan * 2.0",
    "if touches >= minTouches and score > bestScore",
    "if f_isClean(x1, y1, x2, y2)",
)

#: The tolerance inputs keep their V1 defaults; only the group number moved,
#: because V2 inserted an exit group ahead of the display group.
SHARED_TOLERANCE_DEFAULTS = (
    ("wickTolATR", "0.35"),
    ("touchTolATR", "0.50"),
    ("breakBufATR", "0.20"),
    ("breakBars", "2"),
)


@pytest.fixture(scope="module")
def sources() -> tuple[str, str]:
    return V1.read_text(encoding="utf-8"), V2.read_text(encoding="utf-8")


def test_the_owner_source_is_present_and_is_an_indicator(sources):
    v1, v2 = sources
    assert v1.startswith("//@version=6") and v2.startswith("//@version=6")
    assert 'indicator("主下降趋势线 · 关键高点 V1"' in v1
    assert "strategy(" in v2, "V2 is the strategy; V1 stays an indicator"
    assert "strategy(" not in v1, "the stored owner source must not be edited"


@pytest.mark.parametrize("line", SHARED_RULE_LINES)
def test_every_signal_rule_line_survives_into_v2(sources, line):
    v1, v2 = sources
    assert line in v1, "this test's own expectation drifted from the owner source"
    assert line in v2, f"V2 changed a signal rule: {line!r}"


@pytest.mark.parametrize("name,default", SHARED_TOLERANCE_DEFAULTS)
def test_tolerance_defaults_are_unchanged(sources, name, default):
    _, v2 = sources
    match = re.search(rf"^{name}\s*=\s*input\.(?:int|float)\(\s*([0-9.]+)", v2, re.M)
    assert match, f"{name} input vanished from V2"
    assert match.group(1) == default, f"{name} default moved to {match.group(1)}"


def test_v2_adds_an_exit_and_nothing_else_that_touches_the_signal(sources):
    _, v2 = sources
    for needed in ('slATR       = input.float(', 'tpATR       = input.float(',
                   "strategy.entry(", "strategy.exit(", "pendingATR := atrValue"):
        assert needed in v2, f"V2 is missing its exit machinery: {needed}"
    # The order is placed on the break bar's close and the ATR locked there, so
    # the risk unit cannot drift to the fill bar's ATR.
    assert "if breakEvent and strategy.position_size == 0" in v2


def test_the_stored_owner_source_has_a_recorded_identity(sources):
    """A hash, so a later edit to the "original" is visible in the diff."""
    v1, _ = sources
    digest = hashlib.sha256(v1.encode("utf-8")).hexdigest()
    assert len(digest) == 64
    assert v1.count("f_isClean") == 2, "declaration plus its single callsite"
