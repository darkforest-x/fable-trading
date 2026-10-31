"""Census and chart every short-excursion glyph in the frozen current top20.

Only the symbol universe changes from the BTC census: completed NY Monday,
strict directional wick, V2.42 Tue-Fri glyph, entire observed run1-5candles.
The run's final length is retrospective. Current volume ranking is also
retrospective for old samples; neither defines a causal profitable strategy.
Reuse registered OKX CSVs and a read-only confirmed shared-cache snapshot.
OHLC conflicts are removed, forcing the existing census to skip those weeks;
verified repaired ETH5m takes precedence over older overlapping ETH exports.
No execution engine, win-rate labels, price forecast or outcome selection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import sqlite3
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_1618_early_marks import ROOT, STEP, COLS, CACHE, aggregate, census, dump
from yoyo.evaluation.btc_1618_early_marks_charts import setup, draw, stamp, BG, FG, FONT
from yoyo.evaluation.top20_1618_rank import EXP

OUT = ROOT/'artifacts/top20-1618-short-excursions-20261031'
ETH_FIXED = ROOT/'experiments/active/exp-range-v231-fiveyear-20261021-v1/ETH/candles.csv.gz'
ETH_EARLY = ROOT/'data/research/spike_v1210_eth15m_5y_20261012/okx_ETH_USDT_SWAP_1m_2672597.csv'


def file_stamp(p):
    return dict(path=str(p), bytes=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)


def read_csv(path, tf):
    raw = pd.read_csv(path, usecols=['ts']+COLS, float_precision='round_trip')
    raw = raw.sort_values('ts').drop_duplicates('ts')
    if tf != '15m':
        return aggregate(raw, 15//int(tf[:-1]))
    raw.index = pd.to_datetime(raw.ts.astype('int64'), unit='ms', utc=True)
    return raw[COLS].astype(float)


def matches_symbol(r, symbol):
    return 'okx_'+symbol+'_USDT_SWAP_' in r['path'] or r['symbol'] in (
        symbol, symbol+'-USDT-SWAP', symbol+'_USDT_SWAP', symbol+'/USDT:USDT')


def inputs(spec, inventory, cutoff):
    folder = EXP/spec['symbol']; folder.mkdir(exist_ok=True)
    if (folder/'bars.csv.gz').exists():
        return pd.read_csv(folder/'bars.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip'), json.loads((folder/'input.json').read_text())
    sources, frames, fixed = [], [], None
    if spec['symbol'] == 'ETH':
        fixed = read_csv(ETH_FIXED, '5m')
        frames.append(fixed); sources.append(dict(**file_stamp(ETH_FIXED),role='repaired_primary'))
        chunks=[]
        for chunk in pd.read_csv(ETH_EARLY,usecols=['ts']+COLS,float_precision='round_trip',chunksize=100000):
            chunks.append(chunk[chunk.ts < int(fixed.index[0].timestamp()*1000)])
            if chunk.ts.max() >= int(fixed.index[0].timestamp()*1000):break
        frames.append(aggregate(pd.concat(chunks,ignore_index=True),15))
        sources.append(dict(**file_stamp(ETH_EARLY),role='older_prefix_only'))
    seen=set()
    for rec in sorted(inventory,key=lambda r:(r['timeframe']!='15m',r['path'])):
        if not matches_symbol(rec,spec['symbol']):continue
        p=ROOT/rec['path']
        assert p.exists(), f'Registered input offline: {p}'
        if str(p.resolve()) in seen:continue
        seen.add(str(p.resolve()))
        frame=read_csv(p,rec['timeframe'])
        # Use repaired ETH cache for its entire covered period, never stale exports.
        if fixed is not None:frame=frame[(frame.index<fixed.index[0]) | (frame.index>fixed.index[-1])]
        frames.append(frame);sources.append(dict(**file_stamp(p),timeframe=rec['timeframe'],used_bars=len(frame)))
    with sqlite3.connect(CACHE.as_uri()+'?mode=ro',uri=True) as db:
        recent=[json.loads(r[0]) for r in db.execute('SELECT raw FROM candles WHERE key=? ORDER BY t',('https://www.okx.com|'+spec['instId']+'|15m',))]
    raw=pd.DataFrame(recent,columns=['ts']+COLS+['contracts','base','quote','confirm'])
    raw=raw[raw['confirm'].astype(str).eq('1')].copy()
    raw.index=pd.to_datetime(raw.ts.astype('int64'),unit='ms',utc=True)
    tail=raw[COLS].astype(float)
    if fixed is not None:tail=tail[(tail.index<fixed.index[0]) | (tail.index>fixed.index[-1])]
    frames.append(tail)
    merged=pd.concat(frames).sort_index()
    merged=merged[merged.index+STEP<=cutoff]
    assert ((merged.index.asi8 % STEP.value)==0).all()
    assert np.isfinite(merged.to_numpy()).all()
    assert (merged.low<=merged[COLS].min(axis=1)).all() and (merged.high>=merged[COLS].max(axis=1)).all()
    mins,maxs=merged.groupby(level=0).min(),merged.groupby(level=0).max()
    # Tolerance only absorbs CSV floating-point serialization, not a price tick.
    bad=((maxs-mins)>np.maximum(maxs.abs()*1e-11,1e-15)).any(axis=1)
    bad_times=bad.index[bad]
    if len(bad_times):merged.loc[bad_times].to_csv(folder/'conflicts.csv')
    bars=merged[~merged.index.duplicated(keep='first')].drop(bad_times).sort_index()
    gaps=np.flatnonzero(np.diff(bars.index.asi8)!=STEP.value)
    gap_rows=[dict(after=str(bars.index[i]),before=str(bars.index[i+1]),missing_bars=int((bars.index[i+1]-bars.index[i])/STEP)-1) for i in gaps]
    receipt=dict(symbol=spec['symbol'],bars=len(bars),start=str(bars.index[0]),last_open=str(bars.index[-1]),cutoff=str(cutoff),sources=sources,recent_cache=str(CACHE),recent_bars=len(tail),conflicting_times=len(bad_times),gaps=gap_rows,current_listing_time=str(pd.to_datetime(int(spec['list_time']),unit='ms',utc=True)),previous_listing_history_retained=True)
    dump(folder/'input.json',receipt);bars.to_csv(folder/'bars.csv.gz',index_label='open_time')
    return bars,receipt


def prepare(spec,bars,marks,matches):
    """Validate every selected event and collect continuous raw chart context."""
    rows=[]
    marks=marks[marks.basis.eq('wick') & marks.visible_v242]
    boundaries=np.r_[0,np.flatnonzero(np.diff(bars.index.asi8)!=STEP.value)+1,len(bars)]
    for n,r in enumerate(matches.to_dict('records'),1):
        opened=pd.Timestamp(r['signal_open']).tz_convert('UTC');i=bars.index.get_loc(opened)
        block=np.searchsorted(boundaries,i,side='right')-1
        lo,hi=int(boundaries[block]),int(boundaries[block+1])
        a=max(lo,min(i-30,hi-60));b=min(hi,a+60)
        frame=bars.iloc[a:b]
        assert len(frame)>=2 and i>=a and i<b
        assert (np.diff(frame.index.asi8)==STEP.value).all()
        assert np.allclose(bars.iloc[i][COLS],[r[c] for c in COLS],rtol=1e-12,atol=0)
        week=pd.Timestamp(r['week']);ready=(week+pd.Timedelta(days=1)).tz_localize('America/New_York');expiry=(week+pd.Timedelta(days=7)).tz_localize('America/New_York')
        monday=bars[(bars.index>=week.tz_localize('America/New_York')) & (bars.index<ready)]
        assert len(monday)==96
        high,low=float(monday.high.max()),float(monday.low.min())
        level=low+1.618*(high-low) if r['side']=='upper' else high-1.618*(high-low)
        assert math.isclose(level,r['level'],rel_tol=1e-12,abs_tol=0)
        first,last=pd.Timestamp(r['start']),pd.Timestamp(r['last'])
        run=bars.loc[first:last]
        assert len(run)==r['episode_bars']<=5 and r['duration_known']
        assert (run.high.gt(level) if r['side']=='upper' else run.low.lt(level)).all()
        assert r['ordinal']==bars.index.get_loc(opened)-bars.index.get_loc(first)+1
        for edge in (first-STEP,last+STEP):
            if edge>=ready:
                e=bars.loc[edge]
                assert e.high<=level if r['side']=='upper' else e.low>=level
        near=marks[marks.week.eq(r['week']) & marks.side.eq(r['side'])].copy()
        t=pd.to_datetime(near.signal_open,utc=True);near=near[(t>=frame.index[0]) & (t<=frame.index[-1])]
        assert near.signal_open.eq(r['signal_open']).sum()==1
        span=float(frame.high.max()-frame.low.min())
        axis_precision=max(0,int(-math.floor(math.log10(span/6)))+1)
        price_precision=max(axis_precision,2 if abs(level)>=1 else int(-math.floor(math.log10(abs(level))))+4)
        filename=f'{spec["symbol"]}_{n:03d}_{stamp(opened,"%Y%m%d_%H%M")}_{"cross" if r["side"]=="upper" else "check"}.png'
        r.update(symbol=spec['symbol'],number=n,total=len(matches),filename=filename,ready=str(ready),expiry=str(expiry),signal_x=i-a,precision=price_precision,axis_precision=axis_precision,candles=[dict(time=str(t),**{c:float(v[c]) for c in COLS}) for t,v in frame.iterrows()],nearby_marks=near[['signal_open','side','high','low']].to_dict('records'))
        rows.append(r)
    return rows


def scan():
    ranking=json.loads((EXP/'ranking.json').read_text());inventory=json.loads((EXP/'catalog_inventory.json').read_text())
    cutoff=pd.Timestamp(ranking['end']);summaries=[];all_matches=[];all_rows=[]
    for spec in ranking['scan']:
        bars,receipt=inputs(spec,inventory,cutoff)
        marks,episodes,skipped=census(bars)
        if len(marks):
            marks['episode_id']=spec['instId']+'|'+marks['episode_id']
            marks['listing_segment']=np.where(pd.to_datetime(marks.signal_open,utc=True)<pd.to_datetime(int(spec['list_time']),unit='ms',utc=True),'before_current_listTime','current_listing')
            matches=marks[marks.basis.eq('wick') & marks.visible_v242 & marks.ordinal.le(5) & marks.episode_bars.le(5) & marks.duration_known].sort_values(['signal_open','side']).copy()
            matches.insert(0,'symbol',spec['symbol'])
            rows=prepare(spec,bars,marks,matches)
        else:
            matches=pd.DataFrame();rows=[]
        folder=EXP/spec['symbol']
        marks.to_csv(folder/'all_outside_marks.csv',index=False);matches.to_csv(folder/'matches.csv',index=False)
        dump(folder/'chart_rows.json',rows)
        all_matches.append(matches);all_rows.extend(rows)
        summary=dict(symbol=spec['symbol'],rank=spec['rank'],signals=len(matches),episodes=int(matches.episode_id.nunique()) if len(matches) else 0,cross=int(matches.side.eq('upper').sum()) if len(matches) else 0,check=int(matches.side.eq('lower').sum()) if len(matches) else 0,before_current_listTime=int(matches.listing_segment.eq('before_current_listTime').sum()) if len(matches) else 0,start=receipt['start'],last_open=receipt['last_open'],bars=len(bars),gap_count=len(receipt['gaps']),conflicting_times=receipt['conflicting_times'],skipped_weeks=skipped)
        summaries.append(summary);print(json.dumps(summary),flush=True)
    combined=pd.concat(all_matches,ignore_index=True);combined.to_csv(EXP/'matches.csv',index=False)
    assert len(combined)==len(all_rows)
    dump(EXP/'summary.json',dict(signals=len(combined),episodes=sum(r['episodes'] for r in summaries),symbols=summaries,ranking=ranking,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),rule='Same as BTC43: strict wick; NY Tue-Fri V2.42; whole observed excursion1-5bars; all matches; retrospective review only',training_eligible=False,production_eligible=False,reproduction='.venv/bin/python -m yoyo.evaluation.top20_1618_short_charts --scan --render'))


def render_one(r):
    folder=OUT/r['symbol']/'png';folder.mkdir(parents=True,exist_ok=True)
    target=folder/r['filename']
    if not target.exists():draw(r,folder)
    return str(target)


def overview(rows,folder,filename,title):
    from PIL import Image,ImageDraw,ImageFont
    sheet=Image.new('RGB',(2400,100+675*((len(rows)+1)//2)),BG)
    d=ImageDraw.Draw(sheet);d.text((35,23),title,font=ImageFont.truetype(FONT,40),fill=FG)
    for j,r in enumerate(rows):
        with Image.open(OUT/r['symbol']/'png'/r['filename']) as im:
            assert im.size==(2400,1350)
            sheet.paste(im.convert('RGB').resize((1200,675),Image.Resampling.LANCZOS),((j%2)*1200,100+(j//2)*675))
    p=folder/filename;sheet.save(p,quality=93,subsampling=0);return p


def render():
    summary=json.loads((EXP/'summary.json').read_text());allrows=[];groups={}
    OUT.mkdir(parents=True,exist_ok=True)
    for s in summary['symbols']:
        rows=json.loads((EXP/s['symbol']/'chart_rows.json').read_text());groups[s['symbol']]=rows;allrows.extend(rows)
    with ProcessPoolExecutor(max_workers=4,initializer=setup) as pool:
        for n,_ in enumerate(pool.map(render_one,allrows),1):
            if n%50==0:print(f'Rendered {n}/{len(allrows)}',flush=True)
    fields=['symbol','number','filename','signal_open','side','level','ordinal','episode_bars','episode_id','listing_segment']
    pd.DataFrame(allrows)[fields].to_csv(OUT/'index.csv',index=False)
    sheets=[];archives=[];preview=[]
    for symbol,rows in groups.items():
        if not rows:continue
        folder=OUT/symbol;local=[]
        pd.DataFrame(rows)[fields].to_csv(folder/'index.csv',index=False)
        for page,a in enumerate(range(0,len(rows),8),1):
            batch=rows[a:a+8]
            local.append(overview(batch,folder,f'overview_{page:02d}.jpg',f'{symbol} 15分钟 · 全部{len(rows)}例 | {a+1}–{a+len(batch)} | {page}/{math.ceil(len(rows)/8)}'))
        archive=OUT/f'{symbol}_{len(rows)}_samples.zip'
        with ZipFile(archive,'w',ZIP_DEFLATED) as z:
            for p in [folder/'index.csv']+local+[folder/'png'/r['filename'] for r in rows]:z.write(p,p.relative_to(folder))
        with ZipFile(archive) as z:
            assert z.testzip() is None and sum(n.startswith('png/') for n in z.namelist())==len(rows)
        archives.append(str(archive.relative_to(OUT)));sheets.extend(str(p.relative_to(OUT)) for p in local);preview.append(rows[-1])
    previews=[]
    for p,a in enumerate(range(0,len(preview),8),1):
        previews.append(str(overview(preview[a:a+8],OUT,f'preview_latest_{p:02d}.jpg','预览：各币最近一例（全部样本见分币图集）').relative_to(OUT)))
    master=OUT/f'Top20_other19_all_{len(allrows)}_samples.zip'
    with ZipFile(master,'w',ZIP_DEFLATED) as z:
        z.write(OUT/'index.csv','index.csv')
        for symbol,rows in groups.items():
            for r in rows:z.write(OUT/symbol/'png'/r['filename'],symbol+'/png/'+r['filename'])
        for p in sheets:z.write(OUT/p,p)
    with ZipFile(master) as z:
        assert z.testzip() is None and sum('/png/' in n for n in z.namelist())==len(allrows)
    dump(OUT/'delivery.json',dict(signals=len(allrows),dimensions=[2400,1350],symbols=summary['symbols'],archives=archives,master=str(master),overview_sheets=sheets,preview_sheets=previews,validation=dict(all_signal_OHLC=True,all_monday_levels=True,all_excursion_boundaries=True,all_zip_members=True),source=str(EXP),generator_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(f'Complete: {len(allrows)} charts, {len(sheets)} sheets; {master}',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--scan',action='store_true');p.add_argument('--render',action='store_true');a=p.parse_args()
    if a.scan:scan()
    if a.render:render()
