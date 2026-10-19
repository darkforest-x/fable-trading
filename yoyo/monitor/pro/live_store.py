"""Durable Pro approvals and receipts, isolated from Standard execution ledgers.

SQLite transactions reserve an entire plan's risk before any child can run.
An exchange write is recorded before dispatch; unknown writes are never retried.
"""
from contextlib import closing
import json
import sqlite3
import time
import uuid
from pathlib import Path
from decimal import Decimal

NOW = lambda: int(time.time() * 1000)
TERMINAL = {'closed', 'canceled', 'rejected', 'expired'}


class LiveStore:
    def __init__(self, directory, *, clock=NOW):
        self.clock=clock
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'pro-live.sqlite3'
        if self.path.is_symlink():
            raise ValueError('unsafe live state path')
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
                  state TEXT NOT NULL, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS follow_attempts(id TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            db.commit()
        self.path.chmod(0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('PRAGMA synchronous=FULL')
        return closing(db)

    def meta(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value, allow_nan=False)))
            db.commit()

    def rows(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT value FROM plans ORDER BY rowid DESC')]

    def get(self, identity):
        with self.db() as db:
            row = db.execute('SELECT value FROM plans WHERE id=?', (identity,)).fetchone()
        if not row:
            raise ValueError('委托不存在')
        return json.loads(row[0])

    def preview(self, spec, quote):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT value FROM plans WHERE request_id=?', (spec['request_id'],)).fetchone()
            if old:
                value = json.loads(old[0])
                if value['spec'] != spec:
                    raise ValueError('请求标识已用于其他参数')
                return value
            if db.execute('SELECT count(*) FROM plans').fetchone()[0] >= 5000:
                raise ValueError('委托保存数量达到上限')
            value = dict(id=uuid.uuid4().hex, spec=spec, quote=quote, state='preview',
                         created_ms=self.clock(), expires_ms=self.clock()+60000, children=[], next_ms=0,
                         cancel_requested=False, completed=0)
            db.execute('INSERT INTO plans VALUES(?,?,?,?)', (value['id'],spec['request_id'],'preview',json.dumps(value)))
            db.commit()
        return value

    def save(self, value):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM plans WHERE id=?',(value['id'],)).fetchone()
            old = json.loads(row[0])
            value['cancel_requested'] = value.get('cancel_requested',False) or old.get('cancel_requested',False)
            value['close_requested'] = value.get('close_requested',False) or old.get('close_requested',False)
            value['close_generation']=max(value.get('close_generation',0),old.get('close_generation',0))
            db.execute('UPDATE plans SET state=?,value=? WHERE id=?',(value['state'],json.dumps(value,allow_nan=False),value['id']))
            db.commit()
        return value

    def approve(self, identity, session, *, automatic=False, now=None):
        now = self.clock() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT value FROM plans WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('委托不存在')
            value = json.loads(row[0])
            if value['state'] != 'preview':
                return value
            current = db.execute("SELECT value FROM meta WHERE key='session'").fetchone()
            actual = json.loads(current[0]) if current else {}
            if (not actual.get('enabled') or actual.get('id') != session.get('id')
                    or not actual.get('activated_ms',now+1) <= now < actual.get('expires_ms',0)):
                raise ValueError('实盘启用确认已过期或已暂停')
            if not value['created_ms'] <= now < value['expires_ms']:
                raise ValueError('委托预览已过期')
            if value['quote']['account_uid'] != actual['account_uid']:
                raise ValueError('预览与启用账户不一致')
            if automatic and value['spec'].get('follow_id') not in actual.get('follow_ids',[]):
                raise ValueError('该自动跟单规则未获启用确认')
            plans = [json.loads(r[0]) for r in db.execute("SELECT value FROM plans WHERE state!='preview'")]
            approved = [p for p in plans if p.get('session_id') == actual['id']]
            # Gross approved risk is consumed for the session, even after a win.
            spent = sum((Decimal(p['quote']['planned_risk']) for p in approved),Decimal(0))
            if len(approved) >= actual['max_plans'] or spent+Decimal(value['quote']['planned_risk']) > Decimal(actual['risk_budget_usdt']):
                raise ValueError('本次启用的委托数量或累计风险预算已用尽')
            value.update(state='queued',session_id=actual['id'],approved_ms=now,
                         authorization='owner_follow_session' if automatic else 'owner_order_confirmation')
            db.execute('UPDATE plans SET state=?,value=? WHERE id=?',('queued',json.dumps(value),identity));db.commit()
        return value

    def request(self, identity, action):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT value FROM plans WHERE id=?',(identity,)).fetchone()
            if not row:
                raise ValueError('委托不存在')
            value=json.loads(row[0])
            if value['state']=='preview':
                value['state']='canceled'
            else:
                value['cancel_requested']=True
                if action=='close':
                    value['close_requested']=True
                    value['close_generation']=value.get('close_generation',0)+1
            db.execute('UPDATE plans SET state=?,value=? WHERE id=?',(value['state'],json.dumps(value),identity));db.commit()
        return value

    def event(self, value):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?)',(value['id'],json.dumps(value,allow_nan=False)))
            db.commit()

    def events(self, limit=300):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT value FROM events ORDER BY rowid DESC LIMIT ?',(limit,))]

    def settings(self, value):
        """CAS configuration and revoke new-entry authority in one transaction."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
            old=json.loads(row[0]) if row else {'revision':0}
            if old['revision']!=value['revision']:raise ValueError('配置已在其他页面修改，请刷新')
            value={**value,'revision':value['revision']+1}
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('settings',json.dumps(value)))
            row=db.execute("SELECT value FROM meta WHERE key='session'").fetchone()
            if row:
                session=json.loads(row[0]);session.update(enabled=False,paused_reason='配置已修改，需重新核对启用')
                db.execute('UPDATE meta SET value=? WHERE key=?',(json.dumps(session),'session'))
            db.commit()
        return value

    def activate(self, identity, uid, now=None):
        now=self.clock() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM meta WHERE key='activation'").fetchone()
            draft=json.loads(row[0]) if row else {}
            previous=db.execute("SELECT value FROM meta WHERE key='session'").fetchone()
            previous=json.loads(previous[0]) if previous else {}
            if previous.get('id')==identity:
                if previous.get('account_uid')!=uid:raise ValueError('启用账户身份已变化')
                return previous
            row=db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
            settings=json.loads(row[0]) if row else {'revision':0}
            if draft.get('id')!=identity or not draft.get('created_ms',now+1)<=now<draft.get('preview_expires_ms',0):raise ValueError('启用核对已过期，请重新预览')
            if draft['revision']!=settings['revision'] or uid!=draft['account_uid']:raise ValueError('配置或账户已变化，请重新核对')
            session={**draft,'enabled':True,'activated_ms':now,'expires_ms':now+draft['hours']*3600000}
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('session',json.dumps(session)))
            db.execute("DELETE FROM meta WHERE key='activation'");db.commit()
        return session

    def pause(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT value FROM meta WHERE key='session'").fetchone()
            value=json.loads(row[0]) if row else {}
            value.update(enabled=False,paused_reason='Owner 暂停新开仓')
            db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('session',json.dumps(value)));db.commit()
        return value

    def claim_follow(self, identity, value):
        with self.db() as db:
            cursor=db.execute('INSERT OR IGNORE INTO follow_attempts VALUES(?,?)',(identity,json.dumps(value)))
            db.commit();return cursor.rowcount==1

    def finish_follow(self, identity, value):
        with self.db() as db:
            db.execute('UPDATE follow_attempts SET value=? WHERE id=?',(json.dumps(value),identity));db.commit()

    def follow_attempts(self):
        with self.db() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT value FROM follow_attempts ORDER BY rowid DESC LIMIT 50')]
