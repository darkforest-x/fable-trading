"""Build an anonymized, staged OHLC review deck for 12h first-touch events.

The source table is read only for the supplied hand-selected Binance cases.
All chart data and matched controls are reconstructed from the registered raw
hourly candles; future outcome columns are never loaded or used for selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[3]
SOURCE_EXP = ROOT / "experiments/active/exp-alt-isolated-12h-firstweek-wicks-20261111-v1"
sys.path.insert(0, str(SOURCE_EXP))

import strategy_signals  # noqa: E402
from census12 import read_raw  # noqa: E402


HOUR = strategy_signals.HOUR
BAR = 12 * HOUR
SLOT = strategy_signals.SLOT
OUT = strategy_signals.OUT / "blind_review_v1" / "deck"
REFERENCE = (
    ROOT
    / "data/research/alt-isolated-12h-wicks-20261111/expanded12/owner_reference"
    / "原图15张_OKX与币安逐张对照.csv"
)
PAGE_SIZE = 10
CHART_COLUMNS = 2
CELL_W = 640
CELL_H = 300
Y_LIMIT_PCT = 50.0
ID_SALT = b"month-firstweek-blind-review-v1-20261111"


@dataclass(frozen=True)
class Candle:
    open_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class TouchEvent:
    symbol: str
    month: str
    side: str
    rail_side: str
    open_ms: int
    rail: float
    month_end_ms: int


@dataclass
class ReviewSample:
    pair_id: str
    role: str
    event: TouchEvent
    blind_id: str = ""
    control_of: str = ""
    phase1: list[Candle] | None = None
    phase2: Candle | None = None
    phase3: list[Candle] | None = None
    phase3_summary: dict | None = None


def _timestamp_ms(value: pd.Timestamp) -> int:
    return int(value.tz_convert("UTC").timestamp() * 1000)


def _beijing_stamp(open_ms: int) -> str:
    return (
        pd.Timestamp(open_ms, unit="ms", tz="UTC")
        .tz_convert("Asia/Shanghai")
        .strftime("%Y-%m-%d %H:%M")
    )


def _complete_12h_bars(raw: np.ndarray) -> dict[int, Candle]:
    """Aggregate only complete, aligned runs of twelve hourly OHLC bars."""
    if raw is None or not len(raw):
        return {}
    q = np.asarray(raw, dtype=float)
    q = q[q[:, 0] + HOUR <= strategy_signals.CUTOFF]
    if not len(q):
        return {}
    times = q[:, 0].astype(np.int64)
    if np.any(times % HOUR) or np.any(np.diff(times) <= 0):
        raise ValueError("read_raw returned unaligned or non-increasing hourly candles")

    result: dict[int, Candle] = {}
    for bucket in np.unique(times // BAR):
        start = int(bucket * BAR)
        expected = start + np.arange(12, dtype=np.int64) * HOUR
        at = int(np.searchsorted(times, start))
        stop = at + 12
        if stop > len(times) or not np.array_equal(times[at:stop], expected):
            continue
        rows = q[at:stop, 1:5]
        result[start] = Candle(
            open_ms=start,
            open=float(rows[0, 0]),
            high=float(np.max(rows[:, 1])),
            low=float(np.min(rows[:, 2])),
            close=float(rows[-1, 3]),
        )
    return result


def _monthly_first_touches(symbol: str, raw: np.ndarray, bars: dict[int, Candle]) -> list[TouchEvent]:
    """Return the first complete 12h touch per symbol/month/rail.

    The first-week anchor and active-window cutoff follow strategy_signals.
    Only completed bars before its CUTOFF can become events.
    """
    if raw is None or not len(raw):
        return []
    q = np.asarray(raw, dtype=float)
    q = q[q[:, 0] + HOUR <= strategy_signals.CUTOFF]
    if not len(q):
        return []
    times = q[:, 0].astype(np.int64)
    events: list[TouchEvent] = []

    for month, start_ms, ready_ms, end_ms in strategy_signals.WINDOWS:
        start_ms, ready_ms, end_ms = map(int, (start_ms, ready_ms, end_ms))
        if ready_ms < times[0] or ready_ms >= times[-1] + HOUR:
            continue
        i, j = np.searchsorted(times, [start_ms, ready_ms])
        expected_anchor_hours = (ready_ms - start_ms) // HOUR
        if j - i != expected_anchor_hours:
            continue
        anchor = q[i:j, 1:5]
        week_high = float(anchor[:, 1].max())
        week_low = float(anchor[:, 2].min())
        if week_high <= week_low:
            continue
        rails = {
            "lower": week_low - 0.618 * (week_high - week_low),
            "upper": week_high + 0.618 * (week_high - week_low),
        }

        active_limit = min(end_ms, strategy_signals.CUTOFF)
        active_hours = max(0, (active_limit - ready_ms) // HOUR)
        if active_hours <= 0:
            continue
        active_times = times[j : j + active_hours]
        expected = ready_ms + np.arange(len(active_times), dtype=np.int64) * HOUR
        missing = np.flatnonzero(active_times != expected)
        contiguous_hours = int(missing[0]) if len(missing) else len(active_times)
        contiguous_end_ms = ready_ms + contiguous_hours * HOUR

        active = q[j : j + contiguous_hours]
        active_times = times[j : j + contiguous_hours]
        if not len(active):
            continue
        for rail_side, rail in rails.items():
            # Find the first hourly contact after ready, then use its complete
            # UTC-aligned 12h candle. This retains a candle that opened before
            # ready when the actual touch happened after ready.
            side = "long" if rail_side == "lower" else "short"
            eps = abs(rail) * 1e-12
            touched_hours = np.flatnonzero(
                (active[:, 3] <= rail + eps) & (active[:, 2] >= rail - eps)
            )
            if not len(touched_hours):
                continue
            bar_open_ms = int(active_times[int(touched_hours[0])] // BAR * BAR)
            first = bars.get(bar_open_ms)
            if first is None:
                continue
            events.append(
                TouchEvent(symbol, month, side, rail_side, first.open_ms, float(rail), end_ms)
            )
    return events


def _first_reference_bar(value: object, month: str) -> int:
    """Parse the first Binance 12h open listed in the supplied reference row."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing Binance touch-bar time")
    first = value.split(",", 1)[0].strip()
    year = int(month[:4])
    local = pd.Timestamp(f"{year}-{first}").tz_localize("Asia/Shanghai")
    return _timestamp_ms(local)


