"""Reconcile saved exit-study ledgers without rerunning OHLC evaluation."""
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.ma_stoch_exit_v2_study import EXP,ROOT
from yoyo.evaluation.ma_shift_stoch_study import dump,sha


def main():
    counts=dict(arms=0,closed_trades=0,fills=0,matched_controls=0)
    for phase in ('dev','recheck'):
        summary=json.loads((EXP/phase/'summary.json').read_text())
        assert not summary['source']['holdout_consumed'] and summary['source']['restricted_price_rows_parsed']==0
        assert pd.Timestamp(summary['source']['last_close'])<=pd.Timestamp('2026-05-01T00:00Z')
        for name,stats in summary['arms'].items():
            counts['arms']+=1
            tr=pd.read_csv(EXP/phase/f'{name}_trades.csv')
            fills=pd.read_csv(EXP/phase/f'{name}_fills.csv')
            curve=pd.read_csv(EXP/phase/f'{name}_curve.csv')
            controls=pd.read_csv(EXP/phase/f'{name}_controls.csv')
            counts['closed_trades']+=len(tr);counts['fills']+=len(fills)
            equity=1000.
            for r in tr.itertuples(index=False):
                part=fills[fills.trade_id==r.trade_id];enter=part[part.kind=='entry'];exits=part[part.kind=='exit']
                assert len(enter)==1 and abs(exits.fraction.sum()-1)<1e-12
                assert abs(part.cost_return.sum()-.002)<1e-12
                gross=(exits.fraction*r.side*(exits.price-r.entry_price)/r.entry_price).sum()
                assert abs(gross-r.gross_return)<1e-12 and abs(gross-.002-r.net_return)<1e-12
                assert abs(r.entry_equity-equity)<1e-8
                equity*=1+gross-.002
                assert pd.Timestamp(r.entry_time)==pd.Timestamp(summary['source']['first_open'])+pd.Timedelta(minutes=5*int(r.entry_i))
                assert r.entry_i==r.signal_i+1 and r.exit_i>=r.entry_i
                assert (pd.to_datetime(exits.bar_open,utc=True)>=pd.Timestamp(r.entry_time)).all()
                if r.initial_risk>0:assert abs(r.net_r-r.net_return*r.entry_price/r.initial_risk)<1e-9
            assert len(tr)==stats['n']
            assert abs(curve.equity.iloc[-1]-stats['final_equity'])<1e-9
            dd=-(curve.equity/curve.equity.cummax()-1).min()*100
            assert abs(dd-stats['mtm_max_drawdown_pct'])<1e-9
            opened_path=EXP/phase/f'{name}_open_positions.csv'
            if stats['open_count']:
                opened=pd.read_csv(opened_path);assert len(opened)==1
                r=opened.iloc[0];part=fills[fills.trade_id==r.trade_id]
                net=part.net_return.sum()+r.remaining*(r.side*(r.marked_price-r.entry_price)/r.entry_price-.001)
                assert abs(net-r.marked_net_return)<1e-12
                equity*=1+net
            assert abs(equity-stats['final_equity'])<1e-8
            if len(controls):
                paired=controls[controls.matched];counts['matched_controls']+=len(paired)
                assert len(paired)==stats['control']['matched']
                assert (paired.signal_i!=paired.control_signal_i).all()
                assert (pd.to_datetime(paired.control_entry_time,utc=True).dt.strftime('%Y-%m-%d')==paired.day).all()
                np.testing.assert_allclose(paired.target_net_return-paired.control_net_return,paired.excess_net_return,atol=1e-12)
                np.testing.assert_allclose(paired.excess_net_return.mean()*1e4,stats['control']['excess_mean_bp'],atol=1e-9)
    dev=json.loads((EXP/'dev/summary.json').read_text());val=json.loads((EXP/'recheck/summary.json').read_text())
    selection=json.loads((EXP/'selection.json').read_text())
    assert set(val['arms'])=={'baseline','stop_2',selection['selected']}
    assert selection['dev_summary_sha256']==sha(EXP/'dev/summary.json')
    assert val['code']==dev['code']==selection['code']
    # Validation's recorded HEAD must already contain the selected policy.
    path=str((EXP/'selection.json').relative_to(ROOT))
    assert subprocess.check_output(['git','show',val['source_commit']+':'+path],cwd=ROOT)==(EXP/'selection.json').read_bytes()
    receipt=dict(status='passed',**counts,scope='Saved ledgers, costs, compounding, final marks, risk units, control pairing and selection commit; no new price scoring',
                 auditor='main Codex; no independently verified lightweight-agent review',holdout_consumed=False,
                 dev_summary_sha256=sha(EXP/'dev/summary.json'),val_summary_sha256=sha(EXP/'recheck/summary.json'),
                 generated_at=pd.Timestamp.now(tz='UTC'))
    dump(EXP/'ledger_audit.json',receipt);print(json.dumps(receipt,ensure_ascii=False,default=str))

if __name__=='__main__':main()
