"""Owner-requested September ETH3m trade evidence, with frozen V8 rules.

No features or thresholds change. All indicators consume the immutable full
OKX prefix and closed public-API tail. Tail requests use the repository fetcher
transport, in memory, and an isolated compressed evidence snapshot (never the
production kline cache). Original V8 carries its full serial state into the requested window.
This is ledger reconciliation, not parameter selection or live execution.
"""
import argparse
import gzip
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from src.data.fetch_okx import API, _request
from yoyo.evaluation.spike_eth_martingale_study import read_visible
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v8_lowtf_study import v8_mask, _run
from yoyo.evaluation.spike_recovery_exit import prepare, replay_entry
from yoyo.evaluation.spike_net_recovery_exit import replay_net_entry
from yoyo.evaluation.spike_recovery_cash import eligible

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-eth3m-september-trade-audit-20260914-v1'
OUT = EXP / 'results'
BAR = pd.Timedelta(minutes=3)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    def clean(v):
        if isinstance(v, dict): return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)): return [clean(x) for x in v]
        if isinstance(v, np.generic): return clean(v.item())
        if isinstance(v, float) and not np.isfinite(v): return None
        if isinstance(v, (pd.Timestamp, datetime)): return v.isoformat()
        return v
    path.write_text(json.dumps(clean(value), ensure_ascii=False, indent=2)+'\n')


def config():
    return json.loads((EXP/'config.json').read_text())


