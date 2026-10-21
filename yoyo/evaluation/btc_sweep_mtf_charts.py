"""Render all35 frozen BTC sweep20+MTF trades, with no outcome selection.

Owner requested every trade behind the40percent net win rate. Reuse the
60bar black landscape renderer, source5m OHLC and actual native fills. The
highlighted candle, matched key and prior20 extreme are all touch-time
facts. Subsequent candles and pivots are explicitly review context only.
Entry/exit/stop/risk/cost never change. Native Python prepares; project
Python renders; bundled Python packages the exact PNGs into a PDF album.
"""
import argparse
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT/'experiments/active/exp-range-v231-sweep20-20261021-v1'
DEST = ROOT/'artifacts/btc_sweep20_mtf_35_charts_20261021'
CODE = ['yoyo/evaluation/btc_sweep_mtf_charts.py',
        'yoyo/evaluation/btc_annual_signal_landscape.py']


def prepare():
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.monday_range_sweep_reference import sweep_mask
    cfg = json.loads((SOURCE/'config.json').read_text())
    read = lambda p: pd.read_csv(p, float_precision='round_trip')
    gate = read(SOURCE/'BTC/admission.csv').set_index('pair_id')
    matches = read(SOURCE/'BTC/signal_matches.csv').set_index('pair_id')
    ledger = read(SOURCE/'BTC/pivot/trades.csv')
    chosen = ledger[(ledger.population == 'signal') & ledger.pair_id.isin(gate.index[gate.window_both])].sort_values('entry_i')
    assert len(chosen) == 35 and chosen.pair_id.is_unique
    assert (chosen.net_r > 1e-9).sum() == 14
    summary = json.loads((SOURCE/'summary.json').read_text())
    expected = next(r for r in summary['rows'] if r['symbol']=='BTC' and r['arm']=='pivot'
                    and r['period']=='all' and r['group']=='sweep_mtf')
    assert abs(chosen.net_r.sum()-expected['net_r']) < 1e-8
    candles_path = ROOT/cfg['source']/'BTC/candles.csv.gz'
    five = read(candles_path)
    five.index = pd.DatetimeIndex(pd.to_datetime(five.pop('ts'), unit='ms', utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    q = aggregate(five, 15); opened = q.index-pd.Timedelta(minutes=15)
    arrows = signals(q).to_numpy()
    grid, names, weeks, _ = monday_levels(q, cfg['ratios'], cfg['timezone'])
    charts = []; rows = []
    for number, t in enumerate(chosen.itertuples(), 1):
        m = matches.loc[t.pair_id]; g = gate.loc[t.pair_id]
        i, j, e = (int(m[k]) for k in ['signal_i', 'touch_i', 'entry_i'])
        side = int(t.side)
        assert arrows[i] == side and sweep_mask(q, side)[j]
        assert q.index[e] == pd.Timestamp(t.entry_time) == pd.Timestamp(m.entry_time)
        assert opened[i] == pd.Timestamp(m.signal_time) and opened[j] == pd.Timestamp(m.touch_time)
        assert q.index[i] == pd.Timestamp(g.arrow_close) and g.paired_arrow_was_mtf
        assert q.close.iloc[e] == t.entry == m.entry and t.stop == m.stop
        assert abs(grid[j, names.index(m.key)]-m.level) < 1e-8
        b = q.iloc[j]; bodylo, bodyhi = min(b.open,b.close), max(b.open,b.close)
        wick = bodylo-b.low if side == 1 else b.high-bodyhi
        depth = m.level-b.low if side == 1 else b.high-m.level
        assert wick > 0 and .1-1e-8 <= depth/wick <= 1
        assert (b.low <= m.level < bodylo if side == 1 else bodyhi < m.level <= b.high)
        assert t.stop == (b.low if side == 1 else b.high)
        prior20 = float(q.low.iloc[j-20:j].min() if side == 1 else q.high.iloc[j-20:j].max())
        lo = max(0, min(min(i,j)-20, len(q)-60)); hi = lo+60
        assert lo <= min(i,j,e) <= max(i,j,e) < hi
        assert np.all(np.diff(q.index[lo:hi].asi8) == pd.Timedelta(minutes=15).value)
        exit_time = pd.Timestamp(t.exit_time).tz_convert('Asia/Shanghai')
        assert q.index[lo] > opened[lo] and opened[lo] <= exit_time <= q.index[hi-1]
        exitbar = five.iloc[int(t.exit_i)]
        assert exitbar.low-.1 < t.exit_price < exitbar.high+.1
        if int(t.reason) == 7:
            assert t.exit_price == exitbar.close
        risk = side*(t.entry-t.stop)
        assert abs(t.gross_r-side*(t.exit_price-t.entry)/risk) < 1e-8
        assert abs(t.fee_r-.002*t.entry/risk) < 1e-8
        pivot = None
        if np.isfinite(t.pivot_price):
            pt = pd.Timestamp(t.pivot_open_time).tz_convert('Asia/Shanghai')
            pk = int(opened.get_indexer([pt])[0]); assert pk >= 0
            assert abs(q.iloc[pk]['high' if side == 1 else 'low']-t.pivot_price) < 1e-8
            pivot = dict(time=pt.isoformat(), price=float(t.pivot_price), reference_r=float(t.pivot_reference_r))
        r = dict(version='V2.31', number=number, total=35, pair_id=int(t.pair_id),
            side=side, signal_i=i, touch_i=j, entry_i=e,
            signal_time=m.signal_time, touch_time=m.touch_time, entry_bar_time=m.entry_bar_time,
            entry_time=m.entry_time, entry=float(t.entry), stop=float(t.stop),
            key=m.key, level=float(m.level), other_number=None, other_version=None,
            monday_date=str(weeks[j]), monday_low=float(grid[j,0]), monday_high=float(grid[j,1]),
            wick=float(wick), depth=float(depth), penetration=float(depth/wick),
            bars_before=i-lo, bars_after=hi-i-1,
            mtf_arrow_time=pd.Timestamp(g.arrow_close).tz_convert('Asia/Shanghai').isoformat(),
            review_group='扫20根＋多周期', prior20=prior20,
            candles=[dict(index=k,time=opened[k].tz_convert('Asia/Shanghai').isoformat(),
                ny_day=int(opened[k].tz_convert(cfg['timezone']).dayofweek),arrow=int(arrows[k]),
                **{c:float(q.iloc[k][c]) for c in ['open','high','low','close']}) for k in range(lo,hi)],
            levels=[dict(key=name,price=float(grid[j,k])) for k,name in enumerate(names)]+
                   [dict(key='prior20:low' if side == 1 else 'prior20:high',price=prior20)],
            exit=dict(time=exit_time.isoformat(),price=float(t.exit_price),reason=int(t.reason),
                gross_r=float(t.gross_r),net_r=float(t.net_r),outside_window=False,pivot=pivot))
        charts.append(r)
        rows.append(dict(number=number,pair_id=int(t.pair_id),side=side,signal_time=m.signal_time,
            touch_time=m.touch_time,entry_time=m.entry_time,entry=t.entry,stop=t.stop,
            exit_time=exit_time.isoformat(),exit_price=t.exit_price,reason=int(t.reason),
            key=m.key,level=m.level,prior20=prior20,gross_r=t.gross_r,fee_r=t.fee_r,net_r=t.net_r))
    DEST.mkdir(exist_ok=True)
    payload = dict(charts=charts,source=str(SOURCE.relative_to(ROOT)),data=str(candles_path.relative_to(ROOT)),
        selection='Every BTC window_both trade from the frozen sweep20 study, sorted by entry time',
        count=35,net_wins=14,net_losses=21,net_r=float(chosen.net_r.sum()),
        chart_contract=__doc__,builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    (DEST/'chart_data.json').write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False))
    pd.DataFrame(rows).to_csv(DEST/'trade_index.csv',index=False)
    print(json.dumps({k:payload[k] for k in ['count','net_wins','net_losses','net_r']}),flush=True)


def render():
    from yoyo.evaluation.btc_annual_signal_landscape import render as draw, FONT, stamp
    from PIL import Image, ImageDraw, ImageFont
    data = json.loads((DEST/'chart_data.json').read_text())
    draw(destination=DEST, source_data=data, trade_review=True)
    manifest = json.loads((DEST/'manifest.json').read_text())
    assert len(manifest) == 35
    font = ImageFont.truetype(FONT, 26)
    overview = []
    for start in range(0,35,8):
        chunk = manifest[start:start+8]; height=((len(chunk)+1)//2)*492+70
        contact = Image.new('RGB',(1600,height),'#0B0D11'); ink=ImageDraw.Draw(contact)
        ink.text((24,18),f'BTC 扫20根＋多周期  #{start+1:02d}–#{start+len(chunk):02d} / 35',font=font,fill='#D9DADB')
        for offset,row in enumerate(chunk):
            path=DEST/row['file'];r=data['charts'][start+offset]
            assert {r['key'],'0:up','0:down'} <= set(row['labeled_levels'])
            with Image.open(path) as im:
                assert im.size == (2880,1620)
                contact.paste(im.resize((800,450),Image.Resampling.LANCZOS),((offset%2)*800,70+(offset//2)*492))
            text=f"#{r['number']:02d}  {stamp(r['entry_time'])}  {'多' if r['side']==1 else '空'}  净 {r['exit']['net_r']:+.2f}R"
            ink.text(((offset%2)*800+22,70+(offset//2)*492+451),text,font=font,
                fill='#36C9A3' if r['exit']['net_r']>0 else '#F36C7A')
        name=f'overview_{start//8+1:02d}.jpg';contact.save(DEST/name,quality=95);overview.append(name)
    archive=DEST/'BTC_35_sweep20_MTF_60bars.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for row in manifest:z.write(DEST/row['file'],Path(row['file']).name)
        z.write(DEST/'trade_index.csv','trade_index.csv')
    with zipfile.ZipFile(archive) as z:
        assert len([n for n in z.namelist() if n.endswith('.png')]) == 35 and z.testzip() is None
    receipt=dict(count=35,net_wins=14,net_losses=21,all60bars=True,all_fills_visible=True,
        dimensions=[2880,1620],both_monday_bounds=True,named_matched_key=True,prior20_extreme=True,
        actual_fill_prices=True,original_fees=True,zip=archive.name,overview=overview,
        bytes=archive.stat().st_size,training_eligible=False,production_eligible=False)
    (DEST/'validation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(receipt,ensure_ascii=False),flush=True)


def pdf():
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader
    rows=json.loads((DEST/'manifest.json').read_text())
    path=DEST/'BTC_35_sweep20_MTF_charts.pdf'
    book=canvas.Canvas(str(path),pagesize=(1152,648))
    book.setTitle('BTCUSDT.P - all35 sweep20 + MTF trades')
    for row in rows:
        book.bookmarkPage(str(row['number']))
        book.addOutlineEntry(f"{row['number']:02d}  {row['signal_time'][:16]}",str(row['number']))
        book.drawImage(str(DEST/row['file']),0,0,width=1152,height=648)
        book.showPage()
    book.save()
    assert len(PdfReader(str(path)).pages) == 35
    print(str(path),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','render','pdf']);args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit renderer before generation')
    globals()[args.mode]()
