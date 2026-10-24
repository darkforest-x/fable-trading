"""Causal replay windows from registered local OKX files or explicit public OKX.

No account client, credentials, silent source switching or market-data writes.
Local CSVs retain their original symlink paths; availability/size/mtime are used
for caching without content hashes. OHLC is revealed only after its close.
"""
from collections import OrderedDict
import csv
from datetime import datetime, timedelta, time as day_time
import math
from pathlib import Path
import re
import threading
import time
from zoneinfo import ZoneInfo

from .candles import CandleHistory

PERIODS = {'1m': 60, '5m': 300, '15m': 900, '1H': 3600, '4H': 14400}
FILE = re.compile(r'^(?:okx_)?(.+)_(1m|5m|15m|1H|4H)_(\d+)(?:_latest)?\.csv$')
NY = ZoneInfo('America/New_York')


class ReplayError(ValueError):
    pass


def validate_symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9]{1,40}-USDT-SWAP', symbol):
        raise ReplayError('请选择 USDT 永续合约')
    return symbol


def candle(stamp, values):
    try:
        numbers = [float(x) for x in values]
        o, h, l, c, v = numbers
        if not all(math.isfinite(x) for x in numbers) or min(o, h, l, c) <= 0 or v < 0 or h < max(o, c, l) or l > min(o, c):
            raise ValueError()
        if int(stamp) != stamp or stamp <= 0:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ReplayError('历史 K 线含无效价格或时间') from None
    return dict(time=int(stamp), open=o, high=h, low=l, close=c, volume=v)


