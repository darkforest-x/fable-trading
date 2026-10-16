"""Transactional observation jobs and schedules, separate from trading books."""
from __future__ import annotations
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
import uuid


def now():return time.time_ns()//1_000_000

def encode(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


class Book:
    def __init__(self,runtime):
        self.runtime=Path(runtime)/'yolo_workbench';self.runtime.mkdir(parents=True,exist_ok=True)
        self.path=self.runtime/'observations.sqlite3'
        with closing(self.connect()) as db,db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, request_key TEXT UNIQUE, status TEXT NOT NULL,
              created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL, spec TEXT NOT NULL,
              progress TEXT NOT NULL, error TEXT, cancel INTEGER NOT NULL DEFAULT 0, schedule_id TEXT);
            CREATE TABLE IF NOT EXISTS results(job_id TEXT NOT NULL,symbol TEXT NOT NULL,payload TEXT NOT NULL,
              PRIMARY KEY(job_id,symbol));
            CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chart_blobs(id TEXT PRIMARY KEY,payload BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS input_cache(symbol TEXT,timeframe TEXT,payload BLOB NOT NULL,
              PRIMARY KEY(symbol,timeframe));
            CREATE TABLE IF NOT EXISTS patrol_cursors(schedule_id TEXT,symbol TEXT,through_ms INTEGER NOT NULL,
              PRIMARY KEY(schedule_id,symbol));
            CREATE TABLE IF NOT EXISTS patrol_units(job_id TEXT,symbol TEXT,status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,retry_ms INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(job_id,symbol));
            ''')

    def connect(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;return db

    @staticmethod
    def job(row):
        if row is None:return None
        d=dict(row);d['spec']=json.loads(d['spec']);d['progress']=json.loads(d['progress']);return d

    def submit(self,spec,key,schedule_id=None):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT * FROM jobs WHERE request_key=?',(key,)).fetchone()
            if previous:
                # A retry cannot silently attach to a different selection.
                old=json.loads(previous['spec'])
                if any(old.get(k)!=v for k,v in spec.items() if not (spec.get('mode')=='latest' and k in ('start_ms','end_ms'))):
                    raise ValueError('重试请求与原参数不一致')
                return self.job(previous)
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]>=12:
                raise ValueError('已有 12 个待处理任务，请等待或取消后再提交')
            identity=uuid.uuid4().hex;stamp=now()
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (identity,key,'queued',stamp,stamp,encode(spec),encode(dict(completed=0,total=0)),None,0,schedule_id))
            return self.job(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())

    def get(self,identity):
        with closing(self.connect()) as db:return self.job(db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone())

    def jobs(self,limit=30,offset=0):
        with closing(self.connect()) as db:return [self.job(r) for r in db.execute('SELECT * FROM jobs ORDER BY created_ms DESC LIMIT ? OFFSET ?',(limit,offset))]

    def job_count(self):
        with closing(self.connect()) as db:return db.execute('SELECT count(*) FROM jobs').fetchone()[0]

    def claim(self):
        with closing(self.connect()) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM jobs WHERE status='queued' AND COALESCE(json_extract(spec,'$.incremental'),0)=0 ORDER BY created_ms LIMIT 1").fetchone()
            if not row:return None
            db.execute("UPDATE jobs SET status='running',updated_ms=? WHERE id=?",(now(),row['id']))
            return self.job(db.execute('SELECT * FROM jobs WHERE id=?',(row['id'],)).fetchone())

    def update(self,identity,**fields):
        allowed={'status','spec','progress','error'}
        if not set(fields)<=allowed:raise ValueError('invalid update')
        fields={k:encode(v) if k in ('spec','progress') else v for k,v in fields.items()}
        fields['updated_ms']=now()
        with closing(self.connect()) as db,db:
            db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),identity))

    def cancel(self,identity):
        with closing(self.connect()) as db,db:
            db.execute("UPDATE jobs SET cancel=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_ms=? WHERE id=? AND status IN ('queued','running')",(now(),identity))
        return self.get(identity)

    def cancelled(self,identity):
        with closing(self.connect()) as db:
            row=db.execute('SELECT cancel FROM jobs WHERE id=?',(identity,)).fetchone();return not row or bool(row[0])

    def result(self,identity,symbol,payload=None):
        with closing(self.connect()) as db,db:
            if payload is not None:
                from .storage import pack
                db.execute('INSERT INTO results VALUES (?,?,?)',(identity,symbol,encode(pack(db,payload))));return payload
            row=db.execute('SELECT payload FROM results WHERE job_id=? AND symbol=?',(identity,symbol)).fetchone()
            from .storage import unpack
            return unpack(db,json.loads(row[0])) if row else None

    def input_cache(self,symbol,timeframe,rows=None):
        """Bounded research input checkpoint; independent of canonical OHLC."""
        import zlib
        with closing(self.connect()) as db,db:
            if rows is not None:
                db.execute('BEGIN IMMEDIATE')
                # A bounded prewarmer must not replace a newer live prefix.
                current=db.execute('SELECT payload FROM input_cache WHERE symbol=? AND timeframe=?',(symbol,timeframe)).fetchone()
                if current:
                    old=json.loads(zlib.decompress(current[0]))
                    if old and rows and old[-1]['t']>rows[-1]['t']:return rows
                db.execute('INSERT OR REPLACE INTO input_cache VALUES (?,?,?)',
                           (symbol,timeframe,zlib.compress(encode(rows[-2000:]).encode(),3)))
                return rows
            row=db.execute('SELECT payload FROM input_cache WHERE symbol=? AND timeframe=?',(symbol,timeframe)).fetchone()
            return json.loads(zlib.decompress(row[0])) if row else []

    def summaries(self,identity):
        with closing(self.connect()) as db:
            # JSON projection avoids loading every full chart into a page response.
            rows=db.execute("""SELECT symbol,json_extract(payload,'$.error') AS error,
                json_extract(payload,'$.availability') AS availability,
                json_array_length(payload,'$.episodes') AS count,
                json_array_length(payload,'$.raw_detections') AS raw_count,
                json_extract(payload,'$.cutoff_ms') AS cutoff_ms,
                (SELECT MAX(json_extract(value,'$.detected_at_ms')) FROM json_each(payload,'$.episodes')) AS latest_detection_ms,
                (SELECT MAX(json_extract(value,'$.confidence')) FROM json_each(payload,'$.episodes')) AS max_confidence,
                json_extract(payload,'$.windows_scored') AS windows_scored FROM results WHERE job_id=? ORDER BY symbol""",(identity,))
            return [dict(r) for r in rows]

    def meta(self,key,value=None):
        with closing(self.connect()) as db,db:
            if value is not None:db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,encode(value)));return value
            row=db.execute('SELECT payload FROM meta WHERE key=?',(key,)).fetchone();return json.loads(row[0]) if row else None

    def schedules(self):
        with closing(self.connect()) as db:return [json.loads(r[0]) for r in db.execute('SELECT payload FROM schedules ORDER BY id')]

    def schedule(self,payload):
        with closing(self.connect()) as db,db:db.execute('INSERT OR REPLACE INTO schedules VALUES (?,?)',(payload['id'],encode(payload)))
        return payload

    def remove_schedule(self,identity):
        with closing(self.connect()) as db,db:db.execute('DELETE FROM schedules WHERE id=?',(identity,))

    def recover(self):
        with closing(self.connect()) as db,db:
            db.execute("UPDATE jobs SET status='interrupted',error='独立检测进程中断；已完成结果保留，可重新提交',updated_ms=? WHERE status='running' AND COALESCE(json_extract(spec,'$.incremental'),0)=0",(now(),))
            db.execute("UPDATE jobs SET status='queued',updated_ms=? WHERE status='running' AND json_extract(spec,'$.incremental')=1",(now(),))