def _candidate_specs(reference_csv: Path) -> tuple[list[dict], int]:
    columns = ["合约", "区间月（纽约）", "侧", "币安_触轨12h根数", "币安_K线开盘（北京）"]
    frame = pd.read_csv(reference_csv, usecols=columns)
    frame = frame[frame["币安_触轨12h根数"].notna()].copy()
    specs: list[dict] = []
    for row in frame[columns].to_dict("records"):
        symbol = row["合约"]
        month = row["区间月（纽约）"]
        rail_side_cn = row["侧"]
        listed_opens = row["币安_K线开盘（北京）"]
        specs.append(
            {
                "symbol": str(symbol),
                "month": str(month),
                "side": "long" if rail_side_cn == "下" else "short",
                "rail_side": "lower" if rail_side_cn == "下" else "upper",
                "listed_open_ms": _first_reference_bar(listed_opens, str(month)),
            }
        )
    # The explicitly requested PEPE case joins the hand-selected candidates.
    specs.append(
        {
            "symbol": "1000PEPEUSDT",
            "month": "2025-06",
            "side": "long",
            "rail_side": "lower",
            "listed_open_ms": None,
        }
    )
    return specs, len(frame)


def _event_key(event: TouchEvent) -> tuple[str, str, str, int]:
    return (event.symbol, event.month, event.side, event.open_ms)


