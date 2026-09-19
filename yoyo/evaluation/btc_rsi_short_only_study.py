"""One-variable short-only replay of the frozen BTC RSI1h/six-MA5m study.

Only long entry admission is disabled. Keep long diamond events so they still
invalidate pending short setups, preserving the original causal state machine.
All market data, hourly stops, 3R barriers, time boundaries and 20bp costs are
identical to the parent experiment; the new stream removes long occupancy.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_sixma import prepare, simulate
from yoyo.evaluation.btc_rsi_sixma_study import (
    START, SPLIT, END, controls, control_summary, describe, dump, grouped_rows,
    mark_curve, phase_masks, sha,
)

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT/'experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1'
EXP = ROOT/'experiments/active/exp-btc-rsi1h-sixma5m-short-only-20260920-v1'


def short_only_context(ctx):
    """Disable long entries only; preserve OHLC and both hourly event streams."""
    short = dict(ctx)
    short['sixma_long'] = np.zeros_like(ctx['sixma_long'], dtype=bool)
    return short


def run(out):
    if out.exists():
        raise ValueError('immutable output directory exists')
    builders=['yoyo/evaluation/'+name+'.py' for name in
              ['btc_rsi_short_only_study','btc_rsi_sixma','btc_rsi_sixma_study',
               'parabolic_rsi_sar','spike_v6_bb_squeeze']]
    for rel in builders:
        if subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)!=(ROOT/rel).read_bytes():
            raise ValueError('commit before scoring: '+rel)
    data=PARENT/'data/okx_btc_usdt_swap_5m.csv.gz'
    old_summary=json.loads((PARENT/'results/run_v2/summary.json').read_text())
    assert sha(data)==old_summary['data_sha256']
    frame=pd.read_csv(data,index_col='open_time',parse_dates=True)
    frame.index=pd.to_datetime(frame.index,utc=True)
    original=prepare(frame)
    ctx=short_only_context(original)
    assert not ctx['sixma_long'].any()
    np.testing.assert_array_equal(ctx['diamond_side'],original['diamond_side'])
    np.testing.assert_array_equal(ctx['sixma_short'],original['sixma_short'])
    result=simulate(ctx,START,END,arm='sixma')
    tr=result['trades'];opened=result['open_positions']
    assert tr.side.eq(-1).all() and opened.side.eq(-1).all()
    if len(opened):
        opened['terminal_mark_price']=float(frame.close.iloc[-1])
        opened['terminal_mark_time']=END
        opened['marked_net_return']=opened.side*(opened.terminal_mark_price-opened.entry_price)/opened.entry_price-.002
        opened['marked_net_r']=opened.marked_net_return/opened.risk_pct
    out.mkdir(parents=True)
    for key,value in result.items():
        value.to_csv(out/f'short_only_{key}.csv',index=False)
    c=controls(ctx,tr,'short_only',out)
    curve=mark_curve(ctx,result)
    curve.to_csv(out/'short_only_marked_curve.csv.gz',index=False,compression='gzip')
    stats={}
    for phase,mask in phase_masks(tr).items():
        sub=tr.loc[mask]
        stats[phase]=dict(**describe(sub),control=control_summary(c.loc[c.parent_trade_id.isin(sub.trade_id)]))
    old=pd.read_csv(PARENT/'results/run_v2/sixma_trades.csv')
    old=old.loc[old.side==-1].copy()
    old['diamond_close_time']=pd.to_datetime(old.diamond_close_time,utc=True)
    common=old.merge(tr,on='diamond_close_time',suffixes=('_old','_new'))
    for col in ['entry_i','exit_i','side','entry_price','exit_price','stop_price','target_price','gross_r','net_r']:
        np.testing.assert_allclose(common[col+'_old'].astype(float),common[col+'_new'].astype(float),atol=1e-10,rtol=1e-12)
    added=tr.loc[~tr.diamond_close_time.isin(old.diamond_close_time)]
    removed=old.loc[~old.diamond_close_time.isin(tr.diamond_close_time)]
    added.to_csv(out/'added_shorts.csv',index=False)
    removed.to_csv(out/'removed_shorts.csv',index=False)
    pd.DataFrame(grouped_rows(tr,c,'short_only')).to_csv(out/'grouped_metrics.csv',index=False)
    summary=dict(start=START,end_exclusive=END,split=SPLIT,
        change='disable long entry only; long diamonds retain pending invalidation',
        old_short_subgroup=old_summary['arms']['sixma']['stats']['short'],stats=stats,
        open_positions=len(opened),max_5m_close_marked_drawdown_r=float(curve.drawdown_r.max()),
        terminal_marked_net_r=float(curve.marked_net_r.iloc[-1]),
        membership=dict(shared_closed=len(common),added_closed=len(added),removed_closed=len(removed),
                        shared_fill_parity=True,added_net_r=float(added.net_r.sum()),removed_net_r=float(removed.net_r.sum())),
        data_sha256=sha(data),builder_sha256={r:sha(ROOT/r) for r in builders},
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        generated_at=pd.Timestamp.now(tz='UTC'),training_eligible=False,production_eligible=False)
    dump(out/'summary.json',summary)
    dump(out/'manifest.json',{str(p.relative_to(out)):dict(sha256=sha(p),bytes=p.stat().st_size)
                              for p in sorted(out.iterdir()) if p.is_file()})
    print(json.dumps({'all':stats['all'],'membership':summary['membership'],'open':len(opened)},default=str),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=EXP/'results/run_v1')
    run(p.parse_args().output)
