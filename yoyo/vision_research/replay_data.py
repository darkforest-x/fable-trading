"""Read-only, causal local OHLC history for historical vision replay.

The loader reads only first-level OKX CSV files from the configured roots. It
does not fetch, resample, repair, or write market data. Close SMA/EMA values
match ``yoyo.evaluation.spike_burst_replay`` and are seeded from the first bar
of the selected continuous segment, before trimming the visible replay range.
"""
from __future__ import annotations

import hashlib
import io
import math
import re
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .source import SourceError as VisionSourceError


_BAR_MINUTES = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60}
_BAR_PATTERN = "|".join(sorted(_BAR_MINUTES, key=len, reverse=True))
_FILENAME = re.compile(
    rf"^(?:okx_)?(?P<symbol>.+)_(?P<timeframe>{_BAR_PATTERN})_(?P<count>[0-9]+)(?:_latest)?\.csv$"
)
_REQUIRED_COLUMNS = {"open_time", "open", "high", "low", "close"}
_DISPLAY_MA_PERIODS = (20, 60, 120)
_MIN_SEGMENT_BARS = 239
_MAX_PRIOR_BARS = 1120
_MAX_FUTURE_BARS = 2000
_ALGORITHM_VERSION = "okx-close-sma-ema-v1"
_SOURCE_LABEL = "OKX 本地历史"


