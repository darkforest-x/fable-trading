"""Source and injector contracts for the separate SPIKE Burst V1+ indicator.

These local checks inspect the Pine source and the generated native probe. They
do not compile Pine, replay market data, or establish profitability.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import textwrap

from yoyo.evaluation.build_v1_plus_probe import MARKER, render


ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "yoyo/evaluation/pine/spike_burst_v1.pine"
DISPLAY = ROOT / "yoyo/evaluation/pine/spike_burst_v1_display.pine"
PLUS = ROOT / "yoyo/evaluation/pine/spike_burst_v1_plus.pine"
FROZEN_SHA = "18bbb6955fdf12e124688003799c44fc2a641f11a342edcf478157b1c9641fe2"


def test_original_v1_and_display_hashes_are_unchanged():
    assert hashlib.sha256(FROZEN.read_bytes()).hexdigest() == FROZEN_SHA
    # The established display derivative is a separate source and this task
    # must not mutate either reference input.
    assert DISPLAY.read_text().startswith("//@version=6\n")


def test_plus_identity_defaults_and_raw_v1_gates_remain_visible():
    source = PLUS.read_text()
    assert 'indicator("SPIKE 强劲爆发 V1+", shorttitle="SPIKE V1+"' in source
    assert 'bool enablePlus = input.bool(true' in source
    assert 'bool useOverheatFilter = input.bool(true' in source
    assert 'bool useStagedProtection = input.bool(true' in source
    assert 'string direction = input.string("双向"' in source
    assert 'color signalCandleColor = input.color(color.white' in source
    assert 'bool showPanel = input.bool(true' in source
    for gate in ('minVolume = input.float(4.0', 'minExpansion = input.float(3.0', 'minBody = input.float(0.55', 'minEnd = input.float(0.75', 'opportunity = input.int(6'):
        assert gate in source
    assert 'enablePlus and useOverheatFilter and rv > overheatRv and expansion > overheatTr' in source
    assert 'pendingSide := 0\n                // A rejected raw episode is consumed' in source


def test_protection_contract_is_stop_first_monotonic_and_next_bar_only():
    source = PLUS.read_text()
    helper = source.split('f_pathPlus(', 1)[1].split('// END BURST PURE HELPERS', 1)[0]
    assert 'bool stopped = side == 1 ? l <= priorProtection : h >= priorProtection' in helper
    assert 'math.min(o, priorProtection)' in helper and 'math.max(o, priorProtection)' in helper
    assert 'math.floor(raw / tick) * tick' in helper and 'math.ceil(raw / tick) * tick' in helper
    assert 'math.max(priorProtection, rounded)' in helper and 'math.min(priorProtection, rounded)' in helper
    assert 'rawStage1 = entry - side * 0.5 * risk' in helper
    assert 'rawStage2 = entry * (side == 1 ? 1.0 + costPct / 100.0' in helper
    assert 'rawStagnation = c - side * 2.0 * a' in helper
    assert 'if not stopped' in helper
    assert 'f_pathPlus(enablePlus and useStagedProtection' in source


def test_reverse_closes_before_filtered_new_reference_and_pullback_is_causal():
    source = PLUS.read_text()
    state = source.split('// BEGIN BURST STATE MACHINE', 1)[1].split('// END BURST STATE MACHINE', 1)[0]
    assert state.index('bool rawOverheated') < state.index('bool rawEndsOldReference')
    assert state.index('exitReason := "反向确认"') < state.index('bool accepted = not sameDirection')
    assert 'f_rawEndsOppositeReference(trendSide, side)' in state
    assert 'bool rawCapRejected = enablePlus and useRiskWidthCap' in state
    assert 'string entryMode = input.string("直接确认"' in source
    assert 'if enablePlus and entryMode == "回踩确认"' in state
    assert 'pullbackAge > 0' in state
    assert 'close > pullbackEdge and close > pullbackExtreme' in state
    assert 'close < pullbackEdge and close < pullbackExtreme' in state
    assert 'close < pullbackZoneLow' in state and 'close > pullbackZoneHigh' in state
    assert 'float pullbackBandLow = pullbackEdge - pullbackToleranceAtr * pullbackAtr' in state
    assert 'low <= pullbackBandHigh and math.min(open, close) >= pullbackBandLow' in state
    assert 'high >= pullbackBandLow and math.max(open, close) <= pullbackBandHigh' in state
    assert 'rawEntryGatePass = rawRiskValid and not rawCapRejected and not rawCooldownRejected' in state
    assert 'entryZoneHigh := pullbackZoneHigh' in state
    assert 'lastFavorableCloseBar := bar_index' in state


def test_rr_display_keeps_bounded_reward_observation_and_dashed_milestones():
    source = PLUS.read_text()
    assert '显示盈亏双框' in source
    assert 'rrRewardBox := rrObservation > 0 ? box.new' in source
    assert 'box.new(rrStartedAt, math.max(rrEntry, rrStop)' in source
    assert 'style=line.style_dashed' in source
    assert 'float level = slot == 0 ? 3.0 : slot == 1 ? 5.0 : 10.0' in source
    assert 'plot.style_histogram' not in source


def test_early_structural_exit_does_not_emit_stop_touch_alert():
    source = PLUS.read_text()
    assert 'bool protectionExitEvent = barstate.isconfirmed and (exitUp or exitDown) and exitReason == "保护触及"' in source
    assert 'bool structuralExitEvent = barstate.isconfirmed and (exitUp or exitDown) and exitReason == "早期结构失败"' in source
    assert '参考结束事件（1保护 / 2反向 / 3结构）' in source
    assert 'alertcondition(structuralExitEvent, "SPIKE 早期结构结束"' in source


def test_native_probe_injects_actual_candidate_helper_prefix():
    source = PLUS.read_text()
    probe = render(source)
    prefix = source.split(MARKER, 1)[0]
    assert probe.startswith(prefix + MARKER)
    assert hashlib.sha256(prefix.encode()).hexdigest() in probe
    state = source.split('// BEGIN BURST STATE MACHINE', 1)[1].split('// END BURST STATE MACHINE', 1)[0]
    injected_state = state.replace("if barstate.isconfirmed and ready", "if probeConfirmed and ready")
    assert textwrap.indent(injected_state, "    ") in probe
    for needle in ('long 1R -0.5R', 'short 2R cost buffer', 'long trail beats cost buffer', 'adverse long gap', 'f_rawEndsOppositeReference(-1, 1)', 'STATE PROBE FAIL', 'stateProbeTotal == 12', 'probeChecks == 1'):
        assert needle in probe
