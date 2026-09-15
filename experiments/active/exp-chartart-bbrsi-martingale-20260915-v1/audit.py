"""Independent ledger reconciliation, not a replay of strategy decisions."""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pandas as pd

EXP = Path(__file__).resolve().parent


def audit(out):
    manifest=json.loads((out/'manifest.json').read_text())
    for rel,digest in manifest['files'].items():
        assert hashlib.sha256((out/rel).read_bytes()).hexdigest()==digest,rel
    records=json.loads((out/'summary.json').read_text())
    checked=0
    details=[]
    for case in records:
        folder=out/f'{case["minutes"]}m_{case["window"]}'
        unit=pd.read_csv(folder/'unit_trades.csv')
        for a in case['accounts']:
            label=f'm{a["multiplier"]:g}_l{a["margin_leverage"]:g}'
            t=pd.read_csv(folder/f'{label}_trades.csv')
            executed=t.loc[t.accepted]
            if len(executed):
                np.testing.assert_allclose(executed.qty*executed.entry_price,executed.notional,atol=1e-8)
                np.testing.assert_allclose(executed.entry_fee+executed.exit_fee,executed.notional*.002,atol=1e-8)
            settled=t.loc[t.status.isin(['closed','censored','stopped_zero_equity']) & t.accepted]
            np.testing.assert_allclose(settled.gross_pnl-settled.entry_fee-settled.exit_fee,settled.net_pnl,atol=1e-8)
            np.testing.assert_allclose(a['ending_equity'],1000+settled.net_pnl.sum(),atol=1e-7)
            ordinary=settled.loc[settled.status.isin(['closed','censored'])]
            expected=unit.loc[ordinary.source_index.astype(int),'net_return'].to_numpy()*ordinary.notional.to_numpy()
            np.testing.assert_allclose(ordinary.net_pnl,expected,atol=1e-7)
            cash=1000.;losses=0;cycle=0.;started=False;bad_cycles=0
            for r in t.itertuples():
                if pd.isna(r.requested_multiplier):continue
                np.testing.assert_allclose(r.requested_multiplier,a['multiplier']**losses,atol=1e-10)
                if not r.accepted:
                    assert r.status=='stopped_insufficient_margin'
                    assert r.initial_margin+r.entry_fee>cash
                    break
                np.testing.assert_allclose(r.cash_before_entry,cash,atol=1e-7)
                if r.status=='closed':
                    cash+=r.net_pnl
                    if r.net_pnl<0:
                        losses+=1;started=True;cycle+=r.net_pnl
                    elif r.net_pnl>0:
                        if started:
                            bad_cycles+=int(cycle+r.net_pnl<0)
                            cycle=0.;started=False
                        losses=0
                checked+=1
            assert bad_cycles==a['negative_closed_cycles']
            curve=pd.read_csv(folder/f'{label}_curve.csv.gz')
            np.testing.assert_allclose(curve.equity.iloc[-1],a['ending_equity'],atol=1e-7)
            close_peak=np.maximum.accumulate(np.r_[1000.,curve.equity.to_numpy()])
            close_dd=(close_peak-np.r_[1000.,curve.equity.to_numpy()]).max()
            assert close_dd<=a['max_drawdown_amount']+1e-7
            details.append(dict(case=folder.name,account=label,executed=len(executed),closed=a['closed_trades'],passed=True))
    result=dict(scope=out.name,passed=True,executed_checked=checked,accounts=details,
                checks=['all output hashes','independent quantity/cost/PnL reconciliation','terminal-equity conservation',
                        'sizing from settled net outcomes','margin refusals','negative cycles','terminal curve and DD lower bound'])
    (EXP/f'audit_{out.name}.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__':
    for scope in sys.argv[1:] or ['results']:audit(EXP/scope)
