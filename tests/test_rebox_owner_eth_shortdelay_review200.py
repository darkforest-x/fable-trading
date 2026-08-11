from collections import Counter

from scripts.rebox_owner_eth_shortdelay_review200 import (
    REBOX_LOCAL_BOUNDS,
    apply_manual_geometry,
)
from scripts.review_owner_eth_shortdelay_review200 import REBOX_INDICES


def test_manual_rebox_geometry_covers_frozen_queue_with_variable_widths():
    assert set(REBOX_LOCAL_BOUNDS) == set(REBOX_INDICES)
    widths = Counter(end - start + 1 for start, end in REBOX_LOCAL_BOUNDS.values())
    assert widths == Counter({4: 2, 5: 56, 6: 2, 7: 1})
    assert set(widths) == {4, 5, 6, 7}


def test_manual_rebox_boundaries_are_not_a_uniform_shift():
    starts = {start for start, _end in REBOX_LOCAL_BOUNDS.values()}
    ends = {end for _start, end in REBOX_LOCAL_BOUNDS.values()}
    pairs = set(REBOX_LOCAL_BOUNDS.values())
    assert len(starts) > 5
    assert len(ends) > 5
    assert len(pairs) > 10


def test_apply_manual_geometry_preserves_context_and_blocks_training():
    source = {
        "calibration_id": "R006_example",
        "win_start": 100,
        "win_end": 117,
        "win_len": 18,
        "core_global": [108, 114],
        "core_local": [8, 14],
        "pre_bars": 8,
        "post_bars": 3,
        "training_eligible": False,
        "production_eligible": False,
    }
    row = apply_manual_geometry(source)
    assert row["proposal_selected_local_in_frozen_window"] == [3, 7]
    assert row["proposal_launch_local_in_frozen_window"] == 8
    assert row["proposal_core_global"] == [103, 107]
    assert row["proposal_core_local"] == [8, 12]
    assert row["proposal_core_bars"] == 5
    assert row["proposal_win_start"] == 95
    assert row["proposal_win_end"] == 110
    assert row["proposal_win_len"] == 16
    assert row["proposal_end_delta_vs_legacy"] < 0
    assert row["sample_owner_confirmed"] is False
    assert row["training_eligible"] is False
    assert row["production_eligible"] is False
