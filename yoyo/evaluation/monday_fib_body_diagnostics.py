"""Describe frozen v8 target distances and loss excursions; never alter exits.

Use the existing OHLC/OLHC excursion auditor with market-close timing. These
are retrospective path descriptions, not a new TP/SL backtest or entry filter.
"""
from __future__ import annotations
import json
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_fib_diagnostics import quantile,path_mfe
from yoyo.evaluation.monday_fib_limits import aligned,OHLC
from yoyo.evaluation.range_framework import ROOT,dump,file_stamp

EXP=ROOT/'experiments/active/exp-btc-monday-fib-body-20261022-v8'
SOURCE=ROOT/'experiments/active/exp-btc-monday-fib1618-okx-20261022-v1'


def run():
    code=['yoyo/evaluation/monday_fib_body_diagnostics.py','yoyo/evaluation/monday_fib_diagnostics.py']
    if subprocess.run(['git','diff','--quiet','HEAD','--',*code],cwd=ROOT).returncode:
        raise RuntimeError('Commit diagnostic builder first')
    cfg=json.loads((EXP/'config.json').read_text())
    ledger=pd.read_csv(EXP/'all_parents.csv',float_precision='round_trip')
    signal=ledger[ledger.population.eq('signal')]
    entry=pd.read_csv(EXP/'entries.csv',float_precision='round_trip')
    rows=entry.merge(signal[['parent_id','exit_i','outcome','net_r','net_bp']],
                     left_on='trade_id',right_on='parent_id',validate='one_to_one')
    rows['tp1_r']=rows.side*(rows.tp1-rows.entry)/rows.risk
    rows['tp1_pct']=100*rows.side*(rows.tp1-rows.entry)/rows.entry
    rows['holding_minutes']=rows.exit_i-rows.entry_i
    frame=aligned(pd.read_csv(SOURCE/'candles.csv.gz',float_precision='round_trip'))
    bars=np.rint(frame[OHLC].to_numpy()/cfg['tick']).astype(np.int64)
    losses=rows[rows.outcome.eq('loss')].copy();records=[]
    for r in losses.itertuples():
        a=path_mfe(bars,r,cfg['tick'],'OHLC','market')
        b=path_mfe(bars,r,cfg['tick'],'OLHC','market')
        assert max(a,b)<r.tp1_r+1e-8,'Loss excursion reached TP1'
        future=frame.iloc[int(r.exit_i)+1:int(r.exit_i)+1441]
        prices=future.high if r.side==1 else future.low
        records.append(dict(parent_id=r.parent_id,mfe_r_ohlc=a,mfe_r_olhc=b,
            mfe_pct_ohlc=100*a*r.risk/r.entry,
            poststop24h_complete=len(future)==1440,
            poststop24h_tp1=bool((r.side*(prices-r.tp1)>=0).any())))
    losses=losses.merge(pd.DataFrame(records),on='parent_id',validate='one_to_one')
    winners=rows[rows.outcome.eq('win')]
    average_win=float(winners.net_r.mean());average_loss=float(-losses.net_r.mean())
    summary=dict(n=len(rows),wins=len(winners),losses=len(losses),
        tp1_pct=quantile(rows.tp1_pct),tp1_r=quantile(rows.tp1_r),
        target_distance_counts={f'at_least_{n}R':int(rows.tp1_r.ge(n).sum()) for n in (2,3,4,5,7,10)},
        loss_mfe_r=quantile(losses.mfe_r_ohlc),loss_mfe_pct=quantile(losses.mfe_pct_ohlc),
        loss_mfe_thresholds=[dict(r=t,ohlc=int(losses.mfe_r_ohlc.ge(t).sum()),
            olhc=int(losses.mfe_r_olhc.ge(t).sum())) for t in (.25,.5,1,2,3)],
        loss_stop_minutes=quantile(losses.holding_minutes),
        stops_within_minutes={str(n):int(losses.holding_minutes.le(n).sum()) for n in (5,15,30,60,240)},
        average_win_r=average_win,average_loss_r=average_loss,
        sample_break_even_win_pct=100*average_loss/(average_loss+average_win),
        actual_win_pct=100*len(winners)/len(rows),
        poststop24h_complete=int(losses.poststop24h_complete.sum()),
        poststop24h_tp1=int(losses[losses.poststop24h_complete].poststop24h_tp1.sum()),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs=[file_stamp(EXP/'all_parents.csv'),file_stamp(EXP/'entries.csv')],
        no_strategy_parameter_change=True,native_replay_performed=False,
        limitations='MFE is before the first stop, excludes entry-minute pre-fill prices; OHLC/OLHC are path assumptions. Post-stop target touches are retrospective and do not prove a wider stop or a closer TP improves expected return. Sample break-even rate uses realized mean winner and loser, not a forecast.')
    rows.to_csv(EXP/'target_distances.csv',index=False)
    losses.to_csv(EXP/'loss_excursions.csv',index=False)
    dump(EXP/'target_distance_diagnostics.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
