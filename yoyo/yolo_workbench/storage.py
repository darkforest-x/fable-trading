"""Lossless, content-addressed chart storage inside the observation book.

OHLC blocks are shared across observations; per-observation MA values retain
their exact floating-point representation and original warmup provenance.
This is not the canonical market cache and never modifies a saved chart.
"""
import hashlib
import json
import zlib
from .models import TIMEFRAMES


def blob(db, value):
    raw=json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'),sort_keys=True).encode()
    key=hashlib.sha256(raw).hexdigest()
    db.execute('INSERT OR IGNORE INTO chart_blobs VALUES (?,?)',(key,zlib.compress(raw,6)))
    return key


def read_blob(db,key):
    row=db.execute('SELECT payload FROM chart_blobs WHERE id=?',(key,)).fetchone()
    if row is None:raise ValueError('图表存储片段缺失')
    return json.loads(zlib.decompress(row[0]))


def pack(db,payload):
    rows=payload.get('candles',[])
    if not rows or not isinstance(rows[0],dict) or payload.get('timeframe') not in TIMEFRAMES:return payload
    blocks=[];current=[];bucket=None;mas=[];step=TIMEFRAMES[payload['timeframe']]
    for row in rows:
        group=row['t']//(32*step)
        if current and group!=bucket:blocks.append(blob(db,current));current=[]
        bucket=group
        current.append({k:v for k,v in row.items() if not k.startswith(('sma','ema'))})
        mas.append({k:v for k,v in row.items() if k.startswith(('sma','ema'))})
    if current:blocks.append(blob(db,current))
    return {**{k:v for k,v in payload.items() if k!='candles'},'chart_ref':dict(blocks=blocks,mas=blob(db,mas))}


def unpack(db,payload):
    ref=payload.get('chart_ref')
    if ref is None:return payload
    rows=[row for key in ref['blocks'] for row in read_blob(db,key)]
    mas=read_blob(db,ref['mas'])
    if len(rows)!=len(mas):raise ValueError('图表均线片段长度不一致')
    return {**{k:v for k,v in payload.items() if k!='chart_ref'},'candles':[dict(row,**ma) for row,ma in zip(rows,mas)]}
