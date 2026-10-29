"""Compare post-close entry directly with the owner's displayed +403.21% path.

The named baseline is the immutable spike booklet ledger, not the newer causal
paper-rule projection. Reuse original BE0 P18/B18 Nautilus close fills, the
original10slot5x/19percent-MAE allocator, symbol/time stable order and seed20261102.
No new fills, feature fitting, parameter search or candle replay. Saved per-path
MAE is reused only after entry price and entry/exit index equality is asserted.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_limit_paper_accounts import committed
from yoyo.evaluation.monday_limit_spike_hw import slot_replay
from yoyo.evaluation.strategy_booklet_paths import Trace

A = ROOT/'experiments/active'
EXP = A/'exp-spike-booklet-close-compare-20261029-v1'


def clocked(t):
    t = t.copy()
    for key in ('entry_t','exit_t'):
        t[key] = pd.to_datetime(t[key],utc=True).dt.as_unit('ns')
        t[key.replace('_t','_ns')] = t[key].astype('int64')
    return t.sort_values(['symbol','entry_ns'],kind='stable').reset_index(drop=True)


def account(t, cfg):
    recorder = Trace(pd.Timestamp(cfg['start']).value)
    result,_ = slot_replay(clocked(t),cfg['slots'],cfg['leverage'],cfg['liquidation_proxy_pct'],cfg['seed'],trace=recorder)
    ledger = pd.DataFrame(recorder.rows)
    np.testing.assert_allclose(ledger.equity,1000+ledger.pnl.cumsum(),atol=1e-8,rtol=1e-12)
    funded = ledger[ledger.notional > 0]
    gains,losses = funded.pnl[funded.pnl > 0].sum(),-funded.pnl[funded.pnl < 0].sum()
    result.update(initial_equity=1000.,ending_equity=float(ledger.equity.iloc[-1]),
        return_pct=float((result['final_multiple']-1)*100),profit_factor=float(gains/losses),
        independent_fills=len(t),independent_mean_bp=float(t.net.mean()))
    return result,ledger


def run():
    config = EXP/'config.json'; cfg = json.loads(config.read_text())
    committed('yoyo/evaluation/spike_booklet_close_compare.py','yoyo/evaluation/monday_limit_spike_hw.py',
              'yoyo/evaluation/strategy_booklet_paths.py',str(config.relative_to(ROOT)))
    source = A/cfg['baseline_study']; booklet = A/cfg['booklet']
    baseline = pd.read_csv(source/'trades.csv')
    old_result,old_ledger = account(baseline,cfg)
    saved = pd.read_csv(booklet/'spike-ledger.csv.gz')
    # Trace stores reason strings; CSV inference reads the same codes as floats.
    old_ledger['reason'] = pd.to_numeric(old_ledger.reason,errors='raise')
    saved['reason'] = pd.to_numeric(saved.reason,errors='raise')
    pd.testing.assert_frame_equal(old_ledger,saved,check_dtype=False,atol=1e-8,rtol=1e-11)
    manifest = json.loads((booklet/'manifest.json').read_text())['accounts']['spike']
    for key in ('final_multiple','max_drawdown_pct','liquidations','skipped','taken','win_pct'):
        np.testing.assert_allclose(old_result[key],manifest['allocator_result'][key],atol=1e-10,rtol=1e-11)
    admission = pd.read_csv(A/cfg['cached_clocks']/'worse_opportunity.csv.gz')
    admission = admission[(admission.variant == 'close_now') & (admission.control_id == 0)]
    assert set(admission.event) == set(baseline.event)
    selected = admission[admission.filled]
    parts = {'ohlc':[],'olhc':[]}; receipts=[]; checks=0
    for symbol,g in selected.groupby('symbol'):
        for path in parts:
            file = A/cfg['close_be0_study']/path/'symbols'/symbol/'trades.csv.gz'
            t = pd.read_csv(file)
            t['event'] = t.trade_id.str.split(':').str[:3].str.join(':')
            t = t[t.event.isin(g.event)&t.variant.isin(['P18','B18'])].set_index('event')
            cache_file = A/cfg['cached_clocks']/'symbols'/symbol/path/'trades.csv.gz'
            cached = pd.read_csv(cache_file)
            cached = cached[(cached.variant == 'close_now')&(cached.control_id == 0)].set_index('event').reindex(t.index)
            assert len(t)==len(g) and t.index.is_unique
            # Different BE offsets can change the MAE window. Never assume parity.
            for field in ('side','entry','entry_i','exit_i','stop','target_price'):
                np.testing.assert_allclose(t[field],cached[field],atol=1e-12,rtol=1e-12)
            checks += len(t)
            t['symbol'] = symbol
            t['entry_t'] = cached.entry_time
            t['exit_t'] = t.exit_time
            t['mae_pct'] = cached.mae_pct
            t['net'] = t.net_notional_bp-cfg['market_entry_slippage_bp']-np.where(t.reason == 4,0.,cfg['non_target_slippage_bp'])
            parts[path].append(t)
            receipts.extend([file_stamp(file),file_stamp(cache_file)])
    paths={p:pd.concat(rows).sort_index() for p,rows in parts.items()}
    o,l=paths['ohlc'],paths['olhc']
    assert o.index.equals(l.index)
    worse=o.copy(); pick=l.net < o.net; worse.loc[pick]=l.loc[pick]
    worse['chosen_path']=np.where(pick,'olhc','ohlc')
    worse=worse.reset_index()
    # This is the same completed-close BE0 source, with only the display seed
    # and stable input ordering aligned to the owner's exact baseline.
    old_close=json.loads((A/cfg['close_be0_study']/'summary.json').read_text())['results']['plans']['spike|inside+beyond']['all']
    np.testing.assert_allclose([len(worse),worse.net.mean()],[old_close['n'],old_close['mean_bp']],rtol=1e-11)
    new_result,new_ledger=account(worse,cfg)
    old_ledger.to_csv(EXP/'baseline-ledger.csv.gz',index=False)
    new_ledger.to_csv(EXP/'close-ledger.csv.gz',index=False)
    worse.to_csv(EXP/'close-native-projection.csv.gz',index=False)
    # Annual restarts are separate accounts, never stitched into the full path.
    periods={}
    for year in (2025,2026):
        periods[str(year)]={}
        for name,t in [('booklet',baseline),('close_now',worse)]:
            x=clocked(t); x=x[x.entry_t.dt.year==year]
            periods[str(year)][name]=account(x,cfg)[0]
    gone=admission[~admission.filled]
    funded_gone=saved[saved.trade_id.isin(gone.event)&(saved.notional > 0)]
    result=dict(config=cfg,baseline=old_result,close_now=new_result,period_restarts=periods,
        admission=dict(original_signals=len(baseline),close_fills=len(worse),
            skipped_at_close={str(k):int(v) for k,v in gone.status.value_counts().items()},
            old_funded_no_longer_enterable=len(funded_gone),
            old_funded_pnl_no_longer_enterable=float(funded_gone.pnl.sum())),
        difference=dict(ending_equity=float(new_result['ending_equity']-old_result['ending_equity']),
            return_percentage_points=float(new_result['return_pct']-old_result['return_pct'])),
        validation=dict(original_ledger_exact=True,original_booklet_summary_exact=True,
            cached_mae_window_price_checks=checks,old_be0_mean_exact=True,new_native_replays=0,
            native_versions='Original saved NautilusTrader1.231.0 fills',
            source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()),
        training_eligible=False,production_eligible=False)
    dump(EXP/'inputs.json',dict(sources=receipts+[file_stamp(source/'trades.csv'),file_stamp(booklet/'spike-ledger.csv.gz')]))
    dump(EXP/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':
    run()
