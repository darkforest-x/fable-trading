"""Deterministic, data-whitelisted renderer checks for live chart images."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io

from PIL import Image
import pytest

from yoyo.vision_research.auto_chart import RENDER_VERSION, render_live_chart

UTC_PLUS_8 = timezone(timedelta(hours=8))
PERIOD_MS = 5 * 60_000


def live_chart():
    start = int(datetime(2026, 9, 23, 20, 0, tzinfo=UTC_PLUS_8).timestamp() * 1000)
    rows = []
    for index in range(120):
        close = 100 + index * 0.08 + (index % 7) * 0.13
        open_price = close - 0.18
        rows.append({
            "t": start + index * PERIOD_MS,
            "o": open_price,
            "h": close + 0.26,
            "l": open_price - 0.31,
            "c": close,
            "is_closed": index < 119,
            "sma20": close - 0.32,
            "ema20": close - 0.28,
            "sma60": close - 0.41,
            "ema60": close - 0.37,
            "sma120": close - 0.49,
            "ema120": close - 0.45,
        })
    observed = rows[-1]["t"] + 181_000
    return {
        "candles": rows,
        "provenance": {
            "symbol": "BTC-USDT-SWAP",
            "timeframe": "5m",
            "timeframe_min": 5,
            "observed_at_ms": observed,
            "last_bar_closed": False,
            "visible_end_ms": observed,
        },
    }


def test_live_chart_image_is_deterministic_and_matches_fixed_canvas():
    chart = live_chart()
    first = render_live_chart(chart)
    second = render_live_chart(chart)

    assert RENDER_VERSION == "spike-vision-auto-light-v1"
    assert first == second
    assert (first.width, first.height) == (1440, 800)
    assert first.mime_type == "image/png"
    with Image.open(io.BytesIO(first.data)) as image:
        assert image.size == (1440, 800)
        assert image.format == "PNG"


def test_unrelated_outcome_metadata_does_not_change_rendered_pixels():
    original = live_chart()
    changed = deepcopy(original)
    changed["profit"] = 9000
    changed["future_return"] = -123.4
    changed["outcome"] = "match"
    changed["candles"][-1]["pnl"] = 123456
    changed["provenance"].update(
        side="short", recognition_eligible=True, performance={"net": 99},
        future_outcomes=["win", "loss"],
    )

    assert render_live_chart(original).sha256 == render_live_chart(changed).sha256


def test_changing_tip_ohlc_changes_rendered_pixels():
    original = live_chart()
    changed = deepcopy(original)
    changed["candles"][-1]["c"] += 4
    changed["candles"][-1]["h"] += 4

    assert render_live_chart(original).sha256 != render_live_chart(changed).sha256


@pytest.mark.parametrize("mutation", ["unsorted", "duplicate", "future", "bad_ohlc", "wrong_tip_state"])
def test_invalid_live_rows_are_rejected(mutation):
    chart = live_chart()
    rows = chart["candles"]
    if mutation == "unsorted":
        rows[30], rows[31] = rows[31], rows[30]
    elif mutation == "duplicate":
        rows[31]["t"] = rows[30]["t"]
    elif mutation == "future":
        rows[-1]["t"] = chart["provenance"]["observed_at_ms"] + 1
    elif mutation == "bad_ohlc":
        rows[40]["l"] = rows[40]["h"] + 1
    else:
        rows[-1]["is_closed"] = True

    with pytest.raises(ValueError):
        render_live_chart(chart)


def test_newest_closed_state_is_rendered_from_provenance():
    forming = live_chart()
    closed = deepcopy(forming)
    closed["candles"][-1]["is_closed"] = True
    closed["provenance"]["last_bar_closed"] = True
    closed["provenance"]["observed_at_ms"] += PERIOD_MS - 181_000
    closed["provenance"]["visible_end_ms"] = closed["provenance"]["observed_at_ms"]

    assert render_live_chart(forming).sha256 != render_live_chart(closed).sha256
