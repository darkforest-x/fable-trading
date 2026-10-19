"""Process-shared public candle read cache; never an execution/canonical writer.

OKX contract: https://www.okx.com/docs-v5/en/#rest-api-market-data
Keys include origin, instrument and native bar (1D != 1Dutc). Nine exchange
fields are retained: contract/base/quote volumes must never be interchanged.
Only confirm=1 bars ending by the caller's clock enter the durable series.
Per-series flock coalesces readers across threads/processes; SQLite WAL holds
short transactions only. A separate budget reserves capacity for the scanner.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time

PATHS = frozenset({'/api/v5/market/candles', '/api/v5/market/history-candles'})
STEPS = {**{f'{n}m': n*60_000 for n in (1, 3, 5, 15, 30)},
         **{f'{n}H': n*3_600_000 for n in (1, 2, 4, 6, 12)},
         '6Hutc': 21_600_000, '12Hutc': 43_200_000,
         '1D': 86_400_000, '1Dutc': 86_400_000,
         '1W': 604_800_000, '1Wutc': 604_800_000}
TOTAL_RATE, BACKGROUND_RATE = 8, 3
RETAIN_BARS = 4096
OPEN_TTL_MS = 2000


def shared_clock():
    """Use the OS epoch: macOS Python 3.9 monotonic() is process-relative.

    https://docs.python.org/3/library/time.html#time.monotonic
    Local timeout arithmetic can use monotonic(); persisted budgets cannot.
    """
    if hasattr(time, 'CLOCK_MONOTONIC'):
        return time.clock_gettime(time.CLOCK_MONOTONIC)
    return time.monotonic()


class CandleError(RuntimeError):
    """A failed/malformed upstream request is never a successful stale read."""


def runtime_path(runtime=None):
    return Path(runtime or os.environ.get('FABLE_IMPULSE_RUNTIME') or
                Path.home() / 'Library/Application Support/Fable/ImpulseMonitor')


def bar_open(stamp, bar):
    shift = 8*3_600_000 if bar in {'6H', '12H', '1D'} else 0
    if bar in {'1W', '1Wutc'}:
        shift = 3*86_400_000 + (8*3_600_000 if bar == '1W' else 0)
    return (stamp + shift)//STEPS[bar]*STEPS[bar] - shift


def validate(rows, bar, now):
    if not isinstance(rows, list):
        raise CandleError('invalid_candle_payload')
    parsed = {}
    for row in rows:
        try:
            if not isinstance(row, list) or len(row) != 9 or str(row[8]) not in {'0', '1'}:
                raise ValueError()
            stamp = int(row[0])
            values = [Decimal(str(v)) for v in row[1:8]]
            o, h, low, c, contracts, base, quote = values
            if (not all(v.is_finite() for v in values) or min(o, h, low, c) <= 0
                    or min(contracts, base, quote) < 0 or h < max(o, c, low)
                    or low > min(o, c, h) or stamp < 0 or stamp != bar_open(stamp, bar)
                    or stamp > now or (str(row[8]) == '1' and stamp + STEPS[bar] > now)):
                raise ValueError()
            # Numeric equality, without a binary-float round trip or loss of volume units.
            decimal_text = [format(v, 'f') for v in values]
            normalized = [str(stamp)] + [v.rstrip('0').rstrip('.') if '.' in v else v for v in decimal_text] + [str(row[8])]
            if stamp in parsed and parsed[stamp] != normalized:
                raise CandleError('conflicting_candle_response')
            parsed[stamp] = normalized
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            raise CandleError('invalid_candle_row') from None
    return parsed


class CandleHub:
    def __init__(self, runtime=None, origin='https://www.okx.com'):
        self.root = runtime_path(runtime) / 'shared-candles'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.root / 'cache.sqlite3'
        self.origin = origin.rstrip('/')
        with (self.root / 'schema.lock').open('a') as schema_lock:
            fcntl.flock(schema_lock, fcntl.LOCK_EX)
            with self.db() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                  CREATE TABLE IF NOT EXISTS candles (
                    key TEXT NOT NULL, t INTEGER NOT NULL, raw TEXT NOT NULL,
                    PRIMARY KEY(key,t)) WITHOUT ROWID;
                  CREATE TABLE IF NOT EXISTS series (
                    key TEXT PRIMARY KEY, origin TEXT, symbol TEXT, bar TEXT,
                    latest INTEGER, accessed INTEGER DEFAULT 0, success INTEGER DEFAULT 0,
                    requests INTEGER DEFAULT 0, hits INTEGER DEFAULT 0, error TEXT,
                    error_at INTEGER DEFAULT 0, retry_at INTEGER DEFAULT 0,
                    syncing INTEGER DEFAULT 0, pid INTEGER DEFAULT 0);
                  CREATE TABLE IF NOT EXISTS transient (
                    key TEXT PRIMARY KEY, t INTEGER, raw TEXT, fetched INTEGER);
                  CREATE TABLE IF NOT EXISTS receipts (
                    key TEXT, first INTEGER, last INTEGER, at INTEGER,
                    PRIMARY KEY(key,first,last));
                  CREATE TABLE IF NOT EXISTS budget (key TEXT PRIMARY KEY, next REAL);
                ''')
                columns = {r[1] for r in db.execute('PRAGMA table_info(series)')}
                if 'missing' not in columns:
                    db.execute('ALTER TABLE series ADD COLUMN missing INTEGER DEFAULT 0')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def acquire(self, priority='background'):
        """No future reservations: background waiters cannot queue ahead of scan."""
        keys = [('all', TOTAL_RATE)]
        if priority != 'monitor':
            keys.append(('background', BACKGROUND_RATE))
        while True:
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                now = shared_clock()
                deadline = now
                for key, _ in keys + [('cooldown', 1)]:
                    row = db.execute('SELECT next FROM budget WHERE key=?', (key,)).fetchone()
                    # A reboot resets monotonic time; never inherit an old reservation.
                    if row and row[0] < now + 60:
                        deadline = max(deadline, row[0])
                wait = deadline - now
                if wait <= 0:
                    for key, rate in keys:
                        db.execute('INSERT OR REPLACE INTO budget VALUES (?,?)', (key, now+1/rate))
                    return
            time.sleep(min(wait, .15))

    def cooldown(self, seconds):
        with self.db() as db:
            db.execute('INSERT INTO budget VALUES (?,?) ON CONFLICT(key) DO UPDATE SET next=max(next,excluded.next)',
                       ('cooldown', shared_clock()+seconds))

    @contextmanager
    def series_lock(self, key):
        with (self.root / (sha256(key.encode()).hexdigest()+'.lock')).open('a') as lock:
            deadline = time.monotonic()+55
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise CandleError('candle_sync_busy_retry') from None
                    time.sleep(.03)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _rows(self, db, key, first, last):
        return {r['t']: json.loads(r['raw']) for r in db.execute(
            'SELECT t,raw FROM candles WHERE key=? AND t BETWEEN ? AND ?', (key, first, last))}

    def fetch(self, path, params, fetcher, *, now_ms=None, include_open=False):
        """Newest-first raw page; only absent intervals are requested upstream.

        ``after`` and ``before`` are exclusive exchange timestamps. Missing bars
        remain missing. Short/empty responses are retried after two seconds,
        never persisted as evidence of complete history or a delisted contract.
        """
        params = dict(params or {})
        symbol, bar = params.get('instId', ''), params.get('bar', '1m')
        if path not in PATHS or bar not in STEPS or not re.fullmatch(r'[A-Z0-9-]{1,64}', symbol):
            raise CandleError('unsupported_candle_query')
        if set(params) - {'instId', 'bar', 'limit', 'after', 'before'}:
            raise CandleError('unsupported_candle_parameter')
        now = int(time.time()*1000) if now_ms is None else int(now_ms)
        count = max(1, min(300, int(params.get('limit', 100))))
        step = STEPS[bar]
        current = bar_open(now, bar)
        last = min(current if include_open else current-step,
                   bar_open(int(params.get('after', current+1))-1, bar))
        first = max(last-(count-1)*step, int(params.get('before', -1))+1, 0)
        first = bar_open(first+step-1, bar)
        if first > last:
            return []
        key = '|'.join((self.origin, symbol, bar))
        with self.series_lock(key):
            with self.db() as db:
                db.execute('INSERT INTO series(key,origin,symbol,bar,accessed) VALUES (?,?,?,?,?) '
                           'ON CONFLICT(key) DO UPDATE SET accessed=excluded.accessed', (key,self.origin,symbol,bar,now))
                state = db.execute('SELECT * FROM series WHERE key=?', (key,)).fetchone()
                rows = self._rows(db,key,first,last)
                live = db.execute('SELECT * FROM transient WHERE key=?', (key,)).fetchone()
                if include_open and live and live['t'] == current and now-live['fetched'] < OPEN_TTL_MS:
                    if first <= live['t'] <= last:
                        rows[live['t']] = json.loads(live['raw'])
                missing = [t for t in range(first,last+1,step) if t not in rows]
                db.execute('UPDATE series SET missing=? WHERE key=?',(len(missing),key))
                receipt = db.execute('SELECT at FROM receipts WHERE key=? AND first=? AND last=?', (key,first,last)).fetchone()
                if not missing or (receipt and 0 <= now-receipt[0] < OPEN_TTL_MS and not state['error']):
                    db.execute('UPDATE series SET hits=hits+1 WHERE key=?', (key,))
                    return [rows[t] for t in sorted(rows,reverse=True)]
                if state['error'] and now < state['retry_at']:
                    raise CandleError(state['error'])
                db.execute('UPDATE series SET syncing=?,pid=? WHERE key=?', (now,os.getpid(),key))
            try:
                # Fetch one missing bounding interval. This is one request even
                # for fragmented cache holes; existing rows are checked for revision.
                left, right = min(missing), max(missing)
                query = dict(instId=symbol, bar=bar, limit=str((right-left)//step+1), after=str(right+1))
                with self.db() as db:
                    db.execute('UPDATE series SET requests=requests+1 WHERE key=?', (key,))
                received = validate(fetcher(path,query),bar,now)
                fetched = int(time.time()*1000) if now_ms is None else now
                with self.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    for t, raw in received.items():
                        if raw[8] == '0':
                            if t == current:
                                db.execute('INSERT OR REPLACE INTO transient VALUES (?,?,?,?)', (key,t,json.dumps(raw),fetched))
                            continue
                        old = db.execute('SELECT raw FROM candles WHERE key=? AND t=?',(key,t)).fetchone()
                        if old and json.loads(old[0]) != raw:
                            raise CandleError('exchange_revised_confirmed_candle')
                        db.execute('INSERT OR IGNORE INTO candles VALUES (?,?,?)',(key,t,json.dumps(raw)))
                    rows = self._rows(db,key,first,last)
                    if include_open:
                        rows.update({t:r for t,r in received.items() if r[8]=='0' and first<=t<=last})
                    latest = db.execute('SELECT max(t) FROM candles WHERE key=?',(key,)).fetchone()[0]
                    db.execute('UPDATE series SET latest=?,success=?,error=NULL,retry_at=0,syncing=0,pid=0 WHERE key=?',
                               (latest,fetched,key))
                    db.execute('UPDATE series SET missing=? WHERE key=?',(((last-first)//step+1)-len(rows),key))
                    db.execute('INSERT OR REPLACE INTO receipts VALUES (?,?,?,?)',(key,first,last,fetched))
                    db.execute('DELETE FROM receipts WHERE key=? AND at<?',(key,fetched-OPEN_TTL_MS))
                    if latest is not None:
                        db.execute('DELETE FROM candles WHERE key=? AND t<?',(key,latest-(RETAIN_BARS-1)*step))
                return [rows[t] for t in sorted(rows,reverse=True)]
            except Exception as error:
                with self.db() as db:
                    db.execute('UPDATE series SET error=?,error_at=?,retry_at=?,syncing=0,pid=0 WHERE key=?',
                               (str(error)[:240],now,now+OPEN_TTL_MS,key))
                raise

    def snapshot(self, symbol='', limit=80):
        """Read-only status; status polling never initiates market requests."""
        now = int(time.time()*1000)
        with self.db() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM series ORDER BY accessed DESC')]
        counts = dict(series=len(rows), requests=sum(r['requests'] for r in rows),
                      hits=sum(r['hits'] for r in rows), syncing=0, errors=0)
        for row in rows:
            row.pop('key')
            row['last_close_ms'] = row['latest']+STEPS[row['bar']] if row['latest'] is not None else None
            row['lag_bars'] = max(0,(bar_open(now,row['bar'])-(row['last_close_ms'] or 0))//STEPS[row['bar']]) if row['latest'] is not None else None
            row['state'] = 'error' if row['error'] else 'partial' if row['missing'] else 'empty' if row['latest'] is None else 'behind' if row['lag_bars'] else 'ready'
            if row['syncing'] and now-row['syncing'] < 60_000:
                row['state'] = 'syncing'
                counts['syncing'] += 1
            counts['errors'] += bool(row['error'])
        selected = [r for r in rows if symbol.upper() in r['symbol']]
        selected.sort(key=lambda r:(r['state'] not in {'error','syncing'}, -r['accessed']))
        size = sum(p.stat().st_size for p in self.root.glob('cache.sqlite3*'))
        return dict(now_ms=now, summary=counts, items=selected[:limit], matched=len(selected),
                    total_rate=TOTAL_RATE, background_rate=BACKGROUND_RATE, cache_bytes=size,
                    retention_bars=RETAIN_BARS, open_ttl_ms=OPEN_TTL_MS)
