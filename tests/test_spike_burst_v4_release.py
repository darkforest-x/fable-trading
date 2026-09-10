"""Guard confirmation timing and risk provenance in V4's simplified display.

This source contract checks the exact event engine and the intentionally narrow
reference-start change against audited V3. Native compilation is a separate gate.
No market outcomes enter these checks and this is not economic validation.
"""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / 'yoyo/evaluation/pine/spike_burst_v3_layered_display.pine'
RELEASE = ROOT / 'yoyo/evaluation/pine/spike_burst_v4.pine'


def section(source, start, end):
    assert source.count(start) == source.count(end) == 1
    return source.split(start, 1)[1].split(end, 1)[0]


def test_frozen_parent_is_the_audited_version():
    assert hashlib.sha256(PARENT.read_bytes()).hexdigest() == '5c54df7adfa0146c5e124578c5ad18d797f3372e218f07fadcb2bc631b73a2e7'


def test_features_parent_and_confirmation_events_remain_exact():
    parent, release = PARENT.read_text(), RELEASE.read_text()
    assert section(parent, '// BEGIN UNCHANGED V2 RISK HELPERS', '// END ALERT STATE') == section(release, '// BEGIN UNCHANGED V2 RISK HELPERS', '// END ALERT STATE')
    for line in parent.splitlines():
        if line.startswith(('const ', 'alertcondition(', 'hline(0,', 'plot(shownMd,', 'plot(shownSb,', 'plot(showMas ?', 'pProtect =', 'pInitial =')):
            assert line in release


def test_reference_changes_only_to_actual_confirmation_start_and_optional_exit_text():
    parent, release = PARENT.read_text(), RELEASE.read_text()
    expected = section(parent, '// BEGIN REFERENCE STATE', '// END REFERENCE STATE')
    expected = expected.replace('first early alert while flat', 'first confirmation while flat')
    expected = expected.replace('if earlySignal and trendSide == 0 and not endedThisBar', 'if confirmedSignal and trendSide == 0 and not endedThisBar')
    expected = expected.replace('if showLabels\n', 'if showLabels and showMilestones\n')
    actual = section(release, '// BEGIN REFERENCE STATE', '// END REFERENCE STATE')
    assert actual == expected
    assert 'f_risk(1, close, recentLow, atr,' in actual
    assert 'entryBar := bar_index' in actual and 'entry := close' in actual
    assert 'peakR := 0.0' in actual and 'currentR := 0.0' in actual


def test_risk_boxes_remain_exact_except_the_confirmation_provenance_caption():
    parent, release = PARENT.read_text(), RELEASE.read_text()
    expected = section(parent, '// BEGIN V2 RISK BOX DISPLAY', '// END V2 RISK BOX DISPLAY')
    expected = expected.replace('预警收盘参考', '确认收盘参考').replace('预警参考 ·', '确认参考 ·')
    assert section(release, '// BEGIN V2 RISK BOX DISPLAY', '// END V2 RISK BOX DISPLAY') == expected


def test_every_confirmation_is_visible_at_actual_bar_without_owner_filter_or_backfill():
    source = RELEASE.read_text()
    visible = section(source, '// BEGIN CONFIRMED DISPLAY', '// END CONFIRMED DISPLAY')
    assert visible.count('label.new(') == 1
    assert 'plotshape(confirmedSignal,' in visible
    assert 'if barstate.isconfirmed and confirmedSignal and showLabels' in visible
    assert 'label.new(bar_index, low - atr * 0.35, "确认 · " + str.tostring(close, format.mintick)' in visible
    assert all(term not in visible for term in ('parentBar', 'displayOwner', 'earlySignal', 'offset='))
    assert 'plot(confirmedSignal ? md : na, "确认点"' in source
    assert 'barcolor(confirmedSignal ? #00CBB1 : na, title="确认关键K线")' in source


def test_no_layer_selector_or_default_nonconfirmation_annotations():
    source = RELEASE.read_text()
    assert all(term not in source for term in ('displayMode', 'lowInterference', 'displayOwnerUpgrade', '补充观察', '补充确认'))
    assert 'showPanel = input.bool(false,' in source
    assert 'showMilestones = input.bool(false,' in source
    assert 'if showLabels and showMilestones\n                label.new(bar_index, exitPrice' in source
    for line in source.splitlines():
        if line.startswith(('plotshape(', 'barcolor(')):
            assert 'earlySignal' not in line
