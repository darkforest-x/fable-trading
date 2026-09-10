"""Keep V4 display suppression separate from the frozen V3 signal/risk logic.

Read only Pine source. Native compilation and chart checks are separate. No
market outcomes or inferred winning/losing events enter this display change.
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


def test_actual_events_reference_risk_and_owner_state_remain_exact():
    parent, release = PARENT.read_text(), RELEASE.read_text()
    for start, end in [('// BEGIN UNCHANGED V2 RISK HELPERS', '// END LAYERED DISPLAY STATE'),
                       ('// BEGIN V2 RISK BOX DISPLAY', '// END V2 RISK BOX DISPLAY')]:
        assert section(parent, start, end) == section(release, start, end)
    for line in parent.splitlines():
        if line.startswith(('const ', 'alertcondition(', 'hline(0,', 'plot(shownMd,', 'plot(shownSb,', 'plot(showMas ?', 'pProtect =', 'pInitial =', 'pEntry =')):
            assert line in release


def test_full_mode_preserves_every_original_label_and_raw_data_window():
    parent, release = PARENT.read_text(), RELEASE.read_text()
    start, end = '// One main-chart label', '// BEGIN V2 RISK BOX DISPLAY'
    assert section(parent, start, end) == section(release, start, end)
    for line in parent.splitlines():
        if line.startswith('plot(') and 'display=display.data_window' in line:
            assert line in release
    assert 'plotshape(not lowInterference and (earlySignal or confirmedSignal)' in release


def test_default_has_one_warning_label_and_current_owner_confirmation_dot_only():
    source = RELEASE.read_text()
    focused = section(source, '// Default view shows', '// One main-chart label')
    assert focused.count('label.new(') == 1
    assert 'if barstate.isconfirmed and lowInterference and referenceStarted and showLabels' in focused
    assert 'label.new(bar_index, low, "预警\\n" + str.tostring(close, format.mintick)' in focused
    assert 'plotshape(lowInterference and displayOwnerUpgrade' in focused
    assert 'location=location.abovebar' in focused
    assert 'label.new(parentBar' not in focused and 'offset=' not in focused
    assert '补充' not in focused and '参考升级' not in focused
    assert '(lowInterference ? (referenceStarted or displayOwnerUpgrade) : (earlySignal or confirmedSignal)) ? md : na' in source


def test_hidden_candidates_are_disclosed_and_not_called_filtered_losses():
    source = RELEASE.read_text()
    assert '其他原信号在本模式隐藏' in source and '隐藏不代表已经证明是噪音' in source
    assert '实际信号和警报条件不变' in source
    focused_panel = section(source, '// Focused panel describes', '// These are local study conditions.')
    assert '补充' not in focused_panel and '最新收盘发现' not in focused_panel
    assert '完整显示中查看' in focused_panel and '青点 = 所属确认' in focused_panel
