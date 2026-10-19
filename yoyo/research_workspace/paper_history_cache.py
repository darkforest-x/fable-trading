"""Bounded read projections; ledger math stays in paper_desk_analytics.

SQLite triggers version committed account changes across processes, excluding
quotes and journals. Equity buckets append incrementally; corrections, deletions
and backfills invalidate the projection. No synthetic points or P&L are created.
"""
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
import threading

_LOCK = threading.RLock()
_CACHES = OrderedDict()


def install(db):
    db.execute('CREATE TABLE IF NOT EXISTS analytics_versions (account_id TEXT PRIMARY KEY, ledger INTEGER NOT NULL DEFAULT 0, equity INTEGER NOT NULL DEFAULT 0, reset INTEGER NOT NULL DEFAULT 0)')
    for table in ('accounts', 'positions', 'fills', 'equity_samples'):
        for event in ('INSERT', 'UPDATE', 'DELETE'):
            refs = ['NEW'] if event == 'INSERT' else ['OLD'] if event == 'DELETE' else ['OLD', 'NEW']
            statements = []
            for ref in refs:
                key = f'{ref}.id' if table == 'accounts' else f'{ref}.account_id'
                field = 'equity' if table == 'equity_samples' else 'ledger'
                reset = '1' if event != 'INSERT' else f"EXISTS(SELECT 1 FROM equity_samples WHERE account_id={key} AND bucket_ms>{ref}.bucket_ms)"
                tail = f',reset=reset+({reset})' if field == 'equity' else ''
                statements.append(f'INSERT OR IGNORE INTO analytics_versions(account_id) VALUES({key}); UPDATE analytics_versions SET {field}={field}+1{tail} WHERE account_id={key};')
            db.execute(f'CREATE TRIGGER IF NOT EXISTS analytics_{table}_{event.lower()} AFTER {event} ON {table} BEGIN '+''.join(statements)+' END')


class Equity:
    """Mergeable extrema bins, bounded to 1,200 original observations.

    Each bin retains first/last, min/max and one missing observation. As history
    grows, adjacent bins merge without averaging prices or bridging missing data.
    """
    def __init__(self):
        self.bins = []
        self.width = 1
        self.count = 0
        self.through = -1
        self.first = None

    @staticmethod
    def reduce(rows):
        valid = [p for p in rows if p['equity'] is not None]
        selected = [rows[0], rows[-1]]
        if valid:
            selected += [min(valid, key=lambda p: Decimal(p['equity'])), max(valid, key=lambda p: Decimal(p['equity']))]
        missing = next((p for p in rows if p['equity'] is None), None)
        if missing is not None:
            selected.append(missing)
        return sorted({p['bucket_ms']: p for p in selected}.values(), key=lambda p: p['bucket_ms'])

    def append(self, row):
        point = dict(row)
        if self.first is None:
            self.first = point['observed_ms']
        if self.count % self.width == 0:
            self.bins.append([point])
        else:
            self.bins[-1] = self.reduce(self.bins[-1] + [point])
        self.count += 1
        self.through = point['bucket_ms']
        if len(self.bins) > 240:
            self.bins = [self.reduce(sum(self.bins[i:i+2], [])) for i in range(0, len(self.bins), 2)]
            self.width *= 2

    def rows(self):
        return [{k: p[k] for k in ('observed_ms', 'equity', 'balance')} for block in self.bins for p in block]


def read(desk, account_id, build_ledger):
    """One snapshot for versions and data; concurrent readers share projections."""
    path = Path(desk.path).resolve()
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, account_id)
    with _LOCK, desk.connect() as db:
        db.execute('BEGIN')
        account = desk.row(db, 'accounts', account_id)
        version = db.execute('SELECT ledger,equity,reset FROM analytics_versions WHERE account_id=?', (account_id,)).fetchone()
        ledger, equity, reset = tuple(version) if version else (0, 0, 0)
        item = _CACHES.get(key, {})
        if item.get('ledger_version') != ledger:
            fills = [dict(r) for r in db.execute('SELECT * FROM fills WHERE account_id=? ORDER BY created_ms,rowid', (account_id,))]
            positions = [dict(r) for r in db.execute('SELECT id,remaining,opened_ms,closed_ms FROM positions WHERE account_id=?', (account_id,))]
            item['ledger'] = build_ledger(account, positions, fills)
            item['ledger_version'] = ledger
        if item.get('reset') != reset:
            item['samples'] = Equity()
        if item.get('equity_version') != equity:
            samples = item['samples']
            for row in db.execute('SELECT bucket_ms,observed_ms,equity,balance FROM equity_samples WHERE account_id=? AND bucket_ms>? ORDER BY bucket_ms', (account_id, samples.through)):
                samples.append(row)
            item.update(equity_version=equity, reset=reset)
        _CACHES[key] = item
        _CACHES.move_to_end(key)
        while len(_CACHES) > 8:
            _CACHES.popitem(last=False)
        return item['ledger'], item['samples'].rows(), item['samples'].first
