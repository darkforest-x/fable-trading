"""Render exactly the seven net-profitable recent BTC all-key15m trades.

Owner-requested static review images: same black 60-candle landscape style,
yellow complete touch candle, both Monday bounds and named touch-time Fib
levels, actual entry and confirmation-close exit arrows. Reuse validated
OHLC chart context only after matching current frozen identities and prices.
Profit-only selection is explicit; these seven do not describe all15 results.
No new OHLC, signal selection, execution model or TradingView state is created.
"""
import copy
import json
import subprocess
from pathlib import Path
import zipfile

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from yoyo.evaluation.btc_annual_signal_landscape import ROOT, FONT, render, stamp

DEST = ROOT/'artifacts/btc_recent_winners_20261021'
SOURCE = ROOT/'experiments/active/exp-range-v231-recent2m-20261021-v1/BTC'
CONTEXT = ROOT/'artifacts/btc_annual_signal_landscape60_20261021/chart_data.json'
CODE = ['yoyo/evaluation/btc_recent_winners_charts.py','yoyo/evaluation/btc_annual_signal_landscape.py']


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit renderer before producing artifacts')
    trades=pd.read_csv(SOURCE/'pivot_trades.csv',float_precision='round_trip')
    signals=trades[trades.population=='signal']
    winners=signals[signals.net_r>1e-9].sort_values('entry_i')
    assert len(signals)==15 and len(winners)==7
    matches=pd.read_csv(SOURCE/'signal_matches.csv',float_precision='round_trip').set_index('pair_id')
    context=json.loads(CONTEXT.read_text())
    lookup={(r['signal_time'],r['side']):r for r in context['charts'] if r['version']=='V2.30'}
    charts=[]; summary=[]
    for number,t in enumerate(winners.itertuples(),1):
        m=matches.loc[t.pair_id]
        r=copy.deepcopy(lookup[(m.signal_time,int(t.side))])
        for key in ['signal_i','touch_i','entry_i','entry','stop','key','level']:
            assert r[key]==m[key],(t.pair_id,key)
        for key in ['entry','stop']:
            assert abs(r[key]-getattr(t,key))<1e-8
        ex=r['exit']
        for key,column in [('price','exit_price'),('gross_r','gross_r'),('net_r','net_r')]:
            assert abs(ex[key]-getattr(t,column))<1e-8
        assert pd.Timestamp(ex['time'])==pd.Timestamp(t.exit_time)
        assert pd.Timestamp(r['entry_time'])==pd.Timestamp(t.entry_time)
        assert not ex['outside_window'] and len(r['candles'])==60
        assert ex['reason']==7 and abs(ex['pivot']['price']-t.pivot_price)<1e-8
        opened=pd.DatetimeIndex([b['time'] for b in r['candles']])
        exit_i=int(opened.get_indexer([pd.Timestamp(ex['time'])-pd.Timedelta(minutes=15)])[0])
        assert exit_i>=0 and abs(r['candles'][exit_i]['close']-t.exit_price)<1e-8
        r.update(version='V2.31',number=number,total=7,other_number=None,pair_id=int(t.pair_id))
        charts.append(r)
        summary.append(dict(number=number,pair_id=int(t.pair_id),side=int(t.side),
            entry_time=r['entry_time'],entry=t.entry,exit_time=ex['time'],exit=t.exit_price,
            stop=t.stop,key=r['key'],key_price=r['level'],gross_r=t.gross_r,net_r=t.net_r))
    DEST.mkdir(parents=True,exist_ok=True)
    data=dict(charts=charts,period=['2026-08-21','2026-10-21'],selection='Seven net winners from15BTC signals',
              source=str(SOURCE.relative_to(ROOT)),chart_contract=__doc__)
    (DEST/'chart_data.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    render(destination=DEST,source_data=data,trade_review=True)
    manifest=json.loads((DEST/'manifest.json').read_text())
    assert len(manifest)==7
    paths=[DEST/r['file'] for r in manifest]
    for r,path in zip(manifest,paths):
        assert {r['key'],'0:up','0:down'}<=set(r['labeled_levels'])
        with Image.open(path) as im:
            assert im.size==(2880,1620);im.verify()
    archive=DEST/'BTC_recent_7_winners_entries_exits.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for path in paths:z.write(path,path.name)
    with zipfile.ZipFile(archive) as z:assert len(z.namelist())==7 and z.testzip() is None
    # Review sheet is only a navigation aid; the7individual PNGs are deliverables.
    contact=Image.new('RGB',(1600,4*490+70),'#11151B')
    draw=ImageDraw.Draw(contact);font=ImageFont.truetype(FONT,24)
    draw.text((24,20),'BTC 近两个月 · 7笔净盈利交易 · 入场与出场',font=font,fill='#E5E8F0')
    for i,path in enumerate(paths):
        with Image.open(path) as im:
            im=im.resize((800,450),Image.Resampling.LANCZOS)
            contact.paste(im,((i%2)*800,70+(i//2)*490))
        draw.text(((i%2)*800+25,70+(i//2)*490+455),
                  f"#{i+1}   {stamp(summary[i]['entry_time'])}    {summary[i]['net_r']:+.2f}R",font=font,fill='#B9C2D0')
    contact.save(DEST/'overview.jpg',quality=94)
    receipt=dict(count=7,from_signals=15,all_entries_exits_verified=True,all60bars=True,
        dimensions=[2880,1620],both_monday_bounds=True,matched_keys_named=True,
        selection='Only requested net winners; not an unfiltered strategy sample',rows=summary,
        zip=archive.name,bytes=archive.stat().st_size,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    (DEST/'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
