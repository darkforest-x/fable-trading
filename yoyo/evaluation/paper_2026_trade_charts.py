"""Export every 2026 v3lock/spike historical trade as an annotated candle image.

Owner request 2026-10-27: annual counts/wins/returns, every order chart, ZIPs to
the configured Telegram group. Read saved Nautilus fills and capital ledgers;
never simulate fills, recalculate fees, download bars, or change paper state.
Prices/decisions come from the original worse OHLC/OLHC path. Chart context is
post-trade review, not model input. OHLCV resampling is display-only; original
5m entry detail is retained for long trades. Annual cohorts use UTC entry year.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / 'experiments/active'
EXP = A / 'exp-paper-2026-trade-charts-20261027-v1'
PATHS = A / 'exp-strategy-booklet-paths-20261027-v1'
IDENTITIES = A / 'exp-monday-limit-reclaim-20261024-v1/symbols'
START = pd.Timestamp('2026-01-01', tz='UTC')
STOP = pd.Timestamp('2027-01-01', tz='UTC')
CAUSE = {0: '样本末结算', 1: '兜底止损', 4: '目标止盈', 5: '保护止损',
         11: '确认未收回', 12: '收回不足保护价'}
COLORS = dict(up='#24866c', down='#cb5457', entry='#216da4', target='#8c6d21',
              stop='#a44552', protection='#8755a2', ink='#263747', muted='#647382')


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                              default=lambda v: v.item() if hasattr(v, 'item') else str(v)))


def read(path):
    return json.loads(path.read_text())


def annual_summary(track, rows, ledger):
    """Keep candidate, funded entry cohort, and realized-year accounting distinct."""
    y = rows[rows.cohort == '2026']
    ms = START.value // 1_000_000
    funded = ledger[(ledger.entry_ms >= ms) & (ledger.notional > 0)]
    realized = ledger[(ledger.time_ms >= ms) & (ledger.notional > 0)]
    initial = ledger[ledger.time_ms < ms].equity.iloc[-1]
    final = ledger.equity.iloc[-1]
    eq = np.r_[initial, realized.equity]
    if track == 'v3lock':
        old = read(A/'exp-monday-limit-be-lock-compare-20261027-v1/summary.json')['results']
        period = old['sizing']['v3_be50|linear|L1']['y2026']
        original = old['per_trade']['v3_be50']['y2026']
    else:
        old = read(A/'exp-monday-limit-spike-hw-20261026-v1/summary.json')['results']
        period = old['equity']['规则：分10份×5倍|y2026']
        original = old['periods']['y2026']
    assert len(y) == original['n']
    assert abs(100*(y.net > 1).mean()-original['win_pct']) < 1e-9
    assert abs(y.net.mean()-original['mean_bp']) < 1e-7
    assert abs(initial + realized.pnl.sum() - final) < 1e-6
    return dict(candidates=len(y), wins_net_gt_1bp=int((y.net > 1).sum()),
                flat_abs_net_le_1bp=int((y.net.abs() <= 1).sum()),
                losses_net_lt_minus_1bp=int((y.net < -1).sum()),
                win_pct=100*(y.net > 1).mean(), mean_net_pct=y.net.mean()/100,
                first_entry=str(pd.to_datetime(y.entry_t, utc=True).min()),
                last_exit=str(pd.to_datetime(y.exit_t, utc=True).max()),
                period_restart_medians=period,
                period_restart_return_pct=100*(period['final_multiple']-1),
                continuous_path=dict(funded_entries=len(funded), wins=int((funded.pnl > 0).sum()),
                    win_pct=100*(funded.pnl > 0).mean(), entry_cohort_pnl=funded.pnl.sum(),
                    carryover_exits=len(realized)-len(funded), carryover_pnl=realized.pnl.sum()-funded.pnl.sum(),
                    realized_start_equity=initial, realized_end_equity=final,
                    realized_year_pnl=realized.pnl.sum(), return_pct=100*(final/initial-1),
                    realized_max_drawdown_pct=float(100*(1-eq/np.maximum.accumulate(eq)).max())),
                same_bar_exits=int((y.entry_t == y.exit_t).sum()),
                account_note=read(PATHS/'manifest.json')['accounts'][track]['note'])


def prepare():
    native = pd.read_csv(PATHS/'native-fills.csv.gz')
    source = {'v3lock': native[native.variant == 'v3_be50'].copy(),
              'spike': pd.read_csv(A/'exp-monday-limit-spike-hw-20261026-v1/trades.csv')}
    all_rows, summaries = [], {}
    for track, t in source.items():
        ledger = pd.read_csv(PATHS/f'{track}-ledger.csv.gz')
        funded = ledger[ledger.notional > 0].set_index('trade_id')
        entry = pd.to_datetime(t.entry_t, utc=True)
        carry_ids = set(funded[(funded.entry_ms < START.value//10**6) &
                              (funded.time_ms >= START.value//10**6)].index)
        t = t[((entry >= START) & (entry < STOP)) | t.event.isin(carry_ids)].copy()
        t['cohort'] = np.where(pd.to_datetime(t.entry_t, utc=True) >= START, '2026', 'carryover')
        t['track'] = track
        t['funded_continuous'] = t.event.isin(funded.index)
        t['account_pnl'] = t.event.map(funded.pnl)
        t['account_notional'] = t.event.map(funded.notional)
        t['account_net_pct'] = 100*t.account_pnl/t.account_notional
        parts = []
        run = 'exp-monday-limit-be-lock-20261027-v1' if track == 'v3lock' else 'exp-monday-limit-v3-grid-20261025-v1'
        variant = 'v3_be50' if track == 'v3lock' else 'near_P18'
        for symbol, wanted in t.groupby('symbol', sort=True):
            versions = []
            for path in ('ohlc', 'olhc'):
                f = A/run/path/'symbols'/symbol/'trades.csv.gz'
                d = pd.read_csv(f)
                d = d[d.variant == variant].copy()
                d['event'] = d.trade_id.str.split(':').str[:3].str.join(':')
                d = d[d.event.isin(wanted.event)].set_index('event')
                assert d.index.is_unique and set(d.index) == set(wanted.event)
                d['net'] = d.net_notional_bp - np.where(d.reason == 4, 0., 10.)
                d['native_source'] = str(f.relative_to(ROOT))
                d['selected_path'] = path
                versions.append(d)
            o, l = versions[0], versions[1].reindex(versions[0].index)
            chosen = o.copy()
            worse = l.net < o.net
            chosen.loc[worse, :] = l.loc[worse, :]
            w = wanted.set_index('event')
            assert np.allclose(chosen.net, w.loc[chosen.index, 'net'], atol=1e-7, rtol=0)
            for key, other in [('entry_time','entry_t'), ('exit_time','exit_t')]:
                assert (pd.to_datetime(chosen[key], utc=True) == pd.to_datetime(w.loc[chosen.index, other], utc=True)).all()
            extra = ['entry', 'exit_price', 'stop', 'target_price', 'reclaim_status', 'entry_i', 'exit_i',
                     'native_source', 'selected_path']
            parts.append(w.join(chosen[extra]).reset_index())
        t = pd.concat(parts, ignore_index=True).sort_values(['cohort','entry_t','event'], kind='stable')
        t['sequence'] = t.groupby('cohort').cumcount()+1
        t['file'] = [f'{track}/{"2026" if r.cohort == "2026" else "carryover"}/{int(r.sequence):04d}_{r.symbol}_{pd.Timestamp(r.entry_t).strftime("%Y%m%d_%H%M")}.png'
                     for r in t.itertuples()]
        summaries[track] = annual_summary(track, t, ledger)
        all_rows.append(t)
    rows = pd.concat(all_rows, ignore_index=True)
    assert len(rows) == 920 and rows.file.is_unique
    rows.to_csv(EXP/'trades.csv', index=False)
    save(EXP/'summary.json', dict(accounts=summaries, chart_count=len(rows),
        cost_basis='Stored net: maker 2bp, taker 5bp; separate 10bp slippage on non-target exits. No funding.',
        time_basis='2026 UTC entry cohort; six funded 2025 v3lock entries closed in 2026 are separate.',
        wins='Original signal statistics: net > 1bp; |net| <= 1bp flat. Account wins: actual P&L > 0.',
        capital_basis='Original annual restart medians over 10 v3lock / 20 spike tie orders; continuous original-first-seed path separately, never combined.',
        version_note='Historical Binance universe. Spike BE0, not current +0.17%. In-sample; not live account results.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
        training_eligible=False, production_eligible=False))
    print(json.dumps(summaries, ensure_ascii=False, default=str), flush=True)


def fmt(v):
    return f'{float(v):,.7g}'


def display_bars(frame, minutes):
    if minutes == 5:
        return frame
    x = frame.resample(f'{minutes}min', closed='left', label='left').agg(
        dict(open='first', high='max', low='min', close='last', volume='sum'))
    n = frame.close.resample(f'{minutes}min').count()
    # Keep partial endpoint bars visible and explicitly label display aggregation.
    x['partial'] = n < minutes//5
    return x.dropna(subset=['open'])


def panel(ax, frame, r, minutes, title):
    from matplotlib.collections import LineCollection, PolyCollection
    from matplotlib.ticker import FuncFormatter
    x = np.arange(len(frame)); o, h, lo, c = [frame[k].to_numpy(float) for k in ['open','high','low','close']]
    colors = np.where(c >= o, COLORS['up'], COLORS['down'])
    ax.add_collection(LineCollection([[(i,a),(i,b)] for i,a,b in zip(x,lo,h)], colors=colors, linewidths=.8))
    ax.add_collection(PolyCollection([[(i-.3,a),(i+.3,a),(i+.3,b),(i-.3,b)]
        for i,a,b in zip(x,np.minimum(o,c),np.maximum(o,c))], facecolors=colors, edgecolors=colors, linewidths=.6))
    zero = o == c
    ax.hlines(o[zero], x[zero]-.3, x[zero]+.3, colors=colors[zero], linewidths=.8)
    lower, upper = min(lo.min(),r.entry,r.exit_price), max(h.max(),r.entry,r.exit_price)
    pad = max((upper-lower)*.16, abs(upper)*.0005)
    ax.set_ylim(lower-pad, upper+pad)
    ax.set_xlim(-1, len(frame)+max(2,len(frame)*.14))
    et, xt = pd.Timestamp(r.entry_bar), pd.Timestamp(r.exit_bar)
    step = pd.Timedelta(minutes=minutes)
    def pos(t):
        return int(frame.index.searchsorted(t, side='right'))-1
    ei, xi = pos(et), pos(xt)
    exit_visible = frame.index[0] <= xt < frame.index[-1]+step
    entry_visible = frame.index[0] <= et < frame.index[-1]+step
    right = min(len(frame)-.5,xi+.4)
    ax.axvspan(max(-.5,ei-.4),right,color='#dce9f2',alpha=.48,zorder=0)
    price_lines = [('入场',r.entry,COLORS['entry'],'--'), ('目标',r.target_price,COLORS['target'],':')]
    confirmation = et.floor('60min')+pd.Timedelta(hours=2)
    protection = r.entry*(1+int(r.side)*(.005 if r.track == 'v3lock' else 0))
    protected = r.reclaim_status == 'reclaimed' and pd.Timestamp(r.exit_t) >= confirmation
    for label, price, color, style in price_lines:
        if lower-pad <= price <= upper+pad:
            ax.hlines(price,max(-.5,ei),right,color=color,ls=style,lw=1.1)
    stop_end = min(right, pos(confirmation)) if protected else right
    if lower-pad <= r.stop <= upper+pad:
        ax.hlines(r.stop,max(-.5,ei),stop_end,color=COLORS['stop'],ls='--',lw=1)
    if protected and lower-pad <= protection <= upper+pad:
        ax.hlines(protection,max(-.5,pos(confirmation)),right,color=COLORS['protection'],ls='-.',lw=1.2)
    if entry_visible:
        ax.scatter([ei-.16 if ei == xi else ei],[r.entry],s=85,marker='^' if r.side == 1 else 'v',c=COLORS['entry'],zorder=8)
        ax.annotate('E',xy=(ei,r.entry),xytext=(-12,18 if r.side == 1 else -24),textcoords='offset points',
                    fontsize=12,weight='bold',color=COLORS['entry'])
    if exit_visible:
        ax.scatter([xi+.16 if ei == xi else xi],[r.exit_price],s=75,marker='X',c=COLORS['stop'],zorder=8)
        ax.annotate('X',xy=(xi,r.exit_price),xytext=(8,16 if r.exit_price >= r.entry else -22),
                    textcoords='offset points',fontsize=12,weight='bold',color=COLORS['stop'])
    ticks=sorted(set(np.linspace(0,len(frame)-1,6).round().astype(int)))
    ax.set_xticks(ticks, [frame.index[i].tz_convert('Asia/Shanghai').strftime('%m/%d\n%H:%M') for i in ticks])
    ax.tick_params(labelsize=10,colors=COLORS['muted'])
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v,_:f'{v:,.6g}'))
    ax.grid(axis='y',color='#e4e9ee',lw=.6); ax.set_axisbelow(True)
    for side in ('top','right'): ax.spines[side].set_visible(False)
    for side in ('left','bottom'): ax.spines[side].set_color('#cbd4dc')
    gaps=np.flatnonzero(np.diff(frame.index.asi8) != step.value)
    for i in gaps: ax.axvline(i+.5,c='#a44552',ls=':',lw=.8)
    ax.set_title(title,loc='left',fontsize=12,color=COLORS['ink'],pad=12)
    return dict(timeframe_minutes=minutes,bars=len(frame),gaps=len(gaps),entry_visible=entry_visible,exit_visible=exit_visible,
                partial_display_bars=int(frame['partial'].sum()) if 'partial' in frame else 0)


def draw(frame, r):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Heiti SC','Arial Unicode MS'],
                         'axes.unicode_minus':False,'figure.facecolor':'white','axes.facecolor':'white'})
    recorded_entry,recorded_exit=pd.Timestamp(r.entry_t),pd.Timestamp(r.exit_t)
    step=pd.Timedelta(minutes=5)
    et,xt=frame.index[int(r.entry_i)],frame.index[int(r.exit_i)]
    assert et+step == recorded_entry, (r.event, 'native entry close timestamp mismatch')
    assert recorded_exit in (xt,xt+step), (r.event, 'native exit timestamp mismatch')
    r=SimpleNamespace(**r._asdict(),entry_bar=str(et),exit_bar=str(xt))
    raw=frame.loc[et-step*30:xt+step*12]
    minutes=next(m for m in [5,15,60,240,1440] if (xt-et).total_seconds()/60/m+45 <= 220)
    main=display_bars(raw,minutes)
    fig=plt.figure(figsize=(16,9),dpi=120)
    long=minutes>5
    if long:
        gs=fig.add_gridspec(1,2,width_ratios=[1,1.55],left=.065,right=.97,bottom=.25,top=.77,wspace=.23)
        detail=frame.loc[et-step*30:et+step*30]
        a=panel(fig.add_subplot(gs[0]),detail,r,5,'入场细节 · 原始 5m K线')
        b=panel(fig.add_subplot(gs[1]),main,r,minutes,f'持仓全程 · {minutes}m 展示聚合（端点可不满）')
        panels=[a,b]
    else:
        ax=fig.add_axes([.065,.25,.905,.52])
        panels=[panel(ax,main,r,5,'入场至退出 · 原始 5m K线')]
    assert any(p['entry_visible'] and p['exit_visible'] for p in panels)
    side='做多' if r.side == 1 else '做空'
    cohort='跨年持仓' if r.cohort=='carryover' else '2026'
    fig.text(.065,.94,f'{r.track}  /  {cohort}  #{int(r.sequence):04d}     {r.symbol} · {side}',fontsize=21,weight='bold',color=COLORS['ink'])
    win='盈利' if r.net>1 else '亏损' if r.net < -1 else '近乎持平'
    fig.text(.065,.89,f'{CAUSE[int(r.reason)]}   |   {win} {r.net/100:+.2f}%（扣费后，按入场名义本金）',fontsize=15,
             color=COLORS['up'] if r.net>1 else COLORS['stop'])
    when=lambda t:t.tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M')
    holding=(xt-et).total_seconds()/3600
    duration='同一根 5m K线内' if et == xt else f'约 {holding:.2f} 小时'
    fig.text(.065,.84,f'E 入场 {when(recorded_entry)} · {fmt(r.entry)}       X 退出 {when(recorded_exit)} · {fmt(r.exit_price)}       {duration}',fontsize=12,color=COLORS['muted'])
    protection=r.entry*(1+int(r.side)*(.005 if r.track=='v3lock' else 0))
    extra=f' · EMA排列 {int(r.order)}' if r.track=='spike' else ''
    fig.text(.065,.18,f'评分 {r.score:.4f}{extra}     目标 {fmt(r.target_price)}     初始止损 {fmt(r.stop)}     确认后保护 {fmt(protection)}',fontsize=12,color=COLORS['ink'])
    funded='已纳入连续资金路径' if r.funded_continuous else '未纳入连续资金路径（资金/名额限制；保留信号回测）'
    if r.funded_continuous and abs(r.account_net_pct-r.net/100)>.0001:
        funded+=' · 账户另有强平/保证金封顶调整'
    fig.text(.065,.135,f'{funded}。蓝底为持仓；蓝虚线入场、金点线目标、红虚线初始止损、紫点划线确认后保护。',fontsize=10.5,color=COLORS['muted'])
    historical='spike 历史 BE0；当前模拟为 +0.17%。' if r.track=='spike' else 'v3lock 确认后锁利 +0.50%。'
    fig.text(.065,.09,'Binance历史数据 · 北京时间 · '+historical+'远离画面的价位仅列数值；缺K用竖点线标记。',fontsize=10.5,color=COLORS['muted'])
    fig.text(.065,.05,'手续费 maker 0.02% / taker 0.05%；另扣非目标滑点 0.10%。上方为账本时刻，横轴为K线开始；同K先后采用原回测路径。',fontsize=10,color=COLORS['muted'])
    path=EXP/r.file;path.parent.mkdir(parents=True,exist_ok=True)
    fig.savefig(path,dpi=120);plt.close(fig)
    return dict(file=r.file,event=r.event,track=r.track,cohort=r.cohort,dimensions=[1920,1080],panels=panels,
                entry_bar=str(et),exit_bar=str(xt),native_times_match_source_indices=True)


def render_symbol(item):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    symbol, records=item
    job=read(IDENTITIES/symbol/'identity.json')['job']
    assert job['execution_minutes']==5
    stamps=[]
    for p in job['paths']:
        path=Path(p)
        if not path.exists():raise FileNotFoundError(f'Registered source unavailable: {p}')
        stamps.append(dict(path=p,bytes=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns))
    frame,_=load_job(job)
    rows=[]
    for r in pd.DataFrame(records).itertuples(index=False):
        rows.append(draw(frame,r))
    return dict(symbol=symbol,inputs=stamps,charts=rows)


def render(workers, limit):
    t=pd.read_csv(EXP/'trades.csv')
    if limit:
        # Probe diverse long/short, profit/loss and same-bar cases before the batch.
        t=t.iloc[[0,1,40,100,200,600,835,850,900,919][:limit]]
    jobs=[(s,g.to_dict('records')) for s,g in t.groupby('symbol',sort=True)]
    done=[]
    with ProcessPoolExecutor(workers) as pool:
        futures={pool.submit(render_symbol,j):j[0] for j in jobs}
        for k,f in enumerate(as_completed(futures),1):
            result=f.result();done.append(result)
            if k%10==0 or k==len(jobs):print(f'Rendered {k}/{len(jobs)} symbols, {sum(len(x["charts"]) for x in done)} charts',flush=True)
    save(EXP/('preview-validation.json' if limit else 'render-validation.json'),dict(symbols=done,charts=sum(len(x['charts']) for x in done)))


def pack():
    from PIL import Image
    t=pd.read_csv(EXP/'trades.csv');valid=read(EXP/'render-validation.json')
    assert valid['charts']==len(t)==920
    assert {r['file'] for s in valid['symbols'] for r in s['charts']}==set(t.file)
    for p in t.file:
        with Image.open(EXP/p) as im:
            assert im.size==(1920,1080);im.verify()
    archives=[]
    for track,g in t.groupby('track',sort=True):
        chunks=[];batch=[];size=0
        for r in g.itertuples():
            n=(EXP/r.file).stat().st_size
            if batch and size+n>38_000_000:chunks.append(batch);batch=[];size=0
            batch.append(r.file);size+=n
        if batch:chunks.append(batch)
        for i,files in enumerate(chunks,1):
            dest=EXP/f'{track}_2026_orders_{i:02d}-of-{len(chunks):02d}.zip'
            subset=g[g.file.isin(files)].copy()
            with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
                for file in files:z.write(EXP/file,file)
                z.writestr('index.csv',subset.to_csv(index=False).encode('utf-8-sig'))
                z.write(EXP/'summary.json','summary.json')
            with zipfile.ZipFile(dest) as z:assert z.testzip() is None
            assert dest.stat().st_size<49_000_000
            archives.append(dict(file=dest.name,track=track,part=i,parts=len(chunks),count=len(files),bytes=dest.stat().st_size))
    save(EXP/'delivery.json',dict(charts=len(t),archives=archives,all_png_verified=True,all_zip_crc_verified=True))
    print(json.dumps(archives),flush=True)


def send():
    """Upload only explicit requested charts; verify destination and API acknowledgements."""
    import requests
    from yoyo.notify import _load
    delivery=read(EXP/'delivery.json');assert delivery['charts']==920
    credentials=_load()
    if credentials is None:raise RuntimeError('Telegram configuration unavailable')
    token,chat=credentials;url='https://api.telegram.org/bot'+token+'/'
    try:r=requests.post(url+'getChat',json={'chat_id':chat},timeout=(10,20)).json()
    except Exception as exc:raise RuntimeError('getChat failed: '+type(exc).__name__) from None
    if not r.get('ok'):raise RuntimeError('getChat rejected')
    target=r['result']
    assert target.get('title')=='Yolo均线密集交易系统' and target.get('type') in ('group','supergroup','channel')
    path=EXP/'telegram-receipt.json'
    receipt=read(path) if path.exists() else dict(target_id=target['id'],target_title=target['title'],actions=[],complete=False)
    assert receipt['target_id']==target['id']
    summaries=read(EXP/'summary.json')['accounts']
    for a in delivery['archives']:
        p=EXP/a['file'];assert p.stat().st_size==a['bytes']
        previous=next((r for r in receipt['actions'] if r['file']==a['file']),None)
        if previous:
            if previous['status']=='sent' and previous['bytes']==a['bytes']:continue
            raise RuntimeError('Uncertain prior upload; do not resend automatically')
        row=dict(a,status='pending');receipt['actions'].append(row);save(path,receipt)
        s=summaries[a['track']]
        caption=(f'{a["track"]}｜2026年逐单K线｜第 {a["part"]}/{a["parts"]} 包｜{a["count"]} 张\n'
                 f'全部信号 {s["candidates"]} 笔，胜率 {s["win_pct"]:.2f}%（净收益>1bp），平均每笔净收益 {s["mean_net_pct"]:+.2f}%。\n'
                 f'原资金回测：年初1000U重启，多次同刻成交排序中位数，净收益 {s["period_restart_return_pct"]:+.2f}%。'
                 '图中净收益按入场名义本金；不是账户收益。\n'
                 'maker 0.02% / taker 0.05%；非目标退出另扣0.10%滑点。历史截至9月，非全年/实盘。'
                 +('v3lock另附6笔跨年持仓。' if a['track']=='v3lock' else 'spike历史BE0，当前模拟+0.17%；版本不同。')+
                 '\n全部信号逐张保留，注明连续资金路径是否纳入；每包可独立解压，附index.csv。')
        try:
            with p.open('rb') as h:
                result=requests.post(url+'sendDocument',data={'chat_id':chat,'caption':caption},
                    files={'document':(p.name,h,'application/zip')},timeout=(15,60)).json()
        except Exception as exc:
            row.update(status='unknown',error_type=type(exc).__name__);save(path,receipt)
            raise RuntimeError('Upload response uncertain; no automatic retry') from None
        if not result.get('ok'):
            row.update(status='rejected',error_code=result.get('error_code'));save(path,receipt)
            raise RuntimeError('Telegram rejected upload')
        m=result['result'];doc=m.get('document',{})
        if m['chat']['id']!=target['id'] or doc.get('file_name')!=p.name or doc.get('file_size')!=a['bytes']:
            row['status']='unknown';save(path,receipt);raise RuntimeError('Unexpected upload acknowledgement')
        link=f'https://t.me/c/{str(target["id"]).removeprefix("-100")}/{m["message_id"]}'
        row.update(status='sent',message_id=m['message_id'],message_url=link,sent_at=m['date'])
        save(path,receipt);print(json.dumps({k:row[k] for k in ['file','count','status','message_url']}),flush=True)
    receipt.update(complete=True,sent_images=sum(r['count'] for r in receipt['actions'] if r['status']=='sent'))
    assert receipt['sent_images']==920
    save(path,receipt)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['prepare','render','pack','send'])
    ap.add_argument('--workers',type=int,default=4);ap.add_argument('--limit',type=int,default=0)
    args=ap.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating')
    if args.action=='render':render(args.workers,args.limit)
    else:globals()[args.action]()
