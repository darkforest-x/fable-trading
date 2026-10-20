"""Guard the V2.1 delta: preserve the V2.0 engines and reserve drawing capacity.

Source contracts are not Pine execution. Native checks separately exercise the
actual event accumulator and compare its full snapshots across chart periods.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PINE = ROOT / "yoyo/evaluation/pine"
SOURCE = (PINE / "monday_range_v2_1.pine").read_text()
BASE = (PINE / "monday_range_v2_0.pine").read_text()
FANSHEN = "// === Fanshen v1.1: unchanged formulas, namespaced identifiers ==============="
HOURLY = "// === Fixed 1H signal event buffer and vertical rays ========================"


def test_entire_existing_oscillator_and_alert_body_is_preserved():
    assert SOURCE.split(FANSHEN)[1].split(HOURLY)[0].rstrip() == BASE.split(FANSHEN)[1].rstrip()


def test_range_delta_is_limited_to_version_timeframe_guard_and_budget():
    body = SOURCE[SOURCE.index("string timeGroup ="):].split(FANSHEN)[0]
    body = body.replace("V2.1", "V2.0")
    body = body.replace(
        'if barstate.isfirst and not chart.is_standard\n    runtime.error("请使用标准时间 K 线。")',
        'if barstate.isfirst and (not chart.is_standard or timeframe.in_seconds() > 86400)\n    runtime.error("请使用标准时间 K 线，周期不超过 1 日。")',
    )
    body = body.replace("rangeChartSupported and dow == dayofweek.monday", "dow == dayofweek.monday")
    body = body.replace("rangeChartSupported and (dow == dayofweek.saturday or dow == dayofweek.sunday)", "dow == dayofweek.saturday or dow == dayofweek.sunday")
    body = body.replace("barcolor(rangeChartSupported ? candleCol : na,", "barcolor(candleCol,")
    body = body.replace("plotcandle(rangeChartSupported ? open : na,", "plotcandle(open,")
    body = body.replace(" - h1LineReserve)", ")")
    body = body.replace("if rangeChartSupported and not isWeekend", "if not isWeekend")
    body = body.replace('"V2.0 · 区间基底 v1.11" + (rangeChartSupported ? "" : "（周一区间需≤1D）") + " · "', '"V2.0 · 区间基底 v1.11 · "')
    assert body == BASE[BASE.index("string timeGroup ="):].split(FANSHEN)[0]


def test_fixed_hourly_requests_use_complete_state_and_closed_snapshot():
    hourly = SOURCE.split(HOURLY)[1]
    assert '"60", confirmedH1Engine(), lookahead=barmerge.lookahead_on' in hourly
    assert '"60", h1EnginePair(), lookahead=barmerge.lookahead_off' in hourly
    assert "latestH1.closeAt <= h1ObservedAt ? latestH1 : previousH1" in hourly
    assert "if confirmedAt <= h1ObservedAt" in hourly
    assert "h1ObservedAt = math.min(timenow, time_close)" in hourly
    assert "barstate.islast or barstate.islastconfirmedhistory ? value : na" in hourly
    assert "fs_wvf" not in hourly
    assert "line.new(at, anchor, at, anchor + direction * syminfo.mintick" in hourly
    assert 'input.bool(true, "显示 1H 勾叉竖射线"' in SOURCE


def test_drawing_capacity_for_every_supported_retention_setting():
    assert "500 - 2 * keepWeeks - 24 - 19 - 2 - h1LineReserve" in SOURCE
    # The legal 12 Fib ratios produce at most 24 distinct lines per week.
    for weeks in range(1, 66):
        for rays in (0, *range(1, 201)):
            for per_week in range(1, 25):
                budget = min(weeks * per_week, 500 - 2 * weeks - 24 - 19 - 2 - rays)
                assert budget >= per_week
                assert 2 * weeks + budget + 24 + 19 + rays <= 498
