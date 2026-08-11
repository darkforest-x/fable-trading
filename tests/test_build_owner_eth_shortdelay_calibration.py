from collections import Counter

import pandas as pd

from scripts.build_owner_eth_shortdelay_calibration import (
    CORE_WIDTHS,
    PER_DELAY,
    POST_DELAYS,
    PRE_CONTEXTS,
    select_plans,
)


def _candidate(index: int, width: int) -> dict:
    return {
        "event_id": f"event-{width}-{index:03d}",
        "source_stem": f"stem-{width}-{index:03d}",
        "symbol": f"SYM-{width}-{index:03d}",
        "stage_split": "train",
        "source_csv": f"data/kline_fetched/sym-{width}-{index:03d}.csv",
        "mid_global": 1000 + index,
        "core_global": [998 + index, 998 + index + width - 1],
        "core_bars": width,
        "anchor_time": pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=index),
        "time_bucket": ("early", "middle", "late")[index % 3],
    }


def test_select_plans_balances_geometry_without_freezing_position():
    candidates = [
        _candidate(index, width)
        for width in CORE_WIDTHS
        for index in range(80)
    ]
    plans = select_plans(candidates)
    assert len(plans) == len(POST_DELAYS) * PER_DELAY == 30
    assert len({plan.event_id for plan in plans}) == 30
    assert len({plan.symbol for plan in plans}) == 30
    assert Counter(plan.post_bars for plan in plans) == Counter({3: 10, 4: 10, 5: 10})
    assert Counter(plan.core_bars for plan in plans) == Counter({5: 15, 7: 15})
    assert Counter(plan.pre_bars for plan in plans) == Counter({value: 6 for value in PRE_CONTEXTS})
    assert min(plan.win_len for plan in plans) == 14
    assert max(plan.win_len for plan in plans) == 22
    assert len({round(plan.box_center_ratio, 6) for plan in plans}) > 5


def test_every_plan_is_exactly_pre_plus_core_plus_post():
    candidates = [
        _candidate(index, width)
        for width in CORE_WIDTHS
        for index in range(80)
    ]
    for plan in select_plans(candidates):
        assert plan.win_len == plan.pre_bars + plan.core_bars + plan.post_bars
        assert plan.win_start == plan.core_global[0] - plan.pre_bars
        assert plan.win_end == plan.core_global[1] + plan.post_bars
        assert plan.core_local == (plan.pre_bars, plan.pre_bars + plan.core_bars - 1)
        assert plan.semantic_status == "unreviewed"
        assert plan.geometry_status == "unreviewed_legacy_core_proposal"
        assert plan.training_eligible is False
        assert plan.production_eligible is False