def _find_candidate(spec: dict, events: list[TouchEvent]) -> TouchEvent | None:
    matches = [
        e
        for e in events
        if e.symbol == spec["symbol"]
        and e.month == spec["month"]
        and e.side == spec["side"]
    ]
    if not matches:
        return None
    listed = spec["listed_open_ms"]
    if listed is not None:
        exact = [e for e in matches if e.open_ms == listed]
        if exact:
            return exact[0]
        # The file's listed sequence may include non-first intramonth touches;
        # preserve its first listed bar only when it is a valid complete touch.
        return None
    return min(matches, key=lambda e: e.open_ms)


def _nearest_control(case: TouchEvent, events: Iterable[TouchEvent]) -> TouchEvent | None:
    possible = [
        e
        for e in events
        if e.symbol == case.symbol and e.side == case.side and e.month != case.month
    ]
    if not possible:
        return None
    return min(possible, key=lambda e: (abs(e.open_ms - case.open_ms), e.open_ms, e.month))


def _sample_window(event: TouchEvent, bars: dict[int, Candle]) -> tuple[list[Candle], Candle] | None:
    prior = [bars.get(event.open_ms - k * BAR) for k in range(5, -1, -1)]
    next_bar = bars.get(event.open_ms + BAR)
    if any(bar is None for bar in prior) or next_bar is None:
        return None
    return [bar for bar in prior if bar is not None], next_bar


def _find_phase3(event: TouchEvent, bars: dict[int, Candle]) -> tuple[list[Candle], dict] | None:
    """Find the first return to the rail after at least one clear full 12h bar."""
    first = bars.get(event.open_ms)
    if first is None:
        return None
    eps = abs(event.rail) * 1e-12
    ordered = [first]
    offset = 1
    while event.open_ms + offset * BAR + BAR <= event.month_end_ms:
        bar = bars.get(event.open_ms + offset * BAR)
        if bar is None:
            break
        ordered.append(bar)
        offset += 1
    prior_touches = 1
    consecutive_clear = 0
    for index, bar in enumerate(ordered[1:], start=1):
        touches = bar.low <= event.rail + eps and bar.high >= event.rail - eps
        if touches and consecutive_clear >= 1:
            candles = ordered[: index + 1]
            prior = candles[:-1]
            sign = 1 if event.side == "long" else -1
            closed_outside = any(sign * (p.close / event.rail - 1) < 0 for p in prior)
            return candles, {
                "bars_since_first_touch": index,
                "prior_touch_bars": prior_touches,
                "consecutive_clear_bars": consecutive_clear,
                "closed_outside_before_retest": int(closed_outside),
            }
        if touches:
            prior_touches += 1
            consecutive_clear = 0
        else:
            consecutive_clear += 1
    return None


def _blind_id(event: TouchEvent) -> str:
    plain = "\0".join((event.symbol, event.month, event.side, str(event.open_ms))).encode()
    digest = hashlib.blake2s(plain, key=ID_SALT, digest_size=6).hexdigest().upper()
    return "R" + digest


def _normalized(candle: Candle, rail: float, side: str) -> tuple[float, float, float, float]:
    sign = 1.0 if side == "long" else -1.0
    return tuple(
        100.0 * sign * (value / rail - 1.0)
        for value in (candle.open, candle.high, candle.low, candle.close)
    )


