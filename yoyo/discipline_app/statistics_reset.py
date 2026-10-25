"""Owner-requested display reset, preserving the complete live execution ledger.

Only the statistics and completed-record projections use this epoch. Discipline,
settlement, receipts and protection continue to read the original state. Reset is
a local operation, allowed only with no managed position or outstanding intent.
"""
import json
import sqlite3
import time
from pathlib import Path
from uuid import UUID


def history_epoch(state):
    epoch = state.get('statistics_history_epoch')
    if not epoch or epoch.get('account_reference') != state.get('account_reference'):
        return {}
    return epoch


def display_state(state):
    epoch = history_epoch(state)
    if not epoch:
        return state
    hidden = set(epoch['closed_ids'])
    return dict(state, closed=[row for row in state['closed']
        if row['id'] not in hidden and row['closed'] >= epoch['started_at']])


def reset_history(path, request_id, source, now=None):
    """Archive SQLite and set one display epoch atomically under the writer lock.

    No exchange client is constructed. An explicit request UUID makes a retry
    idempotent; credentials and fixed capital are never modified or returned.
    """
    identity = str(UUID(request_id))
    if not source.strip():
        raise ValueError('Reset provenance is required')
    path = Path(path).resolve()
    stamp = time.time() if now is None else now
    db = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=15)
    try:
        db.execute('BEGIN IMMEDIATE')
        state = json.loads(db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
        old = state.get('statistics_history_epoch', {})
        if old.get('id') == identity:
            return dict(id=identity, started_at=old['started_at'], archived_count=len(old['closed_ids']))
        if state.get('mode') != 'okx_live' or not state.get('account_reference'):
            raise ValueError('An identified live ledger is required')
        if state['positions'] or state['pending'] or any(intent.get('phase') not in
                ('closed', 'cancelled', 'rejected') for intent in state.get('intents', {}).values()):
            raise ValueError('Cannot reset with managed positions or outstanding intents')
        if db.execute("SELECT 1 FROM exchange_commands WHERE phase NOT IN ('acknowledged','rejected') LIMIT 1").fetchone():
            raise ValueError('Cannot reset with outstanding exchange commands')
        directory = path.parent/'statistics-archives'
        directory.mkdir(mode=0o700, exist_ok=True)
        archive = directory/f'{path.stem}-{identity}.sqlite3'
        if archive.exists():
            raise ValueError('Archive already exists; inspect the prior attempt before retrying')
        # The reserved writer lock freezes the committed source revision while
        # a separate WAL reader copies it, including journal/receipt tables.
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as reader:
            with sqlite3.connect(archive) as backup:
                reader.backup(backup)
        archive.chmod(0o600)
        epoch = dict(id=identity, started_at=stamp, closed_ids=[r['id'] for r in state['closed']],
                     account_reference=state['account_reference'], source=source)
        state['statistics_history_epoch'] = epoch
        state['version'] += 1
        db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
        receipt = dict(id=identity, started_at=stamp, archived_count=len(epoch['closed_ids']), archive=str(archive))
        db.execute('INSERT INTO journal(time,kind,body) VALUES(?,?,?)',
                   (stamp, 'statistics_history_reset', json.dumps(receipt)))
        db.commit()
        return receipt
    finally:
        db.close()
