from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

from yoyo.datasets.ma_launch_owner_yolo_dataset import (
    _label_text,
    calendar_halfyear,
    interval_split,
    negative_feature_masks,
    plan_positives,
    select_source_negatives,
)


def prereg() -> dict:
    return {
        "protocol": "test",
        "positive_source": {"rows": 2},
        "split": {
            "cutoff": "2025-12-01T00:00:00Z",
            "purge_bars_each_side": 2,
            "bar_minutes": 15,
        },
        "negative_sampling": {
            "negative_per_positive": 1,
            "preferred_hard_total": 1,
            "preferred_easy_total": 1,
            "completed_no_launch_condition": {
                "abs_close_progress_atr_max_core_plus_2": 0.85,
                "abs_close_progress_atr_max_core_plus_3": 1.10,
                "abs_close_progress_atr_max_core_plus_5": 1.35,
                "two_sided_high_low_excursion_atr_max_core_plus_1_to_5": 1.75,
            },
            "hard_definition": {
                "ma_envelope_atr_max": 1.5,
                "ma_spread_end_atr_max": 1.1,
                "max_body_atr_max": 1.2,
                "candle_envelope_atr_max": 2.8,
                "minimum_close_to_ma_atr_max": 1.0,
            },
            "easy_definition": {
                "ma_envelope_atr_min_any": 2.2,
                "ma_spread_end_atr_min_any": 1.6,
                "minimum_close_to_ma_atr_min_any": 1.8,
                "candle_envelope_atr_min_any": 4.0,
            },
            "positive_guard": {
                "before_core_bars": 12,
                "after_dependency_end_bars": 12,
            },
            "negative_separation_bars": 2,
        },
    }


def row(order: int, stamp: str) -> dict:
    end = pd.Timestamp(stamp)
    start = end - pd.Timedelta(minutes=45)
    return {
        "sample_id": f"s{order}",
        "event_id": f"e{order}",
        "source_order": order,
        "symbol": "BTC_USDT_SWAP",
        "direction": "LONG" if order == 1 else "SHORT",
        "source_path": "data/x.csv",
        "source_core_start_i": 97,
        "source_core_end_i": 100,
        "window_start_i": 87,
        "window_end_i": 106,
        "core_bars": 4,
        "pre_core_context_bars": 10,
        "post_core_context_bars": 6,
        "core_start_time": start.isoformat(),
        "core_end_time": end.isoformat(),
        "box": {
            "cx_norm": 0.5,
            "cy_norm": 0.5,
            "w_norm": 0.2,
            "h_norm": 0.3,
        },
        "image_path": f"x{order}.png",
        "image_sha256": "a" * 64,
    }


def test_interval_split_uses_full_dependency_and_purge() -> None:
    kwargs = {
        "cutoff": "2025-12-01T00:00:00Z",
        "purge_bars": 2,
        "bar_minutes": 15,
    }
    assert interval_split("2025-11-01", "2025-11-30T23:30Z", **kwargs) == "train"
    assert interval_split("2025-12-01T00:30Z", "2025-12-02", **kwargs) == "val"
    assert interval_split("2025-11-30T23:45Z", "2025-12-01T00:15Z", **kwargs) == "excluded"


def test_calendar_halfyear_is_stable() -> None:
    assert calendar_halfyear("2025-01-01T00:00:00Z") == "2025H1"
    assert calendar_halfyear("2025-12-31T23:45:00Z") == "2025H2"


def test_positive_plan_keeps_exact_box_and_assigns_equal_negative_kinds() -> None:
    rows = [
        row(1, "2025-10-01T00:00:00Z"),
        row(2, "2025-10-02T00:00:00Z"),
        row(3, "2026-01-01T00:00:00Z"),
        row(4, "2026-01-02T00:00:00Z"),
    ]
    config = prereg()
    config["positive_source"]["rows"] = 4
    config["negative_sampling"]["preferred_hard_total"] = 2
    config["negative_sampling"]["preferred_easy_total"] = 2
    plans = plan_positives(
        rows,
        config,
    )
    assert Counter(plan.negative_kind for plan in plans) == {"easy": 2, "hard": 2}
    assert plans[0].box["w_norm"] == 0.2
    assert plans[0].window_start_i == 87
    assert plans[0].window_end_i == 106


def test_yolo_label_uses_exact_accepted_normalized_geometry() -> None:
    label = _label_text(
        "SHORT",
        {"cx_norm": 0.5, "cy_norm": 0.4, "w_norm": 0.2, "h_norm": 0.3},
    )
    assert label == "1 0.500000000 0.400000000 0.200000000 0.300000000\n"


