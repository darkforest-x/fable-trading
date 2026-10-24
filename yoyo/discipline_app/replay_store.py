"""Independent historical practice accounts backed by native Nautilus workers.

Only this replay database is written. The server chooses every timestamp and
price; request IDs and optimistic versions fence retries and concurrent phones.
A durable command journal restores evicted native engines deterministically.
No live-account resolver, exchange client, order endpoint or credentials enter
this module. Chart history is bounded by the committed simulation clock.
"""
from collections import OrderedDict
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import select
import sqlite3
import subprocess
import threading
import time

from .replay_history import PERIODS, ReplayError, ReplayHistory, validate_symbol

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path.home()/'.local/share/crypto-toolkit/venvs/nautilus/bin/python'


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ReplayError('操作编号无效，请重新打开模拟盘')
    return value


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 < result <= Decimal('1000000000'):
            raise ValueError()
        return str(result)
    except (InvalidOperation, ValueError):
        raise ReplayError('金额须大于 0 且不超过 10 亿 USDT') from None


class NativeProcess:
    def __init__(self, config):
        if not NATIVE.is_file():
            raise ReplayError('本机 Nautilus 回放环境暂不可用')
        self.process = subprocess.Popen([str(NATIVE), '-u', '-m', 'yoyo.layers.l3_backtest.replay_native'],
                                        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        env={'PATH': '/usr/bin:/bin', 'PYTHONUNBUFFERED': '1', 'PYTHONPATH': str(ROOT),
                                             'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'}, text=False, start_new_session=True)
        self.buffer = b''
        try:
            self.call(dict(op='init', config=config))
        except Exception:
            self.close()
            raise

    def call(self, payload):
        try:
            self.process.stdin.write((dump(payload)+'\n').encode())
            self.process.stdin.flush()
            deadline = time.monotonic()+25
            while b'\n' not in self.buffer:
                if time.monotonic() >= deadline or not select.select([self.process.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                    raise ReplayError('模拟撮合响应超时；已保存记录，重试会恢复原操作状态')
                part = os.read(self.process.stdout.fileno(), 65536)
                if not part:
                    raise ReplayError('模拟撮合进程已退出；请重试恢复')
                self.buffer += part
                if len(self.buffer) > 16_000_000:
                    raise ReplayError('回放记录过大，请新建练习')
            line, self.buffer = self.buffer.split(b'\n', 1)
            response = json.loads(line)
            if not response.get('ok'):
                raise ReplayError(response.get('error', '模拟订单未被接受'))
            return response['state']
        except (OSError, ValueError, KeyError) as exc:
            if isinstance(exc, ReplayError):
                raise
            raise ReplayError('模拟撮合连接异常；原记录已保留') from None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout):
            if stream:
                stream.close()


def statistics(native, initial, samples):
    closed = native.get('closed', [])
    results = [float(r['net_pnl']) for r in closed]
    wins, losses = [r for r in results if r > 0], [r for r in results if r < 0]
    streak = longest = 0
    for result in results:
        streak = streak+1 if result < 0 else 0
        longest = max(longest, streak)
    peak, dd, dd_pct = float(initial), 0., 0.
    for point in samples:
        equity = float(point['equity'])
        peak = max(peak, equity)
        dd = max(dd, peak-equity)
        dd_pct = max(dd_pct, (peak-equity)/peak*100 if peak > 0 else 0)
    net = float(native['equity'])-float(initial)
    return dict(trades=len(results), wins=len(wins), losses=len(losses),
                win_rate=len(wins)/len(results)*100 if results else None,
                payoff=(sum(wins)/len(wins))/(-sum(losses)/len(losses)) if wins and losses else None,
                profit_factor=sum(wins)/-sum(losses) if losses else None,
                net_pnl=net, return_pct=net/float(initial)*100, max_drawdown=dd, max_drawdown_pct=dd_pct,
                longest_loss_streak=longest, realized_pnl=float(native['cash'])-float(initial),
                drawdown_basis='每根回放 K 线收盘及人工操作后的净值；不代表逐笔盘中回撤')


class ReplayStore:
    def __init__(self, path, history=None, process_factory=NativeProcess):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.history = history or ReplayHistory()
        self.factory = process_factory
        self.lock = threading.RLock()
        self.workers = OrderedDict()
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, config TEXT NOT NULL,
                context TEXT NOT NULL, native TEXT NOT NULL, samples TEXT NOT NULL, monday TEXT NOT NULL,
                levels TEXT NOT NULL, version INTEGER NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                version INTEGER NOT NULL, input TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session_id,request_id), UNIQUE(session_id,version));''')

    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def stop(self):
        with self.lock:
            for worker in self.workers.values():
                worker.close()
            self.workers.clear()

    def _discard(self, identity):
        worker = self.workers.pop(identity, None)
        if worker:
            worker.close()

    def _worker(self, db, row):
        identity = row['id']
        if identity in self.workers:
            self.workers.move_to_end(identity)
            return self.workers[identity]
        config = json.loads(row['config'])
        native_config = dict(symbol=config['symbol'], cash=config['cash'], period_seconds=PERIODS[config['period']], price_precision=8, size_precision=8)
        worker = self.factory(native_config)
        try:
            for event in db.execute('SELECT payload FROM commands WHERE session_id=? ORDER BY version', (identity,)):
                for payload in json.loads(event['payload']):
                    worker.call(payload)
        except Exception:
            worker.close()
            raise
        self.workers[identity] = worker
        while len(self.workers) > 2:
            _, old = self.workers.popitem(last=False)
            old.close()
        return worker

    def list(self):
        with self.db() as db:
            return dict(items=[dict(id=r['id'], **{k:json.loads(r['config'])[k] for k in ('symbol', 'period', 'source')},
                                   clock=json.loads(r['native'])['clock'], initial_cash=json.loads(r['config'])['cash'], version=r['version'])
                               for r in db.execute('SELECT * FROM sessions ORDER BY created DESC LIMIT 100')])

    @staticmethod
    def _snapshot(row):
        config, native = json.loads(row['config']), json.loads(row['native'])
        return dict(native, id=row['id'], symbol=config['symbol'], period=config['period'], source=config['source'],
                    initial_cash=config['cash'], requested_start=config['start'], version=row['version'],
                    bars=json.loads(row['context']), range=json.loads(row['levels']),
                    statistics=statistics(native, config['cash'], json.loads(row['samples'])),
                    engine='NautilusTrader 1.231.0', fee_rates=dict(maker=.0002, taker=.0005, read_date='2026-10-23'),
                    assumptions='1x 名义本金；OHLC 顺序；限价 maker 0.02%，市价/止损 taker 0.05%；不含滑点、资金费与真实盘口队列')

    def get(self, identity):
        request_id(identity)
        with self.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ReplayError('模拟回放不存在')
            return self._snapshot(row)

    def create(self, body):
        identity = request_id(body.get('request_id'))
        config = dict(symbol=validate_symbol(body.get('symbol')), period=body.get('period'), source=body.get('source'),
                      start=body.get('start'), cash=amount(body.get('cash')))
        if config['period'] not in PERIODS or config['source'] not in ('okx_local', 'okx_online'):
            raise ReplayError('请选择有效的回放周期和来源')
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM sessions WHERE id=?', (identity,)).fetchone()
            if old:
                if json.loads(old['config']) != config:
                    raise ReplayError('操作编号已用于另一组回放参数')
                return self._snapshot(old)
            rows = self.history.initial(config['symbol'], config['period'], config['source'], config['start'])
            worker = self.factory(dict(symbol=config['symbol'], cash=config['cash'], period_seconds=PERIODS[config['period']], price_precision=8, size_precision=8))
            payload = dict(op='advance', bars=rows[-1:])
            try:
                native = worker.call(payload)
                levels = self.history.monday(config, native['clock'])
                db.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)',
                           (identity, dump(config), dump(rows), dump(native), dump([dict(time=native['clock'], equity=native['equity'])]), '[]', dump(levels), 0, time.time()))
                db.execute('INSERT INTO commands VALUES (?,?,?,?,?)', (identity, identity, 0, dump(body), dump([payload])))
                db.commit()
            except Exception:
                worker.close()
                raise
            self.workers[identity] = worker
            while len(self.workers) > 2:
                _, old_worker = self.workers.popitem(last=False)
                old_worker.close()
        return self.get(identity)

    def command(self, identity, body):
        request_id(identity)
        rid = request_id(body.get('request_id'))
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM sessions WHERE id=?', (identity,)).fetchone()
            if not row:
                raise ReplayError('模拟回放不存在')
            duplicate = db.execute('SELECT input FROM commands WHERE session_id=? AND request_id=?', (identity, rid)).fetchone()
            if duplicate:
                if json.loads(duplicate['input']) != body:
                    raise ReplayError('操作编号已被使用，请核对最新模拟账户')
                return self._snapshot(row)
            if type(body.get('version')) is not int or body['version'] != row['version']:
                raise ReplayError('回放已在另一处推进，请刷新后再操作')
            config, native = json.loads(row['config']), json.loads(row['native'])
            context, samples, monday = [json.loads(row[k]) for k in ('context', 'samples', 'monday')]
            action, params = body.get('action'), body.get('params', {})
            if not isinstance(params, dict):
                raise ReplayError('模拟操作参数无效')
            payloads, new_bars = [], []
            if action == 'advance':
                count = params.get('count', 1)
                if type(count) is not int or not 1 <= count <= 20:
                    raise ReplayError('每次只能前进 1 至 20 根 K 线')
                new_bars = self.history.advance(config, native['clock'], count)
                payloads = [dict(op='advance', bars=[bar]) for bar in new_bars]
            elif action == 'order':
                payloads = [dict(op='order', id=rid, **{k:v for k,v in params.items() if k in ('side', 'kind', 'notional', 'price', 'stop', 'target')})]
            elif action == 'close':
                payloads = [dict(op='close', id=rid, position_id=params.get('position_id'), fraction=params.get('fraction', 1))]
            elif action == 'cancel':
                payloads = [dict(op='cancel', order_id=params.get('order_id'))]
            elif action == 'monday':
                levels = self.history.monday(config, native['clock'])
                if levels['state'] != 'ready':
                    raise ReplayError('该历史时点的纽约周一区间尚未完成，或缺少完整周一数据')
                notional = amount(params.get('notional'))
                tp = params.get('take_profit')
                if tp not in ('none', 'near', 'far'):
                    raise ReplayError('请选择一键挂单的止盈方式')
                orders = []
                for side, price, near, far in [('long', levels['lower'], levels['low'], levels['high']), ('short', levels['upper'], levels['high'], levels['low'])]:
                    key = levels['week']+':'+side
                    if key in monday or price <= 0 or (side == 'long' and price >= float(native['price'])) or (side == 'short' and price <= float(native['price'])):
                        continue
                    order = dict(id=hashlib.sha256((identity+key).encode()).hexdigest()[:32], side=side, kind='limit',
                                 notional=notional, price=f'{price:.8f}', tag='monday1618', expires=levels['expires'])
                    if tp != 'none':
                        order['target'] = f'{near if tp == "near" else far:.8f}'
                    orders.append(order)
                    monday.append(key)
                if not orders:
                    raise ReplayError('本周 1.618 已挂过单，或当前价已越过挂单价；没有新增委托')
                payloads = [dict(op='orders', items=orders)]
            else:
                raise ReplayError('不支持的模拟操作')
            worker = self._worker(db, row)
            try:
                for payload in payloads:
                    native = worker.call(payload)
                    samples.append(dict(time=native['clock'], equity=native['equity']))
                context = (context+new_bars)[-300:]
                levels = self.history.monday(config, native['clock'])
                db.execute('UPDATE sessions SET native=?,context=?,samples=?,monday=?,levels=?,version=? WHERE id=?',
                           (dump(native), dump(context), dump(samples), dump(monday), dump(levels), row['version']+1, identity))
                db.execute('INSERT INTO commands VALUES (?,?,?,?,?)', (identity, rid, row['version']+1, dump(body), dump(payloads)))
                db.commit()
            except Exception:
                self._discard(identity)
                raise
            result = self._snapshot(db.execute('SELECT * FROM sessions WHERE id=?', (identity,)).fetchone())
            if action == 'monday':
                result['notice'] = f'已挂出 {len(payloads[0]["items"])} 笔 1.618 委托；已挂过或不再被动的方向已跳过'
            return result
