"""Editable trade reasoning tied to a position's durable lifecycle identity.

Journal reads do not need market data or create rows. Optimistic versions stop
two browser tabs from silently overwriting each other's analysis; an identical
retry after a lost save response is safe. Financial records remain immutable.
"""
from .paper_desk import clock_ms


FIELDS = ('entry_reason', 'analysis', 'plan', 'review')


class PositionJournal:
    def __init__(self, desk):
        self.desk = desk

    def read(self, db, position_id):
        position = self.desk.row(db, 'positions', position_id)
        account = self.desk.row(db, 'accounts', position['account_id'])
        row = db.execute('SELECT * FROM position_journals WHERE position_id=?', (position_id,)).fetchone()
        journal = dict(row) if row else dict(
            position_id=position_id, entry_reason=position['note'], analysis='', plan='', review='',
            version=0, created_ms=None, updated_ms=None)
        from .paper_desk_images import list_images
        return dict(position=position, account_name=account['name'], journal=journal, images=list_images(db, position_id))

    def get(self, position_id):
        with self.desk.connect() as db:
            db.execute('BEGIN')
            return self.read(db, position_id)

    def save(self, position_id, version, content):
        if type(version) is not int or version < 0:
            raise ValueError('日志版本无效，请重新打开')
        if set(content) != set(FIELDS) or any(not isinstance(content[f], str) or len(content[f]) > 10000 for f in FIELDS):
            raise ValueError('日志字段无效，每项最多 10000 字')
        with self.desk.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = self.read(db, position_id)
            old = current['journal']
            if version != old['version']:
                if version + 1 == old['version'] and all(content[f] == old[f] for f in FIELDS):
                    return current
                raise ValueError('这笔日志已在其他页面更新。你的草稿仍保留，请查看最新版本后再保存。')
            at = clock_ms()
            values = dict(content, position_id=position_id, version=version+1,
                          created_ms=old['created_ms'] if old['created_ms'] is not None else at, updated_ms=at)
            db.execute('INSERT INTO position_journals '
                       '(position_id,entry_reason,analysis,plan,review,version,created_ms,updated_ms) '
                       'VALUES(:position_id,:entry_reason,:analysis,:plan,:review,:version,:created_ms,:updated_ms) '
                       'ON CONFLICT(position_id) DO UPDATE SET entry_reason=excluded.entry_reason, '
                       'analysis=excluded.analysis,plan=excluded.plan,review=excluded.review, '
                       'version=excluded.version,updated_ms=excluded.updated_ms', values)
            return self.read(db, position_id)
