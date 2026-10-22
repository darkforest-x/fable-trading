"""Render every ETH sweep20+MTF fixed1R signal behind gross19/27 and +11R.

Reuse original native fills, 15m OHLC renderer and the exact frozen admission
IDs. No outcome filtering or new trading simulation. Known 5m O-H-L-C fill times
are bar-close timestamps, not tick timestamps. Post-entry candles are explicit
review context. Historical fees remain in source fields; displayed fee is zero.
"""
import argparse
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'experiments/active/exp-range-v231-sweep20-20261021-v1'
DEST = ROOT/'experiments/active/exp-eth-sweep20-mtf-27-charts-20261022-v1'
CODE = ['yoyo/evaluation/eth_sweep_mtf_signal_charts.py',
        'yoyo/evaluation/btc_annual_signal_landscape.py']


def prepare():
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.monday_range_sweep_reference import sweep_mask
    cfg=json.loads((SOURCE/'config.json').read_text())
    read=lambda p:pd.read_csv(p,float_precision='round_trip')
    gate=read(SOURCE/'ETH/admission.csv').set_index('pair_id')
    matches=read(SOURCE/'ETH/signal_matches.csv').set_index('pair_id')
    ledger=read(SOURCE/'ETH/fixed_1r/trades.csv')
    selected=ledger[(ledger.population=='signal')&ledger.pair_id.isin(gate.index[gate.window_both])].sort_values('entry_i')
    assert len(selected)==27 and selected.pair_id.is_unique
    assert selected.gross_r.gt(0).sum()==19 and abs(selected.gross_r.sum()-11)<1e-8
    assert not selected.boundary_weight.gt(0).any()
    # Parent accounting omits fill fields. Trace each parent back to its native
    # leg, including the eight sweep-rematched pairs; never infer the fill price.
    prep=json.loads((ROOT/cfg['comparison']/'preparation.json').read_text())
    assert prep['ETH']['changed']==[]
    changed=json.loads((SOURCE/'execution.json').read_text())['metadata']['ETH']['changed']
    old_path=ROOT/cfg['exit_reference']/'ETH/fixed_1r/native_trades.csv'
    delta_path=SOURCE/'delta/exits/ETH/fixed_1r/native_trades.csv'
    old,delta=read(old_path),read(delta_path)
    native=pd.concat([old[~old.pair_id.isin(changed)],delta],ignore_index=True).set_index('parent_id')
    assert native.index.is_unique
    candles_path=ROOT/cfg['source']/'ETH/candles.csv.gz'
    five=read(candles_path)
    five.index=pd.DatetimeIndex(pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    q=aggregate(five,15);opened=q.index-pd.Timedelta(minutes=15)
    arrow=signals(q).to_numpy()
    grid,names,weeks,_=monday_levels(q,cfg['ratios'],cfg['timezone'])
    charts,index=[],[]
    for number,parent in enumerate(selected.itertuples(),1):
        t=native.loc[parent.trade_id];m=matches.loc[parent.pair_id];g=gate.loc[parent.pair_id]
        for field in ['entry_i','exit_i','side','entry','stop','gross_r','fee_r']:
            assert abs(t[field]-getattr(parent,field))<1e-8,(parent.trade_id,field)
        assert t.weight==1 and t.leg==0 and t.reason in (1,4)
        i,j,e=(int(m[k]) for k in ['signal_i','touch_i','entry_i']);side=int(t.side)
        assert arrow[i]==side and sweep_mask(q,side,window=20,tick=.01)[j]
        assert q.index[e]==pd.Timestamp(t.entry_time)==five.index[int(t.entry_i)]
        assert opened[i]==pd.Timestamp(m.signal_time) and opened[j]==pd.Timestamp(m.touch_time)
        assert q.index[i]==pd.Timestamp(g.arrow_close) and bool(g.paired_arrow_was_mtf)
        assert q.close.iloc[e]==t.entry==m.entry and t.stop==m.stop
        assert abs(grid[j,names.index(m.key)]-m.level)<1e-8
        b=q.iloc[j];bodylo,bodyhi=min(b.open,b.close),max(b.open,b.close)
        wick=bodylo-b.low if side==1 else b.high-bodyhi
        depth=m.level-b.low if side==1 else b.high-m.level
        assert wick>0 and .1-1e-8<=depth/wick<=1
        assert t.stop==(b.low if side==1 else b.high)
        prior20=float(q.low.iloc[j-20:j].min() if side==1 else q.high.iloc[j-20:j].max())
        lo=max(0,min(min(i,j)-20,len(q)-60));hi=lo+60
        ex=pd.Timestamp(t.exit_time).tz_convert('Asia/Shanghai')
        hi=min(len(q),max(hi,int(q.index.searchsorted(ex))+4))
        assert opened[lo]<=ex<=q.index[hi-1]
        assert np.all(np.diff(q.index[lo:hi].asi8)==pd.Timedelta(minutes=15).value)
        assert five.low.iloc[int(t.exit_i)]-1e-8<=t.exit_price<=five.high.iloc[int(t.exit_i)]+1e-8
        assert abs(t.exit_price-(t.target_price if t.reason==4 else t.stop))<1e-8
        assert abs(t.gross_r-side*(t.exit_price-t.entry)/t.risk)<1e-8
        r=dict(symbol='ETH',version='ETH_27',display_version='扫极值＋多周期 · 固定1R',number=number,total=27,
            pair_id=int(t.pair_id),side=side,signal_i=i,touch_i=j,entry_i=e,
            signal_time=m.signal_time,touch_time=m.touch_time,entry_bar_time=m.entry_bar_time,entry_time=m.entry_time,
            entry=float(t.entry),stop=float(t.stop),target=float(t.target_price),key=m.key,level=float(m.level),
            other_number=None,other_version=None,monday_date=str(weeks[j]),monday_low=float(grid[j,0]),monday_high=float(grid[j,1]),
            wick=float(wick),depth=float(depth),penetration=float(depth/wick),bars_before=i-lo,bars_after=hi-i-1,
            mtf_arrow_time=g.arrow_close,review_group='扫前20根＋多周期 · 固定1R · 零手续费',
            separate_entry_callout=True,price_decimals=2,zero_fee=True,ambiguous=bool(t.ambiguous),
            extend_to_exit=hi-lo>60,
            entry_5m_i=int(t.entry_i),exit_5m_i=int(t.exit_i),prior20=prior20,
            candles=[dict(index=k,time=opened[k].tz_convert('Asia/Shanghai').isoformat(),
                ny_day=int(opened[k].tz_convert(cfg['timezone']).dayofweek),arrow=int(arrow[k]),
                **{c:float(q.iloc[k][c]) for c in ['open','high','low','close']}) for k in range(lo,hi)],
            levels=[dict(key=name,price=float(grid[j,k])) for k,name in enumerate(names)]+
                [dict(key='prior20:low' if side==1 else 'prior20:high',price=prior20)],
            execution_bars=[dict(index=k,time=(five.index[k]-pd.Timedelta(minutes=5)).tz_convert('Asia/Shanghai').isoformat(),
                **{c:float(five.iloc[k][c]) for c in ['open','high','low','close']})
                for k in range(max(0,int(t.entry_i)-2),min(len(five),int(t.exit_i)+3))],
            exit=dict(time=ex.isoformat(),price=float(t.exit_price),reason=int(t.reason),label='止盈' if t.reason==4 else '止损',
                gross_r=float(t.gross_r),net_r=float(t.gross_r),outside_window=False,pivot=None))
        charts.append(r)
        index.append(dict(number=number,pair_id=int(t.pair_id),side=side,signal_time=m.signal_time,entry_time=m.entry_time,
            exit_time=ex.isoformat(),entry=float(t.entry),stop=float(t.stop),target=float(t.target_price),
            exit_price=float(t.exit_price),reason=int(t.reason),gross_r=float(t.gross_r),fee_r=0.,source_fee_r=float(t.fee_r),
            ambiguous=bool(t.ambiguous),native_source=str((delta_path if int(t.pair_id) in changed else old_path).relative_to(ROOT))))
    random=ledger[(ledger.population=='random')&ledger.pair_id.isin(selected.pair_id)]
    DEST.mkdir(parents=True,exist_ok=True)
    payload=dict(charts=charts,count=27,wins=19,losses=8,gross_r=11.,round_trip_cost=0,
        start=cfg['start'],end=cfg['end'],random_n=len(random),random_wins=int(random.gross_r.gt(0).sum()),
        random_gross_r=float(random.gross_r.sum()),source=str(SOURCE.relative_to(ROOT)),data=str(candles_path.relative_to(ROOT)),
        selection='Every admitted ETH sweep20+MTF fixed1R signal; chronological order; no outcome filtering',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    (DEST/'chart_data.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    pd.DataFrame(index).to_csv(DEST/'trade_index.csv',index=False)
    print(json.dumps({k:v for k,v in payload.items() if k!='charts'},ensure_ascii=False),flush=True)


def render():
    from PIL import Image,ImageDraw,ImageFont
    from yoyo.evaluation.btc_annual_signal_landscape import render as draw,FONT,stamp
    data=json.loads((DEST/'chart_data.json').read_text())
    draw(destination=DEST,source_data=data,trade_review=True)
    manifest=json.loads((DEST/'manifest.json').read_text())
    assert len(manifest)==27
    font=ImageFont.truetype(FONT,30)
    overviews=[]
    for start in range(0,27,9):
        chunk=manifest[start:start+9]
        contact=Image.new('RGB',(2400,1600),'#000000');ink=ImageDraw.Draw(contact)
        ink.text((24,18),f'ETH · 扫极值＋多周期 · #{start+1:02d}–#{start+len(chunk):02d} / 27 · 零手续费',font=font,fill='#D9DADB')
        for offset,m in enumerate(chunk):
            r=data['charts'][start+offset]
            assert {r['key'],'0:up','0:down'}<=set(m['labeled_levels']) and m['candle_count']==len(r['candles'])
            with Image.open(DEST/m['file']) as im:
                assert im.size==(2880,1620)
                contact.paste(im.resize((800,450),Image.Resampling.LANCZOS),((offset%3)*800,80+(offset//3)*500))
            text=f"#{r['number']:02d} {stamp(r['entry_time'])} {'多' if r['side']==1 else '空'} {r['exit']['gross_r']:+.0f}R"
            ink.text(((offset%3)*800+18,535+(offset//3)*500),text,font=font,
                fill='#36C9A3' if r['exit']['gross_r']>0 else '#F36C7A')
        name=f'overview_{start//9+1:02d}.jpg';contact.save(DEST/name,quality=96);overviews.append(name)
    archive=DEST/'ETH_27_sweep20_MTF_fixed1R_PNG.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for m in manifest:z.write(DEST/m['file'],Path(m['file']).name)
        for name in overviews:z.write(DEST/name,name)
        z.write(DEST/'trade_index.csv','trade_index.csv')
    with zipfile.ZipFile(archive) as z:
        assert sum(n.endswith('.png') for n in z.namelist())==27 and z.testzip() is None
    receipt=dict(count=27,wins=19,losses=8,gross_r=11.,round_trip_cost=0,
        dimensions=[2880,1620],bars_per_chart=[len(r['candles']) for r in data['charts']],all_source_fills_reconciled=True,
        all_source_signal_ids_included=True,all_5m_execution_insets=True,
        post_entry_is_review_context=True,ambiguous=sum(r['ambiguous'] for r in data['charts']),
        overviews=overviews,zip=archive.name,zip_bytes=archive.stat().st_size,
        training_eligible=False,production_eligible=False)
    (DEST/'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['prepare','render']);args=parser.parse_args()
    for path in CODE:
        subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit renderer before generating')
    globals()[args.mode]()
