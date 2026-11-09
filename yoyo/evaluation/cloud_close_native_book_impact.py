"""Replay three original entry sizes against the actual native OKX L2 book near the 12H close."""
import gzip
import json
import tarfile

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_entry_turnover as audit

EXP = audit.EXP.parent/'exp-cloud-close-native-book-impact-20261109-v1'
API = 'https://www.okx.com/api/v5/public/market-data-history'
DAY = 86400000


def native_messages(symbol,stamp):
    inst = symbol+'-USDT-SWAP'
    date = stamp//DAY*DAY
    res = requests.get(API,params=dict(module=4,instType='SWAP',instFamilyList=symbol+'-USDT',
                      dateAggrType='daily',begin=date-1,end=date+1),timeout=(8,20))
    res.raise_for_status()
    body = res.json()
    items = body['data'][0]['details'][0]['groupDetails']
    rows = [r for r in items if int(r['dateTs'])==date and r['filename'].startswith(inst+'-L2orderbook-400lv-')]
    if len(rows)!=1 or stamp!=date:
        raise ValueError('This probe requires a unique native UTC session-open snapshot')
    file = EXP/f'{symbol}_events.jsonl.gz'
    events = []
    if file.exists():
        with gzip.open(file,'rt') as pipe:
            events = [json.loads(line) for line in pipe]
    else:
        with requests.get(rows[0]['url'],stream=True,timeout=(8,30)) as stream:
            stream.raise_for_status()
            with tarfile.open(fileobj=stream.raw,mode='r|gz') as archive:
                for member in archive:
                    if not member.isfile() or not member.name.startswith(inst+'-'):
                        continue
                    for line in archive.extractfile(member):
                        msg = json.loads(line)
                        if msg['instId']!=inst:
                            raise ValueError('Unexpected native book instrument')
                        events.append(msg)
                        if int(msg['ts'])>stamp+1000:
                            break
                    break
        if (not events or events[0]['action']!='snapshot' or not stamp<=int(events[0]['ts'])<=stamp+100
                or int(events[-1]['ts'])<=stamp+1000):
            raise ValueError('Missing native opening snapshot or incomplete next-second evidence')
        with gzip.open(file,'wt') as out:
            for msg in events:
                out.write(json.dumps(msg,separators=(',',':'))+'\n')
    return rows[0],events


def walk(book,side,qty,face,reference):
    levels = sorted(book['asks'].items()) if side>0 else sorted(book['bids'].items(),reverse=True)
    best = levels[0][0]
    cost = used = 0.
    remaining = qty
    last = best
    for price,size in levels:
        take = min(size*face,remaining)
        if take<=0:
            continue
        used += 1
        cost += price*take
        remaining -= take
        last = price
        if remaining<1e-10*qty:
            break
    filled = qty-remaining
    vwap = cost/filled
    return dict(side=int(side),best=best,last=last,vwap=vwap,levels=int(used),quantity_filled=filled,
                requested_base=qty,remaining_base=remaining,all_filled=bool(remaining<1e-10*qty),
                adverse_bp_from_trade_open=float(side*(vwap/reference-1)*10000),
                adverse_bp_from_best=float(side*(vwap/best-1)*10000))


def evaluate(row,quotes):
    inst,stamp = row.symbol,int(row.entry_ms)
    hour = stamp-audit.minute.HOUR
    bars,url = audit.minute.minute_quote(inst,hour,quotes)
    if bars is None:
        raise ValueError('Cannot confirm historical contract face prior to entry')
    amounts,_ = audit.volume_hour(inst,hour,bars,url)
    if amounts is None:
        raise ValueError('Missing contract-to-base evidence')
    face = (amounts.base / amounts.vol)[amounts.vol.gt(0)].dropna()
    if len(face)<3 or not np.allclose(face,face.median(),rtol=1e-8,atol=1e-12):
        raise ValueError('Changing or unsupported contract face')
    file,events = native_messages(inst,stamp)
    face = float(face.median())
    qty = row.notional/row.entry_price
    result = {}
    prev = stamp
    last_time = None
    book = {}
    for i,m in enumerate(events):
        t = int(m['ts'])
        if t<prev:
            raise ValueError('Nonchronological native book')
        if t>stamp+1000:
            break
        if m['action']=='snapshot':
            book={'asks':{},'bids':{}}
        elif m['action']!='update':
            raise ValueError('Unknown archive action')
        for side in ('asks','bids'):
            for item in m.get(side,[]):
                price,size = map(float,item[:2])
                if price<=0 or size<0:
                    raise ValueError('Invalid book level')
                if size:
                    book[side][price]=size
                else:
                    book[side].pop(price,None)
            if len(book[side])>400:
                best=sorted(book[side],reverse=side=='bids')[:400]
                book[side]={p:book[side][p] for p in best}
        if max(book['bids'])>=min(book['asks']):
            raise ValueError('Crossed or locked reconstructed book')
        if i==0:
            result['first'] = dict(ms_after_close=t-stamp,**walk(book,row.side,qty,face,row.entry_price))
        prev=last_time=t
    result['one_second'] = dict(last_update_ms_after_close=last_time-stamp,**walk(book,row.side,qty,face,row.entry_price))
    return dict(event_id=row.event_id,symbol=inst,entry_ms=stamp,entry_open=row.entry_price,entry_notional=row.notional,
                cash_pnl=row.cash_pnl,contract_face=face,official_archive=file,observations=result)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed opening book probes')
    funds = pd.read_csv(audit.source.EXP/'funded_contrarian_half.csv.gz')
    native = pd.read_csv(audit.source.repair.EXP/'account/selected_paths.csv.gz',usecols=['event_id','tp_r','entry_price'])
    funds['entry_price'] = funds.event_id.map(native[native.tp_r.eq(0)].set_index('event_id').entry_price)
    labels = {'largest_notional':funds.notional.idxmax(),
              'largest_long_winner':funds[funds.side.eq(1)].cash_pnl.idxmax(),
              'largest_short_winner':funds[funds.side.eq(-1)].cash_pnl.idxmax()}
    quotes,_ = audit.source.repair.checked_quotes()
    result = {k:evaluate(funds.loc[i],quotes) for k,i in labels.items()}
    audit.source.repair.archive.prices.safe_json(EXP/'summary.json',dict(cases=result,scope='Selected native opening snapshots on three exceptional trades.'))


if __name__=='__main__':
    main()
