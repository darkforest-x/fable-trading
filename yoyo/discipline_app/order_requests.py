"""Acknowledge mobile intents before slow exchange I/O; never replay on read/restart.

The execution ledger remains the authority. A durable request envelope permits
read-only status polling after a phone timeout; its identity cannot be reused.
"""
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from .book import encoded
from .policy import Rejected


class OrderRequests:
    def __init__(self):
        self.owner = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.ready = set()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='mobile-order')

    def initialize(self, book):
        with self.lock:
            if book.path in self.ready:
                return
            with closing(book.connect()) as db:
                db.execute('''CREATE TABLE IF NOT EXISTS mobile_requests(
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, owner TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, created REAL NOT NULL)''')
            self.ready.add(book.path)

    def submit(self, book, body):
        if not body['confirmed_live']:
            raise Rejected('本次交易需要用户明确提交')
        key = body['request_id']
        if not key.isalnum():
            raise Rejected('请求编号无效')
        self.initialize(book)
        fingerprint = hashlib.sha256(encoded(body).encode()).hexdigest()
        with self.lock, closing(book.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT fingerprint FROM mobile_requests WHERE id=?', (key,)).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise Rejected('同一请求编号不能用于不同指令')
                db.commit()
                return self.status(book, key)
            if db.execute("SELECT 1 FROM mobile_requests WHERE owner=? AND status='processing'", (self.owner,)).fetchone():
                raise Rejected('上一笔请求仍在核对，请等待结果')
            db.execute('INSERT INTO mobile_requests VALUES(?,?,?,?,?,?)',
                       (key, fingerprint, self.owner, 'processing', None, book.clock()))
            db.commit()
            try:
                self.pool.submit(self.execute, book, body)
            except RuntimeError:
                db.execute("UPDATE mobile_requests SET status='interrupted' WHERE id=?", (key,))
                raise Rejected('服务正在更新，本次请求未发送') from None
        return dict(id=key, status='processing', mode='okx_live')

    def execute(self, book, body):
        key = body['request_id']
        try:
            preview_id = body['preview_id']
            if preview_id is None:
                preview = book.preview(body['ticket'])
                if preview['policy_revision'] != body['policy_revision']:
                    raise Rejected('规则版本已变化，请重新提交')
                preview_id = preview['preview_id']
            result = book.enter(key, body['ticket'], body['policy_revision'],
                                preview_id, body['confirmed_live'])
        except Exception as exc:
            # No exception payload or credentials escape to the mobile client.
            with book.read_transaction() as (_, state):
                exists = key in state['intents']
            result = dict(id=key, status='unknown' if exists else 'rejected', mode='okx_live',
                          message=str(exc) if isinstance(exc, Rejected) else '服务暂时无法完成处理，请核对订单状态')
        with closing(book.connect()) as db:
            db.execute('UPDATE mobile_requests SET status=?,result=? WHERE id=?',
                       (result['status'], encoded(result), key))

    def status(self, book, key):
        self.initialize(book)
        with book.read_transaction() as (db, state):
            intent = state['intents'].get(key)
            if intent:
                return book.receipt(intent)
            row = db.execute('SELECT owner,status,result FROM mobile_requests WHERE id=?', (key,)).fetchone()
        if not row:
            return dict(id=key, status='not_found', mode='okx_live')
        if row[2]:
            return json.loads(row[2])
        if row[0] != self.owner or row[1] == 'interrupted':
            return dict(id=key, status='interrupted', mode='okx_live', message='上次处理已中断，未生成交易指令')
        return dict(id=key, status='processing', mode='okx_live')

    def abandon_unreceived(self, book, key):
        """Fence an absent request, without cancelling any accepted exchange order.

        Serialization with submit means either the original request owns the
        identity, or this tombstone does. A delayed POST cannot trade afterward.
        """
        self.initialize(book)
        with self.lock, closing(book.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            if key not in state['intents'] and not db.execute(
                    'SELECT 1 FROM mobile_requests WHERE id=?', (key,)).fetchone():
                result = dict(id=key, status='cancelled', mode='okx_live',
                              message='请求未送达，已停止等待；本次没有发送开仓委托')
                db.execute('INSERT INTO mobile_requests VALUES(?,?,?,?,?,?)',
                           (key, 'unreceived-tombstone', self.owner, 'cancelled', encoded(result), book.clock()))
            db.commit()
        return self.status(book, key)

    def shutdown(self):
        self.pool.shutdown(wait=True)
