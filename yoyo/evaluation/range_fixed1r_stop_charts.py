"""Render twenty sampled native 1R-stop trades, without replay or archives.

Source is frozen V2.31 original entries with fixed1R TP, exact wick SL and a
120minute deadline. Select two stopped signals per anniversary year per coin
with a fixed seed, without filtering wick/level/direction or subsequent moves.
Review charts contain exactly60 real15m candles, named touch-time Monday/Fib
levels and actual native fills. A 5m execution inset disambiguates entry-close
and stop bars; future candles remain review context only. No strategy changes.
"""
import argparse
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'experiments/active/exp-range-v231-2h-mfe-20261022-v1'
ORIGINAL=ROOT/'experiments/active/exp-range-v231-fiveyear-20261021-v1'
UNCAPPED=ROOT/'experiments/active/exp-range-v231-fixed1r-fiveyear-20261022-v1'
DEST=ROOT/'artifacts/range_fixed1r_stop20_20261022'
CODE=['yoyo/evaluation/range_fixed1r_stop_charts.py','yoyo/evaluation/btc_annual_signal_landscape.py']
SEED=20261022


def prepare():
    import numpy as np
    import pandas as pd
    from yoyo.evaluation.range_twohour_mfe import load,read
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals
    from yoyo.evaluation.pepe_range_exits import aggregate
    cfg=json.loads((SOURCE/'config.json').read_text())
    original=json.loads((ORIGINAL/'config.json').read_text())
    rng=np.random.default_rng(SEED)
    charts,index,counts=[],[],[]
    for spec in cfg['symbols']:
        coin=spec['symbol']
        ledger=read(SOURCE/coin/'native_trades.csv')
        rows=ledger[(ledger.population=='signal')&(ledger.arm=='fixed_1r')].copy()
        assert rows.pair_id.is_unique and len(rows)==spec['n']
        old=read(UNCAPPED/coin/'trades.csv');old=old[old.population=='signal'].set_index('pair_id')
        assert len(old)==len(rows) and not old.boundary_weight.gt(0).any()
        counts.append(dict(symbol=coin,n=len(rows),stop=int(rows.reason.eq(1).sum()),
            target=int(rows.reason.eq(4).sum()),deadline=int(rows.reason.eq(0).sum()),
            net_losses=int(rows.net_r.le(0).sum()),uncapped_stop=int(old.gross_r.lt(0).sum()),
            uncapped_target=int(old.gross_r.gt(0).sum())))
        selected=[]
        times=pd.to_datetime(rows.entry_time,utc=True)
        for year in range(5):
            begin=pd.Timestamp(cfg['start'])+pd.DateOffset(years=year)
            end=begin+pd.DateOffset(years=1)
            pool=rows[(times>=begin)&(times<end)&rows.reason.eq(1)].sort_values('pair_id')
            assert len(pool)>=2
            chosen=pool.iloc[np.sort(rng.choice(len(pool),size=2,replace=False))].copy()
            chosen['sample_year']=year+1;chosen['stratum_stop_n']=len(pool);selected.append(chosen)
        selected=pd.concat(selected).sort_values(['entry_i','pair_id'])
        source,five,_=load(cfg,spec)
        q=aggregate(five,15);opened=q.index-pd.Timedelta(minutes=15)
        arrow=signals(q).to_numpy()
        grid,names,weeks,_=monday_levels(q,original['ratios'],original['timezone'])
        matches=read(source/'signal_matches.csv').set_index('pair_id')
        for t in selected.itertuples():
            m=matches.loc[t.pair_id]
            i,j,e=(int(m[k]) for k in ['signal_i','touch_i','entry_i']);side=int(t.side)
            assert arrow[i]==side
            assert opened[i]==pd.Timestamp(m.signal_time) and opened[j]==pd.Timestamp(m.touch_time)
            assert q.index[e]==pd.Timestamp(t.entry_time)==five.index[t.entry_i]
            assert t.entry==q.close.iloc[e]==m.entry and t.stop==m.stop
            assert abs(grid[j,names.index(m.key)]-m.level)<1e-8
            b=q.iloc[j];bodylo,bodyhi=min(b.open,b.close),max(b.open,b.close)
            wick=bodylo-b.low if side==1 else b.high-bodyhi
            depth=m.level-b.low if side==1 else b.high-m.level
            assert wick>0 and .1-1e-8<=depth/wick<=1
            assert b.low<=m.level<bodylo if side==1 else bodyhi<m.level<=b.high
            assert t.stop==(b.low if side==1 else b.high)
            assert t.reason==1 and abs(t.exit_price-t.stop)<spec['tick']*1e-6 and abs(t.gross_r+1)<1e-8
            assert old.loc[t.pair_id,'exit_i']==t.exit_i and abs(old.loc[t.pair_id,'gross_r']+1)<1e-8
            assert abs(t.target_price-(t.entry+side*t.risk))<spec['tick']*1.01
            lo=max(0,min(min(i,j)-20,len(q)-60));hi=lo+60
            assert len(q.iloc[lo:hi])==60 and lo<=min(i,j,e)<=max(i,j,e)<hi
            ex=pd.Timestamp(t.exit_time).tz_convert('Asia/Shanghai')
            assert opened[lo]<=ex<=q.index[hi-1]
            assert five.low.iloc[t.exit_i]-1e-8<=t.exit_price<=five.high.iloc[t.exit_i]+1e-8
            n=len(charts)+1
            r=dict(symbol=coin,version=coin+'_V2.31',display_version='V2.31 · 1:1',number=n,total=20,
                pair_id=int(t.pair_id),side=side,signal_i=i,touch_i=j,entry_i=e,
                signal_time=m.signal_time,touch_time=m.touch_time,entry_bar_time=m.entry_bar_time,
                entry_time=m.entry_time,entry=float(t.entry),stop=float(t.stop),target=float(t.target_price),
                key=m.key,level=float(m.level),other_number=None,other_version=None,
                monday_date=str(weeks[j]),monday_low=float(grid[j,0]),monday_high=float(grid[j,1]),
                wick=float(wick),depth=float(depth),penetration=float(depth/wick),bars_before=i-lo,bars_after=hi-i-1,
                review_group='原规则 · 固定1R · 2h上限',separate_entry_callout=True,
                price_decimals=1 if coin=='BTC' else 2,ambiguous=bool(t.ambiguous),
                entry_5m_i=int(t.entry_i),exit_5m_i=int(t.exit_i),
                candles=[dict(index=k,time=opened[k].tz_convert('Asia/Shanghai').isoformat(),
                    ny_day=int(opened[k].tz_convert(original['timezone']).dayofweek),arrow=int(arrow[k]),
                    **{c:float(q.iloc[k][c]) for c in ['open','high','low','close']}) for k in range(lo,hi)],
                levels=[dict(key=name,price=float(grid[j,k])) for k,name in enumerate(names)],
                execution_bars=[dict(index=k,time=(five.index[k]-pd.Timedelta(minutes=5)).tz_convert('Asia/Shanghai').isoformat(),
                    **{c:float(five.iloc[k][c]) for c in ['open','high','low','close']})
                    for k in range(max(0,t.entry_i-2),min(len(five),t.exit_i+3))],
                exit=dict(time=ex.isoformat(),price=float(t.exit_price),reason=1,label='止损',gross_r=float(t.gross_r),
                    net_r=float(t.net_r),outside_window=False,pivot=None))
            charts.append(r)
            index.append(dict(number=n,symbol=coin,pair_id=int(t.pair_id),sample_year=int(t.sample_year),
                stratum_stop_n=int(t.stratum_stop_n),signal_time=m.signal_time,entry_time=m.entry_time,
                exit_time=ex.isoformat(),side=side,key=m.key,level=float(m.level),entry=float(t.entry),
                stop=float(t.stop),target=float(t.target_price),gross_r=float(t.gross_r),net_r=float(t.net_r),ambiguous=bool(t.ambiguous)))
    assert len(charts)==20
    DEST.mkdir(parents=True,exist_ok=True)
    data=dict(charts=charts,counts=counts,seed=SEED,selection='Two random initial-stop exits per anniversary-year per coin;10BTC+10ETH;all selected are also uncapped1R stops',
        source=str(SOURCE.relative_to(ROOT)),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    (DEST/'chart_data.json').write_text(json.dumps(data,ensure_ascii=False,allow_nan=False))
    pd.DataFrame(index).to_csv(DEST/'trade_index.csv',index=False)
    (DEST/'counts.json').write_text(json.dumps(counts,indent=2)+'\n')
    print(json.dumps(dict(counts=counts,selected_ambiguous=sum(r['ambiguous'] for r in index)),ensure_ascii=False),flush=True)


def render():
    from PIL import Image
    from yoyo.evaluation.btc_annual_signal_landscape import render as draw
    data=json.loads((DEST/'chart_data.json').read_text())
    draw(destination=DEST,source_data=data,trade_review=True)
    manifest=json.loads((DEST/'manifest.json').read_text())
    assert len(manifest)==20
    for r,m in zip(data['charts'],manifest):
        assert {r['key'],'0:up','0:down'}<=set(m['labeled_levels']) and m['candle_count']==60
        with Image.open(DEST/m['file']) as im:assert im.size==(2880,1620)
    (DEST/'validation.json').write_text(json.dumps(dict(count=20,btc=10,eth=10,seed=SEED,counts=data['counts'],
        all_actual_native_stops=True,uncapped_stop_parity=True,all60real15m_bars=True,all_actual_5m_insets=True,
        named_matched_key_and_both_monday_bounds=True,all_fill_price_time_and_wick_checks=True,
        text_within_canvas=True,no_archives_created=True,training_eligible=False,production_eligible=False),indent=2)+'\n')
    print('Rendered20individualPNG; no archive',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['prepare','render'])
    args=parser.parse_args()
    for name in CODE:
        subprocess.run(['git','ls-files','--error-unmatch',name],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit renderer before generating')
    globals()[args.mode]()
