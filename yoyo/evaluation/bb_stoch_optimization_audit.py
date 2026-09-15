"""Reconcile saved fills and curves without raw prices or new replay outcomes."""
import gzip
import json
import math
import subprocess

import numpy as np

from yoyo.evaluation.bb_stoch_optimization import EXP, ROOT, require_committed
from yoyo.evaluation.eth_bb_stoch_study import save


def main():
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    require_committed(ROOT/'yoyo/evaluation/bb_stoch_optimization_audit.py',head)
    selection=json.loads((EXP/'selection.json').read_text())
    records=[]
    checked=0
    for stage in ['development','recheck']:
        summary=json.loads((EXP/f'{stage}_summary.json').read_text())
        curves=np.load(EXP/f'{stage}_selected_curves.npz',allow_pickle=False)
        with gzip.open(EXP/f'{stage}_ledger.jsonl.gz','rt') as source:
            for line in source:
                if not any(line.startswith('{"id":"'+key+'"') for key in selection['finalists']): continue
                item=json.loads(line);key=item['id']
                for mode,ledger in item['modes'].items():
                    for row in ledger['actual']+ledger['controls']:
                        fills=row['fills'];entry=fills[0]['price'];left=1.-math.fsum(f['qty'] for f in fills[1:])
                        fees=math.fsum(f['price']*f['qty']*.001 for f in fills)
                        gross=math.fsum(row['side']*(f['price']-entry)*f['qty'] for f in fills[1:])+left*row['side']*(row['exit_price']-entry)
                        assert abs(fees-row['fees'])<1e-7 and abs(gross-row['gross_pnl'])<1e-7
                        assert abs(gross-fees-row['net_pnl'])<1e-7
                        checked+=1
                    actual=ledger['actual'];natural=[r for r in actual if not r['censored']]
                    stat=summary[key]['modes'][mode]['stats']; equity=summary[key]['modes'][mode]['equity']
                    pnl=[r['net_pnl'] for r in natural]
                    assert len(natural)==stat['natural'] and sum(p>0 for p in pnl)==stat['wins']
                    cash_pf=math.fsum(p for p in pnl if p>0)/-math.fsum(p for p in pnl if p<0)
                    assert abs(cash_pf-stat['cash_profit_factor'])<1e-10
                    endpoint=math.fsum(r['net_pnl']-(1.-math.fsum(f['qty'] for f in r['fills'][1:]))*r['exit_price']*.001 for r in actual)
                    curve=curves[key+'__'+mode]
                    dd=float((np.maximum.accumulate(curve)-curve).max())
                    assert abs(endpoint-equity['net_liquidation_mark_usdt'])<1e-7
                    assert abs(curve[-1]-curve[0]-endpoint)<1e-7 and abs(dd-equity['close_mtm_drawdown_usdt'])<1e-7
                    current=longest=0
                    for p in pnl:
                        current=current+1 if p<0 else 0;longest=max(longest,current)
                    assert longest==stat['max_loss_streak']
                    records.append(dict(stage=stage,id=key,path=mode,natural=len(natural),wins=stat['wins'],
                        natural_net=math.fsum(pnl),net_liquidation_mark=endpoint,cash_pf=cash_pf,
                        close_mtm_drawdown=dd,max_loss_streak=longest))
    assert len(records)==2*2*len(selection['finalists'])
    save(EXP/'accounting_audit.json',dict(passed=True,audit_commit=head,checked_events=checked,records=records,
        scope='Saved fills and curves only; no price-source access, new replay or reselection.',
        reviewer='Primary agent separately reconciled stored fills; delegated audit was stopped without a completed report.',
        limitations=['No native TradingView broker ledger parity','All periods previously exposed']))
    print('Accounting audit passed:',checked,'saved events')


if __name__=='__main__':
    main()
