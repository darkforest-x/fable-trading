"""Read SPIKE's existing local API without importing or starting its workers.

The input uses only closed OHLC bars and causal SMA/EMA values whose close is
at or before the selected signal close. The visible window is the last 120 bars;
axis bounds are computed from those bars only. Future performance fields,
notification receipts, stops and signal annotations never enter model input.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import threading
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont

from .images import image_from_bytes

MA_COLORS = {"sma20": "#e4b657", "ema20": "#f0d593", "sma60": "#739de7",
             "ema60": "#a3c2fa", "sma120": "#bd87db", "ema120": "#ddc1ef"}
CANDLE_COLORS = {"up": "#3db6a0", "down": "#df6d79"}
CHART_COLORS = {"background": "#10151e", "grid": "#232d3c", "text": "#8999b0",
                "candles": CANDLE_COLORS, "moving_averages": MA_COLORS}


class SourceError(Exception):
    pass


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def causal_candles(payload: Dict[str, Any], signal: Dict[str, Any]):
    cutoff = signal["bar_close_ms"]
    duration = signal["timeframe_min"] * 60_000
    values = payload.get("candles")
    if not isinstance(values, list):
        raise SourceError("SPIKE 图表数据格式不完整")
    rows = []
    previous = None
    for row in values:
        if not isinstance(row, dict) or not finite(row.get("t")):
            raise SourceError("SPIKE K线时间格式不完整")
        t = int(row["t"])
        if t + duration > cutoff:
            continue
        if previous is not None and t <= previous:
            raise SourceError("SPIKE K线时间重复或未排序")
        previous = t
        if not all(finite(row.get(k)) for k in ("o", "h", "l", "c")):
            raise SourceError("SPIKE K线含无效价格")
        if row["l"] > min(row["o"], row["c"]) or row["h"] < max(row["o"], row["c"]) or row["l"] <= 0:
            raise SourceError("SPIKE K线价格关系异常")
        item = {key: row.get(key) for key in ("t", "o", "h", "l", "c")}
        item.update({key: row.get(key) if finite(row.get(key)) else None for key in MA_COLORS})
        rows.append(item)
    rows = rows[-120:]
    if len(rows) < 30 or int(rows[-1]["t"]) + duration != cutoff:
        raise SourceError("当前缓存不足以还原这条候选的决策时点，请换一条候选或上传图片")
    if any(int(b["t"]) - int(a["t"]) != duration for a, b in zip(rows, rows[1:])):
        raise SourceError("决策窗口内有缺失K线，暂不生成识别输入")
    return rows


def render_chart(rows, symbol: str, timeframe: str, cutoff: int):
    """Render the already-truncated causal bars without detector annotations."""
    width, height = 1440, 800
    image = Image.new("RGB", (width, height), "#10151e")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=14)
    title = ImageFont.load_default(size=24)
    stamp = datetime.fromtimestamp(cutoff / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    draw.text((38, 23), f"{symbol}  /  {timeframe}", font=title, fill="#e8edf6")
    draw.text((38, 60), f"Visible through {stamp}  |  {len(rows)} closed bars", font=small, fill="#8999b0")
    for index, (key, color) in enumerate(MA_COLORS.items()):
        draw.text((650 + index * 123, 35), key.upper(), font=small, fill=color)
    left, right, top, bottom = 38, 1310, 115, 710
    prices = [row[k] for row in rows for k in ("l", "h")]
    prices += [row[k] for row in rows for k in MA_COLORS if finite(row.get(k))]
    low, high = min(prices), max(prices)
    padding = max((high - low) * 0.08, high * 0.001)
    low, high = low - padding, high + padding
    step = (right - left) / len(rows)

    def x(i):
        return left + (i + 0.5) * step

    def y(price):
        return bottom - (price - low) / (high - low) * (bottom - top)

    for tick in range(6):
        price = low + (high - low) * tick / 5
        line_y = y(price)
        draw.line((left, line_y, right, line_y), fill="#232d3c")
        draw.text((right + 12, line_y - 7), f"{price:.6g}", font=small, fill="#8999b0")
    half = max(1, step * 0.31)
    for i, row in enumerate(rows):
        color = "#3db6a0" if row["c"] >= row["o"] else "#df6d79"
        draw.line((x(i), y(row["h"]), x(i), y(row["l"])), fill=color, width=1)
        a, b = sorted((y(row["o"]), y(row["c"])))
        draw.rectangle((x(i) - half, a, x(i) + half, max(b, a + 1)), fill=color)
    for key, color in MA_COLORS.items():
        segment = []
        for i, row in enumerate(rows):
            if finite(row.get(key)):
                segment.append((x(i), y(row[key])))
            else:
                if len(segment) > 1:
                    draw.line(segment, fill=color, width=2)
                segment = []
        if len(segment) > 1:
            draw.line(segment, fill=color, width=2)
    for i in range(0, len(rows), 10):
        draw.text((x(i) - 6, bottom + 14), str(i + 1), font=small, fill="#8999b0")
    draw.text((38, 765), "Bar index from left (1-based)  |  Original SPIKE SMA/EMA values", font=small, fill="#718197")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def chart_sha256(rows):
    """Hash the exact normalized OHLC/MA rows offered to the chart renderer."""
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


class SpikeSource:
    def __init__(self, base_url="http://127.0.0.1:8766", transport=None):
        url = urlsplit(base_url)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("SPIKE source must be a local loopback HTTP origin")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._signals = {}
        self._charts = {}
        self._images = {}
        self._lock = threading.RLock()
        self.warning = ""
        self.available = False

    def _get(self, route, params=None):
        try:
            with httpx.Client(timeout=8, trust_env=False, follow_redirects=False, transport=self.transport) as client:
                response = client.get(self.base_url + route, params=params)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Unexpected SPIKE response")
                return result
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError("暂时无法读取现有 SPIKE 服务，请确认 8766 端口服务正在运行；也可以直接上传图片") from exc

    def list_signals(self):
        try:
            payload = self._get("/api/signals", {"source": "live", "confirmation": "raw", "limit": 100})
            if not isinstance(payload.get("items"), list):
                raise SourceError("SPIKE 信号接口格式不完整")
            items = []
            for row in payload["items"]:
                if not isinstance(row, dict) or not row.get("is_closed"):
                    continue
                if not all(finite(row.get(k)) for k in ("bar_close_ms", "timeframe_min", "price")) or row["timeframe_min"] <= 0:
                    continue
                if not all(isinstance(row.get(k), str) and row[k] for k in ("id", "symbol", "timeframe")):
                    continue
                item = {k: row.get(k) for k in ("id", "symbol", "timeframe", "side", "price", "bar_close_ms", "timeframe_min", "protocol", "source_sha256")}
                item["source"] = "spike"
                item["signal_at"] = datetime.fromtimestamp(row["bar_close_ms"] / 1000, timezone.utc).isoformat()
                items.append(item)
            with self._lock:
                self._signals.update({item["id"]: item for item in items})
                self._signals = dict(list(self._signals.items())[-500:])
                self.available, self.warning = True, ""
            return {"items": items, "warning": ""}
        except SourceError as exc:
            self.available, self.warning = False, str(exc)
            return {"items": [], "warning": self.warning}

    def status(self):
        return {"available": self.available, "count": len(self._signals), "source": self.base_url,
                "warning": self.warning}

    def signal_chart(self, signal_id):
        with self._lock:
            cached = self._charts.get(signal_id)
            signal = self._signals.get(signal_id)
        if cached:
            return json.loads(json.dumps(cached))
        if not signal:
            self.list_signals()
            signal = self._signals.get(signal_id)
        if not signal:
            raise SourceError("找不到这条 SPIKE 候选，请刷新列表")
        payload = self._get("/api/chart", {"symbol": signal["symbol"], "timeframe": signal["timeframe"]})
        if payload.get("symbol") != signal["symbol"] or payload.get("timeframe") != signal["timeframe"]:
            raise SourceError("SPIKE 图表与候选标的不一致")
        rows = causal_candles(payload, signal)
        provenance = dict(signal, visible_start_ms=rows[0]["t"], visible_end_ms=signal["bar_close_ms"],
                          bar_count=len(rows), render_version="spike-vision-clean-v1",
                          time_boundary="signal_close", overlay="none")
        result = {"candles": rows, "provenance": provenance,
                  "colors": json.loads(json.dumps(CHART_COLORS)),
                  "chart_sha256": chart_sha256(rows)}
        with self._lock:
            existing = self._charts.setdefault(signal_id, result)
            self._charts = dict(list(self._charts.items())[-100:])
            self._images = {key: value for key, value in self._images.items()
                            if key in self._charts}
        return json.loads(json.dumps(existing))

    def signal_image(self, signal_id):
        with self._lock:
            cached = self._images.get(signal_id)
        if cached:
            return cached
        chart = self.signal_chart(signal_id)
        provenance = chart["provenance"]
        raw = render_chart(chart["candles"], provenance["symbol"], provenance["timeframe"],
                           provenance["bar_close_ms"])
        image = image_from_bytes(raw, provenance["symbol"] + "-" + provenance["timeframe"] + ".png")
        result = (image, provenance)
        with self._lock:
            existing = self._images.setdefault(signal_id, result)
            self._images = dict(list(self._images.items())[-100:])
        return existing
