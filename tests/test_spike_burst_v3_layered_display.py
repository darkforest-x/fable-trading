"""Display-only invariants; no market detection, scores or Pine compiler claims.

Exact frozen V3 core bytes and original alert conditions are the signal-parity
contract. A synthetic one-way classifier exercises the display semantics;
native Pine compilation and actual chart visual QA remain separate acceptance.
"""
from itertools import product
from pathlib import Path
import hashlib
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "yoyo/evaluation/pine/spike_burst_v3_early_warning.pine"
LAYERED = ROOT / "yoyo/evaluation/pine/spike_burst_v3_layered_display.pine"
ORIGINAL_SHA = "d604051c0c633e3ee9dac4c23bbae51ce20fe9365da60aa33a760e08cf4ea882"


def read(): return LAYERED.read_text()


def between(text, begin, end):
    assert text.count(begin) == text.count(end) == 1
    return text.split(begin, 1)[1].split(end, 1)[0]


def test_original_source_is_unchanged():
    assert hashlib.sha256(ORIGINAL.read_bytes()).hexdigest() == ORIGINAL_SHA


def test_all_risk_features_alert_and_reference_state_are_exact_original_bytes():
    begin, end = "// BEGIN UNCHANGED V2 RISK HELPERS\n", "// END REFERENCE STATE\n"
    assert between(read(),begin,end) == between(ORIGINAL.read_text(),begin,end)


def test_all_original_parameters_preserved_only_display_selector_added():
    original=ORIGINAL.read_text()
    for line in original.splitlines():
        if line.startswith("const ") or "input." in line:
            assert line in read()
    added=[line for line in read().splitlines() if re.search(r"input\.(?:bool|string|int|float)\(",line) and line not in original]
    assert len(added)==1 and 'input.string("低干扰", "显示层级", options=["低干扰", "完整显示"]' in added[0]
    assert 'indicator("SPIKE V3 · 分层观察（研究版）"' in read()


def test_alert_conditions_are_exact_and_not_display_filtered():
    expected=[line for line in ORIGINAL.read_text().splitlines() if line.startswith("alertcondition(")]
    actual=[line for line in read().splitlines() if line.startswith("alertcondition(")]
    assert actual == expected and len(actual)==3
    assert all("display" not in line and "lowInterference" not in line for line in actual)


def test_original_risk_boxes_zero_lines_and_six_mas_are_unchanged():
    begin,end="// BEGIN V2 RISK BOX DISPLAY","// END V2 RISK BOX DISPLAY"
    assert between(read(),begin,end)==between(ORIGINAL.read_text(),begin,end)
    for line in ORIGINAL.read_text().splitlines():
        if line.startswith(("hline(0,","plot(shownMd,","plot(shownSb,","plot(showMas ?",
                            "plot(earlySignal or confirmedSignal", "pProtect =", "pInitial =", "pEntry =", "fill(pInitial")):
            assert line in read()
    assert "plot.style_histogram" not in read()


def test_complete_mode_restores_original_labels_and_panel():
    begin,end="// One main-chart label on same-bar early+confirmation.","// BEGIN V2 RISK BOX DISPLAY"
    full=between(read(),begin,end).replace(" and not lowInterference", "")
    assert full==between(ORIGINAL.read_text(),begin,end)
    begin="var table panel = table.new(position.top_right, 2, 5, bgcolor=color.new(chart.bg_color, 8), border_width=0)"
    full=between(read(),begin,"// Low-interference panel separates").replace(" and not lowInterference", "")
    original=between(ORIGINAL.read_text(),begin,"// These are local study conditions.")
    assert full==original
    assert 'barcolor(lowInterference ? displayPrimaryColor : (confirmedSignal ? #00CBB1 : earlySignal ? #D6A14A : na)' in read()


def test_added_mutations_cannot_write_core_state_or_rewire_signals():
    original=ORIGINAL.read_text()
    # All original assignments are still present. New assignments can only
    # update display-owned variables, never detection, risk or reference.
    original_assignments=[line.strip() for line in original.splitlines() if re.search(r"\w+\s*(?::=|\+=)",line)]
    new_assignments=[line.strip() for line in read().splitlines() if re.search(r"\w+\s*(?::=|\+=)",line)]
    for line in original_assignments: assert new_assignments.count(line)==original_assignments.count(line)
    for line in new_assignments:
        if line not in original_assignments: assert re.match(r"display\w+\s*(?::=|\+=)",line)
    state=between(read(),"// BEGIN LAYERED DISPLAY STATE","// END LAYERED DISPLAY STATE")
    for line in state.splitlines():
        if ":=" in line or "+=" in line: assert line.startswith("        ")
    assert "if barstate.isconfirmed\n" in state


def test_owner_upgrade_requires_the_live_reference_original_parent():
    assert "bool displayOwnerUpgrade = confirmedSignal and trendSide != 0 and not na(parentBar) and parentBar == entryBar" in read()
    assert "bool displayAdditionalConfirmation = confirmedSignal and not displayOwnerUpgrade" in read()
    assert "bool displayAdditionalObservation = earlySignal and trendSide != 0 and not referenceStarted" in read()
    assert "if displayOwnerUpgrade\n        displayReferenceConfirmed := true" in read()
    assert "if referenceStarted\n        displayReferenceId += 1" in read()
    assert "displayReferenceConfirmed := false" in read()


