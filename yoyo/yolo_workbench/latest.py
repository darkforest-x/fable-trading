"""Latest observation per coin and period, independent of task completion order.

Read immutable result rows incrementally under a SQLite snapshot. Retain only
one small projection per pair; never unpack candle blobs or start inference.
Historical scans remain in their explicit job view and cannot replace live scans.
"""
from contextlib import closing
from threading import Lock

from .store import now


class Latest:
    def __init__(self, book):
        self.book = book
        self.cursor = 0
        self.items = {}
        self.lock = Lock()

    def read(self, if_revision=''):
        with self.lock, closing(self.book.connect()) as db:
            db.execute('BEGIN')
            high = db.execute('SELECT COALESCE(MAX(rowid),0) FROM results').fetchone()[0]
            if high > self.cursor:
                for row in db.execute("""SELECT r.rowid AS sequence,r.job_id,r.symbol,
                    j.created_ms,json_extract(j.spec,'$.timeframe') AS timeframe,
                    json_extract(j.spec,'$.model_id') AS model_id,
                    COALESCE(json_extract(r.payload,'$.cutoff_ms'),json_extract(j.spec,'$.end_ms'),0) AS cutoff_ms,
                    COALESCE(json_extract(r.payload,'$.observed_ms'),j.updated_ms) AS observed_ms,
                    json_extract(r.payload,'$.error') AS error,
                    json_extract(r.payload,'$.availability') AS availability,
                    COALESCE(json_array_length(r.payload,'$.episodes'),0) AS count,
                    COALESCE(json_array_length(r.payload,'$.raw_detections'),0) AS raw_count,
                    (SELECT MAX(json_extract(value,'$.detected_at_ms')) FROM json_each(r.payload,'$.episodes')) AS latest_detection_ms,
                    (SELECT MAX(json_extract(value,'$.confidence')) FROM json_each(r.payload,'$.episodes')) AS max_confidence
                    FROM results r JOIN jobs j ON j.id=r.job_id
                    WHERE r.rowid>? AND r.rowid<=? AND json_extract(j.spec,'$.mode')='latest'
                    ORDER BY r.rowid""", (self.cursor, high)):
                    item = dict(row)
                    key = (item['symbol'], item['timeframe'])
                    old = self.items.get(key)
                    rank = lambda r: (r['cutoff_ms'], r['created_ms'], r['sequence'])
                    if old is None or rank(item) > rank(old):
                        self.items[key] = item
                self.cursor = high
            revision = str(self.cursor)
            partial = if_revision.isdigit() and 0 < int(if_revision) <= self.cursor
            return dict(revision=revision, unchanged=if_revision == revision, now_ms=now(),
                        **({} if if_revision == revision else dict(partial=partial,items=[
                            {k: v for k, v in item.items() if k != 'sequence'}
                            for _, item in sorted(self.items.items())
                            if not partial or item['sequence'] > int(if_revision)])))