def fetch():
    cfg = config()
    for path in [Path(__file__), EXP/'config.json', EXP/'PROJECT_PLAN.md', EXP/'authorization.json']:
        rel = str(path.relative_to(ROOT))
        subprocess.run(['git','cat-file','-e','HEAD:'+rel], check=True, cwd=ROOT)
        subprocess.run(['git','diff','--exit-code','HEAD','--',rel], check=True, cwd=ROOT)
    OUT.mkdir(parents=True, exist_ok=False)
    save(EXP/'holdout_consumption.json', dict(
        started_at=datetime.now(timezone.utc), configuration_exposure_number=1,
        configurations=cfg['arms'], purpose='owner requested fixed-rule per-trade audit; no tuning',
        approved_start=cfg['start'], approved_cutoff=cfg['cutoff'],
        warmup='full existing prefix plus missing same-source continuation before start',
        authorization=json.loads((EXP/'authorization.json').read_text()),
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    source=ROOT/cfg['prefix']
    if sha(source)!=cfg['prefix_sha256']: raise ValueError('prefix hash changed')
    prefix=read_visible(source,cfg['cutoff'])
    left=int(prefix.index[-1].timestamp()*1000)
    # Exclusive upper bar-open bound; no partially closed bar is requested.
    right=int(pd.Timestamp(cfg['cutoff']).floor('3min').timestamp()*1000)
    cursor=right; rows={}; requests=0
    while cursor>left:
        url=API+'?'+urlencode(dict(instId='ETH-USDT-SWAP',bar='3m',limit=300,after=cursor,before=left-1))
        response=_request(url); requests+=1
        if response.get('code')!='0': raise ValueError(str(response))
        page=response.get('data') or []
        if not page: raise ValueError('OKX history ended before prefix overlap')
        stamps=[int(row[0]) for row in page]
        if len(stamps)!=len(set(stamps)) or min(stamps)>=cursor: raise ValueError('pagination did not advance')
        for row in page:
            ts=int(row[0])
            if not left<=ts<cursor or ts>=right: raise ValueError('response outside bounded request')
            if len(row)!=9 or str(row[8])!='1': raise ValueError('unclosed candle or schema changed')
            if ts in rows and rows[ts]!=row: raise ValueError('conflicting duplicate')
            rows[ts]=row
        cursor=min(stamps)
        if requests%10==0: print('OKX pages',requests,'tail rows',len(rows),flush=True)
        if requests>300: raise ValueError('pagination budget exhausted')
    ordered=[rows[k] for k in sorted(rows)]
    tail=pd.DataFrame([[int(r[0])]+[float(x) for x in r[1:6]] for r in ordered],
                      columns=['ts','open','high','low','close','volume'])
    tail.index=pd.to_datetime(tail.pop('ts'),unit='ms',utc=True)
    np.testing.assert_allclose(tail.iloc[0].to_numpy(),prefix.iloc[-1].to_numpy(),rtol=0,atol=1e-9)
    expected=pd.date_range(prefix.index[-1],pd.to_datetime(right,unit='ms',utc=True)-BAR,freq=BAR)
    if not tail.index.equals(expected): raise ValueError('missing or misaligned tail candles')
    with gzip.open(OUT/'okx_tail_evidence.json.gz','wt') as f: json.dump(ordered,f)
    save(OUT/'source_receipt.json',dict(requests=requests,tail_rows=len(tail),overlap_rows=1,
        first=tail.index[0],last=tail.index[-1],last_close=tail.index[-1]+BAR,
        cutoff=cfg['cutoff'],prefix_sha256=sha(source),
        tail_sha256=sha(OUT/'okx_tail_evidence.json.gz'),data_gaps=0,
        endpoint=API,docs='https://app.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks-history',
        cache_mutations=0,raw_candles_written_only_as_isolated_audit_evidence=True))
    print('FETCH COMPLETE',len(tail),str(tail.index[-1]+BAR),flush=True)


def stamp(value):
    return pd.Timestamp(value).tz_convert('Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S')


def enrich(trade, frame, arm, number):
    t=dict(trade)
    t['arm']=arm; t['trade_no']=number
    t['carry_in']=pd.Timestamp(t['entry_time'])<pd.Timestamp(config()['start'])
    t['signal_id']=stamp(frame.index[int(t['signal_i'])]+BAR)
    t['direction']='多' if int(t['side'])==1 else '空'
    t['signal_open_bj']=stamp(t['signal_bar_open'])
    t['signal_confirm_bj']=t['signal_id']; t['entry_bj']=stamp(t['entry_time'])
    is_open=bool(t['censored'])
    exact=bool(t.get('exit_at_open',False)) or str(t['exit_reason']).endswith('_gap') or t['exit_reason']=='opposite_v6_next_open'
    t['exit_bar_open_bj']=stamp(t['exit_time'])
    t['exit_bj_from']=stamp(t['exit_time']+BAR if is_open else t['exit_time'])
    t['exit_bj_to']=stamp(t['exit_time'] if exact else t['exit_time']+BAR)
    t['status']='截止仍持仓，仅收盘估值' if is_open else '已平仓'
    t['cost_r']=.002/float(t['initial_risk_frac'])
    t['price_risk_1u_quantity_eth']=1/float(t['initial_risk'])
    trigger=t.get('protection_trigger_i')
    t['be_trigger_bar_bj']=stamp(frame.index[int(trigger)]) if trigger is not None and pd.notna(trigger) else ''
    # Bounds for the maximum favorable excursion before exit: stopped exit bar
    # may have reached its extreme only AFTER the stop. Never label upper bound realized.
    ei,xi=int(t['entry_i']),int(t['exit_i']); side=int(t['side'])
    before=frame.iloc[ei:xi]
    lowbound=max(0.,float(t['gross_r']))
    if len(before):
        extreme=before.high.max() if side==1 else before.low.min()
        lowbound=max(lowbound,side*(float(extreme)-float(t['entry_price']))/float(t['initial_risk']))
    highbound=lowbound
    if not exact:
        extreme=frame.high.iloc[xi] if side==1 else frame.low.iloc[xi]
        highbound=max(lowbound,side*(float(extreme)-float(t['entry_price']))/float(t['initial_risk']))
    t['favorable_r_lower']=lowbound; t['favorable_r_upper']=highbound
    t['full_initial_stop']=t['exit_reason'] in ['initial_stop','initial_stop_gap']
    return t


def stop_path(trade, frame, raw):
    """Audit original protection using current/prior OHLC/ATR and raw side.

    active_stop is the stop available at this bar's open; next_stop only becomes
    available on the following bar. This does not change the execution result.
    """
    side=int(trade['side']); entry=float(trade['entry_price'])
    risk=float(trade['initial_risk']); protection=float(trade['initial_stop'])
    armed=False; rows=[]
    for i in range(int(trade['entry_i']),int(trade['exit_i'])+1):
        old=protection
        last=i==int(trade['exit_i'])
        if not last or bool(trade['censored']):
            close=float(frame.close.iloc[i]); atr=float(frame.atr.iloc[i])
            armed=armed or side*(close-entry)/risk>=2.
            if armed and np.isfinite(atr) and atr>0:
                price=close-side*4.*atr
                candidate=(np.floor(price/.01) if side==1 else np.ceil(price/.01))*.01
                protection=max(protection,candidate) if side==1 else min(protection,candidate)
        raw_side=1 if raw.long_signal.iloc[i] else (-1 if raw.short_signal.iloc[i] else 0)
        rows.append(dict(trade_no=trade['trade_no'],bar_i=i,open_bj=stamp(frame.index[i]),
                         active_stop=old,next_stop=protection,trail_armed=armed,raw_v6_side=raw_side))
    if not np.isclose(protection,float(trade['protection']),rtol=0,atol=1e-8):
        raise ValueError('original trailing-stop path does not match frozen replay')
    return rows


def run():
    cfg=config(); receipt=json.loads((OUT/'source_receipt.json').read_text())
    if sha(ROOT/cfg['prefix'])!=cfg['prefix_sha256'] or sha(OUT/'okx_tail_evidence.json.gz')!=receipt['tail_sha256']:
        raise ValueError('source hash mismatch')
    prefix=read_visible(ROOT/cfg['prefix'],cfg['cutoff'])
    with gzip.open(OUT/'okx_tail_evidence.json.gz','rt') as f: rows=json.load(f)
    tail=pd.DataFrame([[int(r[0])]+[float(x) for x in r[1:6]] for r in rows],columns=['ts','open','high','low','close','volume'])
    tail.index=pd.to_datetime(tail.pop('ts'),unit='ms',utc=True)
    bars=pd.concat([prefix,tail.iloc[1:]])
    if bars.index.has_duplicates: raise ValueError('duplicate source clock')
    if not np.isfinite(bars.to_numpy()).all(): raise ValueError('nonfinite OHLCV')
    if (bars.high<bars[['open','close','low']].max(axis=1)).any() or (bars.low>bars[['open','close','high']].min(axis=1)).any():
        raise ValueError('invalid OHLC geometry')
    raw,bb,mask=v8_mask(bars,3); frame=features(bars); frame.attrs['minutes']=3
    allowed=mask & ((bars.index+BAR)>=pd.Timestamp(cfg['start'])) & ((bars.index+BAR)<=pd.Timestamp(cfg['cutoff']))
    indices=np.flatnonzero(allowed.to_numpy())
    prepared=prepare(frame,raw)
    _,original=_run(frame,raw,mask,tick=.01,minutes=3,end=pd.Timestamp(cfg['cutoff']))
    original=original.loc[pd.to_datetime(original.exit_time,utc=True)+BAR>=pd.Timestamp(cfg['start'])].copy()
    ledgers={}; ledgers['original']=[enrich(t,frame,'original',n+1) for n,t in enumerate(original.to_dict('records'))]
    opportunities={}; selected={arm:{} for arm in cfg['arms']}
    for t in ledgers['original']: selected['original'][int(t['signal_i'])]=t
    # Cross-check independent entry replay against the original serial engine.
    for t in ledgers['original']:
        independent=replay_entry(prepared,int(t['signal_i']),take_profit_r=None,protection_mode='none')
        for field in ['entry_price','initial_stop','exit_price','gross_r','net_r']:
            if not np.isclose(independent[field],t[field],rtol=0,atol=1e-8): raise ValueError('original entry parity '+field)
        if independent['exit_i']!=t['exit_i'] or independent['exit_reason']!=t['exit_reason']: raise ValueError('original exit parity')
    for arm in cfg['arms'][1:]:
        previous=None; ledger=[]; all_rows=[]
        for i in indices:
            t=replay_net_entry(prepared,int(i),net_take_profit_r=1.,timing=arm,tick=.01)
            if t is None: continue
            t=enrich(t,frame,arm,len(all_rows)+1); all_rows.append(t)
            if eligible(t,previous):
                previous=t; t=dict(t,trade_no=len(ledger)+1); ledger.append(t); selected[arm][int(i)]=t
        ledgers[arm]=ledger; opportunities[arm]=all_rows
    signals=[]
    for no,i in enumerate(indices,1):
        row=dict(signal_no=no,signal_i=int(i),signal_id=stamp(frame.index[i]+BAR),
                 signal_open_bj=stamp(frame.index[i]),direction='多' if prepared.raw_side[i]==1 else '空',
                 close=float(frame.close.iloc[i]),atr=float(frame.atr.iloc[i]))
        for arm in cfg['arms']:
            t=selected[arm].get(int(i))
            row[arm+'_status']=('已开仓#'+str(t['trade_no'])) if t else ('待下一根完整K线' if i+1==len(frame) else '持仓/同bar退出限制，未开仓')
        signals.append(row)
    for arm,ledger in ledgers.items():
        pd.DataFrame(ledger).to_csv(OUT/(arm+'_trades.csv'),index=False,encoding='utf-8-sig')
    readable={
        'trade_no':'序号','signal_confirm_bj':'信号确认_北京时间','entry_bj':'开仓_北京时间',
        'direction':'方向','entry_price':'开仓价','initial_stop':'初始止损价','initial_risk':'1R价格距离',
        'exit_bj_from':'退出时间下界_北京时间','exit_bj_to':'退出时间上界_北京时间',
        'exit_price':'退出价_未平仓为估值价','exit_reason':'退出原因代码','status':'状态',
        'gross_r':'毛收益R_初始价格风险1U时等于U','cost_r':'成本R_固定往返0.2百分比名义本金',
        'net_r':'净收益R_未平仓为假设平仓净估值','favorable_r_lower':'最高浮盈R下界',
        'favorable_r_upper':'最高浮盈R上界','carry_in':'是否9月前开仓'}
    pd.DataFrame(ledgers['original'])[list(readable)].rename(columns=readable).to_csv(
        OUT/'original_trades_zh.csv',index=False,encoding='utf-8-sig')
    pd.DataFrame(signals).to_csv(OUT/'all_v8_signals.csv',index=False,encoding='utf-8-sig')
    for arm,rows in opportunities.items():
        pd.DataFrame(rows).to_csv(OUT/(arm+'_independent_opportunities.csv'),index=False,encoding='utf-8-sig')
    stop_rows=[]
    for trade in ledgers['original']:
        trade['stop_path']=stop_path(trade,frame,raw)
        stop_rows.extend(trade['stop_path'])
    pd.DataFrame(stop_rows).to_csv(OUT/'original_stop_path.csv',index=False,encoding='utf-8-sig')
    earliest=min([pd.Timestamp(cfg['start'])]+[pd.Timestamp(t['entry_time']) for t in ledgers['original']])
    chart=frame.loc[frame.index>=earliest-pd.Timedelta(hours=12),['open','high','low','close','volume','atr']].copy()
    chart.insert(0,'bar_i',frame.index.get_indexer(chart.index)); chart.insert(1,'open_bj',[stamp(t) for t in chart.index])
    chart.to_csv(OUT/'inspection_bars.csv',index_label='open_utc',encoding='utf-8-sig')
    save(OUT/'audit_payload.json',dict(config=cfg,source=receipt,signals=signals,trades=ledgers,
         bars=chart.reset_index(drop=True).to_dict('records')))
    checks=dict(signals=len(signals),counts={a:len(v) for a,v in ledgers.items()},
        closed={a:sum(not t['censored'] for t in v) for a,v in ledgers.items()},
        original_independent_replay_parity=True,tail_gaps=0,full_prefix_bars=len(prefix),
        last_complete_close_bj=stamp(frame.index[-1]+BAR),original_continuous=True,alternatives_start_flat=True,
        capacity_filters=False,parameter_search=False,holdout_configuration_exposure=1,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    save(OUT/'validation.json',checks); print(json.dumps(checks,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['fetch','run'])
    args=parser.parse_args(); fetch() if args.phase=='fetch' else run()
