"""BTC recent-window exit-only comparison with frozen V2.31 15m entries.

Source: the completed all-key recent2m study, 2026-08-21 to 2026-10-21
Shanghai. All15 signals and their15 original matched controls are retained.
No fee, stop, entry, candidate, key or study-boundary changes. The first
comparison changes pivot clock only; the second changes5m exit policy only.
Native STOP_MARKET orders determine structural exit execution. Verification
uses recorded amendments and raw OHLC; no new execution engine is introduced.
No Pine/default change or account-return claim.
"""
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_v231_returns import FIELDS, describe, read
from yoyo.evaluation.range_management_study import holm
from yoyo.layers.l3_backtest.nautilus_independent import confirmed_pivots

CONFIG=ROOT/'experiments/active/exp-btc-v231-exit5m-20261021-v1/config.json'
SOURCE=ROOT/'experiments/active/exp-range-v231-recent2m-20261021-v1'


def verify_paths(frame,rows,audit,strict=False,structural=False,tick=.1):
    """Audit native fills from causal pivot values and recorded stop amendments."""
    high,low,opened=confirmed_pivots(frame,pivot_minutes=5,strict=strict)
    updates=audit.get('protection_updates',[])
    checked=0
    for row in rows.itertuples():
        side=int(row.side);entry_i=int(row.entry_i);exit_i=int(row.exit_i)
        previous_i=entry_i;stop=float(row.stop)
        changes=[u for u in updates if u['trade_id']==row.trade_id]
        for change in changes:
            at=change['bar_i']
            assert entry_i < at < exit_i
            assert change['pivot_open_ns'] == opened[at] >= frame.index[entry_i].value
            pivot=(low if side==1 else high)[at]
            assert abs(change['pivot_price']-pivot)<tick*1e-6
            assert side*(change['stop']-stop)>0
            assert side*(change['close']-change['stop'])>=tick-1e-7
            assert abs(change['previous_stop']-stop)<1e-7
            window=frame.iloc[previous_i+1:at+1]
            assert (window.low>stop+1e-8).all() if side==1 else (window.high<stop-1e-8).all()
            stop=float(change['stop']);previous_i=at;checked+=1
        window=frame.iloc[previous_i+1:exit_i+1]
        touches=window.low<=stop+1e-8 if side==1 else window.high>=stop-1e-8
        if row.reason in (1,8):
            assert touches.iloc[-1] and not touches.iloc[:-1].any()
            opening=float(frame.open.iloc[exit_i])
            expected=min(opening,stop) if side==1 else max(opening,stop)
            assert abs(row.exit_price-expected)<1e-7
            assert (row.reason==8)==bool(changes)
        else:
            assert not touches.any()
            assert abs(row.exit_price-frame.close.iloc[exit_i])<1e-7
            if structural:
                assert row.reason==0 and exit_i==row.end_i
            elif row.reason==7:
                pivot=(high if side==1 else low)[exit_i]
                assert np.isfinite(pivot) and side*(pivot-row.entry)>0
                assert opened[exit_i]>=frame.index[entry_i].value
        if structural:
            assert len(changes)==row.structural_updates
        np.testing.assert_allclose(row.net_r,row.gross_r-.002*row.entry/row.risk,atol=1e-8,rtol=0)
    return dict(native_trades=len(rows),confirmed_amendments=checked,status='passed')


