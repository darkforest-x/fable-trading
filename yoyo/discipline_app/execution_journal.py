"""Durable exchange outbox: commit an attempt before any external side effect.

SQLite cannot roll back an OKX order. A timeout/crash after 'attempted' therefore
requires reconciliation by the original exchange client ID, never a fresh send.
This journal stores order instructions and receipts, never API credentials.
"""
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from .policy import Rejected
from .okx_execution import ExchangeRejected, ExchangeUncertain


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class ExecutionJournal:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(self.connect()) as db:
            db.executescript('''PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS exchange_commands (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    endpoint TEXT NOT NULL, payload TEXT NOT NULL,
                    phase TEXT NOT NULL, receipt TEXT, created REAL NOT NULL,
                    updated REAL NOT NULL);
            ''')
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=15000')
        return db

    def prepare(self, identity, endpoint, payload, db=None):
        if not isinstance(identity, str) or not 16 <= len(identity) <= 80:
            raise Rejected('交易请求编号无效')
        fingerprint = hashlib.sha256(encode([endpoint, payload]).encode()).hexdigest()
        owned = db is None
        connection = db if db is not None else self.connect()
        try:
            if owned:
                connection.execute('BEGIN IMMEDIATE')
            prior = connection.execute('SELECT fingerprint FROM exchange_commands WHERE id=?', (identity,)).fetchone()
            if prior and prior[0] != fingerprint:
                raise Rejected('同一交易请求编号不能对应不同参数')
            if not prior:
                connection.execute('INSERT INTO exchange_commands VALUES(?,?,?,?,?,?,?,?)',
                    (identity, fingerprint, endpoint, encode(payload), 'prepared', None, self.clock(), self.clock()))
            if owned:
                connection.commit()
        except BaseException:
            if owned:
                connection.rollback()
            raise
        finally:
            if owned:
                connection.close()

    def inspect(self, identity):
        with closing(self.connect()) as db:
            row = db.execute('SELECT * FROM exchange_commands WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise Rejected('交易请求记录不存在')
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['receipt'] = json.loads(result['receipt']) if result['receipt'] else None
        return result

    def dispatch(self, identity, client):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT phase,receipt,endpoint,payload FROM exchange_commands WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise Rejected('交易请求尚未持久化')
            phase, receipt, endpoint, payload = row
            if phase == 'acknowledged':
                return json.loads(receipt)
            if phase == 'rejected':
                raise ExchangeRejected(json.loads(receipt)['code'])
            if phase != 'prepared':
                raise ExchangeUncertain('该请求已发送，等待原请求编号的交易所回执，禁止重复下单')
            db.execute("UPDATE exchange_commands SET phase='attempted',updated=? WHERE id=?", (self.clock(), identity))
            db.commit()
        try:
            receipt = client.post(endpoint, json.loads(payload))
        except ExchangeRejected as exc:
            self.resolve(identity, 'rejected', {'code': exc.code})
            raise
        except BaseException:
            # Includes process interruption. Keep the committed 'attempted'
            # record even when the network call never returned.
            raise
        self.resolve(identity, 'acknowledged', receipt)
        return receipt

    def resolve(self, identity, phase, receipt):
        if phase not in ('acknowledged', 'rejected'):
            raise ValueError('Invalid exchange resolution')
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT phase FROM exchange_commands WHERE id=?', (identity,)).fetchone()
            if not prior or prior[0] != 'attempted':
                raise Rejected('只允许核对已发送但尚未确认的请求')
            db.execute('UPDATE exchange_commands SET phase=?,receipt=?,updated=? WHERE id=?',
                (phase, encode(receipt), self.clock(), identity))
            db.commit()

    def unresolved(self):
        with closing(self.connect()) as db:
            rows = db.execute("SELECT id FROM exchange_commands WHERE phase='attempted' ORDER BY created").fetchall()
        return [self.inspect(row[0]) for row in rows]
