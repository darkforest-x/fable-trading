"""Read-only, on-demand reconstruction of one completed or loaded 12H week.

This reader shares the live cloud signal semantics and CandleHub data but never
records a signal, position check, notification, or account event. Incomplete
weeks are returned for refresh and are not retained in the in-memory cache.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, time
import json
import math
import re
from threading import BoundedSemaphore, Lock

from .cloud_signal_rules import first_eligible, week_key


_SYMBOL = re.compile(r"^[A-Z0-9]+-USDT-SWAP$")
_WEEK = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PAGE_BARS = 300
_CACHE_SIZE = 64


class CloudSignalHistory:
    """Reconstruct display-only cloud signals for one NY Monday week."""

    def __init__(self, service, levels=None):
        self.service = service
        self.levels = levels if levels is not None else service.levels
        self.cache = OrderedDict()
        self.lock = Lock()
        self.slot = BoundedSemaphore(1)

    @staticmethod
    def _week_window(week):
        from zoneinfo import ZoneInfo

        from .monday_fib import week_window

        if not isinstance(week, str) or not _WEEK.fullmatch(week):
            raise ValueError("badweek")
        try:
            day = date.fromisoformat(week)
        except ValueError:
            raise ValueError("badweek") from None
        if day.isoformat() != week or day.weekday() != 0:
            raise ValueError("badweek")
        stamp = int(datetime.combine(day, time.min, ZoneInfo("America/New_York")).timestamp() * 1000)
        window = week_window(stamp)
        if window["week"] != week:
            raise ValueError("badweek")
        return window

    @staticmethod
    def _candidate_times(window, now, step):
        first = ((window["end"] + step - 1) // step) * step
        last = ((window["expires"] - step) // step) * step
        if first > last:
            return [], []
        all_times = list(range(first, last + 1, step))
        closed = [t for t in all_times if t + step <= now]
        return all_times, closed

    @staticmethod
    def _finite_positive(value):
        return isinstance(value, (int, float)) and math.isfinite(value) and value > 0

    @staticmethod
    def _row_map(hub, symbol, last, now, step):
        from yoyo.data.candle_hub import validate

        key = "|".join((hub.origin, symbol, "12Hutc"))
        with hub.db() as db:
            stored = db.execute(
                "SELECT t,raw FROM candles WHERE key=? AND t<=? ORDER BY t",
                (key, last),
            ).fetchall()
        result = {}
        for row in stored:
            try:
                raw = json.loads(row["raw"])
                stamp = int(row["t"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if stamp + step > now:
                continue
            try:
                normalized = validate([raw], "12Hutc", now).get(stamp)
            except Exception:
                continue
            if normalized and normalized[8] == "1":
                result[stamp] = normalized
        return result

    @staticmethod
    def _stored_seed(items, first_candidate, last_candidate, step):
        """Use an exact persisted EMA point only when it can cover this week."""
        candidates = []
        for item in items:
            stamp = item.get("signal_open_ms")
            close = CloudSignalHistory._confirmation_close(item)
            if (not isinstance(stamp, int) or stamp % step or stamp > first_candidate
                    or not CloudSignalHistory._finite_positive(item.get("ema20"))
                    or not CloudSignalHistory._finite_positive(item.get("ema50"))
                    or not CloudSignalHistory._finite_positive(close)):
                continue
            candidates.append(item)
        if not candidates:
            return None
        return max(candidates, key=lambda item: item["signal_open_ms"])

    @staticmethod
    def _confirmation_close(item):
        bar = item.get("signal_bar") or {}
        if item.get("confirmation_close") is not None:
            return item["confirmation_close"]
        if bar.get("close") is not None:
            return bar["close"]
        original = item.get("original_reference") or {}
        return original.get("entry", item.get("entry"))

    @staticmethod
    def _decorate(item, *, range_mode, reference_policy, eligibility_policy):
        value = dict(item)
        value.update(historical=True, provenance="reconstruction",
                     range_mode=range_mode,
                     reference_policy=reference_policy,
                     eligibility_policy=eligibility_policy)
        return value

    @classmethod
    def _stored_bar_map(cls, items, candidate_times):
        """Recover candidate OHLC from persisted observations without their plan."""
        wanted = set(candidate_times)
        result = {}
        for item in items:
            stamp = item.get("signal_open_ms")
            bar = item.get("signal_bar")
            if not isinstance(stamp, int) or stamp not in wanted or not isinstance(bar, dict):
                continue
            if bar.get("time") != stamp:
                continue
            names = ("open", "high", "low", "close")
            values = [bar.get(name) for name in names]
            if not all(cls._finite_positive(value) for value in values):
                continue
            open_, high, low, close = values
            if high < max(open_, close, low) or low > min(open_, close, high):
                continue
            result[stamp] = dict(time=stamp, open=open_, high=high, low=low,
                                 close=close, volume=bar.get("volume", 0.0))
        return result

    def _remember(self, key, result):
        with self.lock:
            self.cache[key] = deepcopy(result)
            self.cache.move_to_end(key)
            while len(self.cache) > _CACHE_SIZE:
                self.cache.popitem(last=False)

    def _cached(self, key):
        with self.lock:
            result = self.cache.get(key)
            if result is None:
                return None
            self.cache.move_to_end(key)
            return deepcopy(result)

    def _load_required_rows(self, symbol, last, now, step, rows):
        """Make at most one bounded history request and merge all visible rows."""
        from yoyo.data.candle_hub import validate

        hub = self.service.client.hub
        try:
            response = self.service.client.get(
                "/api/v5/market/history-candles",
                dict(instId=symbol, bar="12Hutc", limit=str(_PAGE_BARS), after=str(last + 1)),
            )
            parsed = validate(response, "12Hutc", now)
        except Exception:
            raise RuntimeError("history_retry") from None

        # Keep the response in memory as well as rereading CandleHub: an old
        # requested slice can be trimmed by the shared cache's retention rule.
        for stamp, raw in parsed.items():
            if raw[8] != "1" or stamp + step > now:
                continue
            previous = rows.get(stamp)
            if previous is not None and previous != raw:
                raise RuntimeError("history_retry")
            if stamp <= last:
                rows[stamp] = raw
        reread = self._row_map(hub, symbol, last, now, step)
        for stamp, raw in reread.items():
            previous = rows.get(stamp)
            if previous is not None and previous != raw:
                raise RuntimeError("history_retry")
            rows.setdefault(stamp, raw)
        return rows, "candle_hub+history_page"

    @staticmethod
    def _indicator_points(rows, stored_items, seed, step):
        """Run production recurrences over the full prefix, pinning stored points."""
        from .cloud_signal_plans import atr_points
        from .cloud_signals import ema_points

        series = CloudSignalHistory._candles(rows)
        if not series:
            return {}, {}, series
        first, last = series[0]["time"], series[-1]["time"]

        def close_of(item):
            return CloudSignalHistory._confirmation_close(item)

        def ema_state(item):
            return dict(time=item["signal_open_ms"], ema20=item["ema20"],
                        ema50=item["ema50"], count=50)

        def atr_state(item):
            atr = item.get("atr14")
            close = close_of(item)
            if not CloudSignalHistory._finite_positive(atr) or not CloudSignalHistory._finite_positive(close):
                return None
            return dict(time=item["signal_open_ms"], close=close, count=14,
                        sum=atr * 14, atr14=atr)

        if seed:
            if seed["signal_open_ms"] < first:
                points = ema_points(series, ema_state(seed))
                initial_atr = atr_state(seed)
                atrs = atr_points(series, initial_atr) if initial_atr else atr_points(series)
            else:
                points = ema_points(series)
                atrs = atr_points(series)
        else:
            points = ema_points(series)
            atrs = atr_points(series)
        point_by_time = {point["time"]: point for point in points}
        atr_by_time = {point["time"]: point["atr14"] for point in atrs}

        anchors = {}
        for item in stored_items:
            stamp = item.get("signal_open_ms")
            if (isinstance(stamp, int) and first <= stamp <= last and stamp % step == 0
                    and CloudSignalHistory._finite_positive(item.get("ema20"))
                    and CloudSignalHistory._finite_positive(item.get("ema50"))):
                anchors[stamp] = item
        if seed and seed["signal_open_ms"] >= first:
            anchors[seed["signal_open_ms"]] = seed

        # Re-anchor each later recurrence at the exact stored EMA/ATR. The
        # returned signal itself remains the stored projection; subsequent bars
        # inherit its persisted state instead of accumulating seed drift.
        for stamp, item in sorted(anchors.items()):
            point = dict(time=stamp, ema20=item["ema20"], ema50=item["ema50"],
                         count=50, ready=True)
            point_by_time[stamp] = point
            ema_seed = ema_state(item)
            tail = [bar for bar in series if bar["time"] > stamp]
            for value in ema_points(tail, ema_seed):
                point_by_time[value["time"]] = value

            atr_seed = atr_state(item)
            if atr_seed:
                atr_by_time[stamp] = atr_seed["atr14"]
                for value in atr_points(tail, atr_seed):
                    atr_by_time[value["time"]] = value["atr14"]
        return point_by_time, atr_by_time, series

    def read(self, symbol, week, range_mode="fib"):
        """Return projected history for ``week`` without signal or channel writes.

        Invalid week/symbol arguments raise ``ValueError``. Missing anchor or
        candle history and transient contention/fetch failures raise
        ``RuntimeError`` so callers can retry instead of presenting an empty
        history as complete.
        """
        from .cloud_signals import STEP, VERSION, signal
        from .cloud_signal_plans import normalize_range_mode, policy_for
        from .cloud_signal_rules import POSITION_POLICY

        range_mode = normalize_range_mode(range_mode)
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("badsymbol")
        window = self._week_window(week)
        now = int(self.service.client.clock())
        complete = window["expires"] <= now
        reference_policy = policy_for(range_mode)
        cache_key = (symbol, week, VERSION, range_mode, reference_policy, POSITION_POLICY)
        if complete:
            cached = self._cached(cache_key)
            if cached is not None:
                return cached

        base = dict(schema=1, version=VERSION, symbol=symbol, week=week,
                    range_mode=range_mode, reference_policy=reference_policy,
                    eligibility_policy=POSITION_POLICY,
                    complete=complete)
        all_week_times, candidate_times = self._candidate_times(window, now, STEP)
        coverage = dict(week_start_ms=window["start"], anchor_end_ms=window["end"],
                        week_expires_ms=window["expires"],
                        week_candidate_bars=len(all_week_times),
                        candidate_bars_considered=len(candidate_times),
                        candidate_first_ms=all_week_times[0] if all_week_times else None,
                        candidate_last_ms=all_week_times[-1] if all_week_times else None,
                        closed_through_ms=now, anchor_state="developing")

        # The 5m Monday range does not exist until the whole NY Monday is closed.
        if now < window["end"]:
            return dict(base, state="developing", items=[], coverage=coverage)

        if not candidate_times:
            try:
                anchor = self.levels.read(symbol, min(now, window["end"]))
            except Exception:
                raise RuntimeError("monday_anchor_retry") from None
            if anchor.get("state") != "ready":
                raise RuntimeError("monday_anchor_missing")
            coverage.update(anchor_state="ready", source="no_closed_candidate_bars")
            result = dict(base, state="ready", items=[], coverage=coverage)
            if complete:
                self._remember(cache_key, result)
            return result

        if not self.slot.acquire(False):
            raise RuntimeError("history_busy")
        try:
            try:
                anchor = self.levels.read(symbol, min(now, window["end"]))
            except Exception:
                raise RuntimeError("monday_anchor_retry") from None
            if (anchor.get("state") != "ready" or anchor.get("symbol") != symbol
                    or anchor.get("week") != week):
                raise RuntimeError("monday_anchor_missing")

            # service.history is the existing DB-only reader. Its stored plan is
            # authoritative for a matching signal id; this class never calls
            # service.record or any notification path.
            try:
                stored_items = self.service.history(symbol).get("items", [])
            except Exception:
                raise RuntimeError("history_retry") from None
            stored_week = [item for item in stored_items
                           if week_key(item) == week and item.get("confirmed_ms", now + 1) <= now]
            stored_by_time = {item["signal_open_ms"]: item for item in stored_week
                              if isinstance(item.get("signal_open_ms"), int)}

            first_candidate, last_candidate = candidate_times[0], candidate_times[-1]
            seed = self._stored_seed(stored_items, first_candidate, last_candidate, STEP)
            hub = self.service.client.hub
            rows = self._row_map(hub, symbol, last_candidate, now, STEP)
            point_by_time, atr_by_time, series = self._indicator_points(
                rows, stored_items, seed, STEP)
            stored_bar_by_time = self._stored_bar_map(stored_week, candidate_times)

            def needs_history_page():
                missing = [t for t in candidate_times if t not in rows and
                           (range_mode == "monday" and t not in stored_bar_by_time or
                            range_mode == "fib" and t not in stored_by_time)]
                cold = [t for t in candidate_times if t not in stored_by_time and
                        (t not in point_by_time or not point_by_time[t].get("ready"))]
                return bool(missing or cold)

            source = "candle_hub"
            if needs_history_page():
                rows, source = self._load_required_rows(symbol, last_candidate, now, STEP, rows)
                point_by_time, atr_by_time, series = self._indicator_points(
                    rows, stored_items, seed, STEP)
                stored_bar_by_time = self._stored_bar_map(stored_week, candidate_times)
            if needs_history_page():
                raise RuntimeError("history_missing")

            # A persisted signal carries an authoritative confirmation bar even
            # when CandleHub no longer holds that individual candidate candle.
            bar_by_time = {bar["time"]: bar for bar in self._candles(
                {t: rows[t] for t in candidate_times if t in rows})}
            if range_mode == "monday":
                for stamp, bar in stored_bar_by_time.items():
                    bar_by_time.setdefault(stamp, bar)

            found = {}
            for stamp in candidate_times:
                if range_mode == "fib" and stamp in stored_by_time:
                    item = stored_by_time[stamp]
                    found[item["id"]] = self._decorate(
                        item, range_mode=range_mode, reference_policy=reference_policy,
                        eligibility_policy=POSITION_POLICY)
                    break
                # Historical eligibility is causal: stop at the first eligible
                # candidate instead of projecting later signals in the week.
                bar = bar_by_time.get(stamp)
                point = point_by_time.get(stamp)
                if bar is None or point is None:
                    raise RuntimeError("history_missing")
                item = signal(symbol, bar, point, anchor, now, range_mode=range_mode)
                if item is None:
                    continue
                atr = atr_by_time.get(stamp)
                projected = self.service.project(item, {item["id"]: atr}, range_mode=range_mode)
                found[item["id"]] = self._decorate(
                    projected, range_mode=range_mode, reference_policy=reference_policy,
                    eligibility_policy=POSITION_POLICY)
                break

            items = first_eligible(found.values())
            items.sort(key=lambda item: (item["confirmed_ms"], item["id"]))
            coverage.update(
                anchor_state="ready", anchor_high=anchor.get("high"), anchor_low=anchor.get("low"),
                anchor_lower=anchor.get("lower"), anchor_upper=anchor.get("upper"),
                source=source, warmup=("stored_signal_ema" if seed else "full_cached_prefix"),
                warmup_start_ms=(seed["signal_open_ms"] if seed else
                                 (series[0]["time"] if series else None)),
                cache_prefix_start_ms=(series[0]["time"] if series else None),
                warmup_bars=len(series), required_end_ms=last_candidate,
                candidate_bars_available=len(candidate_times), missing_bars=0,
            )
            result = dict(base, state="ready", items=items, coverage=coverage)
            if complete:
                self._remember(cache_key, result)
            return result
        finally:
            self.slot.release()

    @staticmethod
    def _candles(rows):
        from .chart_periods import candles

        return candles(rows)