def main():
    cfg=json.loads(CONFIG.read_text());dest=CONFIG.parent
    if (dest/'summary.json').exists():
        print('Completed result exists: '+str(dest/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    source_cfg=json.loads((SOURCE/'config.json').read_text())
    assert source_cfg['round_trip_cost']==cfg['round_trip_cost']==.002
    baseline=read(SOURCE/'BTC/pivot_trades.csv')
    candidates=read(ROOT/cfg['entry_candidates'])
    assert len(baseline)==len(candidates)==30
    assert (candidates.population=='signal').sum()==15
    native=run(CONFIG)
    frame=read(ROOT/cfg['data'])
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    expected=candidates.set_index(['pair_id','population']).sort_index()
    source_signal=baseline[baseline.population=='signal'].set_index('pair_id')
    rows=[];checks=[];comparison=[]
    for arm in ['pivot15m']+[p['name'] for p in cfg['policies']]:
        ledger=baseline if arm=='pivot15m' else read(dest/'native'/arm/'trades.csv')
        actual=ledger.set_index(['pair_id','population']).sort_index()
        pd.testing.assert_index_equal(actual.index,expected.index)
        np.testing.assert_allclose(actual[FIELDS],expected[FIELDS],atol=1e-8,rtol=0)
        if arm!='pivot15m':
            audit=json.loads((dest/'native'/arm/'audit.json').read_text())
            checks.append(dict(arm=arm,**verify_paths(frame,ledger,audit,
                strict=arm.endswith('strict'),structural=arm.startswith('structure'))))
        desc=describe(ledger,'BTC',arm,'v231_15m_entries_exit5m','all',source_cfg['seed'])
        s=ledger[ledger.population=='signal'].set_index('pair_id')
        delta=s.exit_i-source_signal.exit_i
        best=s.net_r.idxmax()
        desc.update(boundary_n=int((s.reason==0).sum()),boundary_net_r=float(s.loc[s.reason==0,'net_r'].sum()),
            wins=int((s.net_r>1e-9).sum()),median_hold_minutes=float(((s.exit_i-s.entry_i)*5).median()),
            exits_earlier=int((delta<0).sum()),exits_later=int((delta>0).sum()),
            exits_unchanged=int((delta==0).sum()),largest_pair=int(best),largest_net_r=float(s.loc[best,'net_r']),
            net_r_without_largest=float(s.net_r.sum()-s.loc[best,'net_r']))
        rows.append(desc)
        if not arm.endswith('strict'):
            for pair,r in s.iterrows():
                b=source_signal.loc[pair]
                comparison.append(dict(pair_id=int(pair),arm=arm,side=int(r.side),entry_time=r.entry_time,
                    entry=float(r.entry),initial_stop=float(r.stop),exit_time=r.exit_time,exit_price=float(r.exit_price),
                    net_r=float(r.net_r),baseline_exit_time=b.exit_time,baseline_exit_price=float(b.exit_price),
                    baseline_net_r=float(b.net_r),delta_r=float(r.net_r-b.net_r),delta_exit_minutes=int(r.exit_i-b.exit_i)*5))
    primary=[r for r in rows if not r['arm'].endswith('strict')]
    for r,p in zip(primary,holm([r['p_one_sided'] for r in primary])):
        r['holm_p_three_policies']=float(p)
    pd.DataFrame(comparison).to_csv(dest/'trade_comparison.csv',index=False)
    dump(dest/'verification.json',dict(checks=checks,unchanged_candidate_rows=30,
        native_positions=sum(c['native_trades'] for c in checks),cached_baseline_rows=len(baseline)))
    dump(dest/'summary.json',dict(start=source_cfg['start'],end=source_cfg['end'],signal_minutes=15,
        cost=.002,rows=rows,entry_keys=source_cfg['entry_keys'],
        semantics='5m 2/2 first profitable pivot at confirmation close versus opposite-pivot stop ratchet; no profit gate or buffer; next-bar activation; native stop first',
        limitations='Known15signal sample; independent original-riskR versus equal-notionalbp, no account compounding, funding/spread/depth. No maximum-profit guarantee or Pine changes.',
        inputs=[file_stamp(ROOT/cfg['data']),file_stamp(ROOT/cfg['entry_candidates']),file_stamp(SOURCE/'BTC/pivot_trades.csv')],
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print(json.dumps([dict(arm=r['arm'],wins=r['wins'],net_r=r['net_r'],random_net_r=r['random_net_r']) for r in rows],indent=2))


if __name__=='__main__': main()
