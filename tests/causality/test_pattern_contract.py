"""Each rule in yoyo.contracts.pattern must fire on a real violation.

The contract's value is entirely in its refusals, so every refusal is exercised
here with the smallest record that triggers it, plus the round trip from both
storage schemas that feed it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yoyo.contracts.pattern import (
    PatternContractError,
    PatternEvent,
    from_gold_row,
    from_pattern_event,
    timeframe_delta,
)

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _event(**overrides) -> PatternEvent:
    base = dict(
        event_id="e1",
        symbol="ETH-USDT-SWAP",
        timeframe="15m",
        window_start_at=T0,
        decision_at=T0 + timedelta(minutes=15 * 19),
        visible_end_at=T0 + timedelta(minutes=15 * 19),
        pattern_valid=True,
        label_origin="owner",
        window_bars=20,
    )
    base.update(overrides)
    return PatternEvent(**base)


def test_a_clean_label_is_accepted():
    event = _event()
    assert event.future_bars_visible == 0


# -- rule 1: the reviewer did not see past the bar they judged --------------

def test_seeing_past_the_decision_bar_is_refused():
    with pytest.raises(PatternContractError, match="saw future bars"):
        _event(visible_end_at=T0 + timedelta(minutes=15 * 40))


def test_a_naive_timestamp_is_refused():
    with pytest.raises(PatternContractError, match="naive"):
        _event(decision_at=datetime(2026, 3, 1, 12, 0))


# -- rule 2: anchors live inside their own window --------------------------

def test_an_anchor_outside_the_window_is_refused():
    with pytest.raises(PatternContractError, match=r"outside window \[0,19\]"):
        _event(formation_start_i=25)


def test_anchors_must_not_run_backwards():
    with pytest.raises(PatternContractError, match="anchor order violated"):
        _event(
            formation_start_i=10,
            causal_onset_i=4,
            causal_onset_source="owner_causal_review",
        )


def test_launch_before_confirm_is_allowed_and_surfaces_elsewhere():
    """Section 7.1 of the onset spec: a real exception reaches the audit.

    Clamping it into order would make the ordering property vacuous -- it would
    hold because the code enforced it, not because the labelling was good.
    """
    event = _event(formation_confirm_i=15, launch_i=12)
    assert event.launch_i == 12


# -- rule 3: validity is not justified by the outcome ----------------------

def test_validity_justified_by_the_outcome_is_refused():
    with pytest.raises(PatternContractError, match="appeals to the outcome"):
        _event(validity_justification="valid because price dropped 4% afterwards it fell")


def test_a_shape_justification_is_accepted():
    event = _event(validity_justification="six MAs inside 0.4 ATR for five bars")
    assert event.pattern_valid


# -- rule 4: a causal onset needs a human origin that is not box geometry ---

def test_an_onset_without_a_stated_origin_is_refused():
    with pytest.raises(PatternContractError, match="causal_onset_source is null"):
        _event(causal_onset_i=8)


def test_box_geometry_is_not_an_acceptable_onset_origin():
    with pytest.raises(PatternContractError, match="box geometry"):
        _event(causal_onset_i=8, causal_onset_source="box_end_i")


def test_an_origin_without_an_onset_is_refused():
    with pytest.raises(PatternContractError, match="while causal_onset_i is null"):
        _event(causal_onset_source="owner_causal_review")


# -- rule 5: a proposal is not gold ----------------------------------------

def test_a_model_proposal_cannot_be_training_eligible():
    with pytest.raises(PatternContractError, match="proposal does not become gold"):
        _event(label_origin="model_proposal", training_eligible=True)


def test_a_rule_proposal_cannot_be_training_eligible():
    with pytest.raises(PatternContractError, match="proposal does not become gold"):
        _event(label_origin="rule_proposal", training_eligible=True)


def test_production_requires_training_eligibility_first():
    with pytest.raises(PatternContractError, match="production_eligible without"):
        _event(training_eligible=False, production_eligible=True)


# -- timeframe grid --------------------------------------------------------

def test_an_unknown_timeframe_fails_closed():
    with pytest.raises(PatternContractError, match="unknown timeframe"):
        timeframe_delta("7s", "e1")


# -- adapters --------------------------------------------------------------

def _gold_row(**overrides):
    row = {
        "gold_id": "ETH-USDT-SWAP_15m_20260301T124500Z",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "source_repo": "fable-trading",
        "source_path": "datasets/x/gold.jsonl",
        "candidate_source": "owner",
        "decision_bar": 119,
        "decision_time": "2026-03-01T12:45:00+00:00",
        "local_start_bar": 100,
        "local_end_bar": 119,
        "local_window_length": 20,
        "shape_label": "POSITIVE",
        "core_start_bar": 110,
        "core_end_bar": 116,
        "box_rule": "core4",
        "box_status": "owner",
        "reviewer": "owner",
        "holdout_read": False,
    }
    row.update(overrides)
    return row


def test_a_gold_row_converts_and_is_causally_clean():
    event = from_gold_row(_gold_row())
    assert event.label_origin == "owner"
    assert event.visible_end_at == event.decision_at
    assert event.pattern_valid is True
    # core box becomes window-relative anchors
    assert (event.formation_start_i, event.formation_confirm_i) == (10, 16)


def test_the_window_start_is_derived_from_the_bar_grid_not_assumed():
    event = from_gold_row(_gold_row())
    assert event.decision_at - event.window_start_at == timedelta(minutes=15 * 19)


def test_a_gold_row_never_arrives_with_a_causal_onset():
    """A core box's edges are geometry. Rule 4 says they are not an onset."""
    event = from_gold_row(_gold_row())
    assert event.causal_onset_i is None
    assert event.causal_onset_source is None


