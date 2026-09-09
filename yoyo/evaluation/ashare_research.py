"""Preregistered temporal selection and locked final evaluation of A-share IMACD.

Source: exp-imacd-ashare-daily-long-20260909-v1/PROJECT_PLAN.md. Data through
2021 selects one coordinate at a time; 2022-23 selects among stage endpoints.
The final command refuses to run without a hashed frozen selection. Neither
selection stage reads 2024-25 price rows. All reported entries use causal
indicators, the cash/settlement simulator, and the same execution costs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.ashare_imacd import (
    Costs, Parameters, buy_quantity, indicators, price_limits, prepare_books, randomized_signals, simulate,
)


STAGES = [('stop_atr',[2.,3.,4.]), ('trail_atr',[3.,4.,5.,6.]),
          ('focus_bars',[8,12,18,24]), ('band_atr',[0.,.05,.10,.15]),
          ('quality',[0,1,2]), ('ma_length',[26,34,45,55]),
          ('structure_bars',[5,10,20])]
FOLDS = dict(development=('2020-01-01','2021-12-31'),
             validation=('2022-01-01','2023-12-31'),
             final=('2024-01-01','2025-12-31'))


def save_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False)+'\n')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key(p):
    return hashlib.sha256(json.dumps(asdict(p),sort_keys=True).encode()).hexdigest()[:12]


def input_fingerprint(data):
    """Bind source bytes and cached inputs without interpreting future prices."""
    source=Path(__file__).parent
    return dict(code={name:digest(source/name) for name in ('ashare_data.py','ashare_imacd.py','ashare_research.py')},
                daily={p.name:digest(p) for p in sorted((data/'daily').glob('*.csv'))},
                universe=digest(data/'universe.json'),
                exclusions=digest(data/'exclusions.json') if (data/'exclusions.json').exists() else None)


def read_through(path, end):
    """Stop parsing the chronologically ordered CSV at the allowed date.

    Iterating raw lines avoids materializing later outcome rows in selection.
    Dates are the first column in the committed data-builder CSV contract.
    """
    import io
    with Path(path).open() as stream:
        header = next(stream)
        if header.split(',')[0] != 'date':
            raise ValueError('expected first-column date contract')
        lines = [header]
        for line in stream:
            date = line.split(',',1)[0]
            if date > end:
                break
            lines.append(line)
    return pd.read_csv(io.StringIO(''.join(lines)),dtype={'date':str,'code':str,'isST':str,'tradestatus':str})


def load_frames(data, end):
    manifest = json.loads((data/'universe.json').read_text())
    exclusion_path = data/'exclusions.json'
    excluded = json.loads(exclusion_path.read_text())['codes'] if exclusion_path.exists() else {}
    frames, failures = {}, []
    for code in manifest['codes']:
        path = data/'daily'/f'{code}.csv'
        if code in excluded or not path.exists():
            failures.append(code); continue
        f = read_through(path,end)
        if len(f):
            frames[code] = f
    if not frames:
        raise ValueError('no sourced daily history')
    return frames,failures


def feature_frames(raw,p,start,end):
    result = {}
    for code,f in raw.items():
        featured = indicators(f,p)
        cut = featured.loc[featured.date.between(start,end)].copy()
        if len(cut):
            result[code] = cut
    return result


def run_one(raw,p,fold,out,label,costs=Costs(),write=True):
    start,end = FOLDS[fold]
    begun = time.monotonic()
    frames = feature_frames(raw,p,start,end)
    result = simulate(frames,p,start,end,costs=costs)
    row = dict(label=label,fold=fold,config_id=key(p),parameters=asdict(p),**result['metrics'])
    row['seconds'] = round(time.monotonic()-begun,2)
    if write:
        target = out/fold/label; target.mkdir(parents=True,exist_ok=True)
        result['equity'].to_csv(target/'equity.csv',index=False)
        result['trades'].to_csv(target/'trades.csv',index=False)
        save_json(target/'metrics.json',row)
    print(json.dumps(row,ensure_ascii=False,default=str),flush=True)
    return row,frames,result


def winner(rows):
    eligible = [r for r in rows if r['trades']>=30]
    if not eligible:
        raise ValueError('No candidate has 30 natural exits; selection is inconclusive')
    return sorted(eligible,key=lambda r:(-r['net_return'],r['max_drawdown']))[0]


def select(data,out):
    if (out/'frozen_selection.json').exists():
        raise ValueError('selection is frozen; do not overwrite or retune after final evaluation')
    snapshot=input_fingerprint(data)
    save_json(out/'selection_inputs.json',snapshot)
    baseline = Parameters(); current = baseline
    raw,missing = load_frames(data,FOLDS['development'][1])
    dev,cache = [],{}
    initial,_,_ = run_one(raw,baseline,'development',out,'baseline')
    dev.append(initial); cache[key(baseline)] = initial
    endpoints = [('baseline',baseline)]
    for stage,(field,values) in enumerate(STAGES,1):
        trials=[]
        for value in values:
            p = replace(current,**{field:value}); label=f's{stage}_{field}_{value}'
            if key(p) in cache:
                row = dict(cache[key(p)],label=label,cached_from=cache[key(p)]['label'])
            else:
                row,_,_ = run_one(raw,p,'development',out,label)
                cache[key(p)] = row
            trials.append(row); dev.append(row)
            save_json(out/'development_results.json',dev)
        best=winner(trials); current=Parameters(**best['parameters'])
        endpoints.append((f'stage_{stage}',current))
        print(json.dumps({'stage_selected':stage,'field':field,'value':getattr(current,field),'config':asdict(current)}),flush=True)
    del raw
    raw,missing_val = load_frames(data,FOLDS['validation'][1])
    val,cache=[],{}
    for label,p in endpoints:
        if key(p) in cache:
            row=dict(cache[key(p)],label=label,cached_from=cache[key(p)]['label'])
        else:
            row,_,_=run_one(raw,p,'validation',out,label)
            cache[key(p)]=row
        val.append(row)
    save_json(out/'validation_results.json',val)
    chosen=winner(val)
    if input_fingerprint(data)!=snapshot:
        raise ValueError('research code or input snapshot changed during selection')
    frozen=dict(created_at=datetime.now(timezone.utc).isoformat(),
                source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                selection_rule='development coordinate endpoints; validation net return then drawdown; >=30 natural exits',
                chosen=chosen,baseline=asdict(baseline),
                universe_sha256=digest(data/'universe.json'),
                development_results_sha256=digest(out/'development_results.json'),
                validation_results_sha256=digest(out/'validation_results.json'),
                selection_inputs_sha256=digest(out/'selection_inputs.json'),
                missing_development=missing,missing_validation=missing_val,
                final_period_not_evaluated=True,holdout_consumed=False)
    save_json(out/'frozen_selection.json',frozen)
    (out/'frozen_selection.sha256').write_text(digest(out/'frozen_selection.json')+'\n')
    print(json.dumps({'FROZEN':chosen},ensure_ascii=False),flush=True)


def buy_hold(raw,start,end,costs=Costs(),initial=1_000_000.,index=False,universe_size=None):
    """Equal initial cash weights; raw lot/limit eligibility; adjusted returns.

    Purchase only at the common first market open, keeping unavailable slots
    in cash. Terminal holdings are valuations with exit-cost reserve, not
    exchange fills. CSI300 is a notional non-investable index reference.
    """
    dates=sorted({d for f in raw.values() for d in f.date if start<=d<=end})
    slots = len(raw) if universe_size is None else universe_size
    if slots < len(raw) or slots <= 0 or not dates:
        raise ValueError('invalid frozen universe size or empty benchmark dates')
    slot=initial/slots; values=pd.Series(initial,index=dates,dtype=float)
    invested=0;stale=0
    for code,f in raw.items():
        f=f.loc[f.date.between(start,end)&(f.tradestatus.astype(str)=='1')&(f.volume>0)].set_index('date')
        if dates[0] not in f.index:
            continue
        first=f.loc[dates[0]].to_dict();first['date']=dates[0]
        if not index:
            _,upper=price_limits(first)
            if first['raw_open']>=upper-.005 or str(first['isST'])!='0':
                continue
        reserve=1+costs.commission+costs.slippage+.00002
        factor=first['raw_open']/first['open']
        qty=slot/reserve/first['raw_open'] if index else buy_quantity(slot/reserve/first['raw_open'],1,1e10,first['board'])
        def purchase_cost(quantity):
            value=quantity*first['raw_open']
            return value+costs.fee(value,dates[0],False)+value*costs.slippage if quantity else 0.
        minimum=0.000001 if index else (200 if first['board'].lower()=='star' else 100)
        decrement=1 if index or first['board'].lower()=='star' else 100
        while qty>=minimum and purchase_cost(qty)>slot:
            qty-=decrement
        paid=purchase_cost(qty)
        if paid>slot or qty<minimum:
            continue
        invested+=1
        path=f.close.reindex(dates).ffill()*qty*factor
        last=path.iloc[-1];path.iloc[-1]-=costs.fee(last,dates[-1],True)+last*costs.slippage
        values+=path-paid
        stale+=f.index[-1]!=dates[-1]
    a=values.to_numpy();peak=np.maximum.accumulate(np.r_[initial,a])[1:]
    return dict(net_return=float(a[-1]/initial-1),max_drawdown=float(np.max(1-a/peak)),
                stocks_invested=invested,stale_terminal=stale),pd.DataFrame({'date':dates,'equity':a})


def monthly_excess_test(equity,controls,seed=813,permutations=10000):
    """Calendar-month block sign permutation of strategy-minus-control return.

    The null is zero expected monthly excess, not model classification skill.
    Both price beta and capital/turnover effects may remain in this statistic.
    """
    def monthly(frame):
        s=frame.set_index(pd.to_datetime(frame.date)).equity
        m=s.resample('ME').last()
        return m.pct_change().fillna(m.iloc[0]/1e6-1)
    actual=monthly(equity)
    means=pd.concat([monthly(c) for c in controls],axis=1).mean(axis=1)
    diff=(actual-means).dropna().to_numpy()
    rng=np.random.default_rng(seed)
    null=(rng.choice([-1,1],size=(permutations,len(diff)))*diff).mean(axis=1)
    return dict(months=len(diff),mean_monthly_excess=float(np.mean(diff)),
                p_one_sided=float((1+(null>=diff.mean()).sum())/(permutations+1)),
                seed=seed,permutations=permutations)


def ranking_metrics(trades,seed=817):
    """Fixed -prior12 MA-width score, evaluated only on natural realized trades."""
    from scipy.stats import rankdata
    if trades.empty:
        return {'available':False,'reason':'no completed or valued trades'}
    f=trades.loc[~trades.reason.str.startswith('period_end') & ~trades.reason.str.startswith('terminal_')].dropna(subset=['score','return_net'])
    if len(f)<10:
        return {'available':False,'reason':'fewer than 10 natural exits'}
    y=f.return_net.to_numpy()>0;s=f.score.to_numpy();pos=int(y.sum());neg=len(y)-pos
    auc=float((rankdata(s)[y].sum()-pos*(pos+1)/2)/(pos*neg)) if pos and neg else None
    n=max(1,int(np.ceil(len(f)*.1)));ix=np.argsort(-s,kind='stable')[:n]
    net=f.return_net.to_numpy();gross=f.return_gross.to_numpy()
    rng=np.random.default_rng(seed)
    null=np.array([rng.permutation(net)[:n].mean() for _ in range(10000)])
    return dict(available=True,score='negative prior12 mean six-MA width / ATR',auc=auc,
                natural_trades=len(f),positive_rate=float(y.mean()),top_decile_n=n,
                top_decile_gross=float(gross[ix].mean()),top_decile_net=float(net[ix].mean()),
                top_decile_win=float(y[ix].mean()),all_trades_gross=float(gross.mean()),
                all_trades_net=float(net.mean()),all_trades_win=float(y.mean()),
                permutation_p=float((1+(null>=net[ix].mean()).sum())/10001),
                note='Diagnostic among portfolio-admitted trades; unweighted trade returns, not portfolio return. No score selection.')


def final(data,out,controls=49):
    frozen_path=out/'frozen_selection.json'
    if not frozen_path.exists() or digest(frozen_path)!=(out/'frozen_selection.sha256').read_text().strip():
        raise ValueError('valid frozen selection is required before final outcomes')
    if (out/'final_complete.json').exists():
        raise ValueError('final evaluation already completed; preserve first evaluation')
    frozen=json.loads(frozen_path.read_text());p=Parameters(**frozen['chosen']['parameters'])
    dependencies={data/'universe.json':frozen['universe_sha256'],
                  out/'development_results.json':frozen['development_results_sha256'],
                  out/'validation_results.json':frozen['validation_results_sha256'],
                  out/'selection_inputs.json':frozen['selection_inputs_sha256']}
    for path,expected in dependencies.items():
        if digest(path)!=expected:
            raise ValueError(f'frozen dependency changed: {path}')
    if input_fingerprint(data)!=json.loads((out/'selection_inputs.json').read_text()):
        raise ValueError('research code or daily inputs changed since selection')
    started=out/'final_started.json'
    fingerprint=dict(freeze_sha256=digest(frozen_path),
                     replay_source_sha256=digest(Path(__file__).with_name('ashare_imacd.py')),
                     runner_source_sha256=digest(__file__))
    attempts=[]
    if started.exists():
        previous=json.loads(started.read_text())
        if previous['fingerprint']!=fingerprint:
            raise ValueError('final evaluation inputs changed after first outcome access')
        attempts=previous['attempts']
    attempts.append(datetime.now(timezone.utc).isoformat())
    save_json(started,dict(fingerprint=fingerprint,attempts=attempts,
                         note='Same frozen configuration; repeated invocation is a disclosed recovery attempt, never reselection.'))
    raw,missing=load_frames(data,FOLDS['final'][1])
    chosen,frames,result=run_one(raw,p,'final',out,'selected')
    baseline,_,_=run_one(raw,Parameters(),'final',out,'baseline')
    tight,_,_=run_one(raw,replace(Parameters(),quality=0,stop_atr=1.),'final',out,'tight_reference')
    stress,_,_=run_one(raw,p,'final',out,'double_slippage',Costs(slippage=.001))
    start,end=FOLDS['final']
    universe_size=len(json.loads((data/'universe.json').read_text())['codes'])
    hold,hold_e=buy_hold(raw,start,end,universe_size=universe_size);hold_e.to_csv(out/'final'/'equal_weight_hold.csv',index=False)
    index_raw={'sh.000300':read_through(data/'daily'/'sh.000300.csv',end)}
    index_hold,index_e=buy_hold(index_raw,start,end,index=True);index_e.to_csv(out/'final'/'csi300.csv',index=False)
    random_rows=[];random_equities=[]
    prepared=prepare_books(frames,start,end)
    for i in range(controls):
        r=simulate(frames,p,start,end,signals=randomized_signals(frames,start,end,91000+i),prepared=prepared)
        random_rows.append(dict(seed=91000+i,**r['metrics']));random_equities.append(r['equity'])
        r['equity'].to_csv(out/'final'/f'random_{i:02d}_equity.csv',index=False)
        if i==0:
            r['trades'].to_csv(out/'final'/'random_00_trades.csv',index=False)
        save_json(out/'random_control_results.json',random_rows)
        print(json.dumps({'random_control':i+1,'total':controls,'net_return':r['metrics']['net_return']},ensure_ascii=False),flush=True)
    net=np.array([r['net_return'] for r in random_rows])
    dd=np.array([r['max_drawdown'] for r in random_rows])
    control_stats=dict(n=controls,median_net=float(np.median(net)),mean_net=float(net.mean()),
                       net_05=float(np.quantile(net,.05)),net_95=float(np.quantile(net,.95)),
                       median_drawdown=float(np.median(dd)),
                       randomization_p=float((1+(net>=chosen['net_return']).sum())/(controls+1)),
                       percentile=float(np.mean(net<chosen['net_return'])))
    boards=[]
    for board in sorted({f.board.iloc[0] for f in frames.values()}):
        subset={c:f for c,f in frames.items() if f.board.iloc[0]==board}
        r=simulate(subset,p,start,end)
        r['equity'].to_csv(out/'final'/f'board_{board}_equity.csv',index=False)
        boards.append(dict(board=board,stocks=len(subset),**r['metrics']))
    summary=dict(created_at=datetime.now(timezone.utc).isoformat(),freeze_sha256=digest(frozen_path),
                 selected=chosen,baseline=baseline,tight_reference=tight,double_slippage=stress,
                 equal_weight_hold=hold,csi300=index_hold,matched_random=control_stats,
                 monthly_excess=monthly_excess_test(result['equity'],random_equities),
                 fixed_score_diagnostic=ranking_metrics(result['trades']),boards=boards,
                 missing=missing,holdout_consumed=False,final_evaluation_number=1,
                 invocation_attempts=len(attempts),
                 data_manifest_sha256=digest(data/'universe.json'))
    save_json(out/'final_complete.json',summary)
    print(json.dumps({'FINAL':summary},ensure_ascii=False,default=str),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['select','final'])
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--controls',type=int,default=49)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    if args.mode=='select':
        select(args.data,args.out)
    else:
        if args.controls<49:
            raise ValueError('preregistered minimum is 49 controls')
        final(args.data,args.out,args.controls)


if __name__=='__main__':
    main()