class ReplayHistory:
    def __init__(self, relay=None, roots=None, clock=time.time):
        repo = Path(__file__).resolve().parents[2]
        self.roots = tuple(Path(p) for p in (roots if roots is not None else [repo/'data/kline_fetched', repo/'data/kline_cache']))
        self.relay = relay or CandleHistory()
        self.clock = clock
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.index_cache = None

    def index(self):
        signature = []
        for root in self.roots:
            try:
                signature.append((str(root), root.stat().st_mtime_ns))
            except OSError:
                signature.append((str(root), None))
        if self.index_cache and self.index_cache[0] == signature:
            return self.index_cache[1]
        result = {}
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in root.glob('*.csv'):
                match = FILE.fullmatch(path.name)
                if not match:
                    continue
                symbol, period, _ = match.groups()
                symbol = symbol.replace('_', '-').upper()
                if not re.fullmatch(r'[A-Z0-9]{1,40}-USDT-SWAP', symbol):
                    continue
                result.setdefault((symbol, period), []).append(path)
        self.index_cache = (signature, result)
        return result

    def catalog(self):
        index = self.index()
        local = set()
        for symbol, base in index:
            for period, step in PERIODS.items():
                if step % PERIODS[base] == 0:
                    local.add((symbol, period))
        return dict(periods=list(PERIODS), sources=[dict(id='okx_local', label='本地 OKX 历史'), dict(id='okx_online', label='OKX 在线历史')],
                    local=[dict(symbol=s, period=p) for s, p in sorted(local)],
                    warning='' if local else '本地历史目录暂不可读；可明确选择 OKX 在线历史')

    def _local(self, symbol, period, start, end):
        index = self.index()
        choices = [(PERIODS[p], p) for s, p in index if s == symbol and PERIODS[period] % PERIODS[p] == 0]
        if not choices:
            raise ReplayError('本地没有该合约和周期的历史；可新建回放并选择 OKX 在线历史')
        best = []
        for base, base_period in sorted(choices, reverse=True):
            rows = self._local_period(sorted(index[(symbol, base_period)]), base, period, start, end)
            # Prefer the finest available coverage, not a recently-created
            # coarser file which has no rows at the requested historic date.
            if len(rows) > len(best):
                best = rows
            first = (start+PERIODS[period]-1)//PERIODS[period]*PERIODS[period]
            expected = list(range(first, end//PERIODS[period]*PERIODS[period], PERIODS[period]))
            if [r['time'] for r in rows] == expected:
                return rows
        return best

    def _local_period(self, paths, base, period, start, end):
        try:
            identity = tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in paths)
        except OSError:
            raise ReplayError('本地历史所在磁盘暂不可读，请恢复连接后重试') from None
        # One read serves a day of stepping. Future context stays server-side;
        # callers receive only rows whose close is <= their committed clock.
        cache_end = (end//86400+1)*86400
        cache_start = end//86400*86400-302*PERIODS[period]
        key = (identity, cache_start, cache_end, period)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                raw_rows = self.cache[key]
            else:
                raw_rows = None
        if raw_rows is None:
            raw_rows = self._read_local_rows(paths, cache_start, cache_end, base)
            with self.lock:
                self.cache[key] = raw_rows
                while len(self.cache) > 8:
                    self.cache.popitem(last=False)
        # Validate only closed, requested target bars. Cached future OHLC must
        # neither leak into a response nor reject an earlier valid decision.
        merged = {}
        step = PERIODS[period]
        for stamp, values in raw_rows:
            target = int(stamp // step * step)
            if target < start or target+step > end:
                continue
            item = candle(stamp, values)
            if stamp in merged and merged[stamp] != item:
                raise ReplayError('本地重叠历史存在冲突，不能用于模拟成交')
            merged[stamp] = item
        grouped = {}
        for stamp, item in sorted(merged.items()):
            grouped.setdefault(int(stamp // step * step), []).append(item)
        rows = []
        for stamp, items in grouped.items():
            if [x['time'] for x in items] != list(range(stamp, stamp+step, base)):
                continue
            rows.append(dict(time=stamp, open=items[0]['open'], high=max(x['high'] for x in items),
                             low=min(x['low'] for x in items), close=items[-1]['close'], volume=sum(x['volume'] for x in items)))
        return rows

    def _read_local_rows(self, paths, cache_start, cache_end, base):
        rows = []
        try:
            for path in paths:
                with path.open(newline='') as handle:
                    previous = -1
                    for row in csv.DictReader(handle):
                        raw = row.get('ts') or row.get('open_time')
                        try:
                            stamp = float(raw) / 1000
                        except (TypeError, ValueError):
                            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00')).timestamp()
                        if stamp < previous:
                            raise ReplayError('本地历史时间顺序异常')
                        previous = stamp
                        if stamp < cache_start:
                            continue
                        if stamp + base > min(cache_end, self.clock()):
                            break
                        if row.get('confirm', '1') != '1':
                            continue
                        rows.append((stamp, [row[k] for k in ('open', 'high', 'low', 'close', 'volume')]))
        except (OSError, csv.Error, KeyError, ValueError):
            raise ReplayError('本地历史读取失败，请检查磁盘连接和数据格式') from None
        return rows

    def read(self, symbol, period, source, through, count=300):
        validate_symbol(symbol)
        if period not in PERIODS or source not in ('okx_local', 'okx_online') or type(through) is not int or through <= 0:
            raise ReplayError('回放来源、周期或时间无效')
        step = PERIODS[period]
        through = min(through, int(self.clock()))
        if source == 'okx_local':
            return self._local(symbol, period, through-(count+1)*step, through)[-count:]
        try:
            raw = self.relay.read(symbol, period, through+1)['data']
        except Exception:
            raise ReplayError('OKX 历史行情暂不可用；原回放和成交记录已保留，请重试') from None
        merged = {}
        for row in raw:
            stamp = int(row[0]) // 1000
            if row[8] != '1' or stamp + step > through:
                continue
            item = candle(stamp, row[1:6])
            if stamp in merged and merged[stamp] != item:
                raise ReplayError('OKX 返回重叠冲突 K 线')
            merged[stamp] = item
        return [merged[t] for t in sorted(merged)][-count:]

    def initial(self, symbol, period, source, start):
        if type(start) is not int or start > self.clock():
            raise ReplayError('回放开始时间必须是已发生的历史时间')
        rows = self.read(symbol, period, source, start)
        step = PERIODS[period]
        if not rows or start-(rows[-1]['time']+step) >= step:
            raise ReplayError('所选时刻不在该来源的数据覆盖范围内，或落在历史缺口中')
        # Context never bridges a gap. Trading begins at the final close only.
        beginning = 0
        for i in range(1, len(rows)):
            if rows[i]['time'] != rows[i-1]['time']+step:
                beginning = i
        return rows[beginning:]

    def advance(self, config, clock, count):
        step = PERIODS[config['period']]
        rows = self.read(config['symbol'], config['period'], config['source'], clock+count*step, count+2)
        selected = [r for r in rows if r['time'] >= clock]
        if not selected or selected[0]['time'] != clock:
            raise ReplayError('已到数据末尾或遇到缺口；回放已暂停，未越过缺口撮合')
        result = []
        for row in selected:
            if row['time'] != clock+len(result)*step:
                break
            result.append(row)
            if len(result) == count:
                break
        return result

    def monday(self, config, clock):
        date = datetime.fromtimestamp(clock, NY).date()
        monday = date-timedelta(days=date.weekday())
        stamp = lambda d: int(datetime.combine(d, day_time.min, NY).timestamp())
        result = dict(week=monday.isoformat(), start=stamp(monday), end=stamp(monday+timedelta(days=1)), expires=stamp(monday+timedelta(days=7)))
        if clock < result['end']:
            return dict(result, state='developing')
        try:
            rows = self.read(config['symbol'], '1H', config['source'], result['end'], 26)
        except ReplayError:
            return dict(result, state='missing_monday')
        rows = [r for r in rows if result['start'] <= r['time'] < result['end']]
        if [r['time'] for r in rows] != list(range(result['start'], result['end'], 3600)):
            return dict(result, state='missing_monday')
        high, low = max(r['high'] for r in rows), min(r['low'] for r in rows)
        if high <= low:
            return dict(result, state='missing_monday')
        return dict(result, state='ready', high=high, low=low, upper=high+.618*(high-low), lower=low-.618*(high-low))
