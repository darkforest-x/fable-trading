"""Render a causal PNG for an IMACD arrow followed by model confirmation.

Uses only the event and its supplied analyze()['chart'] prefix ending at
bar_open_ms (at most 120 candles). OHLC and indicator values are never
recomputed, interpolated or extended into future bars. The event close must
exactly equal the target candle close; earlier missing indicator values leave
gaps, while missing target values are rejected. Local fonts are the only file
reads. No credentials, network, output files or shared drawing state are used.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from yoyo.monitor import MODEL_KIND, MONITORED_TIMEFRAMES, SIGNAL_KIND, TIMEFRAMES
from yoyo.monitor.policy import is_model_signal

SIZE = (1080, 1080)
MAX_CANDLES = 120
_SCALE = 2
_MA = (("sma20", "s20", "#82aaa0"), ("ema20", "e20", "#48695e"),
       ("sma60", "s60", "#7194b0"), ("ema60", "e60", "#405c72"),
       ("sma120", "s120", "#909eae"), ("ema120", "e120", "#556576"))
_CJK_FONTS = ("/System/Library/Fonts/STHeiti Medium.ttc",
              "/System/Library/Fonts/PingFang.ttc",
              "/System/Library/Fonts/Hiragino Sans GB.ttc",
              "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
_LATIN_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/System/Library/Fonts/Supplemental/Arial.ttf")
_TZ = timezone(timedelta(hours=8))


def _number(value, name):
    if value is None or isinstance(value, bool):
        raise ValueError(f"snapshot_invalid_{name}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"snapshot_invalid_{name}") from exc
    if not math.isfinite(number):
        raise ValueError(f"snapshot_invalid_{name}")
    return number


def _integer(value, name):
    number = _number(value, name)
    if number < 0 or number != int(number):
        raise ValueError(f"snapshot_invalid_{name}")
    return int(number)


def _prepare(event, candles):
    if not isinstance(event, dict) or not isinstance(candles, list):
        raise ValueError("snapshot_invalid_input")
    period = event.get("timeframe")
    if period not in MONITORED_TIMEFRAMES or event.get("kind") not in (SIGNAL_KIND, MODEL_KIND):
        raise ValueError("snapshot_invalid_event")
    model_signal = event.get("kind") == MODEL_KIND
    if model_signal and not is_model_signal(event):
        raise ValueError("snapshot_invalid_model_confirmation")
    if event.get("side") not in ("long", "short"):
        raise ValueError("snapshot_invalid_side")
    if not isinstance(event.get("symbol"), str) or not event["symbol"].strip():
        raise ValueError("snapshot_invalid_symbol")
    target = _integer(event.get("bar_open_ms"), "event_open")
    close_ms = _integer(event.get("bar_close_ms"), "event_close")
    if close_ms != target + TIMEFRAMES[period]:
        raise ValueError("snapshot_confirmation_time_mismatch")
    price = _number(event.get("price"), "event_price")
    if price <= 0:
        raise ValueError("snapshot_invalid_event_price")
    _integer(event.get("near_zero_bars"), "near_zero_bars")
    prefix = []
    for raw in candles:
        if not isinstance(raw, dict):
            raise ValueError("snapshot_invalid_candle")
        t = _integer(raw.get("t"), "candle_time")
        if t <= target:
            prefix.append((t, raw))
    if sum(t == target for t, _ in prefix) != 1:
        raise ValueError("snapshot_target_missing_or_duplicate")
    selected = prefix[-MAX_CANDLES:]
    if not selected or selected[-1][0] != target:
        raise ValueError("snapshot_candles_not_ordered")
    result = []
    for index, (t, raw) in enumerate(selected):
        if index and t - selected[index - 1][0] != TIMEFRAMES[period]:
            raise ValueError("snapshot_candles_not_contiguous")
        row = {key: _number(raw.get(key), key) for key in ("o", "h", "l", "c")}
        if row["l"] <= 0 or row["l"] > min(row["o"], row["c"]) or row["h"] < max(row["o"], row["c"]):
            raise ValueError("snapshot_invalid_ohlc")
        row["t"] = t
        for key, alias in [("md", "md"), ("sb", "sb"), *[(a, b) for a, b, _ in _MA]]:
            value = raw.get(key, raw.get(alias))
            row[key] = None if value is None and t != target else _number(value, key)
        row["focus"] = raw.get("focus") is True
        row["focus_band"] = None
        if row["focus"]:
            row["focus_band"] = _number(raw.get("focus_band"), "focus_band")
            if row["focus_band"] <= 0:
                raise ValueError("snapshot_invalid_focus_band")
        result.append(row)
    if Decimal(str(selected[-1][1]["c"])) != Decimal(str(event["price"])):
        raise ValueError("snapshot_signal_price_mismatch")
    if model_signal:
        visible = {row["t"]: row for row in result}
        indicator, model = event["indicator"], event["model"]
        if any(t not in visible for t in (indicator["bar_open_ms"], model["core_start_ms"], model["core_end_ms"])):
            raise ValueError("snapshot_model_context_missing")
        if Decimal(str(visible[indicator["bar_open_ms"]]["c"])) != Decimal(str(indicator["price"])):
            raise ValueError("snapshot_original_arrow_price_mismatch")
    for key in ("md", "sb"):
        if key in event:
            _number(event[key], f"event_{key}")
            if Decimal(str(result[-1][key])) != Decimal(str(event[key])):
                raise ValueError(f"snapshot_signal_{key}_mismatch")
    return result


def _price(value):
    text = format(Decimal(str(value)), ",f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _axis(value):
    return f"{value:,.2f}" if abs(value) >= 10 else f"{value:.6g}"


class _Painter:
    def __init__(self):
        self.image = Image.new("RGB", (SIZE[0] * _SCALE, SIZE[1] * _SCALE), "#0b1218")
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.font_path = next((p for p in _CJK_FONTS if Path(p).is_file()), None)
        self.cjk = self.font_path is not None
        if self.font_path is None:
            self.font_path = next((p for p in _LATIN_FONTS if Path(p).is_file()), None)

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = (ImageFont.truetype(self.font_path, size * _SCALE)
                                if self.font_path else ImageFont.load_default(size=size * _SCALE))
        return self.fonts[size]

    def text(self, xy, text, size=20, fill="#aebec9", anchor="lt", limit=None):
        while limit and size > 12 and self.draw.textlength(text, font=self.font(size)) > limit * _SCALE:
            size -= 1
        self.draw.text(tuple(v * _SCALE for v in xy), text, font=self.font(size), fill=fill, anchor=anchor)

    def line(self, points, fill, width=1):
        self.draw.line([(x * _SCALE, y * _SCALE) for x, y in points], fill=fill,
                       width=max(1, round(width * _SCALE)), joint="curve")

    def rectangle(self, box, fill, outline=None, width=1):
        self.draw.rectangle(tuple(v * _SCALE for v in box), fill=fill, outline=outline,
                            width=max(1, round(width * _SCALE)))

    def polygon(self, points, fill):
        self.draw.polygon([(x * _SCALE, y * _SCALE) for x, y in points], fill=fill)


def render_signal(event: dict, candles: list[dict]) -> bytes:
    """Return a PNG ending at model confirmation, never its future outcome.

    Raises ValueError for invalid event identity/time, missing or duplicate
    target, non-contiguous visible bars, invalid OHLC/indicator values, or any
    difference between the target close and event price. Future candle prices
    and indicators are ignored. Rendering errors are allowed to propagate so
    the delivery layer can choose an explicit text fallback. Inputs unchanged.
    """
    bars = _prepare(event, candles)
    model = event.get("model") if event.get("kind") == MODEL_KIND else None
    p = _Painter()
    chinese = p.cjk
    accent = "#8fe3be" if event["side"] == "long" else "#f299a4"
    up = event["side"] == "long"
    close_time = datetime.fromtimestamp(event["bar_close_ms"] / 1000, _TZ)
    title = f"{event['symbol'].removesuffix('-SWAP').replace('-', ' / ')}  ·  {event['timeframe']}"
    p.text((54, 34), "SPIKE  /  IMACD" + (" + YOLO" if model else ""), 19, "#788d9c")
    p.text((1026, 34), ("指标 + 模型确认" if model else "收盘确认") if chinese else "CONFIRMED CLOSE", 19, "#9caeac", "rt")
    p.text((54, 83), title, 39, "#e5edf1", limit=965)
    p.text((54, 141), _price(event["price"]), 56, "#e5edf1", limit=720)
    p.text((1026, 163), (("多头确认" if up else "空头确认") if model else ("向上启动" if up else "向下启动"))
           if chinese else (("LONG CONFIRMED" if up else "SHORT CONFIRMED") if model else ("LONG RELEASE" if up else "SHORT RELEASE")),
           25, accent, "rt", limit=250)
    p.text((54, 217), close_time.strftime("%m-%d %H:%M") + ("  北京时间" if chinese else "  UTC+8"), 22)
    run = int(event["near_zero_bars"])
    p.text((1026, 217), (f"等待 {model['wait_bars']} 根 · 蓄势 {run} 根" if chinese else f"Wait {model['wait_bars']} bars / buildup {run}")
           if model else (f"蓄势 {run} 根" if chinese else f"{run} bars of buildup"), 22, "#c9b575", "rt")
    p.line([(54, 262), (1026, 262)], "#26333e")
    p.text((54, 282), "价格" if chinese else "PRICE", 18, "#8ca2b1")
    for x0, label, color in [(178, "MA 20", _MA[0][2]), (335, "MA 60", _MA[2][2]), (492, "MA 120", _MA[4][2])]:
        p.line([(x0, 293), (x0 + 28, 293)], color, 1.2)
        p.text((x0 + 38, 282), label, 18, "#879ca9")

    left, right, top, bottom = 54, 901, 332, 680
    count = len(bars)
    step = (right - left) / count
    x = lambda i: left + (i + .5) * step
    values = [b[key] for b in bars for key in ("h", "l", *[m[0] for m in _MA]) if b[key] is not None]
    low, high = min(values), max(values)
    padding = max((high - low) * .13, high * .0003, 1e-15)
    low, high = low - padding, high + padding
    py = lambda value: bottom - (value - low) / (high - low) * (bottom - top)
    for i in range(4):
        value = high - (high - low) * i / 3
        y = py(value)
        p.line([(left, y), (right, y)], "#1b2934")
        p.text((1026, y - 8), _axis(value), 17, "#708795", "rt", limit=115)

    def series(key, scale, color, width):
        segment = []
        for i, bar in enumerate(bars):
            if bar[key] is None:
                if len(segment) > 1:
                    p.line(segment, color, width)
                segment = []
            else:
                segment.append((x(i), scale(bar[key])))
        if len(segment) > 1:
            p.line(segment, color, width)

    target_x = x(count - 1)
    p.rectangle((target_x - max(6, step * .6), top, target_x + max(6, step * .6), bottom), "#182725" if up else "#291d24")
    arrow_index = count - 1
    if model:
        indexes = {bar["t"]: i for i, bar in enumerate(bars)}
        core_first, core_last = indexes[model["core_start_ms"]], indexes[model["core_end_ms"]]
        core = bars[core_first:core_last + 1]
        p.rectangle((x(core_first) - step / 2, py(max(bar["h"] for bar in core)),
                     x(core_last) + step / 2, py(min(bar["l"] for bar in core))),
                    "#201b30", "#ad8bde", 1.4)
        p.text((x(core_first) - step / 2, top - 25), "模型核心区" if chinese else "MODEL CORE", 16, "#ad8bde")
        arrow_index = indexes[event["indicator"]["bar_open_ms"]]
        for index, color in ((arrow_index, "#e5b76b"), (count - 1, "#ad8bde")):
            for y0 in range(top, bottom, 10):
                p.line([(x(index), y0), (x(index), min(y0 + 5, bottom))], color, 1.2)
    for key, _, color in _MA:
        series(key, py, color, 1)
    for i, bar in enumerate(bars):
        color = accent if i == count - 1 else "#6da58e" if bar["c"] >= bar["o"] else "#bf7882"
        half = max(1, min(5, step * .28))
        p.line([(x(i), py(bar["h"])), (x(i), py(bar["l"]))], color, 1.8 if i == count - 1 else 1)
        y0, y1 = sorted((py(bar["o"]), py(bar["c"])))
        p.rectangle((x(i) - half, y0, x(i) + half, max(y0 + 1.5, y1)), color)
    arrow_x = x(arrow_index)
    tip_y = py(bars[arrow_index]["l"]) + 9 if up else py(bars[arrow_index]["h"]) - 9
    p.polygon([(arrow_x, tip_y), (arrow_x - 7, tip_y + (12 if up else -12)),
               (arrow_x + 7, tip_y + (12 if up else -12))], "#e5b76b" if model else accent)
    if model:
        p.text((54, 700), f"原箭头 {event['indicator']['price']:.10g}" if chinese else f"ARROW {event['indicator']['price']:.10g}", 17, "#e5b76b")
        p.text((1026, 700), f"模型确认 {event['price']:.10g}" if chinese else f"MODEL CONFIRMED {event['price']:.10g}", 17, "#ad8bde", "rt")
    target_y = py(bars[-1]["c"])
    p.line([(target_x + 6, target_y), (923, target_y)], accent, 1.2)
    p.rectangle((917, target_y - 18, 1037, target_y + 18), "#16362c" if up else "#3b242d", accent)
    p.text((1025, target_y - 9), _price(event["price"]), 19, accent, "rt", limit=98)

    p.line([(54, 727), (1026, 727)], "#26333e")
    p.text((54, 750), "IMACD", 20, "#a9bcc8")
    p.line([(206, 761), (235, 761)], "#7fa5ed", 2)
    p.text((247, 750), "主线" if chinese else "Impulse", 18, "#889dad")
    p.line([(369, 761), (398, 761)], "#e5b76b", 2)
    p.text((410, 750), "信号线" if chinese else "Signal", 18, "#889dad")
    p.text((1026, 750), "蓄势区" if chinese else "Buildup", 18, "#c9b575", "rt")
    mt, mb = 801, 962
    impulse = [0.0] + [b[k] for b in bars for k in ("md", "sb") if b[k] is not None]
    impulse += [v for b in bars if b["focus"] for v in (b["focus_band"], -b["focus_band"])]
    mlow, mhigh = min(impulse), max(impulse)
    gap = max((mhigh - mlow) * .15, (high - low) * .0001, 1e-15)
    mlow, mhigh = mlow - gap, mhigh + gap
    my = lambda value: mb - (value - mlow) / (mhigh - mlow) * (mb - mt)
    for i, bar in enumerate(bars):
        if bar["focus"]:
            x0, x1 = x(i) - step / 2, x(i) + step / 2
            y0, y1 = my(bar["focus_band"]), my(-bar["focus_band"])
            p.rectangle((x0, y0, x1, y1), "#29291f")
            p.line([(x0, y0), (x1, y0)], "#675c37")
            p.line([(x0, y1), (x1, y1)], "#675c37")
    zero = my(0)
    for start in range(left, right, 11):
        p.line([(start, zero), (min(start + 5, right), zero)], "#71818d")
    p.text((941, zero - 9), "0.00", 18, "#a2b0b9")
    series("md", my, "#7fa5ed", 2)
    series("sb", my, "#e5b76b", 2)
    if model:
        for index, color in ((arrow_index, "#e5b76b"), (count - 1, "#ad8bde")):
            for y0 in range(mt, mb, 10):
                p.line([(x(index), y0), (x(index), min(y0 + 5, mb))], color, 1.2)
    p.rectangle((target_x - 3, my(bars[-1]["md"]) - 3, target_x + 3, my(bars[-1]["md"]) + 3), accent)
    for i in sorted({0, (count - 1) // 2, count - 1}):
        label = datetime.fromtimestamp(bars[i]["t"] / 1000, _TZ).strftime("%m-%d %H:%M")
        p.text((x(i), 990), label, 17, "#6f8696", "lt" if i == 0 else "rt" if i == count - 1 else "mt")
    p.text((54, 1042), ("行情截至模型确认 · 不含后续走势" if model else "只展示信号当根及之前行情")
           if chinese else ("History ends at model confirmation" if model else "History ends at the signal candle"), 18, "#6d8391")
    p.text((1026, 1042), f"{count} 根 K 线" if chinese else f"{count} candles", 18, "#6d8391", "rt")
    image = p.image.resize(SIZE, Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="PNG", compress_level=6)
    return output.getvalue()
