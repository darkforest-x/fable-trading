"""Independent recent-window evaluation of unchanged V2.31 15m trades.

Select entries by their actual close timestamp, never by return or exit time.
Reuse verified native executions because every opportunity is independent,
the end boundary and all trade inputs remain unchanged, and earlier candles
still provide indicator/Monday-range warmup. Require matched controls inside
the same window. Fail instead of reusing a path beyond a changed end boundary.
No new entry filter, parameter search, account compounding or Pine change.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.range_v231_returns import read, describe, FIELDS

CONFIG = ROOT/'experiments/active/exp-range-v231-recent2m-20261021-v1/config.json'


def select_window(matches, candidates, start, end):
    """Use admitted entry close; no outcome columns are accepted or consulted."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    t = pd.to_datetime(matches.entry_time, utc=True)
    selected = matches[(t >= start) & (t < end)].copy()
    base = candidates[candidates.pair_id.isin(selected.pair_id)].copy()
    assert len(base) == 2*len(selected)
    assert not base.duplicated(['pair_id','population']).any()
    ct = pd.to_datetime(base.entry_time,utc=True)
    if not ((ct >= start) & (ct < end)).all():
        raise ValueError('Matched control outside the study window; rematch and replay it')
    for population in ('signal','random'):
        assert set(base.loc[base.population==population,'pair_id']) == set(selected.pair_id)
    return selected, base


def main():
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder and config before generation')
    cfg=json.loads(CONFIG.read_text()); dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Completed recent-window result exists: '+str(dest/'summary.json')); return
    source=ROOT/cfg['source']
    prior=json.loads((source/'summary.json').read_text())
    assert pd.Timestamp(cfg['end']) == pd.Timestamp(prior['end'])
    assert cfg['round_trip_cost'] == prior['cost']
    arms=list(dict.fromkeys(r['arm'] for r in prior['rows']))
    rows=[]; counts={}; concentration=[]; inputs=[]
    for symbol in cfg['symbols']:
        folder=source/symbol
        selected,base=select_window(read(folder/'signal_matches.csv'),read(folder/'candidates.csv'),cfg['start'],cfg['end'])
        out=dest/symbol;out.mkdir(exist_ok=False)
        selected.to_csv(out/'signal_matches.csv',index=False)
        base.to_csv(out/'candidates.csv',index=False)
        counts[symbol]=dict(n=len(selected),longs=int((selected.side==1).sum()),shorts=int((selected.side==-1).sum()))
        inputs.extend(file_stamp(folder/name) for name in ['signal_matches.csv','candidates.csv'])
        for arm in arms:
            path=folder/arm/'trades.csv';lowpath=folder/arm/'sensitivity.csv'
            entries=read(path);entries=entries[entries.pair_id.isin(selected.pair_id)].copy()
            lower=read(lowpath);lower=lower[lower.pair_id.isin(selected.pair_id)].copy()
            expected=base.set_index(['pair_id','population']).sort_index()
            for ledger in [entries,lower]:
                actual=ledger.set_index(['pair_id','population']).sort_index()
                pd.testing.assert_index_equal(actual.index,expected.index)
                np.testing.assert_allclose(actual[FIELDS],expected[FIELDS],rtol=0,atol=1e-8)
                assert (pd.to_datetime(ledger.exit_time,utc=True)<=pd.Timestamp(cfg['end'])).all() if 'exit_time' in ledger else (ledger.exit_i<=ledger.end_i).all()
                np.testing.assert_allclose(ledger.net_r,ledger.gross_r-ledger.fee_r,rtol=0,atol=1e-8)
            result=describe(entries,symbol,arm,'v231_all_keys_recent2m','all',cfg['seed'])
            s=entries[entries.population=='signal']
            result['sensitivity_net_r']=float(lower.loc[lower.population=='signal','net_r'].sum())
            result['boundary_n']=int((s.reason==0).sum()) if arm=='pivot' else int((s.boundary_weight>0).sum())
            rows.append(result)
            inputs.extend([file_stamp(path),file_stamp(lowpath)])
            if arm=='pivot':
                entries.to_csv(out/'pivot_trades.csv',index=False)
                best=s.loc[s.net_r.idxmax()]
                concentration.append(dict(symbol=symbol,pair_id=int(best.pair_id),entry_time=str(best.entry_time),
                    net_r=float(best.net_r),net_r_without_largest=float(s.net_r.sum()-best.net_r),
                    price_return_bp=float(best.net_notional_bp)))
        family=[r for r in rows if r['symbol']==symbol]
        for r,p,b in zip(family,holm([r['p_one_sided'] for r in family]),holm([r['bp_excess_p'] for r in family])):
            r['holm_p_arms']=float(p);r['bp_holm_p_arms']=float(b)
    dump(dest/'summary.json',dict(config=cfg,counts=counts,rows=rows,concentration=concentration,
        inputs=inputs,unchanged_paths_reused=len(rows),
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps([r for r in rows if r['arm']=='pivot'],indent=2))


if __name__=='__main__': main()
