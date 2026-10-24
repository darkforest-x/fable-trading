"""Persist explicit, paper-only Monday 1.618 universe entry campaigns.

Levels and consumed touches come only from the completed current New York
Monday monitor. Quotes, collateral, reservations, fees and fills belong to the
existing paper ledger. This coordinator never creates exchange orders or uses
future candles. A database uniqueness key survives restarts and configuration
changes; a generation fence inside placement/matching makes stop atomic.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

from yoyo.monitor.monday_fib import week_window
from .paper_desk import clock_ms, positive, leverage_value, number, MAKER_FEE

logger = logging.getLogger(__name__)
KIND = 'monday_1618'


def validate_settings(value):
    keys = {'notional_usdt', 'leverage', 'direction', 'mode', 'take_profit', 'stop_loss_percent'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('请完整填写单笔金额、杠杆、方向、有效期与止盈止损设置')
    result = dict(value)
    result['notional_usdt'] = str(positive(result['notional_usdt']))
    result['leverage'] = leverage_value(result['leverage'])
    for key, choices in [('direction', {'both', 'long', 'short'}),
                         ('mode', {'once', 'weekly'}), ('take_profit', {'none', 'near', 'far'})]:
        if result[key] not in choices:
            raise ValueError('批量挂单设置无效')
    if result['stop_loss_percent'] is not None:
        pct = positive(result['stop_loss_percent'])
        if pct >= 100:
            raise ValueError('止损距离须小于 100%')
        result['stop_loss_percent'] = str(pct)
    return result


def counts(items):
    result = dict(candidates=len(items), eligible=0, skipped=0, created=0,
                  pending=0, filled=0, cancelled=0, rejected=0)
    for item in items:
        state = item['status']
        if state in result:
            result[state] += 1
        if item.get('order_id'):
            result['created'] += 1
    return result


def rounded(value, tick, up=False):
    return str((positive(value) / tick).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick)


class MondayBulk:
    interval_seconds = 5

    def __init__(self, desk, limits, snapshot, quotes, catalog):
        self.desk, self.limits = desk, limits
        self.snapshot, self.quotes, self.catalog = snapshot, quotes, catalog
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.last_error = None
        self.tick_lock = threading.Lock()

    def status(self, account_id):
        week = week_window(clock_ms())['week']
        with self.desk.connect() as db:
            self.desk.row(db, 'accounts', account_id)
            raw = db.execute('SELECT * FROM monday_bulk_configs WHERE account_id=?', (account_id,)).fetchone()
            config = dict(raw) if raw else {}
            items = [dict(r) for r in db.execute(
                'SELECT * FROM monday_bulk_items WHERE account_id=? AND week=? ORDER BY symbol,side',
                (account_id, week))]
            by_key = {(i['symbol'], i['side']): i for i in items}
            for order in db.execute("SELECT * FROM limit_orders WHERE account_id=? AND bulk_kind=? AND bulk_week=?",
                                    (account_id, KIND, week)):
                key = (order['symbol'], order['side'])
                item = by_key.setdefault(key, dict(symbol=key[0], side=key[1]))
                item.update(status=order['status'], reason=order['reason'], order_id=order['id'],
                            entry=order['limit_price'], quantity=order['quantity'],
                            take_profit=order['take_profit'], stop_loss=order['stop_loss'])
        items = list(by_key.values())
        return dict(account_id=account_id, enabled=bool(config.get('enabled')), week=week,
                    version=config.get('version', 0), generation=config.get('generation', 0),
                    settings=json.loads(config['settings_json']) if config.get('settings_json') else None,
                    last_error=config.get('last_error'), last_tick_ms=config.get('last_tick_ms'),
                    worker_running=bool(self.thread and self.thread.is_alive()), counts=counts(items), items=items)

    def configure(self, account_id, settings, enabled, request_id, version):
        if type(enabled) is not bool or type(version) is not int or version < 0:
            raise ValueError('批量开关或版本无效')
        settings = validate_settings(settings) if settings is not None else None
        if enabled and settings is None:
            raise ValueError('开启前必须明确单笔金额和交易设置')
        payload = dict(action='configure_monday_bulk', account_id=account_id, settings=settings,
                       enabled=enabled, version=version)
        at = clock_ms()
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self.desk.replay(db, request_id, payload)
            if previous:
                return previous
            self.desk.row(db, 'accounts', account_id)
            raw = db.execute('SELECT * FROM monday_bulk_configs WHERE account_id=?', (account_id,)).fetchone()
            current = dict(raw) if raw else dict(version=0, generation=0, settings_json=None)
            if current['version'] != version:
                raise ValueError('设置已被其他窗口修改，请刷新后重试')
            saved = json.dumps(settings, ensure_ascii=False) if settings else current['settings_json']
            generation = current['generation'] + 1
            armed_week = week_window(at)['week'] if settings and settings['mode'] == 'once' else None
            db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason=? "
                       "WHERE account_id=? AND bulk_kind=? AND status='pending'",
                       (at, '全币种 1.618 设置已停止或更新', account_id, KIND))
            db.execute('INSERT INTO monday_bulk_configs(account_id,settings_json,enabled,version,generation,armed_week,updated_ms) '
                       'VALUES(?,?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET settings_json=excluded.settings_json,'
                       'enabled=excluded.enabled,version=excluded.version,generation=excluded.generation,'
                       'armed_week=excluded.armed_week,updated_ms=excluded.updated_ms,last_error=NULL',
                       (account_id, saved, int(enabled), version + 1, generation, armed_week, at))
            response = dict(account_id=account_id, enabled=enabled, version=version + 1,
                            generation=generation, settings=json.loads(saved) if saved else None)
            result = self.desk.receipt(db, request_id, payload, response)
        self.wake_event.set()
        return result

    def _source(self):
        snapshot = self.snapshot()
        at = clock_ms()
        window = week_window(at)
        if snapshot.get('week') != window['week'] or not -5000 <= at - int(snapshot.get('now_ms', 0)) <= 15000:
            raise ValueError('周一 1.618 行情快照过期，等待当前周数据')
        if not snapshot.get('monday_complete') or at < window['end']:
            raise ValueError('纽约周一区间尚未结束，等待完整周一数据')
        if not snapshot.get('stream', {}).get('connected'):
            raise ValueError('周一 1.618 行情流断开，暂停新增挂单')
        return snapshot, window

    @staticmethod
    def _untouched(row, side):
        if not row or row.get('state') != 'ready' or row.get('stale'):
            raise ValueError('周一区间未就绪或行情过期')
        edge = 'upper' if side == 'short' else 'lower'
        if row.get('touches', {}).get(edge):
            raise ValueError('本周该方向已触及 1.618，不补挂')
        price, level = positive(row.get('price')), positive(row.get(edge))
        if (side == 'long' and price <= level) or (side == 'short' and price >= level):
            raise ValueError('当前监控价格已触及 1.618，不补挂')

    def _preview(self, account_id, settings):
        snapshot, window = self._source()
        rows = {r['symbol']: r for r in snapshot['items']}
        symbols = sorted({r['symbol'] for r in self.catalog()})
        marks = self.quotes(sorted(set(symbols) | set(self.desk.symbols(account_id))))
        with self.desk.connect() as db:
            account = self.desk.row(db, 'accounts', account_id)
            positions = [dict(r) for r in db.execute(
                'SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0', (account_id,))]
            totals = self.desk.totals(account, positions, marks, clock_ms(), self.desk.pending_orders(db, account_id))
            existing = {(r['symbol'], r['side']): dict(r) for r in db.execute(
                'SELECT * FROM limit_orders WHERE account_id=? AND bulk_kind=? AND bulk_week=?',
                (account_id, KIND, window['week']))}
        items, virtual = [], []
        sides = ['long', 'short'] if settings['direction'] == 'both' else [settings['direction']]
        for symbol in symbols:
            for side in sides:
                item = dict(symbol=symbol, side=side, status='skipped', reason='', order_id=None)
                items.append(item)
                order = existing.get((symbol, side))
                if order:
                    item.update(status=order['status'], reason='本周此方向已有委托记录，不重复挂单',
                                order_id=order['id'], entry=order['limit_price'], quantity=order['quantity'])
                    continue
                try:
                    row = rows.get(symbol)
                    self._untouched(row, side)
                    quote = marks.get(symbol)
                    if not quote:
                        raise ValueError('缺少新鲜买卖报价')
                    instrument = quote.get('risk', {}).get('instrument', {})
                    if not instrument:
                        raise ValueError('合约规格或风险档位正在加载')
                    tick = positive(instrument.get('tickSz'))
                    raw = positive(row['upper' if side == 'short' else 'lower'])
                    price = rounded(raw, tick, up=side == 'short')
                    tp, sl = None, None
                    if settings['take_profit'] != 'none':
                        near = settings['take_profit'] == 'near'
                        edge = ('high' if side == 'short' else 'low') if near else ('low' if side == 'short' else 'high')
                        tp = rounded(row[edge], tick, up=side == 'long')
                    if settings['stop_loss_percent'] is not None:
                        fraction = number(settings['stop_loss_percent']) / 100
                        raw_sl = number(price) * (1 + fraction if side == 'short' else 1 - fraction)
                        sl = str((raw_sl / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick)
                    item.update(entry=price, raw_level=str(raw), take_profit=tp, stop_loss=sl)
                    sized = self.desk.size_bulk_entry(account_id, side, quote, settings['leverage'],
                                                      settings['notional_usdt'], marks, price, tp, sl, virtual)
                    item.update(status='eligible', reason='可挂单', quantity=sized['quantity'],
                                estimated_margin=sized['margin'], fee_rate=sized['fee_rate'])
                    virtual.append(dict(id=f'preview-{len(virtual)}', symbol=symbol, action='open', status='pending',
                                        side=side, quantity=sized['quantity'], limit_price=price,
                                        leverage=settings['leverage'], fee_rate=str(MAKER_FEE)))
                except (ValueError, KeyError, TypeError) as error:
                    item.update(status='rejected' if '资金不足' in str(error) else 'skipped', reason=str(error))
        margin = sum((number(i['estimated_margin']) for i in items if i['status'] == 'eligible'), number(0))
        fees = sum((number(o['quantity']) * number(o['limit_price']) * MAKER_FEE for o in virtual), number(0))
        available = totals['available']
        return dict(account_id=account_id, week=window['week'], expires_ms=window['expires'],
                    available=available, estimated_margin=str(margin), estimated_entry_fees=str(fees),
                    available_after=str(number(available) - margin - fees) if available is not None else None,
                    settings=settings, counts=counts(items), items=items), marks

    def preview(self, account_id, settings):
        return self._preview(account_id, validate_settings(settings))[0]

    def _record(self, account_id, generation, week, items, error=None):
        at = clock_ms()
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT enabled,generation FROM monday_bulk_configs WHERE account_id=?', (account_id,)).fetchone()
            if not current or not current['enabled'] or current['generation'] != generation:
                return
            db.execute('UPDATE monday_bulk_configs SET last_error=?,last_tick_ms=? WHERE account_id=?', (error, at, account_id))
            for item in items:
                fields = ['status', 'reason', 'entry', 'raw_level', 'take_profit', 'stop_loss', 'quantity',
                          'estimated_margin', 'fee_rate', 'order_id']
                db.execute('INSERT INTO monday_bulk_items(account_id,week,symbol,side,generation,' + ','.join(fields) +
                           ',updated_ms) VALUES(' + ','.join('?' for _ in range(16)) + ') ON CONFLICT(account_id,week,symbol,side) '
                           'DO UPDATE SET generation=excluded.generation,updated_ms=excluded.updated_ms,' +
                           ','.join(f'{f}=excluded.{f}' for f in fields),
                           (account_id, week, item['symbol'], item['side'], generation,
                            *(item.get(f) for f in fields), at))

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.tick_lock.release()

    def _tick(self):
        at = clock_ms()
        week = week_window(at)['week']
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE monday_bulk_configs SET enabled=0,version=version+1,generation=generation+1,updated_ms=? "
                       "WHERE enabled=1 AND armed_week IS NOT NULL AND armed_week<>?", (at, week))
            db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason='纽约周已结束，旧挂单过期' "
                       "WHERE status='pending' AND bulk_kind=? AND bulk_expires_ms<=?", (at, KIND, at))
            configs = [dict(r) for r in db.execute('SELECT * FROM monday_bulk_configs WHERE enabled=1')]
        for config in configs:
            if self.stop_event.is_set():
                break
            account_id, generation = config['account_id'], config['generation']
            try:
                settings = validate_settings(json.loads(config['settings_json']))
                preview, marks = self._preview(account_id, settings)
                for item in preview['items']:
                    if self.stop_event.is_set():
                        break
                    if item['status'] != 'eligible':
                        continue
                    try:
                        # Recheck first touch after sizing; a slow quote fetch must
                        # not submit a missed first-touch order on the rebound.
                        latest, window = self._source()
                        if window['week'] != preview['week']:
                            raise ValueError('纽约周已切换，等待下一轮刷新')
                        current = next((r for r in latest['items'] if r['symbol'] == item['symbol']), None)
                        self._untouched(current, item['side'])
                        quote = marks[item['symbol']]
                        tagged = dict(quote, risk={**quote.get('risk', {}), 'monday_bulk':
                                      dict(account_id=account_id, week=preview['week'], generation=generation),
                                      'monday_order_type': 'limit'})
                        saved = self.desk.save_quote(tagged)
                        response = self.limits.place('open', account_id, item['quantity'], item['entry'], saved['id'],
                            uuid.uuid4().hex, marks, item['side'], settings['leverage'], '全币种 周一1.618',
                            item['take_profit'], item['stop_loss'], bulk_identity=dict(week=preview['week'],
                            generation=generation, expires_ms=preview['expires_ms'], level=item['raw_level']))
                        order = response['order']
                        item.update(status=order['status'], reason=order['reason'], order_id=order['id'])
                    except (ValueError, KeyError, TypeError) as error:
                        item.update(status='rejected', reason=str(error))
                self._record(account_id, generation, preview['week'], preview['items'])
            except Exception as error:
                logger.warning('Monday paper campaign %s deferred: %s', account_id, error)
                self._record(account_id, generation, week, [], str(error))

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name='paper-monday-bulk', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception as error:
                self.last_error = str(error)
                logger.exception('Monday paper campaign worker failed')
            self.wake_event.wait(self.interval_seconds)
            self.wake_event.clear()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread:
            self.thread.join(timeout=10)
