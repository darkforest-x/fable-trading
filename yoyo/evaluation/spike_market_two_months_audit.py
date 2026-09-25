"""Independent ledger arithmetic and extreme-price checks for the all-market slice.

No trading decisions or features. Standard-library CSV reductions independently
verify group counts, net returns, event clocks and serial occupancy. Extreme
closed trades are then checked against immutable source OHLCV. Candle extrema
are descriptive outcomes only; no future information enters a decision rule.
"""
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import subprocess

EXP=Path('experiments/active/exp-spike-market-two-months-20260925-v1')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    with gzip.open(path,'rt') as f:return list(csv.DictReader(f))


def truth(value):
    return str(value).lower()=='true'


def main():
    own=Path(__file__).resolve().relative_to(Path.cwd())
    if subprocess.check_output(['git','show',f'HEAD:{own}'])!=own.read_bytes():raise ValueError('Commit audit builder first')
    cfg=json.loads((EXP/'config.json').read_text());root=EXP/'run_v1'
    manifest=json.loads((root/'manifest.json').read_text())
    if not manifest['complete'] or manifest['errors'] or manifest['subset']:raise ValueError('Incomplete replay')
    start=datetime.fromisoformat(cfg['start'].replace('Z','+00:00'));end=datetime.fromisoformat(cfg['end'].replace('Z','+00:00'))
    groups=defaultdict(list);status_counts=defaultdict(lambda:defaultdict(int));seen=set();violations=[];all_closed=[]
    def require(ok,message):
        if not ok:violations.append(message)
    for receipt in manifest['receipts']:
        folder=root/'streams'/f"{receipt['symbol']}_{receipt['minutes']}m"
        require(json.loads((folder/'receipt.json').read_text())==receipt,f'receipt mismatch {folder}')
        for name,digest in receipt['files'].items():require(sha(folder/name)==digest,f'hash {folder/name}')
        trades=read(folder/'trades.csv.gz');statuses=read(folder/'statuses.csv.gz');controls=read(folder/'controls.csv.gz')
        cmap={r['trade_key']:r for r in controls};last={}
        require(len(cmap)==len(controls),f'control duplicates {folder}')
        for status in statuses:status_counts[(status['arm'],int(status['timeframe_min']))][status['status']]+=1
        for r in trades:
            key=r['trade_key'];require(key not in seen,f'duplicate {key}');seen.add(key)
            entry=datetime.fromisoformat(r['entry_time']);exit_=datetime.fromisoformat(r['exit_time'])
            require(start<=entry<end and exit_>=entry,f'clock bounds {key}')
            require(entry==datetime.fromisoformat(r['signal_close']),f'next-open time {key}')
            require(int(r['entry_i'])==int(r['signal_i'])+1,f'next-open index {key}')
            require(int(r['signal_i'])>=last.get(r['arm'],-1),f'serial overlap {key}')
            last[r['arm']]=int(r['exit_i'])
            r['_censored']=truth(r['censored']);groups[(r['arm'],int(r['timeframe_min']))].append(r)
            if r['_censored']:continue
            risk=float(r['initial_risk']);ep=float(r['entry_price']);xp=float(r['exit_price']);side=int(r['side'])
            gross=side*(xp-ep)/ep;net=gross-.002
            require(risk>0 and math.isclose(risk/ep,float(r['initial_risk_frac']),rel_tol=1e-9),f'risk {key}')
            require(math.isclose(net,float(r['net_return']),abs_tol=1e-10),f'net return {key}')
            require(math.isclose(net/(risk/ep),float(r['net_r']),rel_tol=1e-9,abs_tol=1e-9),f'net R {key}')
            require(float(r['mfe_known_r'])>=-1e-12 and float(r['mfe_upper_r'])+1e-9>=float(r['mfe_known_r']),f'MFE ordering {key}')
            c=cmap.get(key);require(c is not None,f'missing control {key}')
            if c and truth(c['matched']):
                ce=datetime.fromisoformat(c['control_signal_close']);cx=datetime.fromisoformat(c['control_exit_time'])
                require(start<=ce<=cx<end,f'control time bounds {key}')
                require(int(c['side'])==side and c['symbol']==r['symbol'],f'control stratum {key}')
                require(math.isclose(float(c['target_net_return']),net,abs_tol=1e-10),f'control target {key}')
                r['_control_net_return']=float(c['control_net_return'])
            all_closed.append(r)
    totals=[]
    for (arm,minutes),rows in sorted(groups.items()):
        closed=[r for r in rows if not r['_censored']];net=[float(r['net_r']) for r in closed]
        matched=[r for r in closed if '_control_net_return' in r]
        totals.append({'arm':arm,'timeframe_min':minutes,'trades':len(rows),'closed':len(closed),
            'censored':len(rows)-len(closed),'wins':sum(v>0 for v in net),'losses':sum(v<0 for v in net),
            'sum_net_r':math.fsum(net),'mean_net_bp':1e4*math.fsum(float(r['net_return']) for r in closed)/len(closed),
            'mfe_known_mean_r':math.fsum(float(r['mfe_known_r']) for r in closed)/len(closed),
            'net_float_then_loss':sum(float(r['mfe_known_r'])>.002/float(r['initial_risk_frac']) and float(r['net_r'])<0 for r in closed),
            'matched_n':len(matched),'matched_excess_bp':1e4*math.fsum(float(r['net_return'])-r['_control_net_return'] for r in matched)/len(matched) if matched else None,
            'statuses':dict(status_counts[(arm,minutes)])})
    # Check both risk-normalized and actual-price extremes, never just a pretty winner.
    selected={}
    for field,reverse,count in [('net_r',True,4),('net_return',True,4),('net_r',False,3)]:
        for r in sorted(all_closed,key=lambda x:float(x[field]),reverse=reverse)[:count]:selected[r['trade_key']]=r
    import pandas as pd
    data_manifest=json.loads((Path(cfg['data_dir'])/'manifest.json').read_text())
    inputs={r['symbol']:r for r in data_manifest['streams']};extremes=[]
    for symbol in sorted({r['symbol'] for r in selected.values()}):
        source=inputs[symbol];require(sha(source['path'])==source['sha256'],f'raw source {symbol}')
        raw=pd.read_csv(source['path']);raw.index=pd.to_datetime(raw.ts,unit='ms',utc=True)
        for r in [x for x in selected.values() if x['symbol']==symbol]:
            e,z=pd.Timestamp(r['entry_time']),pd.Timestamp(r['exit_time']);minutes=int(r['timeframe_min'])
            ep,xp,risk,side=float(r['entry_price']),float(r['exit_price']),float(r['initial_risk']),int(r['side'])
            require(math.isclose(raw.loc[e,'open'],ep,rel_tol=1e-10),f'raw entry {r["trade_key"]}')
            exit_bar=raw[(raw.index>=z)&(raw.index<z+pd.Timedelta(minutes=minutes))]
            require(exit_bar.low.min()-1e-9<=xp<=exit_bar.high.max()+1e-9,f'raw exit {r["trade_key"]}')
            held=raw[(raw.index>=e)&(raw.index<z)]
            favorable=(held.high.max()-ep)/risk if side==1 else (ep-held.low.min())/risk
            mfe=max(0.,side*(float(raw.loc[z,'open'])-ep)/risk,0. if held.empty else favorable)
            require(math.isclose(mfe,float(r['mfe_known_r']),rel_tol=1e-9,abs_tol=1e-8),f'raw MFE {r["trade_key"]}')
            extremes.append({k:r[k] for k in ['trade_key','symbol','arm','timeframe_min','entry_time','exit_time','entry_price','initial_stop','exit_price','exit_reason','net_r','net_return','mfe_known_r']})
    output={'builder_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'builder_sha256':sha(own),'run_manifest_sha256':sha(root/'manifest.json'),
        'method':'Independent standard-library reductions; raw OHLCV price/clock/MFE extreme checks',
        'groups':totals,'extreme_trades':extremes,'violations':violations,'passed':not violations,
        'native_pine_parity_verified':False,'production_eligible':False}
    (EXP/'audit_v1.json').write_text(json.dumps(output,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'trades':len(seen),'groups':totals,'extremes_checked':len(extremes),'violations':violations[:20]},ensure_ascii=False))
    if violations:raise ValueError('Independent audit failed')


if __name__=='__main__':main()
