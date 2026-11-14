"""Initial stop for a 1.618 quick ticket, from the original touch and public 1m OHLC."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal
import threading
import time

from .candles import CandleUnavailable
from .monday_fib import FibUnavailable


MINUTE = 60_000
PAGE_MINUTES = 240  # OKX returns up to 300 bars, including the open bar on the latest endpoint.


@dataclass(frozen=True)
class Progress:
    next_ms: int
    first_ms: int | None = None
    extreme: Decimal | None = None
    extreme_ms: int | None = None
    recovered: bool = False


class WickStops:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.progress = OrderedDict()

    def _remember(self, key, state):
        with self.lock:
            previous = self.progress.get(key)
            if previous is None or (not previous.recovered and state.next_ms >= previous.next_ms):
                self.progress[key] = state
            self.progress.move_to_end(key)
            if len(self.progress) > 1024:
                self.progress.popitem(last=False)

    def read(self, history, symbol, side, anchor, now_ms, source='week'):
        """Start at the monitor's recorded minute (or its earliest historical interval).

        Use only contiguous closed 1m bars. The first close back to the level or
        inside ends that excursion; subsequent bars never participate in the stop.
        """
        if not self.slots.acquire(blocking=False):
            raise FibUnavailable('首次触价 K 线正在读取，请稍后重试')
        try:
            period_key, raw_level, start, search_end = anchor
            level = Decimal(str(raw_level))
            key = (source, period_key, symbol, side, level, start, search_end)
            boundary = now_ms // MINUTE * MINUTE
            deadline = time.monotonic() + 8
            with self.lock:
                state = self.progress.get(key, Progress(start))

            while not state.recovered and state.next_ms < boundary:
                if time.monotonic() > deadline:
                    raise FibUnavailable('首次触价历史仍在读取，请点自动计算继续')
                end = min(state.next_ms + PAGE_MINUTES * MINUTE, boundary)
                before = None if end == boundary else end // 1000
                payload = history.read(symbol, '1m', before)
                closed = {int(row[0]): row for row in payload['data'] if row[8] == '1'}
                state = self._scan(closed, state, end, search_end, level, side)
                self._remember(key, state)
            if not state.recovered:
                raise FibUnavailable('首次触价尚未确认收回 1.618，请等 1 分钟 K 线收盘后重试')
            return dict(symbol=symbol, side=side, **{source: period_key},
                        stop_loss=format(state.extreme, 'f'), candle_ms=state.extreme_ms,
                        first_touch_ms=state.first_ms, recovered_ms=state.next_ms,
                        level=format(level, 'f'), timeframe='1m')
        except (CandleUnavailable, TimeoutError, OSError):
            raise FibUnavailable('首次触价的 1 分钟 K 线暂不可用，请稍后重试') from None
        finally:
            self.slots.release()

    @staticmethod
    def _scan(closed, state, end, search_end, level, side):
        first, extreme, extreme_ms = state.first_ms, state.extreme, state.extreme_ms
        long = side == 'long'
        for stamp in range(state.next_ms, end, MINUTE):
            row = closed.get(stamp)
            if row is None:
                raise FibUnavailable('首次触价的 1 分钟 K 线尚未到齐，请稍后重试')
            wick = Decimal(row[3] if long else row[2])
            if first is None:
                touched = wick <= level if long else wick >= level
                if not touched:
                    if stamp + MINUTE >= search_end:
                        raise FibUnavailable('首次触价记录与分钟 K 线不符，请手动填写止损')
                    continue
                first = stamp
            if extreme is None or (wick < extreme if long else wick > extreme):
                extreme, extreme_ms = wick, stamp
            close = Decimal(row[4])
            recovered = close >= level if long else close <= level
            if recovered:
                return Progress(stamp + MINUTE, first, extreme, extreme_ms, recovered=True)
        return Progress(end, first, extreme, extreme_ms)
