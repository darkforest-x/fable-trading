from collections import Counter

from scripts.build_owner_eth_shortdelay_review200 import PRE_CONTEXTS, assign_pre_contexts


def _rows() -> list[dict]:
    rows = []
    for index in range(200):
        rows.append(
            {
                "event_id": f"event-{index:03d}",
                "post_bars": (3, 4, 5)[index % 3],
                "box_bars": (5, 7)[index % 2],
            }
        )
    return rows


def test_pre_context_assignment_is_exactly_balanced_and_deterministic():
    rows = _rows()
    first = assign_pre_contexts(rows)
    second = assign_pre_contexts(list(reversed(rows)))
    assert first == second
    assert Counter(first.values()) == Counter({pre: 40 for pre in PRE_CONTEXTS})
    assert set(first) == {row["event_id"] for row in rows}


def test_pre_context_assignment_covers_each_stratum_without_fixed_position():
    rows = _rows()
    assigned = assign_pre_contexts(rows)
    for post in (3, 4, 5):
        for width in (5, 7):
            values = {
                assigned[row["event_id"]]
                for row in rows
                if row["post_bars"] == post and row["box_bars"] == width
            }
            assert values == set(PRE_CONTEXTS)
