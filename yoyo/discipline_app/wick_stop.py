"""On-demand initial stops using the same public candles as the App chart.

No ledger or execution client is accessed. Current NY-week anchors come from
the monitor; native App candle periods (including UTC+8 daily) stay intact.
Only a continuous closed history can identify the most recent eligible wick.
"""
import threading
import time
from yoyo.data.monday_wick import wick_stop
from .candles import CandleUnavailable
from .monday_fib import FibUnavailable

STEPS = {'1m': 60000, '5m': 300000, '15m': 900000,
         '1H': 3600000, '4H': 14400000, '1D': 86400000}


class WickStops:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(2)

    def read(self, feed, history, symbol, period, side):
        if not self.slots.acquire(blocking=False):
            raise FibUnavailable('插针 K 线正在读取，请稍后重试')
        try:
            snapshot = feed.read()
            now = int(time.time() * 1000)
            if not snapshot['week_start_ms'] <= now < snapshot['next_week_ms'] or abs(snapshot['now_ms']-now) > 60000:
                raise FibUnavailable('本周区间尚未就绪，请稍后重试')
            row = next((r for r in snapshot['items'] if r['symbol'] == symbol), None)
            if not snapshot['monday_complete'] or not row:
                raise FibUnavailable('本周尚无有效触价区间，请手动填写止损')
            window = dict(end=snapshot['monday_end_ms'], expires=snapshot['next_week_ms'])
            level = row['lower' if side == 'long' else 'upper']
            step = STEPS[period]
            # OKX 1D bars start at 00:00 UTC+8, matching CandlePanel.
            offset = 8*3600000 if period == '1D' else 0
            boundary = (now+offset)//step*step-offset
            expected, before, deadline = boundary-step, None, time.monotonic()+8
            while expected >= window['end']:
                if time.monotonic() > deadline:
                    raise FibUnavailable('插针历史仍在读取，请点 1R 自动计算重试')
                payload = history.read(symbol, period, before)
                candles = {int(c[0]): c for c in payload['data']
                           if c[8] == '1' and int(c[0]) <= expected}
                continuous = []
                while expected >= window['end'] and expected in candles:
                    c = candles[expected]
                    continuous.append(dict(time=expected, open=c[1], high=c[2], low=c[3], close=c[4]))
                    expected -= step
                found = wick_stop(continuous, period=period, side=side, level=level, window=window, now=boundary)
                if found:
                    return dict(symbol=symbol, side=side, week=snapshot['week'], **found)
                if expected < window['end']:
                    break
                if not continuous or any(t < expected for t in candles):
                    raise FibUnavailable('已收盘 K 线尚未到齐，请稍后重试')
                before = (expected+step)//1000
            raise FibUnavailable('该周期暂无影线触及本周 1.618 的已收盘 K 线，请手动填写止损')
        except (CandleUnavailable, TimeoutError, OSError):
            raise FibUnavailable('当前周期 K 线暂不可用，请重试或手动填写止损') from None
        finally:
            self.slots.release()
