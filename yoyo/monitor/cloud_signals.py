"""12H EMA20/50 + completed NY Monday wick-reclaim observations.

Only confirmed 12Hutc OHLC and a completed 5m Monday range available BEFORE
the signal bar opens are used. EMA uses close, alpha=2/(n+1), first-close
seed and at least 50 contiguous closed bars. No orders or account mutations.
The scanner remains on the default 1.618 range; Monday-boundary mode is only
available to the read-only history reconstruction endpoint.
Public candle contract: https://www.okx.com/docs-v5/en/#order-book-trading-market-data
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Annotated
import sqlite3
import threading
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Query

from yoyo.data.candle_hub import validate
from .chart_periods import candles
from .monday_chart_levels import MondayChartLevels
from .monday_fib import week_window
from .okx import OKX
from .cloud_signal_plans import (POLICY, RANGE_MODES, atr_points, fixed_stop,
                                 normalize_range_mode, policy_for, reference, stop_basis_for)
from .cloud_signal_rules import POSITION_POLICY, first_eligible, week_key

STEP = 43_200_000
FRESH_MS = 30 * 60_000
VERSION = 'ema20-50-12h-monday-wick-v1'


def ema_points(rows, seed=None):
    """Causal close-only EMA20/50; restart warmup after any missing 12H bar."""
    out = []
    a, b, previous, count = ((seed['ema20'], seed['ema50'], seed['time'], seed['count'])
                             if seed else (None, None, None, 0))
    for row in rows:
        if previous is None or row['time'] != previous + STEP:
            a = b = row['close']; count = 0
        a += (row['close'] - a) * (2 / 21)
        b += (row['close'] - b) * (2 / 51)
        count += 1
        out.append(dict(time=row['time'], ema20=a, ema50=b, count=count, ready=count >= 50))
        previous = row['time']
    return out


def signal(symbol, bar, point, anchor, now, range_mode='fib'):
    """One closed bar, strict wick penetration/reclaim; no future columns."""
    range_mode = normalize_range_mode(range_mode)
    t = bar['time']
    anchor_end, anchor_expires = anchor.get('end_ms'), anchor.get('expires_ms')
    if (not point['ready'] or point['time'] != t or t % STEP or t + STEP > now
            or anchor.get('state') != 'ready' or anchor.get('symbol') != symbol
            or not isinstance(anchor_end, int) or not isinstance(anchor_expires, int)
            or not anchor_end <= t < t + STEP <= anchor_expires):
        return None
    a, b, c, lo, hi = point['ema20'], point['ema50'], bar['close'], bar['low'], bar['high']
    window = week_window(t)
    if range_mode == 'monday':
        lower, upper = anchor.get('low'), anchor.get('high')
    else:
        lower, upper = anchor.get('lower'), anchor.get('upper')
    if (anchor.get('week') != window['week']
            or not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0
                       for x in (lower, upper))):
        return None
    side = (1 if a > b and lo < lower < c < upper else
            -1 if a < b and lower < c < upper < hi else 0)
    if not side:
        return None
    entry, target = (lower, upper) if side == 1 else (upper, lower)
    stop, stop_pct = fixed_stop(symbol, side, entry)
    if stop is None:
        return None
    risk, reward = (entry - stop) * side, (target - entry) * side
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0
               for x in (c, entry, stop, target, risk, reward)):
        return None
    signal_id = f'{VERSION}:{symbol}:{t}:{side}'
    if range_mode == 'monday':
        signal_id += ':monday'
    entry_basis = ('触及侧周一边界（12H 收盘确认后展示）' if range_mode == 'monday'
                   else '触及侧 1.618（12H 收盘确认后展示）')
    return dict(id=signal_id, version=VERSION, symbol=symbol, side=side,
                timeframe='12H', signal_open_ms=t, confirmed_ms=t+STEP, entry=entry,
                confirmation_close=c, stop=stop, stop_pct=stop_pct, target=target,
                planned_rr=reward/risk, range_mode=range_mode,
                reference_policy=policy_for(range_mode), entry_basis=entry_basis,
                stop_basis=stop_basis_for(symbol, range_mode),
                week=window['week'], range=dict(anchor),
                ema20=a, ema50=b, signal_bar=dict(bar), eligibility_policy=POSITION_POLICY,
                reference_only=True)


def notification_text(item):
    at = datetime.fromtimestamp(item['confirmed_ms']/1000, ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M')
    side = '多' if item['side'] == 1 else '空'
    stop = f"{item['stop']:.8g}" if item.get('stop') is not None else '待补齐'
    stop_pct = item.get('stop_pct')
    stop_distance = f"{stop_pct * 100:g}%" if isinstance(stop_pct, (int, float)) else '固定比例'
    rr = f"{item['planned_rr']:.2f}R" if item.get('planned_rr') is not None else '—（参考止损未就绪或位置无效）'
    return (f"12H 云带 · {item['symbol'].split('-')[0]} {side}\n1.618 插针收回 · 已收盘 {at}\n"
            f"1.618 参考入场 {item['entry']:.8g} · 固定止损 {stop}（{stop_distance}）\n"
            f"对侧 1.618 {item['target']:.8g} · 计划 {rr}\n"
            f"止损：{item.get('stop_basis') or stop_basis_for(item.get('symbol'))}\n"
            f"https://spikeok.com/#signals?pane=records&cloud={item['id']}\n参考计划，不代表成交")


class CloudSignals:
    def __init__(self, runtime, *, client=None, levels=None, sender=None, telegram_creds=None, bark_key=None):
        self.runtime = Path(runtime)
        self.path = self.runtime / 'cloud-signals.sqlite3'
        self.client = client or OKX(runtime=runtime, timeout=(3, 6))
        self.levels = levels or MondayChartLevels(runtime)
        self.stop = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.status = dict(state='starting', total=0, completed=0, errors=0, last_scan_ms=None)
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, confirmed_ms INTEGER NOT NULL,
                payload TEXT NOT NULL, telegram TEXT NOT NULL, bark TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS checkpoints (symbol TEXT PRIMARY KEY, boundary INTEGER NOT NULL, ema_state TEXT);
              CREATE TABLE IF NOT EXISTS ranges (symbol TEXT, week TEXT, payload TEXT NOT NULL, PRIMARY KEY(symbol,week));
              CREATE TABLE IF NOT EXISTS reference_inputs (signal_id TEXT PRIMARY KEY, atr14 REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS signal_time ON signals(confirmed_ms DESC);
            ''')
            if 'ema_state' not in {r['name'] for r in db.execute('PRAGMA table_info(checkpoints)')}:
                db.execute('ALTER TABLE checkpoints ADD COLUMN ema_state TEXT')
            if 'atr_state' not in {r['name'] for r in db.execute('PRAGMA table_info(checkpoints)')}:
                db.execute('ALTER TABLE checkpoints ADD COLUMN atr_state TEXT')

    def prepare_references(self):
        """Reuse cached confirmed OHLC for ATR metadata.

        Called by the background worker, not an HTTP read. No exchange requests,
        original signal rewrites, channel resets, activation changes, or insertion.
        """
        with self.db() as db:
            missing = [json.loads(r[0]) for r in db.execute('SELECT s.payload FROM signals s '
                'LEFT JOIN reference_inputs p ON p.signal_id=s.id WHERE p.signal_id IS NULL')]
            checkpoints = {r['symbol']:json.loads(r['ema_state']) for r in db.execute(
                'SELECT * FROM checkpoints WHERE atr_state IS NULL AND ema_state IS NOT NULL')}
        symbols = {s['symbol'] for s in missing} | checkpoints.keys()
        if not symbols:
            return
        hub, now = self.client.hub, self.client.clock()
        with hub.db() as cached, self.db() as db:
            for symbol in sorted(symbols):
                key = '|'.join((hub.origin, symbol, '12Hutc'))
                rows = {r['t']:json.loads(r['raw']) for r in cached.execute(
                    'SELECT t,raw FROM candles WHERE key=? AND t<=? ORDER BY t', (key, now-STEP))}
                series = candles(rows)
                points = {p['time']:p for p in atr_points(series)}
                for item in (s for s in missing if s['symbol'] == symbol):
                    point = points.get(item['signal_open_ms'])
                    if point and point['atr14'] is not None:
                        db.execute('INSERT OR IGNORE INTO reference_inputs VALUES (?,?)', (item['id'], point['atr14']))
                seed = checkpoints.get(symbol)
                if seed and seed['time'] in points:
                    db.execute('UPDATE checkpoints SET atr_state=? WHERE symbol=? AND atr_state IS NULL',
                               (json.dumps(points[seed['time']]), symbol))

    def project(self, item, inputs=None, range_mode=None):
        if inputs is None:
            with self.db() as db:
                row = db.execute('SELECT atr14 FROM reference_inputs WHERE signal_id=?', (item['id'],)).fetchone()
            atr = row[0] if row else None
        else:
            atr = inputs.get(item['id'])
        return dict(reference(item, atr, range_mode), eligibility_policy=POSITION_POLICY)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def activate(self):
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES ('activated_ms',?)", (self.client.clock(),))
            # An interrupted HTTP request may have been delivered. Never resend it.
            for ch in ('telegram', 'bark'):
                db.execute(f"UPDATE signals SET {ch}='unknown' WHERE {ch}='sending'")
                db.execute(f"UPDATE signals SET {ch}='record_only' WHERE {ch}='pending'")

    def record(self, item, atr=None):
        now = self.client.clock()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = [json.loads(r[0]) for r in db.execute('SELECT payload FROM signals')]
            item_key = (item.get('symbol'), week_key(item))
            if not item_key[0] or not item_key[1]:
                return False
            existing_group = [old for old in existing
                              if (old.get('symbol'), week_key(old)) == item_key]
            candidate_rank = self._rank(item)
            if any(self._rank(old) <= candidate_rank for old in existing_group):
                return False
            row = db.execute("SELECT value FROM meta WHERE key='activated_ms'").fetchone()
            fresh = row is not None and row[0] < item['confirmed_ms'] <= now < item['confirmed_ms'] + FRESH_MS
            item = dict(item, historical=not fresh, detected_ms=now)
            channel_state = "record_only" if fresh else "historical"
            added = db.execute('INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?)',
                              (item['id'], item['confirmed_ms'], json.dumps(item, allow_nan=False), channel_state, channel_state)).rowcount == 1
            if atr is not None and math.isfinite(atr) and atr > 0:
                db.execute('INSERT OR IGNORE INTO reference_inputs VALUES (?,?)', (item['id'], atr))
            return added

    @staticmethod
    def _rank(item):
        stamp, confirmed = item.get('signal_open_ms'), item.get('confirmed_ms')
        return (stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else
                confirmed if isinstance(confirmed, int) and not isinstance(confirmed, bool) else 0,
                confirmed if isinstance(confirmed, int) and not isinstance(confirmed, bool) else 0,
                str(item.get('id') or ''))

    @staticmethod
    def _canonical_rows(rows):
        parsed = []
        row_by_id = {}
        for row in rows:
            try:
                item = json.loads(row['payload'])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict) or not item.get('id'):
                continue
            parsed.append(item)
            row_by_id[item['id']] = row
        return [row_by_id[item['id']] for item in first_eligible(parsed)
                if item['id'] in row_by_id]


    def scan_symbol(self, symbol, boundary):
        if self.stop.is_set():
            return
        raw = self.client.get('/api/v5/market/candles', dict(instId=symbol, bar='12Hutc', limit='300'))
        now = self.client.clock()
        closed = {t:r for t,r in validate(raw, '12Hutc', now).items() if r[8] == '1' and t+STEP <= boundary}
        series = candles(closed)
        if not series or series[-1]['time'] + STEP < boundary:
            raise ValueError('closed_tail_lagging')
        with self.db() as db:
            checkpoint = db.execute('SELECT * FROM checkpoints WHERE symbol=?', (symbol,)).fetchone()
        seed = json.loads(checkpoint['ema_state']) if checkpoint and checkpoint['ema_state'] else None
        atr_seed = json.loads(checkpoint['atr_state']) if checkpoint and checkpoint['atr_state'] else None
        atr_series = [r for r in series if r['time'] > atr_seed['time']] if atr_seed else series
        atrs = atr_points(atr_series, atr_seed)
        atr_by_time = {p['time']:p['atr14'] for p in atrs}
        if seed:
            series = [r for r in series if r['time'] > seed['time']]
        points = ema_points(series, seed)
        # Last two weeks of confirmed references at initial discovery. Historical
        # rows are explicitly labelled and never pushed; INSERT preserves each plan.
        anchors = {}
        for bar, point in list(zip(series, points))[-28:]:
            if not point['ready']:
                continue
            at = bar['time']
            window = week_window(at)
            if at < window['end'] or at+STEP > window['expires']:
                continue
            # All anchors share exactly the chart's confirmed 5m definition.
            if window['week'] not in anchors:
                with self.db() as db:
                    saved = db.execute('SELECT payload FROM ranges WHERE symbol=? AND week=?', (symbol, window['week'])).fetchone()
                anchors[window['week']] = json.loads(saved[0]) if saved else self.levels.read(symbol, at)
                if not saved and anchors[window['week']]['state'] == 'ready':
                    with self.db() as db:
                        db.execute('INSERT OR IGNORE INTO ranges VALUES (?,?,?)',
                                   (symbol, window['week'], json.dumps(anchors[window['week']])))
            anchor = anchors[window['week']]
            if anchor['state'] not in ('ready', 'developing'):
                raise ValueError('monday_anchor_missing')
            item = signal(symbol, bar, point, anchor, now)
            if item:
                self.record(item, atr_by_time.get(bar['time']))
        with self.db() as db:
            last = points[-1] if points else seed
            last_atr = atrs[-1] if atrs else atr_seed
            db.execute('INSERT OR REPLACE INTO checkpoints (symbol,boundary,ema_state,atr_state) VALUES (?,?,?,?)',
                       (symbol, boundary, json.dumps(last), json.dumps(last_atr) if last_atr else None))

    def scan_once(self):
        self.prepare_references()
        self.client.synchronize()
        boundary = self.client.clock() // STEP * STEP
        symbols = [r['instId'] for r in self.client.instruments() if r['instId'].endswith('-USDT-SWAP')]
        with self.db() as db:
            done = {r['symbol'] for r in db.execute('SELECT symbol FROM checkpoints WHERE boundary>=?', (boundary,))}
        pending = [s for s in symbols if s not in done]
        with self.lock:
            self.status.update(state='scanning' if pending else 'watching', total=len(symbols),
                               completed=len(symbols)-len(pending), errors=0, waiting=0, error_samples=[], boundary_ms=boundary)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='cloud-12h-fetch') as pool:
            jobs = {pool.submit(self.scan_symbol, symbol, boundary):symbol for symbol in pending}
            for future in as_completed(jobs):
                if self.stop.is_set():
                    for job in jobs:
                        job.cancel()
                    break
                try:
                    future.result()
                    with self.lock:
                        self.status['completed'] += 1
                except Exception as exc:
                    with self.lock:
                        waiting = isinstance(exc, ValueError) and str(exc) == 'closed_tail_lagging'
                        self.status['waiting' if waiting else 'errors'] += 1
                        if len(self.status['error_samples']) < 8:
                            self.status['error_samples'].append(dict(symbol=jobs[future],
                                reason='12H 收盘数据滞后' if waiting else '行情读取未完成'))
        with self.lock:
            self.status.update(state='partial' if self.status['errors'] or self.status['waiting'] else 'watching', last_scan_ms=self.client.clock())

    def start(self):
        if self.thread is not None:
            return
        self.activate()
        self.thread = threading.Thread(target=self._run, name='cloud-12h', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                self.scan_once()
            except Exception:
                with self.lock:
                    self.status.update(state='error', error='行情同步暂不可用')
            self.stop.wait(60)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=3)

    def snapshot(self, limit=300, signal_id='', search='', side=0, sort='newest', scope='all', offset=0):
        # Origin is immutable. Recency is a display window, not trade validity.
        now = self.client.clock()
        with self.db() as db:
            all_rows = db.execute('SELECT * FROM signals WHERE confirmed_ms<=? ORDER BY confirmed_ms DESC,id',
                                  (now,)).fetchall()
            canonical_rows = self._canonical_rows(all_rows)
            selected_raw = db.execute('SELECT * FROM signals WHERE id=? AND confirmed_ms<=?',
                                      (signal_id, now)).fetchone() if signal_id else None
            inputs = dict(db.execute('SELECT signal_id,atr14 FROM reference_inputs'))
            activated = db.execute("SELECT value FROM meta WHERE key='activated_ms'").fetchone()
        canonical = list(canonical_rows)
        if search:
            needle = ''.join(c for c in search.upper() if c.isascii() and c.isalnum())
            canonical = [row for row in canonical if needle in ''.join(
                c for c in json.loads(row['payload']).get('symbol', '').upper()
                if c.isascii() and c.isalnum())]
        if side:
            canonical = [row for row in canonical if json.loads(row['payload']).get('side') == side]
        activated_ms = activated[0] if activated else None
        all_count = len(canonical)
        live_rows = [row for row in canonical if
                     not json.loads(row['payload']).get('historical', True)
                     and now >= row['confirmed_ms'] > now-STEP]
        live_count = len(live_rows)

        def present(row):
            value = self.project(json.loads(row['payload']), inputs)
            origin = ('live' if not value.get('historical', True) else
                      'delayed' if activated_ms is not None and value['confirmed_ms'] > activated_ms else 'backfill')
            age = now-value['confirmed_ms']
            freshness = ('new' if 0 <= age < FRESH_MS else 'recent' if 0 <= age < STEP else 'archived') if origin == 'live' else 'archived'
            return dict(value, provenance=origin, freshness=freshness,
                        notifications=dict(telegram=row['telegram'], bark=row['bark']))
        scoped = canonical
        if scope == 'live':
            scoped = live_rows
        elif scope == 'history':
            live_ids = {r['id'] for r in live_rows}
            scoped = [row for row in canonical if row['id'] not in live_ids]
        if sort == 'rr':
            ranked = [present(row) for row in scoped]
            ranked.sort(key=lambda x: (-(x['planned_rr'] if x['planned_rr'] is not None else -math.inf),
                                       -x['confirmed_ms'], x['id']))
            items = ranked[offset:offset + limit]
        else:
            oldest = sort == 'oldest'
            ordered = sorted(scoped, key=lambda r: (r['confirmed_ms'] if oldest else -r['confirmed_ms'], r['id']))
            items = [present(row) for row in ordered[offset:offset + limit]]
        with self.lock:
            status = dict(self.status)
        selected = None
        if selected_raw:
            selected = next((row for row in canonical_rows if row['id'] == selected_raw['id']), None)
            if selected is None:
                selected_payload = json.loads(selected_raw['payload'])
                key = (selected_payload.get('symbol'), week_key(selected_payload))
                selected = next((row for row in canonical_rows
                                 if (json.loads(row['payload']).get('symbol'),
                                     week_key(json.loads(row['payload']))) == key), None)
        focus = present(selected) if selected else None
        redirect_id = focus['id'] if focus and focus['id'] != signal_id else None
        counts = dict(live=live_count, history=all_count-live_count, all=all_count)
        return dict(schema=1, version=VERSION, items=items, selected=focus,
                    selected_requested_id=signal_id, selection_redirect_id=redirect_id,
                    total=counts.get(scope, all_count), now_ms=now,
                    range_mode='fib', reference_policy=POLICY, eligibility_policy=POSITION_POLICY,
                    scope=scope, counts=counts, activated_ms=activated_ms, fresh_ms=FRESH_MS, next_close_ms=(now//STEP+1)*STEP,
                    status=dict(status, worker_alive=bool(self.thread and self.thread.is_alive()),
                                channels=dict(telegram=False, bark=False)))

    def history(self, symbol):
        """All stored plans for exactly one symbol, independent of card filters."""
        with self.db() as db:
            rows = db.execute("SELECT * FROM signals WHERE json_extract(payload,'$.symbol')=? "
                              'AND confirmed_ms<=? ORDER BY confirmed_ms,id', (symbol, self.client.clock())).fetchall()
            inputs = dict(db.execute('SELECT signal_id,atr14 FROM reference_inputs'))
        rows = self._canonical_rows(rows)
        items = [self.project(json.loads(r['payload']), inputs) for r in rows]
        return dict(symbol=symbol, items=items, latest_id=items[-1]['id'] if items else None,
                    range_mode='fib', reference_policy=POLICY, eligibility_policy=POSITION_POLICY,
                    reconstruction_supported=True)


def install(app, runtime):
    from .cloud_signal_history import CloudSignalHistory
    service = CloudSignals(runtime)
    app.state.cloud_signals = service
    replay = CloudSignalHistory(service, levels=getattr(app.state, 'monday_chart_levels', None))
    app.state.cloud_signal_history = replay

    @app.get('/api/cloud-signals/history')
    def history(symbol: str = Query(..., pattern=r'^[A-Z0-9]+-USDT-SWAP$', max_length=64),
                week: str = Query('', pattern=r'^$|^\d{4}-\d{2}-\d{2}$'),
                range_mode: str = 'fib'):
        if range_mode not in RANGE_MODES:
            raise HTTPException(400, 'badrangemode')
        if week:
            try:
                return replay.read(symbol, week, range_mode=range_mode)
            except ValueError as error:
                raise HTTPException(400, str(error)) from None
            except RuntimeError as error:
                raise HTTPException(503, str(error)) from None
            except Exception:
                raise HTTPException(503, '历史参考框暂不可用，请重试') from None
        if range_mode == 'monday':
            from .cloud_signal_plans import MONDAY_POLICY
            return dict(schema=1, symbol=symbol, items=[], latest_id=None,
                        reconstruction_supported=True, range_mode='monday',
                        reference_policy=MONDAY_POLICY, eligibility_policy=POSITION_POLICY)
        return service.history(symbol)

    @app.get('/api/cloud-signals')
    def snapshot(limit: int = Query(300, ge=1, le=1000), signal_id: str = Query('', max_length=240),
                 search: str = Query('', max_length=40), side: int = Query(0, ge=-1, le=1),
                 sort: str = Query('newest', pattern='^(newest|oldest|rr)$'),
                 scope: str = Query('all', pattern='^(live|history|all)$'), offset: Annotated[int, Query(ge=0)] = 0):
        return service.snapshot(limit, signal_id, search, side, sort, scope, offset)

