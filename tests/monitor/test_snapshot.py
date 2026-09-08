"""Snapshot causality, exact event identity and image validity; no sends."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import math

from PIL import Image
import pytest

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.signals import analyze
from yoyo.monitor.snapshot import MAX_CANDLES, SIZE, render_signal
from model_fixture import model_event


def sample(side="long"):
    """Synthetic chart values for drawing tests, not market evidence."""
    target = 139
    start, duration = 1_600_000_200_000, TIMEFRAMES["15m"]
    rows = []
    for i in range(160):
        flat = 110 <= i < target
        close = 103.8 + .1 * math.sin(i) if flat else 100 + .029 * i + .6 * math.sin(i / 8)
        if i == target:
            close = 105.4 if side == "long" else 102.0
        open_ = close - .15 * math.sin(i / 3)
        if i == target:
            open_ = 103.8
        row = dict(t=start + i * duration, o=open_, h=max(open_, close) + .3,
                   l=min(open_, close) - .3, c=close,
                   md=.025 * math.sin(i) if flat else .6 * math.sin(i / 12),
                   sb=.015 * math.sin(i - 2) if flat else .35 * math.sin((i - 3) / 12),
                   focus=flat, focus_band=.09 if flat else None)
        for length, offset in [(20, .2), (60, .4), (120, .75)]:
            row[f"sma{length}"] = 100 + .026 * i - offset
            row[f"ema{length}"] = 100 + .027 * i - offset + .1
        rows.append(row)
    rows[target].update(md=.7 if side == "long" else -.7, sb=.15 if side == "long" else -.15)
    event = dict(kind="tv_start", symbol="BTC-USDT-SWAP", timeframe="15m", side=side,
                 bar_open_ms=rows[target]["t"], bar_close_ms=rows[target]["t"] + duration,
                 price=rows[target]["c"], near_zero_bars=29,
                 md=rows[target]["md"], sb=rows[target]["sb"])
    return event, rows


@pytest.mark.parametrize("side", ["long", "short"])
def test_png_is_valid_and_within_telegram_photo_limits(side):
    event, rows = sample(side)
    payload = render_signal(event, rows)
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) < 10_000_000
    with Image.open(BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert image.size == SIZE == (1080, 1080)
        assert sum(image.size) <= 10000
        image.verify()


def test_future_quotes_never_change_bytes_or_mutate_inputs():
    event, rows = sample()
    before = deepcopy((event, rows))
    prefix = [row for row in rows if row["t"] <= event["bar_open_ms"]]
    expected = render_signal(event, prefix)
    assert render_signal(event, rows) == expected
    assert (event, rows) == before
    for row in rows:
        if row["t"] > event["bar_open_ms"]:
            row.clear()
            row.update(t=event["bar_open_ms"] + TIMEFRAMES["15m"], o=float("nan"), c=10**30)
    assert render_signal(event, rows) == expected


def test_only_last_120_candles_affect_picture():
    event, rows = sample()
    expected = render_signal(event, rows)
    prefix = [row for row in rows if row["t"] <= event["bar_open_ms"]]
    assert len(prefix) > MAX_CANDLES
    for row in prefix[:-MAX_CANDLES]:
        row.update(o=99999, h=99999, l=99999, c=99999, md=-1e8, sb=1e8)
    assert render_signal(event, prefix) == expected


@pytest.mark.parametrize("change", ["empty", "missing", "duplicate", "gap"])
def test_unavailable_or_ambiguous_target_is_rejected(change):
    event, rows = sample()
    if change == "empty":
        rows = []
    elif change == "missing":
        rows = [r for r in rows if r["t"] != event["bar_open_ms"]]
    elif change == "duplicate":
        rows.append(deepcopy(rows[139]))
    else:
        del rows[130]
    with pytest.raises(ValueError, match="snapshot_"):
        render_signal(event, rows)


def test_one_float_step_price_difference_is_rejected():
    event, rows = sample()
    event["price"] = math.nextafter(event["price"], math.inf)
    with pytest.raises(ValueError, match="signal_price_mismatch"):
        render_signal(event, rows)


@pytest.mark.parametrize("key", ["md", "sb"])
def test_event_indicator_mismatch_is_rejected(key):
    event, rows = sample()
    event[key] = math.nextafter(event[key], math.inf)
    with pytest.raises(ValueError, match=f"signal_{key}_mismatch"):
        render_signal(event, rows)


@pytest.mark.parametrize("key", ["o", "h", "l", "c", "md", "sb", "sma20", "ema20", "sma60", "ema60", "sma120", "ema120"])
def test_missing_target_values_are_rejected(key):
    event, rows = sample()
    rows[139][key] = None
    with pytest.raises(ValueError, match="snapshot_invalid"):
        render_signal(event, rows)


@pytest.mark.parametrize("changes", [{"h": 1}, {"l": 1000}, {"c": float("nan")}, {"o": float("inf")}])
def test_invalid_visible_ohlc_is_rejected(changes):
    event, rows = sample()
    rows[100].update(changes)
    with pytest.raises(ValueError, match="snapshot_invalid"):
        render_signal(event, rows)


def test_wrong_confirmation_clock_or_period_is_rejected():
    event, rows = sample()
    event["bar_close_ms"] += 1
    with pytest.raises(ValueError, match="confirmation_time_mismatch"):
        render_signal(event, rows)
    event["timeframe"] = "1D"
    with pytest.raises(ValueError, match="invalid_event"):
        render_signal(event, rows)


def test_short_ma_aliases_match_real_chart_keys():
    event, rows = sample()
    expected = render_signal(event, rows)
    for row in rows:
        for length in (20, 60, 120):
            row[f"s{length}"] = row.pop(f"sma{length}")
            row[f"e{length}"] = row.pop(f"ema{length}")
    assert render_signal(event, rows) == expected


def test_other_markers_are_not_drawn_and_no_shared_render_state():
    event, rows = sample()
    expected = render_signal(event, rows)
    for row in rows:
        row.update(entry_side="long", exit_side="short", tv_start_side="short",
                   zero_breakout_side="long", retest_side="short", release_side="long")
    with ThreadPoolExecutor(max_workers=2) as pool:
        images = list(pool.map(lambda _: render_signal(event, rows), range(2)))
    assert images == [expected, expected]


def test_real_analyze_chart_contract_without_recalculating_indicators():
    duration = TIMEFRAMES["1H"]
    quotes = [dict(t=i * duration, o=100, h=101, l=99, c=100, v=10) for i in range(353)]
    quotes[-1].update(o=100, h=141, l=99, c=140)
    result = analyze(quotes, [], "1H")
    event = dict(next(e for e in result["events"] if e["kind"] == "tv_start"), symbol="TEST-USDT-SWAP")
    assert render_signal(event, result["chart"]).startswith(b"\x89PNG")


def confirmed_sample(side="long"):
    old, rows = sample(side)
    event = model_event(close=old["bar_close_ms"], timeframe="15m", side=side,
                        price=rows[139]["c"], arrow_price=rows[137]["c"], near_zero_bars=29)
    return event, rows


@pytest.mark.parametrize("side", ["long", "short"])
def test_model_image_ends_at_confirmation_and_preserves_inputs(side):
    event, rows = confirmed_sample(side)
    before = deepcopy((event, rows))
    prefix = rows[:140]
    result = render_signal(event, rows)
    assert result == render_signal(event, prefix)
    assert (event, rows) == before
    for row in rows[140:]:
        row.update(o=float("nan"), c=1e12, md=1e12)
    assert render_signal(event, rows) == result
    with Image.open(BytesIO(result)) as image:
        assert image.size == SIZE
        image.verify()


def test_model_image_draws_purple_core_and_both_clocks(monkeypatch):
    from yoyo.monitor import snapshot
    event, rows = confirmed_sample()
    drawn = {"texts": [], "boxes": [], "lines": []}
    original_text = snapshot._Painter.text
    original_box = snapshot._Painter.rectangle
    original_line = snapshot._Painter.line
    def text(self, xy, value, *args, **kwargs):
        drawn["texts"].append(value)
        return original_text(self, xy, value, *args, **kwargs)
    def box(self, coords, fill, outline=None, width=1):
        drawn["boxes"].append((coords, outline))
        return original_box(self, coords, fill, outline, width)
    def line(self, points, fill, width=1):
        drawn["lines"].append((points, fill))
        return original_line(self, points, fill, width)
    monkeypatch.setattr(snapshot._Painter, "text", text)
    monkeypatch.setattr(snapshot._Painter, "rectangle", box)
    monkeypatch.setattr(snapshot._Painter, "line", line)
    render_signal(event, rows)
    texts = " ".join(drawn["texts"])
    assert "YOLO" in texts
    assert "模型核心区" in texts or "MODEL CORE" in texts
    assert "原箭头" in texts or "ARROW" in texts
    assert "模型确认" in texts or "MODEL CONFIRMED" in texts
    assert any(outline == "#ad8bde" for _, outline in drawn["boxes"])
    assert any(color == "#e5b76b" and points[0][0] == points[1][0]
               for points, color in drawn["lines"])
    assert any(color == "#ad8bde" and points[0][0] == points[1][0]
               for points, color in drawn["lines"])


def test_model_snapshot_rejects_original_price_mismatch_and_unproven_event():
    event, rows = confirmed_sample()
    event["indicator"]["price"] += 1
    with pytest.raises(ValueError, match="original_arrow_price_mismatch"):
        render_signal(event, rows)
    event, rows = confirmed_sample()
    event["model"]["model_sha256"] = "unapproved-model"
    with pytest.raises(ValueError, match="invalid_model_confirmation"):
        render_signal(event, rows)
