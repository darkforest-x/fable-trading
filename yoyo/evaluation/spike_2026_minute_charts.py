"""Render saved spike trades against actual Binance UM 1m candles, without replay.

Owner 2026-10-27: replace the delivered 5m charts with 1m charts to inspect
same-bar exits. Original native source indices define 5m execution intervals;
their close timestamps are not observed intrabar fills. Preserve every original
outcome and show interval marks, never infer new fills from minute extrema.
Reuse the registered daily 1m cache; fetch only missing same-exchange days.
All chart data are post-trade review context, not model inputs or new features.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import zipfile

import numpy as np
import pandas as pd

from yoyo.evaluation.paper_2026_trade_charts import ROOT, A, COLORS, CAUSE, fmt, read, save

EXP = A/'exp-spike-2026-1m-trade-charts-20261027-v1'
OLD = A/'exp-paper-2026-trade-charts-20261027-v1'
CACHE = ROOT/'data/crypto/market/kline_binance_um1m_touchdays_20261027'
FIELDS = ['open', 'high', 'low', 'close', 'volume']
MINUTE = pd.Timedelta(minutes=1)
FIVE = pd.Timedelta(minutes=5)


def orders():
    data = pd.read_csv(OLD/'trades.csv')
    data = data[(data.track == 'spike') & (data.cohort == '2026')].copy()
    provenance = {r['event']: r for s in read(OLD/'render-validation.json')['symbols']
                  for r in s['charts'] if r['track'] == 'spike'}
    for key in ['entry_bar', 'exit_bar']:
        data[key] = data.event.map(lambda event: provenance[event][key])
    assert len(data) == 85 and data.event.is_unique
    assert all(provenance[event]['native_times_match_source_indices'] for event in data.event)
    data['file'] = [f'charts/{int(r.sequence):04d}_{r.symbol}_1min.png' for r in data.itertuples()]
    return data.sort_values('sequence')


def days_for(row):
    start = pd.Timestamp(row.entry_bar)-30*MINUTE
    end = pd.Timestamp(row.exit_bar)+FIVE+20*MINUTE
    return list(pd.date_range(start.floor('D'), end.floor('D'), freq='D').strftime('%Y-%m-%d'))


def prepare():
    from yoyo.evaluation.monday_limit_partial_bar_score import fetch, URL
    data = orders()
    wanted = sorted({(r.symbol, day) for r in data.itertuples() for day in days_for(r)})
    missing = [(symbol, day, str(CACHE)) for symbol, day in wanted if not (CACHE/symbol/f'{day}.csv').exists()]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(fetch, missing))
    failures = [r for r in results if r.startswith('error:')]
    save(EXP/'fetch-receipt.json', dict(required_days=len(wanted), reused_days=len(wanted)-len(missing),
        requested=[dict(symbol=s, date=d, url=URL.format(s=s, d=d)) for s, d, _ in missing], errors=failures))
    if failures:
        raise RuntimeError(f'Minute source unavailable: {failures}')
    inputs = []
    for symbol, day in wanted:
        path = CACHE/symbol/f'{day}.csv'
        inputs.append(dict(symbol=symbol, day=day, path=str(path.relative_to(ROOT)),
                           bytes=path.stat().st_size, mtime_ns=path.stat().st_mtime_ns))
    data.to_csv(EXP/'trades.csv', index=False)
    save(EXP/'inputs.json', dict(files=inputs, source_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()))
    print(json.dumps(dict(orders=len(data), files=len(inputs), downloaded=len(missing))), flush=True)


def minute_frame(symbol, days):
    parts = []
    for day in sorted(set(days)):
        path = CACHE/symbol/f'{day}.csv'
        header = path.open().readline().startswith('open_time,')
        d = pd.read_csv(path, header=0 if header else None, usecols=range(6))
        d.columns = ['ts', *FIELDS]
        d = d.astype(float)
        if d.ts.iloc[0] > 1e14:
            d.ts = d.ts//1000
        d.index = pd.DatetimeIndex(pd.to_datetime(d.pop('ts'), unit='ms', utc=True)).as_unit('ns')
        assert d.index.is_unique and d.index.is_monotonic_increasing, path
        assert np.isfinite(d.to_numpy()).all() and (d[FIELDS[:4]] > 0).all().all(), path
        assert (d.high >= d[['open', 'close', 'low']].max(axis=1)).all(), path
        assert (d.low <= d[['open', 'close', 'high']].min(axis=1)).all(), path
        parts.append(d)
    result = pd.concat(parts).sort_index()
    assert result.index.is_unique
    return result


def verify_frame(minute, original, row):
    """Verify actual minute coverage and same-exchange 5m OHLCV, no fills calculated."""
    entry, exit_ = pd.Timestamp(row.entry_bar), pd.Timestamp(row.exit_bar)
    assert original.index[int(row.entry_i)] == entry and original.index[int(row.exit_i)] == exit_
    start, end = entry-30*MINUTE, exit_+FIVE+20*MINUTE
    window = minute.loc[start:end-MINUTE]
    expected = pd.date_range(start, end, freq='min', inclusive='left')
    assert window.index.equals(expected), (row.event, 'missing minute candles')
    rebuilt = window.resample('5min').agg(dict(open='first', high='max', low='min', close='last', volume='sum'))
    reference = original.reindex(rebuilt.index)
    assert reference[FIELDS].notna().all().all(), (row.event, 'missing original 5m')
    # An empty leading minute can carry the preceding close while the 5m
    # archive opens at its first traded minute. Accept only this observed
    # context-only distinction; never modify the plotted minutes or fills.
    compared = rebuilt.copy()
    empty_minute_differences = []
    for stamp in rebuilt.index[~np.isclose(rebuilt.open, reference.open, rtol=1e-7, atol=1e-10)]:
        block = window.loc[stamp:stamp+4*MINUTE]
        traded = block[block.volume > 0]
        if traded.empty or entry <= stamp <= exit_:
            continue
        leading = block.loc[block.index < traded.index[0]]
        if (len(leading) and (leading.volume == 0).all()
                and np.allclose(leading[FIELDS[:4]], leading.close.iloc[0], rtol=0, atol=1e-12)
                and stamp-MINUTE in minute.index
                and np.isclose(leading.close.iloc[0], minute.loc[stamp-MINUTE, 'close'], rtol=0, atol=1e-12)
                and np.isclose(reference.loc[stamp, 'open'], traded.open.iloc[0], rtol=1e-7, atol=1e-10)):
            empty_minute_differences.append(dict(time=str(stamp), minute_open=rebuilt.loc[stamp, 'open'],
                original_5m_open=reference.loc[stamp, 'open'], first_traded_minute=str(traded.index[0]),
                first_traded_open=float(traded.open.iloc[0]), scope='outside_trade_interval'))
            compared.loc[stamp, 'open'] = reference.loc[stamp, 'open']
    assert np.allclose(compared[FIELDS], reference[FIELDS], rtol=1e-7, atol=1e-10), (row.event, '1m/5m OHLCV mismatch')
    return window, dict(minute_bars=len(window), verified_5m_bars=len(rebuilt), gaps=0,
                        empty_minute_open_differences=empty_minute_differences,
                        max_ohlc_abs_error=float(np.abs(rebuilt[FIELDS[:4]]-reference[FIELDS[:4]]).to_numpy().max()))


def draw(frame, row, quality):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({'font.family':'sans-serif', 'font.sans-serif':['Heiti SC', 'Arial Unicode MS'],
                         'axes.unicode_minus':False, 'figure.facecolor':'white', 'axes.facecolor':'white'})
    fig = plt.figure(figsize=(16, 9), dpi=120)
    ax = fig.add_axes([.075, .25, .87, .50])
    origin = frame.index[0]
    at = lambda stamp: (pd.Timestamp(stamp)-origin).total_seconds()/60
    entry, exit_ = pd.Timestamp(row.entry_bar), pd.Timestamp(row.exit_bar)
    e, x = at(entry), at(exit_)
    o, h, lo, c = [frame[k].to_numpy(float) for k in FIELDS[:4]]
    positions = np.arange(len(frame))+.5
    colors = np.where(c >= o, COLORS['up'], COLORS['down'])
    ax.add_collection(LineCollection([[(i,a),(i,b)] for i,a,b in zip(positions,lo,h)], colors=colors, linewidths=.85))
    ax.add_collection(PolyCollection([[(i-.31,a),(i+.31,a),(i+.31,b),(i-.31,b)]
        for i,a,b in zip(positions,np.minimum(o,c),np.maximum(o,c))], facecolors=colors, edgecolors=colors, linewidths=.6))
    zero = o == c
    ax.hlines(o[zero], positions[zero]-.31, positions[zero]+.31, colors=colors[zero], linewidths=.85)
    low, high = min(lo.min(),row.entry,row.exit_price), max(h.max(),row.entry,row.exit_price)
    pad = max((high-low)*.17, high*.0005)
    ax.set_ylim(low-pad, high+pad)
    ax.set_xlim(0, len(frame)+len(frame)*.12)
    exact_exit = int(row.reason) in (11,12) or pd.Timestamp(row.exit_t) == exit_
    end = at(row.exit_t) if exact_exit else x+5
    ax.axvspan(e, e+5, color=COLORS['entry'], alpha=.10, zorder=0)
    if not exact_exit:
        ax.axvspan(x, x+5, color=COLORS['stop'], alpha=.08, zorder=0)
    ax.hlines(row.entry,e,end,color=COLORS['entry'],ls='--',lw=1)
    ax.hlines(row.entry,e,e+5,color=COLORS['entry'],lw=3)
    cap = max((high-low)*.025, high*.0001)
    ax.vlines([e,e+5],row.entry-cap,row.entry+cap,color=COLORS['entry'],lw=1.5)
    if exact_exit:
        ax.scatter([end],[row.exit_price],marker='x',c=COLORS['stop'],s=65,lw=2,zorder=6)
    else:
        ax.hlines(row.exit_price,x,x+5,color=COLORS['stop'],lw=3)
        ax.vlines([x,x+5],row.exit_price-cap,row.exit_price+cap,color=COLORS['stop'],lw=1.5)
    for label, price, color, style in [('入场',row.entry,COLORS['entry'],'--'),
            ('目标',row.target_price,COLORS['target'],':'), ('初始止损',row.stop,COLORS['stop'],':')]:
        if low-pad < price < high+pad:
            ax.hlines(price,e,len(frame),color=color,ls=style,lw=.9)
            ax.text(len(frame)+1,price,label,color=color,fontsize=11,va='center')
    if entry == exit_:
        ax.text(e+2.5,1.025,'E / X 原 5m 区间',ha='center',transform=ax.get_xaxis_transform(),fontsize=11,color=COLORS['ink'])
    else:
        for value, label, color in [(e+2.5,'E 入场区间',COLORS['entry']),
                (end if exact_exit else x+2.5,'X 边界退出' if exact_exit else 'X 退出区间',COLORS['stop'])]:
            ax.text(value,1.025,label,ha='center',transform=ax.get_xaxis_transform(),fontsize=11,color=color)
    ticks = sorted(set(np.linspace(0,len(frame)-1,7).round().astype(int)))
    ax.set_xticks([i+.5 for i in ticks],[frame.index[i].tz_convert('Asia/Shanghai').strftime('%m/%d\n%H:%M') for i in ticks])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:,.6g}'))
    ax.tick_params(labelsize=11,colors=COLORS['muted'])
    ax.grid(axis='y',color='#e4e9ee',lw=.6);ax.set_axisbelow(True)
    for side in ['top','right']:ax.spines[side].set_visible(False)
    for side in ['left','bottom']:ax.spines[side].set_color('#cbd4dc')
    ax.set_ylabel('价格 / USDT',color=COLORS['muted'],fontsize=11)
    when=lambda stamp:pd.Timestamp(stamp).tz_convert('Asia/Shanghai').strftime('%m/%d %H:%M')
    span=lambda stamp:when(stamp)+'–'+(pd.Timestamp(stamp)+FIVE).tz_convert('Asia/Shanghai').strftime('%H:%M')
    side='做多' if row.side == 1 else '做空'
    fig.text(.075,.94,f'spike  #{int(row.sequence):04d}   {row.symbol} · {side}   |   1min',fontsize=22,weight='bold',color=COLORS['ink'])
    result='同一根原 5m 内'+CAUSE[int(row.reason)] if entry == exit_ else CAUSE[int(row.reason)]
    fig.text(.075,.89,f'原账本：{result}   ·   原净收益 {row.net/100:+.2f}%（按名义本金）',fontsize=15,color=COLORS['ink'])
    exit_label=(when(row.exit_t)+(' 收盘时点' if int(row.reason) in (11,12) else '开盘时点')) if exact_exit else span(exit_)
    fig.text(.075,.84,f'E 入场 {fmt(row.entry)}  ·  {span(entry)}      X 退出 {fmt(row.exit_price)}  ·  {exit_label}',fontsize=12,color=COLORS['muted'])
    if quality['empty_minute_open_differences']:
        fig.text(.075,.795,'* 交易区间外存在零成交分钟，1m 聚合首价与原 5m 首价有差异；图中保留真实 1m。',fontsize=10,color=COLORS['muted'])
    fig.text(.075,.18,'每根 K 线 = 真实 1 分钟。E / X 横段和色带表示原 5 分钟成交时窗；盘中具体分钟未知。',fontsize=12,color=COLORS['ink'])
    funded='已纳入' if row.funded_continuous else '未纳入（原资金/名额限制）'
    fig.text(.075,.135,f'原连续资金路径：{funded}。目标 {fmt(row.target_price)} · 初始止损 {fmt(row.stop)} · 历史 BE0。',fontsize=11,color=COLORS['muted'])
    fig.text(.075,.09,'Binance USDT 永续 · 北京时间 · 原 maker 0.02% / taker 0.05%，非目标退出另扣 0.10% 滑点。',fontsize=10.5,color=COLORS['muted'])
    fig.text(.075,.05,'旧 5m 回测账本，仅供复核；已发现评分前视，尚未修复。本次只换 1m 行情展示，未做 1m 成交回测。',fontsize=10.5,color=COLORS['stop'])
    dest=EXP/row.file;dest.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(dest,dpi=120);plt.close(fig)
    return dict(file=row.file,event=row.event,dimensions=[1920,1080],timeframe_minutes=1,
                entry_interval=[str(entry),str(entry+FIVE)],exit_interval=[str(exit_),str(exit_+FIVE)],
                exit_mark='exact_boundary' if exact_exit else '5m_interval',same_5m=entry==exit_)


def render_symbol(job):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    symbol,records=job
    data=pd.DataFrame(records)
    identity=read(A/'exp-monday-limit-reclaim-20261024-v1/symbols'/symbol/'identity.json')['job']
    original,_=load_job(identity)
    minute=minute_frame(symbol,[day for r in data.itertuples() for day in days_for(r)])
    results=[]
    for r in data.itertuples():
        window,quality=verify_frame(minute,original,r)
        results.append({**draw(window,r,quality),**quality})
    return dict(symbol=symbol,charts=results)


def render(workers, preview=False):
    data=pd.read_csv(EXP/'trades.csv')
    if preview:data=data[data.sequence.isin([1,2,15,18,31,62])]
    jobs=[(symbol,group.to_dict('records')) for symbol,group in data.groupby('symbol',sort=True)]
    results=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for i,result in enumerate(pool.map(render_symbol,jobs),1):
            results.append(result)
            if i%10==0 or i==len(jobs):print(f'Rendered {i}/{len(jobs)} symbols',flush=True)
    save(EXP/('preview-validation.json' if preview else 'render-validation.json'),
         dict(symbols=results,charts=sum(len(r['charts']) for r in results)))


def pack():
    from PIL import Image
    data=pd.read_csv(EXP/'trades.csv');validation=read(EXP/'render-validation.json')
    charts=[r for s in validation['symbols'] for r in s['charts']]
    assert len(charts)==len(data)==85 and set(data.file)=={r['file'] for r in charts}
    for row in charts:
        assert row['timeframe_minutes']==1 and row['gaps']==0
        with Image.open(EXP/row['file']) as im:assert im.size==(1920,1080);im.verify()
    dest=EXP/'spike_2026_85orders_1min.zip'
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for file in data.file:z.write(EXP/file,file)
        z.writestr('index.csv',data.to_csv(index=False).encode('utf-8-sig'))
        z.write(EXP/'config.json','config.json')
        z.write(EXP/'render-validation.json','render-validation.json')
    with zipfile.ZipFile(dest) as z:assert z.testzip() is None
    assert dest.stat().st_size<49_000_000
    receipt=dict(file=dest.name,charts=len(charts),bytes=dest.stat().st_size,timeframe_minutes=1,
        same_5m_exits=int((data.entry_i==data.exit_i).sum()),
        same_5m_target_exits=int(((data.entry_i==data.exit_i)&(data.reason==4)).sum()),
        funded_continuous=int(data.funded_continuous.sum()),
        total_minute_bars=sum(c['minute_bars'] for c in charts),
        verified_5m_bars=sum(c['verified_5m_bars'] for c in charts),
        empty_minute_context_differences=[dict(event=c['event'],**d) for c in charts for d in c['empty_minute_open_differences']],
        warnings=['Original score lookahead unresolved','1m display only; original 5m execution intervals preserved'])
    save(EXP/'delivery.json',receipt);print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','preview','render','pack'])
    parser.add_argument('--workers',type=int,default=4);args=parser.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating')
    if args.action=='prepare':prepare()
    elif args.action=='pack':pack()
    else:render(args.workers,args.action=='preview')