def test_a_negative_gold_row_converts_without_a_box():
    event = from_gold_row(
        _gold_row(shape_label="NEGATIVE", core_start_bar=None, core_end_bar=None)
    )
    assert event.pattern_valid is False
    assert event.formation_start_i is None


def _v3_record(**overrides):
    record = {
        "event_id": "evt_000001",
        "source_pattern_id": "dense_00001",
        "source": "golden_pool",
        "symbol": "ETH-USDT-SWAP",
        "timeframe": "15m",
        "source_window": {"start_i": 8000, "end_i": 8019, "bars": 20, "available_at": None},
        "original_box": {"xywhn": [0.7, 0.5, 0.1, 0.2], "box_start_i": 8010, "box_end_i": 8016},
        "anchors": {
            "formation_start_i": None,
            "causal_onset_i": None,
            "formation_confirm_i": None,
            "launch_i": None,
        },
        "event_validity": "unreviewed",
        "review": {"reviewer": None, "protocol_version": None},
        "provenance": {"pattern_library_sha256": "a" * 64},
    }
    record.update(overrides)
    return record


def test_an_unreviewed_v3_record_converts_as_a_proposal():
    event = from_pattern_event(_v3_record(), decision_at="2026-03-01T16:45:00+00:00")
    assert event.label_origin == "model_proposal"
    assert event.training_eligible is False
    assert event.causal_onset_i is None


def test_an_onset_from_a_causal_review_keeps_its_warrant():
    event = from_pattern_event(
        _v3_record(
            anchors={"causal_onset_i": 8012, "formation_start_i": None,
                     "formation_confirm_i": None, "launch_i": None},
            review={"reviewer": "owner", "protocol_version": "causal_onset_review_v1"},
            event_validity="valid",
        ),
        decision_at="2026-03-01T16:45:00+00:00",
    )
    assert event.causal_onset_i == 12
    assert event.causal_onset_source == "causal_onset_review"
    assert event.label_origin == "owner"


def test_an_onset_with_no_review_protocol_is_refused_by_the_adapter():
    """The adapter reads the warrant; it must never supply one."""
    with pytest.raises(PatternContractError, match="not one of the causal review protocols"):
        from_pattern_event(
            _v3_record(
                anchors={"causal_onset_i": 8012, "formation_start_i": None,
                         "formation_confirm_i": None, "launch_i": None},
                review={"reviewer": "owner", "protocol_version": "quality_grading_v1"},
            ),
            decision_at="2026-03-01T16:45:00+00:00",
        )


def test_a_hindsight_label_must_be_declared_not_smuggled():
    with pytest.raises(PatternContractError, match="saw future bars"):
        from_pattern_event(
            _v3_record(),
            decision_at="2026-03-01T16:45:00+00:00",
            visible_end_at="2026-03-02T16:45:00+00:00",
        )
