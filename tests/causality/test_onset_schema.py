from yoyo.layers.l1_detection.onset.events.schema import PatternEvent, SCHEMA_VERSION
from yoyo.layers.l1_detection.onset.events.validator import validate_event, validate_many


def _base(**over):
    d = {
        "schema_version": SCHEMA_VERSION,
        "event_id": "evt_000001",
        "source_pattern_id": "dense_00001",
        "source": "golden_pool",
        "symbol": "ETH_USDT_SWAP",
        "timeframe": "15m",
        "source_window": {"start_i": 8700, "end_i": 8899, "bars": 200,
                          "available_at": None},
        "original_box": {"xywhn": [0.75, 0.5, 0.12, 0.2],
                         "box_start_i": 8831, "box_end_i": 8854},
        "anchors": {"formation_start_i": None, "causal_onset_i": None,
                    "formation_confirm_i": None, "launch_i": None},
        "anchors_time": {},
        "quality_label": "A",
        "event_validity": "unreviewed",
        "review": {}, "provenance": {},
    }
    d.update(over)
    return d


def test_valid_minimal_record():
    errors, warnings = validate_event(_base())
    assert errors == []
    assert warnings == []


def test_null_anchors_are_not_errors():
    """Before a human has stepped through the bars, null is the correct value."""
    errors, _ = validate_event(_base())
    assert errors == []


def test_anchor_order_violation_is_an_error():
    d = _base(anchors={"formation_start_i": 8845, "causal_onset_i": 8841,
                       "formation_confirm_i": None, "launch_i": None})
    errors, _ = validate_event(d)
    assert any("anchor order violated" in e for e in errors)


def test_launch_before_confirm_is_a_warning_not_an_error():
    """Spec 7.1: real exceptions must surface in the audit, not be reordered."""
    d = _base(anchors={"formation_start_i": 8834, "causal_onset_i": 8841,
                       "formation_confirm_i": 8845, "launch_i": 8843})
    errors, warnings = validate_event(d)
    assert errors == []
    assert any("launch_i" in w for w in warnings)


def test_anchor_outside_source_window_is_an_error():
    d = _base(anchors={"formation_start_i": None, "causal_onset_i": 9999,
                       "formation_confirm_i": None, "launch_i": None})
    errors, _ = validate_event(d)
    assert any("outside source_window" in e for e in errors)


def test_window_bars_must_match_span():
    d = _base(source_window={"start_i": 8700, "end_i": 8899, "bars": 150,
                             "available_at": None})
    errors, _ = validate_event(d)
    assert any("bars inconsistent" in e for e in errors)


def test_roundtrip_dict():
    d = _base()
    ev = PatternEvent.from_dict(d)
    again = ev.to_dict()
    assert again["event_id"] == d["event_id"]
    assert again["anchors"]["causal_onset_i"] is None
    assert again["original_box"]["box_end_i"] == 8854


def test_duplicate_event_ids_are_flagged():
    rep = validate_many([_base(), _base()])
    assert rep["n_errors"] >= 1
    assert any("duplicate event_id" in e
               for f in rep["findings"] for e in f["errors"])


def test_side_defaults_to_null():
    """Direction is a human answer; an event nobody asked stays unanswered."""
    errors, warnings = validate_event(_base())
    assert errors == []
    assert PatternEvent.from_dict(_base()).side is None


def test_side_round_trips():
    ev = PatternEvent.from_dict(_base(side="short", side_source="owner_side_review"))
    assert ev.side == "short"
    assert ev.to_dict()["side_source"] == "owner_side_review"


def test_side_without_source_is_an_error():
    """A side with no stated origin cannot be told apart from one a rule wrote."""
    errors, _ = validate_event(_base(side="long"))
    assert any("side_source is null" in e for e in errors)


def test_unknown_side_source_is_an_error():
    errors, _ = validate_event(_base(side="long", side_source="cluster_geometry"))
    assert any("side_source" in e for e in errors)


def test_bad_side_value_is_an_error():
    errors, _ = validate_event(_base(side="up", side_source="owner_side_review"))
    assert any("side must be" in e for e in errors)


def test_orphan_side_source_warns_but_does_not_fail():
    errors, warnings = validate_event(_base(side_source="owner_side_review"))
    assert errors == []
    assert any("side is null" in w for w in warnings)
