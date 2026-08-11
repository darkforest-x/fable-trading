from collections import Counter

from scripts.review_owner_eth_shortdelay_calibration import (
    FIRST_PASS,
    MIRROR_UNCONFIRMED,
    SHORT_HARD_NEGATIVE,
    SHORT_KEEP,
    SHORT_REBOX,
    STATUSES,
    apply_first_pass,
)


def _source_row(calibration_id: str) -> dict:
    return {
        "calibration_id": calibration_id,
        "event_id": calibration_id,
        "source_stem": calibration_id,
        "symbol": calibration_id,
        "stage_split": "train",
        "source_csv": "data/example.csv",
        "mid_global": 100,
        "core_global": [100, 106],
        "core_local": [10, 16],
        "core_bars": 7,
        "pre_bars": 10,
        "post_bars": int(calibration_id[4]),
        "win_start": 90,
        "win_end": 109 + int(calibration_id[4]),
        "win_len": 20,
        "box_center_ratio": 0.6,
        "expected_start_time": "2026-01-01T00:00:00+00:00",
        "expected_anchor_time": "2026-01-01T00:00:00+00:00",
        "expected_end_time": "2026-01-01T00:00:00+00:00",
        "time_bucket": "early",
        "semantic_status": "unreviewed",
        "geometry_status": "unreviewed_legacy_core_proposal",
        "training_eligible": False,
        "production_eligible": False,
    }


def test_first_pass_is_a_complete_four_way_partition():
    counts = Counter(decision["status"] for decision in FIRST_PASS.values())
    assert len(FIRST_PASS) == 30
    assert set(counts) == set(STATUSES)
    assert counts == Counter(
        {
            MIRROR_UNCONFIRMED: 17,
            SHORT_KEEP: 5,
            SHORT_REBOX: 4,
            SHORT_HARD_NEGATIVE: 4,
        }
    )


def test_first_pass_never_fakes_owner_confirmation_or_training_eligibility():
    reviewed = apply_first_pass([_source_row(key) for key in FIRST_PASS])
    assert len(reviewed) == 30
    assert all(row["owner_confirmed"] is False for row in reviewed)
    assert all(row["training_eligible"] is False for row in reviewed)
    assert all(row["production_eligible"] is False for row in reviewed)
    for row in reviewed:
        if row["codex_firstpass_status"] == SHORT_REBOX:
            width = row["revised_core_global"][1] - row["revised_core_global"][0] + 1
            assert 4 <= width <= 7
            assert row["revised_core_global"][1] < row["legacy_core_global"][1]
        else:
            assert row["revised_core_global"] is None