def test_every_original_event_has_a_visible_current_bar_marker_in_low_mode():
    section=between(read(),"// Every original event keeps", "// One main-chart label on same-bar early+confirmation.")
    assert "bool displayEvent = earlySignal or confirmedSignal" in read()
    assert "if barstate.isconfirmed and lowInterference and displayEvent and showLabels\n" in section
    assert section.count("label.new(")==1
    assert "label.new(bar_index, low, displayText" in section
    assert "style=referenceStarted ? label.style_label_up : label.style_none" in section
    assert "label.style_circle" not in section
    assert "size=referenceStarted ? size.small : size.tiny" in section
    assert "实际事件收盘 " in section and "str.tostring(close, format.mintick)" in section
    assert 'str.format_time(time_close, "yyyy-MM-dd HH:mm", "Asia/Shanghai")' in section
    assert "tooltip=displayTip" in section
    assert "label.new(parentBar" not in section and "label.set_" not in section


def test_display_highlights_only_reference_start_or_its_own_upgrade():
    assert "color displayPrimaryColor = displayOwnerUpgrade ? #00CBB1 : referenceStarted ? #D6A14A : na" in read()
    assert "displayAdditionalConfirmation ? #00" not in read()
    assert "可能是独立机会" in read() and "不代表当前参考通过确认" in read()


def test_raw_data_window_events_and_new_price_owner_metadata_are_preserved():
    for line in ORIGINAL.read_text().splitlines():
        if line.startswith("plot(") and "display=display.data_window" in line: assert line in read()
    for caption in ("原事件实际收盘价","本图参考编号","当前参考owner原父编号","事件原父编号",
                    "显示 · 观察起点","显示 · 当前参考升级","显示 · 补充观察","显示 · 补充确认"):
        assert '"'+caption+'"' in read()


def classify(early,child,started,active,parent,owner):
    """Display projection only; inputs are supplied frozen events/reference."""
    owned=bool(child and active and parent is not None and parent==owner)
    return dict(event=bool(early or child),owner_upgrade=owned,
                extra_observation=bool(early and active and not started),
                extra_confirmation=bool(child and not owned),highlight=bool(started or owned))


@pytest.mark.parametrize("early,child", product((False,True),repeat=2))
def test_public_event_identity_never_changes_with_display_ownership(early,child):
    for active,started,same_parent in product((False,True),repeat=3):
        row=classify(early,child,started,active,10 if same_parent else 20,10)
        assert row["event"] == (early or child)
        assert row["extra_confirmation"] + row["owner_upgrade"] == int(child)
        if child and not same_parent: assert not row["owner_upgrade"]


@pytest.mark.parametrize("args,expected",[
    ((True,False,True,True,10,10),(True,False,False,False,True)),
    ((False,True,False,True,10,10),(True,True,False,False,True)),
    ((True,True,False,True,22,10),(True,False,True,True,False)),
    ((False,True,False,False,10,10),(True,False,False,True,False)),
    ((True,False,False,False,20,10),(True,False,False,False,False)),
])
def test_current_parent_other_parent_exit_and_invalid_risk_are_distinct(args,expected):
    row=classify(*args)
    assert tuple(row.values())==expected


def test_owner_quality_reset_and_upgrade_are_one_way_and_prefix_stable():
    events=[(True,False,True,True,10,10),(True,True,False,True,22,10),
            (False,True,False,True,10,10),(False,True,False,False,10,10),
            (True,False,True,True,40,40)]
    def project(rows):
        identifier,confirmed=0,False
        out=[]
        for row in rows:
            view=classify(*row)
            if row[2]: identifier,confirmed=identifier+1,False
            if view['owner_upgrade']: confirmed=True
            out.append((identifier,confirmed,view))
        return out
    result=project(events)
    assert [x[1] for x in result]==[False,False,True,True,False]
    for length in range(1,len(events)+1): assert project(events[:length])==result[:length]


def test_historical_event_anchors_are_independent_of_label_gc_and_visibility():
    anchors=[line for line in read().splitlines() if line.startswith("plotshape(")]
    assert len(anchors)==1
    line=anchors[0]
    assert "plotshape(lowInterference and (earlySignal or confirmedSignal)," in line
    assert "force_overlay=true" in line and "location=location.belowbar" in line
    assert "style=shape.circle" in line and "size=size.tiny" in line
    assert "showLabels" not in line and "offset" not in line
    assert "最近300个标签对象限制" in read()
    assert "较旧事件仍可查数据窗口" in read()
    assert "max_labels_count=300" in read()


def test_conservative_plot_budget_remains_below_64():
    # Each plot contributes its series; any explicit pane color is conservatively
    # treated as another series. Dynamic shape color is also charged, while
    # constant widths/styles do not add series. Hlines/labels/tables cost no plot.
    lines=read().splitlines()
    plots=[line for line in lines if re.match(r"(?:\w+ = )?plot\(",line)]
    explicit_colors=[line for line in plots if "display=display.pane" in line]
    shapes=[line for line in lines if line.startswith("plotshape(")]
    alerts=[line for line in lines if line.startswith("alertcondition(")]
    barcolors=[line for line in lines if line.startswith("barcolor(")]
    fills=[line for line in lines if line.startswith("fill(")]
    budget=len(plots)+len(explicit_colors)+2*len(shapes)+len(alerts)+len(barcolors)+len(fills)
    assert len(plots)==33 and len(shapes)==1 and len(alerts)==3
    assert budget==52 and budget<64
