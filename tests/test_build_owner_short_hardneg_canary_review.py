import pandas as pd

from scripts.build_owner_short_hardneg_canary_review import (
    ROOT,
    future_bar_count,
    make_human_review_transform,
    render_html,
)


def test_future_bar_count_caps_review_at_48_bars() -> None:
    assert future_bar_count(
        "2026-05-03T00:00:00Z", "2026-05-03T23:45:00Z"
    ) == 48


def test_future_bar_count_shortens_at_physical_prefix_end() -> None:
    assert future_bar_count(
        pd.Timestamp("2026-05-03T12:00:00Z"),
        pd.Timestamp("2026-05-03T23:45:00Z"),
    ) == 47


def test_review_html_has_three_shortcuts_and_no_continuation_button(tmp_path) -> None:
    source = tmp_path / "events.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    row = {
        "review_id": "C001",
        "event_id": "event-1",
        "symbol": "ETH_USDT_SWAP",
        "decision_time": "2026-05-03T00:00:00Z",
        "window_len": 15,
        "predicted_core_bars": 5,
        "decision_delay_bars": 3,
        "conf": 0.5,
        "event_conf_max": 0.8,
        "raw_detection_count": 2,
        "future_review_actual_span_pct": 1.0,
        "causal_review_path": "analysis/output/example/causal.png",
        "future_review_path": "analysis/output/example/future.png",
    }

    page = render_html([row], source, ROOT / "analysis/html/example.html")

    assert 'data-value="target"' in page
    assert 'data-value="rebox"' in page
    assert 'data-value="hard_negative"' in page
    assert 'data-value="continuation"' not in page
    assert "const mapping={'1':'target','2':'rebox','3':'hard_negative'}" in page
    assert "advance(id)" in page


def test_human_review_transform_does_not_apply_training_six_percent_floor() -> None:
    frame = pd.DataFrame(
        {
            "low": [99.5, 99.8],
            "high": [100.2, 100.5],
            "sma20": [100.0, 100.0],
            "sma60": [100.0, 100.0],
            "sma120": [100.0, 100.0],
            "ema20": [100.0, 100.0],
            "ema60": [100.0, 100.0],
            "ema120": [100.0, 100.0],
        }
    )

    transform, actual_span_pct = make_human_review_transform(frame)

    rendered_span_pct = (
        (transform.price_max - transform.price_min) / 100.0 * 100
    )
    assert actual_span_pct == 1.0
    assert rendered_span_pct < 2.0
