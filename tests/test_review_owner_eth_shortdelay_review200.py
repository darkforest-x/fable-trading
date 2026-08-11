from collections import Counter

from scripts.review_owner_eth_shortdelay_review200 import (
    HARD_NEGATIVE_INDICES,
    INDEX_STATUS,
    KEEP_INDICES,
    MIRROR_EXCLUDED,
    MIRROR_INDICES,
    REBOX_INDICES,
    SHORT_HARD_NEGATIVE,
    SHORT_KEEP,
    SHORT_REBOX_PENDING,
)


def test_manual_partition_covers_each_frozen_index_once():
    all_indices = (
        list(MIRROR_INDICES)
        + list(KEEP_INDICES)
        + list(HARD_NEGATIVE_INDICES)
        + list(REBOX_INDICES)
    )
    assert len(all_indices) == 200
    assert len(set(all_indices)) == 200
    assert set(all_indices) == set(range(1, 201))
    assert set(INDEX_STATUS) == set(range(1, 201))


def test_manual_partition_has_expected_conservative_counts():
    counts = Counter(INDEX_STATUS.values())
    assert counts == Counter(
        {
            MIRROR_EXCLUDED: 74,
            SHORT_REBOX_PENDING: 61,
            SHORT_KEEP: 40,
            SHORT_HARD_NEGATIVE: 25,
        }
    )
