"""Private multi-account registry; selection never changes a ledger's identity.

The request chooses an immutable account ID. Each OKX UID has exactly one ledger
and worker; switching views never stops protection or resets cooldown/counters.
Credentials are verified with GET only and stored atomically outside the repo.
"""
import hashlib
import json
import re
import stat
import threading
import time
from pathlib import Path
from .live_book import LiveBook
from .okx_demo import Credentials, DemoError, LiveReadOnlyProbe, save_private
from .okx_execution import OkxExecutionClient
from .policy import Rejected


class Accounts:
    mode = 'okx_live'

    def __init__(self, primary, runtime, client_factory=None, probe_factory=LiveReadOnlyProbe):
        self.primary = primary
        self.client = primary.client
        self.runtime = Path(runtime)
        self.registry = self.runtime / 'accounts.json'
        self.lock = threading.RLock()
        self.enroll_lock = threading.Lock()
        self.running = False
        self.last_add = None
        self.probe_factory = probe_factory
        self.client_factory = client_factory or (lambda creds: OkxExecutionClient(creds,
            environment='okx_live', write_enabled=self.client.write_enabled))
        self.books = {'primary': primary}
        self.records = [{'id':'primary', 'name':'主账户'}]
        if self.registry.exists():
            saved = self._read(self.registry)
            if not isinstance(saved, list) or not saved or saved[0]['id'] != 'primary':
                raise ValueError('账户登记格式无效')
            self.records = saved
            for item in saved[1:]:
                identity = item['id']
                if not re.fullmatch(r'a[0-9a-f]{16}', identity):
                    raise ValueError('账户登记标识无效')
                path = self.runtime / 'accounts' / identity
                raw = self._read(path / 'credentials.json')
                self.books[identity] = LiveBook(path / 'live.sqlite3',
                    self.client_factory(Credentials(raw['key'],raw['secret'],raw['passphrase'])))

    @staticmethod
    def _read(path):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('账户文件权限异常')
        return json.loads(path.read_text())

    def resolve(self, identity=''):
        with self.lock:
            identity = identity or 'primary'
            if identity not in self.books:
                raise Rejected('交易账户不存在，请重新选择')
            return self.books[identity]

    def list(self):
        with self.lock:
            selected = [(dict(record), self.books[record['id']]) for record in self.records]
        items = []
        for record, book in selected:
            state = book.snapshot()
            items.append(dict(id=record['id'], name=record['name'], environment='okx_live',
                connected=not state.get('private_error') and 0 <= book.clock()-state.get('private_at', 0) <= 15,
                positions=len(state['positions']), pending=len(state['pending']), blockers=state['blockers']))
        return dict(items=items, default_account_id='primary')

    def add(self, name, key, secret, passphrase):
        # Enrollment IO must never block quote fanout to existing protection workers.
        with self.enroll_lock:
            if self.last_add is not None and time.monotonic()-self.last_add < 3:
                raise Rejected('请稍后再验证账户')
            self.last_add = time.monotonic()
            if len(self.books) >= 8:
                raise Rejected('最多保存 8 个交易账户')
            name = name.strip()
            if not name or len(name)>30 or any(ord(c)<32 for c in name):
                raise Rejected('请输入 1 至 30 字的账户名称')
            try:
                credentials = Credentials(key.strip(), secret.strip(), passphrase)
                probe = self.probe_factory(credentials)
                try:
                    result = probe.inspect()
                finally:
                    probe.close()
            except DemoError as exc:
                raise Rejected(str(exc)) from None
            if result.get('account_mode') != '2' or result.get('position_mode') != 'long_short_mode':
                raise Rejected('账户需为合约账户、双向持仓模式；请先在 OKX 设置')
            reference = result['account_reference']
            if not re.fullmatch(r'[0-9a-f]{16}', reference):
                raise Rejected('未取得有效账户身份')
            for book in self.books.values():
                with book.transaction() as (_, state):
                    existing = state.get('account_reference')
                if existing is None:
                    config = book.client.get('/api/v5/account/config')[0]
                    existing = hashlib.sha256(config['uid'].encode()).hexdigest()[:16]
                if reference == existing:
                    raise Rejected('这个 OKX 账户已经添加，请直接切换；不能重复添加以重置风控')
            identity = 'a'+reference
            directory = self.runtime / 'accounts' / identity
            if directory.is_symlink():
                raise Rejected('账户目录异常')
            # A prior interrupted save reuses the same identity and ledger.
            save_private(directory / 'credentials.json', dict(key=credentials.key,
                secret=credentials.secret, passphrase=credentials.passphrase))
            book = LiveBook(directory / 'live.sqlite3', self.client_factory(credentials))
            with book.transaction() as (_,state):
                if state.get('account_reference') not in (None,reference):
                    raise Rejected('账户身份不匹配')
                state['account_reference'] = reference
            source = self.primary.snapshot()
            book.update_market(source['instruments'],source['quotes'])
            records = self.records + [dict(id=identity,name=name)]
            save_private(self.registry, records)
            with self.lock:
                self.records, self.books[identity] = records, book
                if self.running:
                    book.start()
            return dict(id=identity,name=name,environment='okx_live')

    def update_market(self, instruments, quotes):
        with self.lock:
            books = list(self.books.values())
        for book in books:
            book.update_market(instruments,quotes)

    def market_failed(self, message):
        with self.lock:
            books = list(self.books.values())
        for book in books:
            book.market_failed(message)

    def start(self):
        with self.lock:
            self.running = True
            for book in self.books.values():
                book.start()

    def stop(self):
        with self.lock:
            self.running = False
            books = list(self.books.values())
        for book in books:
            book.stop()
