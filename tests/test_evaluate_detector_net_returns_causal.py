"""Event-unit tests for the causal detector evaluator."""
from scripts.evaluate_detector_net_returns_causal import collapse_first_causal_proposals


def test_repeated_views_collapse_to_the_earliest_decision_not_highest_confidence() -> None:
    rows = [
        {
            "event_id": "e1",
            "name": "later-high",
            "decision_i": 20,
            "entry_time": "2026-01-01T01:40:00+00:00",
            "conf": 0.99,
        },
        {
            "event_id": "e1",
            "name": "first-low",
            "decision_i": 18,
            "entry_time": "2026-01-01T01:30:00+00:00",
            "conf": 0.30,
        },
        {
            "event_id": "e2",
            "name": "only",
            "decision_i": 25,
            "entry_time": "2026-01-01T02:05:00+00:00",
            "conf": 0.50,
        },
    ]

    collapsed = collapse_first_causal_proposals(rows)

    assert [row["name"] for row in collapsed] == ["first-low", "only"]
