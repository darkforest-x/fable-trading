"""Synthetic semantic checks for the bounded V12 pairing oracle."""

from __future__ import annotations

from yoyo.evaluation.spike_v12_pairing_reference import (
    BreakSpec,
    PairBar,
    PairingParams,
    replay_pairing,
)


def _bars(
    end: int,
    *,
    v9_at=(),
    boxes=None,
    above=None,
    gaps=(),
):
    boxes = boxes or {}
    above = above or {}
    return [
        PairBar(
            i=i,
            v9=i in set(v9_at),
            box_id=boxes.get(i),
            above=above.get(i, {}),
            gap=i in set(gaps),
        )
        for i in range(end)
    ]


def test_late_v9_after_legacy_window_joins_only_with_held_pairing():
    bars = _bars(11, v9_at=(10,), boxes={10: "box-1"})
    spec = BreakSpec("L", break_i=0)

    held = replay_pairing(bars, chart_breaks=[spec])
    assert [(event.line_id, event.joint_i, event.order) for event in held.events] == [("L", 10, "break-first")]

    legacy = replay_pairing(
        bars,
        chart_breaks=[spec],
        params=PairingParams(mode="window", hold_pair=False, window_bars=6),
    )
    assert legacy.events == []


def test_touch_or_cross_cancels_permanently_without_resurrection():
    bars = _bars(
        6,
        v9_at=(3, 5),
        boxes={3: "box-1", 5: "box-2"},
        above={2: {"L": False}},
    )
    result = replay_pairing(bars, chart_breaks=[BreakSpec("L", break_i=0)])
    assert result.events == []
    assert any(row["line_id"] == "L" and row["reason"] == "touch_or_cross" for row in result.trace[2]["canceled"])


def test_gap_and_life_expiry_are_permanent():
    gapped = replay_pairing(
        _bars(5, v9_at=(4,), boxes={4: "box-1"}, gaps=(2,)),
        chart_breaks=[BreakSpec("L", break_i=0)],
    )
    assert gapped.events == []
    assert any(row["line_id"] == "L" and row["reason"] == "gap" for row in gapped.trace[2]["canceled"])

    expired = replay_pairing(
        _bars(4, v9_at=(3,), boxes={3: "box-1"}),
        chart_breaks=[BreakSpec("L", break_i=0)],
        params=PairingParams(life_bars=2),
    )
    assert expired.events == []
    assert any(row["line_id"] == "L" and row["reason"] == "expiry" for row in expired.trace[3]["canceled"])


def test_samebar_break_uses_original_active_box_path():
    bars = _bars(4, v9_at=(0,), boxes={0: "box-1", 1: "box-1", 2: "box-1"})
    result = replay_pairing(bars, chart_breaks=[BreakSpec("L", break_i=2)])
    assert len(result.events) == 1
    assert result.events[0].order == "box-first"
    assert result.events[0].joint_i == 2


def test_one_break_cannot_be_reused_by_a_second_box_but_a_new_break_can_join():
    bars = _bars(
        6,
        v9_at=(2, 5),
        boxes={2: "box-1", 5: "box-2"},
    )
    result = replay_pairing(
        bars,
        chart_breaks=[BreakSpec("L", break_i=0), BreakSpec("M", break_i=3)],
    )
    assert [(event.line_id, event.box_id) for event in result.events] == [("L", "box-1"), ("M", "box-2")]


def test_htf_break_is_not_known_before_visible_bar():
    bars = _bars(
        7,
        v9_at=(3, 5),
        boxes={3: "old-box", 5: "new-box"},
    )
    spec = BreakSpec("H", break_i=2, source="htf", visible_bar=4)
    result = replay_pairing(bars, htf_breaks=[spec])
    assert len(result.events) == 1
    assert result.events[0].source == "htf"
    assert result.events[0].visible_bar == 4
    assert result.events[0].joint_i == 5
    assert result.events[0].order == "break-first"
    assert replay_pairing(bars[:4], htf_breaks=[spec]).events == []


def test_prefix_invariance_for_late_chart_and_htf_events():
    bars = _bars(12, v9_at=(10,), boxes={10: "box-1"})
    specs = [BreakSpec("L", break_i=0), BreakSpec("H", break_i=2, source="htf", visible_bar=4)]
    full = replay_pairing(bars, chart_breaks=[specs[0]], htf_breaks=[specs[1]])
    for end in range(1, len(bars) + 1):
        prefix = replay_pairing(bars[:end], chart_breaks=[specs[0]], htf_breaks=[specs[1]])
        expected = [event for event in full.events if event.joint_i < end]
        assert prefix.events == expected
        assert prefix.trace == full.trace[:end]
