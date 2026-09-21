"""Send the explicitly requested 50 review images to the configured owner TG.

Uses the existing credential loader without logging credentials. Telegram Bot
API sendMediaGroup accepts 2-10 media and returns each Message:
https://core.telegram.org/bots/api#sendmediagroup . Persist a pending action
before sending; a timeout or uncertain response blocks automatic repetition.
This manual delivery never enables the disabled production Telegram worker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import pandas as pd
import requests
from PIL import Image

from yoyo.notify import _load
from yoyo.evaluation.spike_10r_top50_charts import OUT
from yoyo.evaluation.spike_10r_search import digest


def save(path, value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2)
        handle.flush();os.fsync(handle.fileno())
    temporary.replace(path)


def deliver(send=False):
    manifest_path=OUT/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    rows=manifest['images']
    if len(rows)!=50 or [r['rank'] for r in rows]!=list(range(1,51)) or len({r['event_key'] for r in rows})!=50:
        raise ValueError('expected the unique ranked 50-image gallery')
    for row in rows:
        path=Path(row['path'])
        if path.parent!=OUT or digest(path)!=row['sha256']:raise ValueError('image identity differs')
        with Image.open(path) as img:
            if img.size!=(1080,1620):raise ValueError('unexpected dimensions')
    if not send:
        print(json.dumps({'validated':50,'albums':5,'total_bytes':sum(Path(r['path']).stat().st_size for r in rows)}));return
    credentials=_load()
    if credentials is None:raise ValueError('configured TG credentials missing')
    token,chat=credentials
    url='https://api.telegram.org/bot'+token+'/'
    try:
        info=requests.post(url+'getChat',json={'chat_id':chat},timeout=(10,25)).json()
    except Exception as exc:
        raise RuntimeError('getChat failed: '+type(exc).__name__) from None
    if not info.get('ok'):raise ValueError('getChat rejected')
    target=info['result']
    if target.get('type') not in ('channel','supergroup','group') or target.get('title')!='Yolo均线密集交易系统':
        raise ValueError('configured destination differs from verified owner group')
    receipt_path=OUT/'telegram_receipt.json'
    identity=digest(manifest_path)
    receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {
        'manifest_sha256':identity,'target_title':target['title'],'target_type':target['type'],
        'target_id':target['id'],'owner_request':'send top 50 of 448 winner review charts',
        'batches':[],'complete':False}
    if receipt['manifest_sha256']!=identity or receipt['target_id']!=target['id']:
        raise ValueError('delivery identity changed')
    if any(b['status'] not in ('sent','rejected') for b in receipt['batches']):
        raise ValueError('uncertain previous upload: inspect receipts before any resend')
    for batch in range(5):
        previous=next((b for b in receipt['batches'] if b['batch']==batch+1),None)
        if previous and previous['status']=='sent':continue
        selected=rows[batch*10:(batch+1)*10]
        files={};media=[]
        for row in selected:
            key=f'image{row["rank"]}'
            path=Path(row['path'])
            files[key]=(path.name,path.read_bytes(),'image/png')
            t=pd.Timestamp(row['entry_time']).tz_convert('Asia/Shanghai')
            caption=(f'{row["rank"]:02d}/50 · {row["asset"]} · {row["venue"].upper()} · {row["timeframe_min"]}m\n'
                     f'入场 {t:%Y-%m-%d %H:%M} 北京时间 · 最终净 {row["net_r"]:.2f}R\n'
                     '历史复盘：448笔净>10R中按最终净R降序取前50。保留原交易所/周期记录。\n'
                     'K线＋六均线＋原始入场/止损＋实际退出；不含手动涨幅测量框。')
            media.append({'type':'photo','media':'attach://'+key,'caption':caption})
        action={'batch':batch+1,'ranks':[r['rank'] for r in selected],'status':'pending'}
        if previous:receipt['batches'].remove(previous)
        receipt['batches'].append(action)
        save(receipt_path,receipt)
        for attempt in range(4):
            try:
                response=requests.post(url+'sendMediaGroup',data={'chat_id':chat,'media':json.dumps(media,ensure_ascii=False),'disable_notification':'true'},files=files,timeout=(10,60))
                payload=response.json()
            except Exception as exc:
                action.update(status='unknown',error_type=type(exc).__name__);save(receipt_path,receipt)
                raise RuntimeError('upload response uncertain; no automatic resend') from None
            if payload.get('ok'):break
            if payload.get('error_code')==429 and attempt<3:
                time.sleep(min(60,max(1,int(payload.get('parameters',{}).get('retry_after',30)))));continue
            action.update(status='rejected',error_code=payload.get('error_code'));save(receipt_path,receipt)
            raise RuntimeError('Telegram upload rejected; error code '+str(payload.get('error_code')))
        messages=payload['result']
        if len(messages)!=10 or any(m['chat']['id']!=target['id'] for m in messages):
            action.update(status='unknown');save(receipt_path,receipt)
            raise ValueError('unexpected upload receipt; no resend')
        action.update(status='sent',message_ids=[m['message_id'] for m in messages],
                      media_group_id=messages[0].get('media_group_id'),sent_at=pd.Timestamp.now(tz='UTC').isoformat())
        save(receipt_path,receipt)
        print(f'sent {(batch+1)*10}/50; batch {batch+1}; message IDs {action["message_ids"]}',flush=True)
        if batch<4:time.sleep(5)
    receipt['complete']=True;receipt['sent_images']=50;save(receipt_path,receipt)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--send',action='store_true')
    deliver(parser.parse_args().send)