def _write_numeric_csv(samples: list[ReviewSample], out: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    phase1_rows: list[dict] = []
    phase2_rows: list[dict] = []
    phase3_rows: list[dict] = []
    for sample in samples:
        assert sample.phase1 is not None and sample.phase2 is not None
        for offset, candle in zip(range(-5, 1), sample.phase1):
            op, hi, lo, cl = _normalized(candle, sample.event.rail, sample.event.side)
            phase1_rows.append(
                dict(blind_id=sample.blind_id, bar_offset=offset,
                     open_pct=op, high_pct=hi, low_pct=lo, close_pct=cl)
            )
        op, hi, lo, cl = _normalized(sample.phase2, sample.event.rail, sample.event.side)
        phase2_rows.append(
            dict(blind_id=sample.blind_id, bar_offset=1,
                 open_pct=op, high_pct=hi, low_pct=lo, close_pct=cl)
        )
        if sample.phase3 is None or sample.phase3_summary is None:
            continue
        retest_offset = int(sample.phase3_summary["bars_since_first_touch"])
        for offset, candle in enumerate(sample.phase3):
            op, hi, lo, cl = _normalized(candle, sample.event.rail, sample.event.side)
            eps = abs(sample.event.rail) * 1e-12
            touched = int(candle.low <= sample.event.rail + eps and candle.high >= sample.event.rail - eps)
            is_retest = int(offset == retest_offset)
            phase3_rows.append(
                dict(
                    blind_id=sample.blind_id,
                    bar_offset=offset,
                    open_pct=op,
                    high_pct=hi,
                    low_pct=lo,
                    close_pct=cl,
                    is_touch=touched,
                    is_retest=is_retest,
                    bars_since_first_touch=offset,
                    prior_touch_bars=(sample.phase3_summary["prior_touch_bars"] if is_retest else np.nan),
                    consecutive_clear_bars=(sample.phase3_summary["consecutive_clear_bars"] if is_retest else np.nan),
                    closed_outside_before_retest=(sample.phase3_summary["closed_outside_before_retest"] if is_retest else np.nan),
                )
            )

    phase1_columns = ["blind_id", "bar_offset", "open_pct", "high_pct", "low_pct", "close_pct"]
    phase2_columns = phase1_columns.copy()
    phase3_columns = [
        "blind_id", "bar_offset", "open_pct", "high_pct", "low_pct", "close_pct",
        "is_touch", "is_retest", "bars_since_first_touch", "prior_touch_bars",
        "consecutive_clear_bars", "closed_outside_before_retest",
    ]
    p1 = pd.DataFrame(phase1_rows, columns=phase1_columns)
    p2 = pd.DataFrame(phase2_rows, columns=phase2_columns)
    p3 = pd.DataFrame(phase3_rows, columns=phase3_columns)
    p1.to_csv(out / "phase1.csv", index=False, float_format="%.8f")
    p2.to_csv(out / "phase2.csv", index=False, float_format="%.8f")
    p3.to_csv(out / "phase3.csv", index=False, float_format="%.8f")
    return p1, p2, p3


def _font(size: int = 16) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except OSError:
        return ImageFont.load_default()


def _draw_chart(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    blind_id: str,
    candles: list[Candle],
    offsets: list[int],
    rail: float,
    side: str,
) -> None:
    left, right = x0 + 48, x0 + CELL_W - 18
    top, bottom = y0 + 34, y0 + CELL_H - 32
    draw.text((x0 + 16, y0 + 7), blind_id, font=_font(18), fill="#20252B")
    y = lambda value: int(top + (Y_LIMIT_PCT - value) / (2 * Y_LIMIT_PCT) * (bottom - top))
    for pct in (-40, -20, 0, 20, 40):
        yy = y(pct)
        color = "#252B33" if pct == 0 else "#E0E4E8"
        width = 2 if pct == 0 else 1
        draw.line((left, yy, right, yy), fill=color, width=width)
        draw.text((x0 + 7, yy - 7), f"{pct}", font=_font(11), fill="#7A838D")
    if not candles:
        return
    slot_w = (right - left) / len(candles)
    body_w = max(4, min(15, int(slot_w * 0.42)))
    for i, (bar, offset) in enumerate(zip(candles, offsets)):
        op, hi, lo, cl = _normalized(bar, rail, side)
        xx = int(left + (i + 0.5) * slot_w)
        clipped_hi = min(Y_LIMIT_PCT, max(-Y_LIMIT_PCT, hi))
        clipped_lo = min(Y_LIMIT_PCT, max(-Y_LIMIT_PCT, lo))
        clipped_op = min(Y_LIMIT_PCT, max(-Y_LIMIT_PCT, op))
        clipped_cl = min(Y_LIMIT_PCT, max(-Y_LIMIT_PCT, cl))
        color = "#258B58" if cl >= op else "#C64C4C"
        draw.line((xx, y(clipped_hi), xx, y(clipped_lo)), fill="#59636F", width=2)
        body_top = min(y(clipped_op), y(clipped_cl))
        body_bottom = max(y(clipped_op), y(clipped_cl))
        if body_bottom - body_top < 2:
            body_bottom = body_top + 2
        draw.rectangle((xx - body_w // 2, body_top, xx + body_w // 2, body_bottom), fill=color)
        draw.text((xx - 8, bottom + 7), f"{offset:+d}" if offset > 0 else str(offset), font=_font(11), fill="#69727D")


def _render_pages(samples: list[ReviewSample], out: Path, phase: int) -> None:
    page_dir = out / f"phase{phase}"
    page_dir.mkdir(parents=True, exist_ok=True)
    for old in page_dir.glob("page_*.png"):
        old.unlink()
    ordered = sorted(samples, key=lambda sample: sample.blind_id)
    rows = math.ceil(PAGE_SIZE / CHART_COLUMNS)
    for start in range(0, len(ordered), PAGE_SIZE):
        page = ordered[start : start + PAGE_SIZE]
        image = Image.new("RGB", (CHART_COLUMNS * CELL_W, rows * CELL_H), "white")
        draw = ImageDraw.Draw(image)
        for position, sample in enumerate(page):
            col, row = position % CHART_COLUMNS, position // CHART_COLUMNS
            x0, y0 = col * CELL_W, row * CELL_H
            if phase == 1:
                candles = sample.phase1 or []
                offsets = list(range(-5, 1))
            else:
                candles = (sample.phase1 or []) + ([sample.phase2] if sample.phase2 else [])
                offsets = list(range(-5, 2))
            _draw_chart(draw, x0, y0, sample.blind_id, candles, offsets,
                        sample.event.rail, sample.event.side)
            draw.rectangle((x0, y0, x0 + CELL_W - 1, y0 + CELL_H - 1), outline="#D4D9DE", width=1)
        image.save(page_dir / f"page_{start // PAGE_SIZE + 1:03d}.png")


def _write_key(samples: list[ReviewSample], out: Path) -> None:
    rows = []
    for sample in sorted(samples, key=lambda s: s.blind_id):
        event = sample.event
        retest_open = None
        if sample.phase3 is not None and len(sample.phase3) > 1:
            retest_open = sample.phase3[-1].open_ms
        rows.append(
            {
                "blind_id": sample.blind_id,
                "group": "hand_selected" if sample.role == "case" else "ordinary_control",
                "pair_id": sample.pair_id,
                "role": sample.role,
                "symbol": event.symbol,
                "month": event.month,
                "side": event.side,
                "rail_side": event.rail_side,
                "first_touch_open_ms": event.open_ms,
                "first_touch_open_beijing": _beijing_stamp(event.open_ms),
                "phase3_retest_open_ms": retest_open,
                "phase3_retest_open_beijing": _beijing_stamp(retest_open) if retest_open is not None else "",
                "control_of": sample.control_of,
            }
        )
    pd.DataFrame(rows).to_csv(out / "key.csv", index=False)


def build_blind_deck(
    output_dir: Path | str | None = None,
    reference_csv: Path | str | None = None,
) -> dict:
    """Build the staged blind review files and return a count-only manifest."""
    output = Path(output_dir) if output_dir is not None else OUT
    reference = Path(reference_csv) if reference_csv is not None else REFERENCE
    output.mkdir(parents=True, exist_ok=True)

    specs, reference_case_count = _candidate_specs(reference)
    symbols = sorted({spec["symbol"] for spec in specs})
    raw_by_symbol: dict[str, np.ndarray] = {}
    bars_by_symbol: dict[str, dict[int, Candle]] = {}
    events_by_symbol: dict[str, list[TouchEvent]] = {}
    for symbol in symbols:
        raw = read_raw(symbol)
        raw_by_symbol[symbol] = raw
        bars = _complete_12h_bars(raw)
        bars_by_symbol[symbol] = bars
        events_by_symbol[symbol] = _monthly_first_touches(symbol, raw, bars)

    case_records: list[tuple[dict, TouchEvent]] = []
    missing_cases = 0
    for spec in specs:
        event = _find_candidate(spec, events_by_symbol.get(spec["symbol"], []))
        if event is None:
            missing_cases += 1
            continue
        case_records.append((spec, event))

    samples: list[ReviewSample] = []
    no_control = 0
    no_window = 0
    control_reused = 0
    control_use_counts: dict[tuple[str, str, str, int], int] = {}
    all_events = [event for group in events_by_symbol.values() for event in group]
    for pair_number, (_, case) in enumerate(case_records, start=1):
        pair_id = f"P{pair_number:03d}"
        control = _nearest_control(case, all_events)
        pair_events = [("case", case)]
        if control is None:
            no_control += 1
        else:
            pair_events.append(("control", control))
            key = _event_key(control)
            control_use_counts[key] = control_use_counts.get(key, 0) + 1
        for role, event in pair_events:
            window = _sample_window(event, bars_by_symbol.get(event.symbol, {}))
            if window is None:
                no_window += 1
                continue
            phase1, phase2 = window
            sample = ReviewSample(pair_id=pair_id, role=role, event=event,
                                  phase1=phase1, phase2=phase2)
            later = _find_phase3(event, bars_by_symbol.get(event.symbol, {}))
            if later is not None:
                sample.phase3, sample.phase3_summary = later
            samples.append(sample)

    # Keep IDs stable if the same source population is rebuilt; IDs do not encode
    # role, symbol, direction, date, pair, or outcome.
    for sample in samples:
        sample.blind_id = _blind_id(sample.event)
    if len({sample.blind_id for sample in samples}) != len(samples):
        raise RuntimeError("blind ID collision")
    for sample in samples:
        if sample.role == "control":
            case = next((s for s in samples if s.pair_id == sample.pair_id and s.role == "case"), None)
            sample.control_of = case.blind_id if case else ""

    # Controls are selected independently by nearest time distance. Record reuse
    # only as a count; no sample identity appears in the manifest.
    control_reused = sum(count - 1 for count in control_use_counts.values() if count > 1)
    p1, p2, p3 = _write_numeric_csv(samples, output)
    _render_pages(samples, output, phase=1)
    _render_pages(samples, output, phase=2)
    _write_key(samples, output)

    manifest = {
        "schema": "blind_review_v1",
        "hand_selected_reference_rows": reference_case_count,
        "hand_selected_requested": len(specs),
        "hand_selected_resolved": len(case_records),
        "hand_selected_missing": missing_cases,
        "cases_without_control": no_control,
        "reused_control_assignments": control_reused,
        "events_missing_six_bar_context_or_next_bar": no_window,
        "blind_samples": len(samples),
        "phase1_rows": len(p1),
        "phase2_rows": len(p2),
        "phase3_samples": int(p3["blind_id"].nunique()) if not p3.empty else 0,
        "phase3_rows": len(p3),
        "page_size_max": PAGE_SIZE,
        "phase1_axes_pct": [-Y_LIMIT_PCT, Y_LIMIT_PCT],
        "outputs": ["phase1.csv", "phase2.csv", "phase3.csv", "key.csv", "phase1/", "phase2/"],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()
    # Deliberately print count-only metadata; never print the anonymous key or outcomes.
    print(json.dumps(build_blind_deck(args.out, args.reference), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
