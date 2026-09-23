"""Render a bounded live SPIKE chart into the automatic research image input.

The renderer consumes only the supplied live OHLC bars, original SMA/EMA values,
and observation provenance. It uses a fixed light palette so it can run without
a browser, and it never adds outcome, signal, or detector annotations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .images import image_from_bytes
from .schemas import ImageInput

RENDER_VERSION = "spike-vision-auto-light-v1"

WIDTH = 1440
HEIGHT = 800
MAX_CANDLES = 120
UTC_PLUS_8 = timezone(timedelta(hours=8))

# Match the light workbench chart tokens in static/styles.css.
COLORS = {
    "background": "#f4f7f9",
    "surface": "#ffffff",
    "grid": "#edf1f4",
    "border": "#e1e8eb",
    "text": "#1a2b35",
    "text_soft": "#425965",
    "text_muted": "#5c707c",
    "up": "#19835d",
    "down": "#c65369",
    "sma20": "#377d68",
    "ema20": "#7b9f91",
    "sma60": "#487897",
    "ema60": "#869db2",
    "sma120": "#697586",
    "ema120": "#a5adba",
}
MA_KEYS = ("sma20", "ema20", "sma60", "ema60", "sma120", "ema120")


def _timestamp(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer Unix timestamp in milliseconds")
    try:
        datetime.fromtimestamp(value / 1000, UTC_PLUS_8)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"{field} is outside the supported date range") from exc
    return value


def _price(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return result


def _validate_chart(chart: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(chart, dict):
        raise ValueError("live chart must be a mapping")
    rows = chart.get("candles")
    provenance = chart.get("provenance")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_CANDLES:
        raise ValueError(f"live chart must contain 1 to {MAX_CANDLES} candles")
    if not isinstance(provenance, dict):
        raise ValueError("live chart provenance is missing")
    for field in ("symbol", "timeframe"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise ValueError(f"live chart provenance requires {field}")

    observed = _timestamp(provenance.get("observed_at_ms"), "observed_at_ms")
    visible_end = _timestamp(provenance.get("visible_end_ms"), "visible_end_ms")
    last_bar_closed = provenance.get("last_bar_closed")
    if type(last_bar_closed) is not bool:
        raise ValueError("last_bar_closed must be a boolean")
    if visible_end > observed:
        raise ValueError("visible_end_ms cannot be later than observed_at_ms")

    normalized: list[dict[str, Any]] = []
    previous_time = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"candle {index} must be a mapping")
        timestamp = _timestamp(row.get("t"), f"candles[{index}].t")
        if timestamp > observed:
            raise ValueError("live chart contains a candle newer than observed_at_ms")
        if previous_time is not None and timestamp <= previous_time:
            raise ValueError("live candle timestamps must be strictly increasing")
        previous_time = timestamp

        values = {key: _price(row.get(key), f"candles[{index}].{key}")
                  for key in ("o", "h", "l", "c")}
        if (values["l"] > min(values["o"], values["c"]) or
                values["h"] < max(values["o"], values["c"]) or
                values["l"] > values["h"]):
            raise ValueError(f"candle {index} has inconsistent OHLC values")

        closed = row.get("is_closed")
        if type(closed) is not bool:
            raise ValueError(f"candles[{index}].is_closed must be a boolean")
        if index < len(rows) - 1 and not closed:
            raise ValueError("only the newest live candle may be forming")
        if index == len(rows) - 1 and closed != last_bar_closed:
            raise ValueError("last_bar_closed disagrees with the newest candle")

        values.update(t=timestamp, is_closed=closed)
        for key in MA_KEYS:
            ma = row.get(key)
            if ma is None:
                values[key] = None
            else:
                values[key] = _price(ma, f"candles[{index}].{key}")
        normalized.append(values)

    if visible_end < normalized[-1]["t"]:
        raise ValueError("visible_end_ms cannot precede the newest candle")
    return normalized, provenance


def _clock(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, UTC_PLUS_8)


def _centered_text(draw: ImageDraw.ImageDraw, center_x: float, y: int,
                   text: str, font: ImageFont.ImageFont, fill: str) -> None:
    bounds = draw.textbbox((0, 0), text, font=font)
    width = bounds[2] - bounds[0]
    draw.text((int(center_x - width / 2), y), text, font=font, fill=fill)


def _time_ticks(rows: list[dict[str, Any]]) -> list[tuple[int, datetime, bool]]:
    count = len(rows)
    stride = max(1, math.ceil(count / 8))
    indices = set(range(0, count, stride))
    indices.add(count - 1)
    dates = [_clock(row["t"]).date() for row in rows]
    transitions = {index for index in range(count)
                   if index == 0 or dates[index] != dates[index - 1]}
    indices.update(transitions)
    return [(index, _clock(rows[index]["t"]), index in transitions)
            for index in sorted(indices)]


def render_live_chart(chart: dict[str, Any]) -> ImageInput:
    """Render live OHLC and supplied causal MAs into a metadata-free PNG input.

    Time axis uses candle open timestamps in UTC+8. Price limits use only the
    supplied visible candles and their already-computed moving averages.
    """
    rows, provenance = _validate_chart(chart)
    image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["background"])
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=24)
    body_font = ImageFont.load_default(size=16)
    small_font = ImageFont.load_default(size=13)

    symbol = provenance["symbol"].strip()
    timeframe = provenance["timeframe"].strip()
    observed = _clock(provenance["observed_at_ms"])
    visible_end = _clock(provenance["visible_end_ms"])
    last_state = "latest bar closed" if provenance["last_bar_closed"] else "latest bar forming"
    title = f"{symbol}  /  {timeframe}"
    details = (
        f"Observed {observed:%Y-%m-%d %H:%M:%S} UTC+8"
        f"  |  Visible through {visible_end:%Y-%m-%d %H:%M:%S} UTC+8"
        f"  |  {last_state}  |  {len(rows)} bars"
    )

    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=COLORS["background"])
    draw.rectangle((24, 18, WIDTH - 24, HEIGHT - 18), fill=COLORS["surface"])
    draw.text((48, 32), title, font=title_font, fill=COLORS["text"])
    draw.text((48, 69), details, font=body_font, fill=COLORS["text_soft"])

    ma_left, ma_gap, legend_y = 48, 205, 105
    for index, key in enumerate(MA_KEYS):
        x = ma_left + index * ma_gap
        color = COLORS[key]
        draw.line((x, legend_y + 8, x + 22, legend_y + 8), fill=color, width=1)
        draw.text((x + 29, legend_y), key.upper(), font=small_font, fill=COLORS["text_soft"])

    left, right, top, bottom = 54, 1328, 151, 686
    prices = [row[key] for row in rows for key in ("l", "h")]
    prices.extend(row[key] for row in rows for key in MA_KEYS if row[key] is not None)
    low, high = min(prices), max(prices)
    padding = max((high - low) * 0.08, high * 0.001)
    low -= padding
    high += padding
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        raise ValueError("live chart price range is not renderable")
    plot_height = bottom - top
    step = (right - left) / len(rows)

    def x(index: int) -> float:
        return left + (index + 0.5) * step

    def y(price: float) -> float:
        return bottom - (price - low) / (high - low) * plot_height

    for tick in range(6):
        price = low + (high - low) * tick / 5
        line_y = y(price)
        draw.line((left, line_y, right, line_y), fill=COLORS["grid"], width=1)
        label = f"{price:.7g}"
        bounds = draw.textbbox((0, 0), label, font=small_font)
        draw.text((right + 14, int(line_y - (bounds[3] - bounds[1]) / 2)),
                  label, font=small_font, fill=COLORS["text_muted"])

    candle_half_width = max(1, step * 0.31)
    for index, row in enumerate(rows):
        color = COLORS["up"] if row["c"] >= row["o"] else COLORS["down"]
        center = x(index)
        draw.line((center, y(row["h"]), center, y(row["l"])), fill=color, width=1)
        body_top, body_bottom = sorted((y(row["o"]), y(row["c"])))
        draw.rectangle((center - candle_half_width, body_top,
                        center + candle_half_width, max(body_bottom, body_top + 1)),
                       fill=color)

    for key in MA_KEYS:
        segment: list[tuple[float, float]] = []
        for index, row in enumerate(rows):
            if row[key] is not None:
                segment.append((x(index), y(row[key])))
            else:
                if len(segment) > 1:
                    draw.line(segment, fill=COLORS[key], width=1)
                segment = []
        if len(segment) > 1:
            draw.line(segment, fill=COLORS[key], width=1)

    for index, stamp, date_transition in _time_ticks(rows):
        center = x(index)
        draw.line((center, bottom, center, bottom + 4), fill=COLORS["border"], width=1)
        if date_transition:
            _centered_text(draw, center, bottom + 7, stamp.strftime("%m-%d"),
                           small_font, COLORS["text_muted"])
            _centered_text(draw, center, bottom + 24, stamp.strftime("%H:%M"),
                           small_font, COLORS["text_soft"])
        else:
            _centered_text(draw, center, bottom + 12, stamp.strftime("%H:%M"),
                           small_font, COLORS["text_soft"])

    footer = "SMA / EMA values supplied by SPIKE  |  Visible live window only"
    draw.text((48, 760), footer, font=small_font, fill=COLORS["text_muted"])

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False)
    filename = f"{symbol}-{timeframe}-{RENDER_VERSION}.png"
    return image_from_bytes(output.getvalue(), filename)
