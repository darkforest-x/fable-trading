"""Read-only patrol feed over immutable result rows, independent of inference.

Project only episode metadata, incrementally by SQLite rowid. A service restart
rebuilds this cache from the observation book; it never creates observations.
Merge overlapping cores only within the same symbol, period, side and frozen
input contract. First observation orders discoveries; repeat scans do not bump
an old shape to the top. The latest observation supplies the exact chart link.
"""
from contextlib import closing
import json
import re
from threading import Lock

from .store import encode, now


def merge_findings(rows):
    groups = {}
    for row in rows:
        key = (row['symbol'], row['timeframe'], row['side'], row['contract'])
        groups.setdefault(key, []).append(row)
    items = []
    for group in groups.values():
        cluster = []
        end = -1
        for row in sorted(group, key=lambda r: (r['core_start_ms'], r['core_end_ms'])):
            if cluster and row['core_start_ms'] > end:
                items.append(summarize(cluster))
                cluster = []
            cluster.append(row)
            end = max(r['core_end_ms'] for r in cluster)
        if cluster:
            items.append(summarize(cluster))
    return sorted(items, key=lambda r: (r['first_observed_ms'], r['id']), reverse=True)


def summarize(rows):
    first = min(rows, key=lambda r: (r['observed_ms'], r['job_id'], r['detection_id']))
    latest = max(rows, key=lambda r: (r['observed_ms'], r['detected_at_ms'], r['confidence'], r['detection_id']))
    return dict({k: v for k, v in latest.items() if k not in ('contract', 'observed_ms')},
                id=first['job_id']+':'+first['detection_id'],
                first_observed_ms=first['observed_ms'], last_observed_ms=latest['observed_ms'],
                first_confirmed_ms=min(r['first_confirmed_ms'] for r in rows),
                scan_count=len({r['job_id'] for r in rows}))


class Feed:
    def __init__(self, book):
        self.book = book
        self.cursor = 0
        self.rows = []
        self.items = []
        self.lock = Lock()

    def read(self, *, days=7, query='', timeframe='', side='', limit=30, sort='newest', offset=0, before_ms=None):
        """Page discoveries; full OKX contracts match exactly, abbreviations by substring."""
        with self.lock, closing(self.book.connect()) as db:
            # A snapshot prevents the high-water mark from skipping a concurrent insert.
            db.execute('BEGIN')
            high = db.execute('SELECT COALESCE(MAX(rowid),0) FROM results').fetchone()[0]
            if high > self.cursor:
                added = []
                for row in db.execute("""SELECT j.id,j.schedule_id,j.spec,r.symbol,
                    COALESCE(json_extract(r.payload,'$.observed_ms'),j.updated_ms) observed_ms,
                    e.value episode FROM results r JOIN jobs j ON j.id=r.job_id
                    JOIN json_each(r.payload,'$.episodes') e
                    WHERE r.rowid>? AND r.rowid<=? AND j.schedule_id IS NOT NULL
                    AND json_extract(j.spec,'$.mode')='latest'
                    AND json_extract(r.payload,'$.error') IS NULL""", (self.cursor, high)):
                    spec, box = json.loads(row['spec']), json.loads(row['episode'])
                    contract = encode({k: spec.get(k) for k in ('model_id', 'model_sha256', 'renderer_sha256', 'parameters', 'adapter_version')})
                    added.append(dict(symbol=row['symbol'], timeframe=spec['timeframe'],
                        model_id=spec['model_id'], contract=contract, job_id=row['id'],
                        schedule_id=row['schedule_id'], detection_id=box['id'],
                        observed_ms=row['observed_ms'], first_confirmed_ms=box.get('first_detected_at_ms', box['detected_at_ms']),
                        **{k: box[k] for k in ('side', 'confidence', 'core_start_ms', 'core_end_ms', 'core_bars', 'post_bars', 'detected_at_ms')}))
                self.rows.extend(added)
                self.cursor = high
                if added:
                    self.items = merge_findings(self.rows)
            stamp = now()
            cutoff = min(stamp, before_ms) if before_ms is not None else stamp
            symbol_query = query.strip().upper()
            exact_symbol = bool(re.fullmatch(r"[A-Z0-9]{1,32}-USDT-SWAP", symbol_query))
            # Repeat scans may change score or bridge clusters. Freeze raw observations
            # before merging so later pages keep their original membership and order.
            items = merge_findings([r for r in self.rows if r['observed_ms'] <= cutoff]) if before_ms is not None else self.items
            matched = [r for r in items if (not days or r['first_observed_ms'] >= cutoff-days*86400000)
                       and r['first_observed_ms'] <= cutoff
                       and (not symbol_query or (r['symbol'] == symbol_query if exact_symbol else symbol_query in r['symbol']))
                       and (not timeframe or r['timeframe'] == timeframe)
                       and (not side or r['side'] == side)]
            if sort == 'score':
                matched.sort(key=lambda r: (-r['confidence'], -r['first_observed_ms'], r['symbol']))
            elif sort == 'symbol':
                matched.sort(key=lambda r: (r['symbol'], -r['first_observed_ms']))
            latest = db.execute("""SELECT * FROM jobs WHERE schedule_id IS NOT NULL
                AND json_extract(spec,'$.mode')='latest' ORDER BY created_ms DESC,id DESC LIMIT 1""").fetchone()
            job = self.book.job(latest)
            return dict(items=matched[offset:offset+limit], total=len(matched), now_ms=stamp, offset=offset, limit=limit, before_ms=cutoff,
                        latest_job={k: job[k] for k in ('id','status','created_ms','updated_ms','progress','error')} if job else None)