class SourceError(VisionSourceError):
    """A user-readable reason why a local replay source cannot be used."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class LocalReplayHistory:
    """Index and load closed OKX OHLC bars without network or filesystem writes.

    ``roots`` may be one directory or a sequence of directories. Only direct
    ``*.csv`` children are indexed. The default roots are the repository's old
    read-only cache symlink and its fetched-history directory.
    """

    def __init__(self, roots=None, clock: Callable[[], int] | None = None):
        repo_root = Path(__file__).resolve().parents[2]
        if roots is None:
            root_values = (repo_root / "data" / "kline_cache", repo_root / "data" / "kline_fetched")
        elif isinstance(roots, (str, Path)):
            root_values = (Path(roots),)
        else:
            root_values = tuple(Path(root) for root in roots)
        self.roots = tuple(root.expanduser() for root in root_values)
        self.clock = clock or (lambda: time.time_ns() // 1_000_000)
        self._lock = threading.RLock()
        self._index_signature = None
        self._index: dict[tuple[str, str], list[Path]] = {}
        self._root_availability: tuple[bool, ...] = ()

    @staticmethod
    def _canonical_symbol(raw_symbol: str) -> str:
        return raw_symbol.replace("_", "-").upper()

    def _signature(self):
        signature = []
        available = []
        for root in self.roots:
            try:
                stat = root.stat()
                is_directory = root.is_dir()
                available.append(is_directory)
                signature.append((str(root), stat.st_mtime_ns if is_directory else -1))
            except OSError:
                available.append(False)
                signature.append((str(root), -1))
        return tuple(signature), tuple(available)

    def _ensure_index(self) -> dict[tuple[str, str], list[Path]]:
        signature, availability = self._signature()
        with self._lock:
            if signature == self._index_signature:
                return self._index
            groups: dict[tuple[str, str], list[Path]] = {}
            for root, is_directory in zip(self.roots, availability):
                if not is_directory:
                    continue
                try:
                    paths = sorted(root.glob("*.csv"))
                except OSError:
                    continue
                for path in paths:
                    if not path.is_file() or path.name.startswith(("gate_", "binance_", "bybit_")):
                        continue
                    match = _FILENAME.fullmatch(path.name)
                    if match is None:
                        continue
                    symbol = self._canonical_symbol(match.group("symbol"))
                    timeframe = match.group("timeframe")
                    groups.setdefault((symbol, timeframe), []).append(path)
            self._index = {key: sorted(set(paths)) for key, paths in groups.items()}
            self._index_signature = signature
            self._root_availability = availability
            return self._index

    def catalog(self) -> dict:
        """Return available local markets; coverage is verified by ``load``.

        Coverage timestamps are omitted here so cataloging never reads every
        potentially large history file. ``load`` gives a precise coverage error
        for a requested start time.
        """
        index = self._ensure_index()
        items = [
            {"symbol": symbol, "timeframe": timeframe, "source_label": _SOURCE_LABEL}
            for symbol, timeframe in sorted(index)
        ]
        if not any(self._root_availability):
            warning = "本地 OKX 行情目录不可用"
        elif not items:
            warning = "没有可用的本地 OKX 历史行情"
        elif not all(self._root_availability):
            warning = "部分本地 OKX 行情目录不可用"
        else:
            warning = ""
        return {"items": items, "warning": warning}

    @staticmethod
    def _read_timestamp(open_time, ts_value, duration_ms: int) -> int:
        open_text = str(open_time).strip()
        if ts_value is not None:
            try:
                numeric_ts = float(str(ts_value).strip())
            except (TypeError, ValueError) as exc:
                raise SourceError("bad_timestamp", "本地行情含无效时间戳，无法回放") from exc
            if not math.isfinite(numeric_ts) or not numeric_ts.is_integer():
                raise SourceError("bad_timestamp", "本地行情含无效时间戳，无法回放")
            stamp = int(numeric_ts)
            if not re.fullmatch(r"[+-]?\d+(?:\.0+)?", open_text):
                try:
                    parsed = pd.Timestamp(open_text)
                    if pd.isna(parsed):
                        raise ValueError("missing open_time")
                    if parsed.tzinfo is None:
                        parsed = parsed.tz_localize("UTC")
                    else:
                        parsed = parsed.tz_convert("UTC")
                    parsed_ms = int(parsed.value // 1_000_000)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise SourceError("bad_timestamp", "本地行情含无效 open_time，无法回放") from exc
                if parsed_ms != stamp:
                    raise SourceError("timestamp_conflict", "本地行情 open_time 与 ts 不一致，无法回放")
        else:
            if re.fullmatch(r"[+-]?\d+(?:\.0+)?", open_text):
                try:
                    stamp = int(float(open_text))
                except (ValueError, OverflowError) as exc:
                    raise SourceError("bad_timestamp", "本地行情含无效时间戳，无法回放") from exc
            else:
                try:
                    parsed = pd.Timestamp(open_text)
                    if pd.isna(parsed):
                        raise ValueError("missing open_time")
                    if parsed.tzinfo is None:
                        parsed = parsed.tz_localize("UTC")
                    else:
                        parsed = parsed.tz_convert("UTC")
                    stamp = int(parsed.value // 1_000_000)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise SourceError("bad_timestamp", "本地行情含无效时间戳，无法回放") from exc
        if stamp < 0 or stamp % duration_ms:
            raise SourceError("bad_timestamp_alignment", "本地行情时间与周期边界不一致，无法回放")
        return stamp

    @staticmethod
    def _parse_confirm(value) -> int:
        try:
            numeric = float(str(value).strip())
        except (TypeError, ValueError) as exc:
            raise SourceError("bad_confirm", "本地行情确认标记异常，无法回放") from exc
        if not math.isfinite(numeric) or numeric not in (0.0, 1.0):
            raise SourceError("bad_confirm", "本地行情确认标记异常，无法回放")
        return int(numeric)

    @staticmethod
    def _parse_ohlc(row) -> tuple[float, float, float, float]:
        try:
            values = tuple(float(row[key]) for key in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("bad_ohlc", "本地行情含无效 OHLC，无法回放") from exc
        o, h, low, close = values
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or h < max(o, low, close) or low > min(o, h, close)):
            raise SourceError("bad_ohlc", "本地行情含无效 OHLC，无法回放")
        return values

    @classmethod
    def _read_file(cls, path: Path, *, duration_ms: int, now_ms: int):
        try:
            raw_bytes = path.read_bytes()
        except OSError as exc:
            raise SourceError("unreadable_file", "本地行情文件无法读取，无法回放") from exc
        digest = hashlib.sha256(raw_bytes).hexdigest()
        try:
            frame = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=False)
        except (ValueError, UnicodeError, pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise SourceError("unreadable_file", "本地行情文件无法读取，无法回放") from exc
        if not frame.columns.is_unique or not _REQUIRED_COLUMNS.issubset(frame.columns):
            raise SourceError("bad_schema", "本地行情文件缺少必需字段，无法回放")
        has_ts = "ts" in frame.columns
        has_confirm = "confirm" in frame.columns
        closed_rows: dict[int, tuple[float, float, float, float]] = {}
        latest_partial_open = None
        for row in frame.to_dict("records"):
            stamp = cls._read_timestamp(row["open_time"], row.get("ts") if has_ts else None, duration_ms)
            if has_confirm and cls._parse_confirm(row["confirm"]) == 0:
                latest_partial_open = stamp if latest_partial_open is None else max(latest_partial_open, stamp)
                continue
            if stamp + duration_ms > now_ms:
                latest_partial_open = stamp if latest_partial_open is None else max(latest_partial_open, stamp)
                continue
            ohlc = cls._parse_ohlc(row)
            previous = closed_rows.get(stamp)
            if previous is not None and previous != ohlc:
                raise SourceError("conflicting_duplicate", "本地行情同一时间存在冲突 OHLC，无法回放")
            closed_rows[stamp] = ohlc
        return closed_rows, latest_partial_open, digest

    @staticmethod
    def _segments(rows: list[dict], duration_ms: int) -> list[tuple[int, int]]:
        if not rows:
            return []
        starts = [0]
        for index in range(1, len(rows)):
            if rows[index]["t"] - rows[index - 1]["t"] != duration_ms:
                starts.append(index)
        starts.append(len(rows))
        return list(zip(starts, starts[1:]))

    def _read_market(self, symbol: str, timeframe: str):
        if not isinstance(symbol, str) or not isinstance(timeframe, str):
            raise SourceError("invalid_market", "交易对或周期格式无效，无法读取本地行情")
        duration_ms = _BAR_MINUTES.get(timeframe, 0) * 60_000
        if duration_ms <= 0:
            raise SourceError("unsupported_timeframe", "本地没有该周期的原始行情")
        paths = self._ensure_index().get((symbol, timeframe))
        if not paths:
            raise SourceError("history_missing", "没有找到该交易对和周期的本地 OKX 历史数据")
        try:
            now_ms = int(self.clock())
        except (TypeError, ValueError, OverflowError) as exc:
            raise SourceError("clock_invalid", "本机时钟不可用，无法确定已闭合行情") from exc
        if now_ms < 0:
            raise SourceError("clock_invalid", "本机时钟不可用，无法确定已闭合行情")

        combined: dict[int, tuple[float, float, float, float]] = {}
        partial_opens = []
        file_digests = {}
        for path in paths:
            file_rows, partial_open, digest = self._read_file(path, duration_ms=duration_ms, now_ms=now_ms)
            file_digests[path] = digest
            if partial_open is not None:
                partial_opens.append(partial_open)
            for stamp, ohlc in file_rows.items():
                previous = combined.get(stamp)
                if previous is not None and previous != ohlc:
                    raise SourceError("conflicting_duplicate", "本地行情同一时间存在冲突 OHLC，无法回放")
                combined[stamp] = ohlc
        if not combined:
            raise SourceError("history_empty", "该交易对和周期没有可用的已闭合本地行情")
        raw_rows = [dict(t=stamp, o=ohlc[0], h=ohlc[1], l=ohlc[2], c=ohlc[3])
                    for stamp, ohlc in sorted(combined.items())]
        return duration_ms, paths, now_ms, partial_opens, raw_rows, file_digests

    def coverage(self, symbol: str, timeframe: str) -> dict:
        """Return closed-bar coverage and replayable continuous segments.

        Only files for the requested catalog key are read. Short segments stay
        in the overall coverage bounds but are omitted from ``segments``.
        """
        duration_ms, _, _, _, raw_rows, _ = self._read_market(symbol, timeframe)
        segments = []
        for left, right in self._segments(raw_rows, duration_ms):
            if right - left < _MIN_SEGMENT_BARS:
                continue
            segments.append({
                "first_close_ms": raw_rows[left]["t"] + duration_ms,
                "last_close_ms": raw_rows[right - 1]["t"] + duration_ms,
                "first_replay_close_ms": raw_rows[left + 238]["t"] + duration_ms,
            })
        return {
            "first_close_ms": raw_rows[0]["t"] + duration_ms,
            "last_close_ms": raw_rows[-1]["t"] + duration_ms,
            "segments": segments,
            "source_label": _SOURCE_LABEL,
        }

    def load(self, symbol: str, timeframe: str, start_ms: int) -> dict:
        """Load a bounded continuous segment and causal MA rows for replay.

        ``start_ms`` is a requested wall-clock time. The cursor resolves to the
        latest actually closed bar at or before it, unless the time falls in a
        data gap or beyond local coverage. ``rows`` starts after its segment's
        120-close MA warmup; its first legal cursor is therefore index 119.
        """
        if isinstance(start_ms, bool) or not isinstance(start_ms, (int, np.integer)):
            raise SourceError("invalid_start", "回放时间格式无效")
        start_ms = int(start_ms)
        duration_ms, paths, now_ms, partial_opens, raw_rows, file_digests = self._read_market(symbol, timeframe)
        close_times = [row["t"] + duration_ms for row in raw_rows]
        last_closed_close_ms = close_times[-1]
        if start_ms > last_closed_close_ms:
            latest_partial_open = max(partial_opens, default=None)
            in_current_partial = (
                latest_partial_open is not None
                and latest_partial_open > raw_rows[-1]["t"]
                and latest_partial_open + duration_ms >= start_ms
                and latest_partial_open + duration_ms > now_ms
                and start_ms <= now_ms
            )
            if not in_current_partial:
                raise SourceError("start_out_of_range", "指定时间晚于本地行情覆盖范围")
        if start_ms < close_times[0]:
            raise SourceError("start_out_of_range", "指定时间早于本地行情覆盖范围")

        segments = self._segments(raw_rows, duration_ms)
        for (left, right), (next_left, _) in zip(segments, segments[1:]):
            previous_close = raw_rows[right - 1]["t"] + duration_ms
            next_close = raw_rows[next_left]["t"] + duration_ms
            if previous_close < start_ms < next_close:
                raise SourceError("start_in_gap", "指定时间落在本地行情缺口中，无法回放")

        cursor_raw_index = int(np.searchsorted(close_times, start_ms, side="right") - 1)
        if cursor_raw_index < 0:
            raise SourceError("start_out_of_range", "指定时间早于本地行情覆盖范围")
        segment_bounds = next(
            ((left, right) for left, right in segments if left <= cursor_raw_index < right), None
        )
        if segment_bounds is None:
            raise SourceError("start_in_gap", "指定时间落在本地行情缺口中，无法回放")
        segment_left, segment_right = segment_bounds
        segment = raw_rows[segment_left:segment_right]
        if len(segment) < _MIN_SEGMENT_BARS:
            raise SourceError("warmup_insufficient", "所选连续行情不足239根，无法显示120根均线窗口")
        cursor_segment_index = cursor_raw_index - segment_left
        if cursor_segment_index < 238:
            raise SourceError("warmup_insufficient", "指定时间之前均线预热或120根显示窗口不足")

        close = pd.Series([row["c"] for row in segment], dtype="float64")
        averages = {}
        for period in _DISPLAY_MA_PERIODS:
            averages[f"sma{period}"] = close.rolling(period, min_periods=period).mean()
            averages[f"ema{period}"] = close.ewm(
                alpha=2.0 / (period + 1), adjust=False, ignore_na=True
            ).mean()
        display_start = _DISPLAY_MA_PERIODS[-1] - 1
        displayable = []
        for offset in range(display_start, len(segment)):
            source_row = segment[offset]
            output_row = dict(source_row)
            for name, series in averages.items():
                value = float(series.iloc[offset])
                if not math.isfinite(value):
                    raise SourceError("warmup_insufficient", "本地行情均线预热不足，无法回放")
                output_row[name] = value
            displayable.append(output_row)

        cursor_display_index = cursor_segment_index - display_start
        if cursor_display_index < 119:
            raise SourceError("warmup_insufficient", "指定时间之前不足120根均线完备K线")
        slice_start = max(0, cursor_display_index - _MAX_PRIOR_BARS)
        slice_end = min(len(displayable), cursor_display_index + _MAX_FUTURE_BARS + 1)
        output_rows = displayable[slice_start:slice_end]
        cursor_index = cursor_display_index - slice_start

        file_receipts = [{"path": str(path.resolve()), "sha256": file_digests[path]} for path in paths]
        receipt_basis = "\n".join(f"{item['path']}:{item['sha256']}" for item in file_receipts)
        next_gap_open_ms = (raw_rows[segment_right]["t"] if segment_right < len(raw_rows) else None)
        previous_gap_close_ms = (
            raw_rows[segment_left - 1]["t"] + duration_ms if segment_left > 0 else None
        )
        return {
            "rows": output_rows,
            "cursor_index": cursor_index,
            "first_cursor_index": 119,
            "duration_ms": duration_ms,
            "source_label": _SOURCE_LABEL,
            "source_receipt": {
                "schema_version": 1,
                "algorithm_version": _ALGORITHM_VERSION,
                "files": file_receipts,
                "files_sha256": hashlib.sha256(receipt_basis.encode("utf-8")).hexdigest(),
                "ema_seed_open_ms": segment[0]["t"],
                "segment_start_open_ms": segment[0]["t"],
                "segment_last_close_ms": segment[-1]["t"] + duration_ms,
                "previous_gap_close_ms": previous_gap_close_ms,
                "previous_gap_next_open_ms": (segment[0]["t"] if segment_left > 0 else None),
                "next_gap_open_ms": next_gap_open_ms,
                "raw_segment_bars": len(segment),
                "returned_rows": len(output_rows),
                "visible_history_cap": _MAX_PRIOR_BARS,
                "visible_future_cap": _MAX_FUTURE_BARS,
            },
        }