def test_negative_masks_separate_dense_hard_from_wide_easy() -> None:
    n = 180
    close = np.full(n, 100.0)
    dense = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "atr": np.ones(n),
            "sma20": close,
            "ema20": close,
            "sma60": close,
            "ema60": close,
            "sma120": close,
            "ema120": close,
        }
    )
    hard = negative_feature_masks(dense, core_len=4, prereg=prereg())
    assert hard["hard"][150]
    assert not hard["easy"][150]

    wide = dense.copy()
    wide["sma20"] = 97.0
    wide["ema20"] = 98.0
    wide["sma60"] = 99.0
    wide["ema60"] = 101.0
    wide["sma120"] = 102.0
    wide["ema120"] = 103.0
    easy = negative_feature_masks(wide, core_len=4, prereg=prereg())
    assert easy["easy"][150]
    assert not easy["hard"][150]


def test_safe_kind_fallback_uses_easy_when_hard_pool_is_empty() -> None:
    config = prereg()
    config["negative_sampling"].update(
        {
            "preferred_hard_total": 2,
            "preferred_easy_total": 2,
            "minimum_hard_share_overall": 0.0,
            "minimum_hard_share_train": 0.0,
            "minimum_hard_share_val": 0.0,
        }
    )
    template = plan_positives(
        [
            row(1, "2025-10-01T00:00:00Z"),
            row(2, "2025-10-02T00:00:00Z"),
            row(3, "2026-01-01T00:00:00Z"),
            row(4, "2026-01-02T00:00:00Z"),
        ],
        config,
    )[1]
    n = 720
    close = np.full(n, 100.0)
    times = pd.date_range("2025-07-01T00:00:00Z", periods=n, freq="15min")
    wide = pd.DataFrame(
        {
            "open_time": times,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "atr": np.ones(n),
            "sma20": np.full(n, 97.0),
            "ema20": np.full(n, 98.0),
            "sma60": np.full(n, 99.0),
            "ema60": np.full(n, 101.0),
            "sma120": np.full(n, 102.0),
            "ema120": np.full(n, 103.0),
            "_segment_id": np.zeros(n, dtype=int),
        }
    )
    selected, audit = select_source_negatives(
        wide,
        source_path="data/x.csv",
        symbol="BTC_USDT_SWAP",
        positives=[template],
        strict_candidates=[
            {"source_core_start_i": template.core_start_i, "source_core_end_i": template.core_end_i}
        ],
        prereg=config,
    )
    assert selected[0].negative_kind == "easy"
    assert selected[0].pair_slot == 1
    assert audit["safe_kind_fallbacks"] == {"hard_to_easy": 1}


def test_seeded_expansion_keeps_seed_and_adds_unique_pair_slots() -> None:
    config = prereg()
    config["positive_source"]["rows"] = 4
    config["negative_sampling"].update(
        {
            "negative_per_positive": 3,
            "target_kinds_per_positive": ["hard", "hard", "easy"],
            "preferred_hard_total": 8,
            "preferred_easy_total": 4,
        }
    )
    template = plan_positives(
        [
            row(1, "2025-10-01T00:00:00Z"),
            row(2, "2025-10-02T00:00:00Z"),
            row(3, "2026-01-01T00:00:00Z"),
            row(4, "2026-01-02T00:00:00Z"),
        ],
        config,
    )[0]
    n = 720
    times = pd.date_range("2025-07-01T00:00:00Z", periods=n, freq="15min")
    close = np.full(n, 100.0)
    dense = pd.DataFrame(
        {
            "open_time": times,
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "atr": np.ones(n),
            "sma20": close,
            "ema20": close,
            "sma60": close,
            "ema60": close,
            "sma120": close,
            "ema120": close,
            "_segment_id": np.zeros(n, dtype=int),
        }
    )
    first, _ = select_source_negatives(
        dense,
        source_path="data/x.csv",
        symbol="BTC_USDT_SWAP",
        positives=[template],
        strict_candidates=[
            {
                "source_core_start_i": template.core_start_i,
                "source_core_end_i": template.core_end_i,
            }
        ],
        prereg={
            **config,
            "negative_sampling": {
                **config["negative_sampling"],
                "negative_per_positive": 1,
                "target_kinds_per_positive": ["hard"],
            },
        },
    )
    expanded, audit = select_source_negatives(
        dense,
        source_path="data/x.csv",
        symbol="BTC_USDT_SWAP",
        positives=[template],
        strict_candidates=[
            {
                "source_core_start_i": template.core_start_i,
                "source_core_end_i": template.core_end_i,
            }
        ],
        prereg=config,
        seed_negatives=first,
    )
    assert len(expanded) == 3
    assert expanded[0].sample_id == first[0].sample_id
    assert {item.pair_slot for item in expanded} == {1, 2, 3}
    assert len({item.core_end_i for item in expanded}) == 3
    assert audit["seed_negative_rows"] == 1
