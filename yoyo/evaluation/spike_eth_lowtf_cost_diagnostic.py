"""Read-only ETH V8 low-timeframe cost and 1R-break-even diagnostic.

It consumes only two frozen trade artifacts.  No OHLCV, signal mask, cost,
stop, or serial account path is rebuilt. ``fee_r`` is a descriptive conversion
of the already-frozen 0.2% round-trip cost: ``0.002 / initial_risk_frac``.
The sole predeclared gate is ``fee_r <= 0.5`` (risk fraction >= 0.4%).
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess
from pathlib import Path
from typing import Iterable
import numpy as np
import pandas as pd

EXP=Path('experiments/active/exp-spike-eth-lowtf-cost-diagnostic-20260914-v1')
CONFIG=EXP/'config.json'; PLAN=EXP/'PROJECT_PLAN.md'; RECEIPT=EXP/'holdout_receipt.json'; TEST=Path('tests/evaluation/test_spike_eth_lowtf_cost_diagnostic.py')
LOWTF=Path('experiments/active/exp-spike-v8-lowtf-20260913-v1/results/run_20260913_v3')
BE=Path('experiments/active/exp-spike-v8-eth3m-be-20260913-v1/results/run_v2')
COST=0.002


def sha256(path: Path)->str: return hashlib.sha256(path.read_bytes()).hexdigest()

def _clean(paths: Iterable[Path])->bool:
    root=Path.cwd().resolve()
    for p in paths:
        try: rel=p.resolve().relative_to(root)
        except ValueError: return False
        if subprocess.run(['git','cat-file','-e',f'HEAD:{rel}'],capture_output=True).returncode: return False
        if subprocess.run(['git','diff','--quiet','HEAD','--',str(rel)]).returncode: return False
    return True

def _pf(values: pd.Series)->float:
    wins=values[values>0].sum(); losses=-values[values<0].sum()
    return float(wins/losses) if losses else (float('inf') if wins else float('nan'))

def _summary(frame: pd.DataFrame, keys: list[str])->pd.DataFrame:
    """Summarize frozen closed trades; no compounding or new exits are implied."""
    rows=[]
    for key,g in frame.groupby(keys,dropna=False,sort=True,observed=True):
        key=(key,) if not isinstance(key,tuple) else key
        r=pd.to_numeric(g.net_r,errors='coerce'); ret=pd.to_numeric(g.net_return,errors='coerce')
        rec=dict(zip(keys,key)); best=r.max()
        rec.update(n=int(len(g)),wins=int((r>0).sum()),win_rate=float((r>0).mean()),sum_net_r=float(r.sum()),mean_net_r=float(r.mean()),
                   pf_net_r=_pf(r),pf_net_return=_pf(ret),sum_net_return=float(ret.sum()),mean_net_return=float(ret.mean()),
                   realized_ge10r=int((r>=10).sum()),realized_ge10r_rate=float((r>=10).mean()),best_net_r=float(best),
                   sum_without_best_net_r=float(r.sum()-best),mean_fee_r=float(g.fee_r.mean()),median_fee_r=float(g.fee_r.median()),
                   median_risk_fraction=float(g.initial_risk_frac.median()))
        rows.append(rec)
    return pd.DataFrame(rows)

def _bucket(fee: pd.Series)->pd.Categorical:
    return pd.cut(fee,[-np.inf,.25,.5,1,np.inf],labels=['<=0.25','0.25-0.5','0.5-1','>1'],right=True)

def _require(config: dict)->None:
    for path_text, expected in config['input_sha256'].items():
        path=Path(path_text)
        if sha256(path)!=expected: raise ValueError(f'frozen input SHA mismatch: {path}')

def load_baseline()->pd.DataFrame:
    """Load only frozen ETH V8 3m/5m closed trades from the original low-TF output."""
    src=pd.read_csv(LOWTF/'primary_closed_trades.csv.gz')
    out=src.loc[(src.arm=='v8') & src.symbol.isin(['ETH-USDT-SWAP','ETHUSDT']) & src.minutes.isin([3,5])].copy()
    if len(out)!=3525 or set(out.groupby(['symbol','minutes','fold']).size()) != {1460,111,1261,693}: raise ValueError('unexpected frozen ETH low-TF denominators')
    out['stream']=np.where(out.minutes.eq(3),'ETH 3m OKX','ETH 5m Binance')
    out['period']=out.fold.map({'development':'development','validation':'later'})
    out['signal_confirm_time']=pd.to_datetime(out.signal_confirm_time,utc=True)
    out['exit_time']=pd.to_datetime(out.exit_time,utc=True)
    out['initial_risk_frac']=pd.to_numeric(out.initial_risk_frac,errors='coerce')
    if (out.initial_risk_frac<=0).any() or out.initial_risk_frac.isna().any(): raise ValueError('invalid frozen initial risk')
    out['fee_r']=COST/out.initial_risk_frac
    out['cost_budget_pass']=out.fee_r.le(.5)
    out['fee_r_bucket']=_bucket(out.fee_r)
    out['month']=out.signal_confirm_time.dt.to_period('M').astype(str)
    return out

def load_be_pairs()->pd.DataFrame:
    """Load frozen same-entry ETH 3m baseline/next-bar 1R break-even pairs only."""
    parts=[]
    for period,name in [('development','development_fixed_entry_pairs.csv.gz'),('later','post_authorized_holdout_fixed_entry_pairs.csv.gz')]:
        raw=pd.read_csv(BE/name)
        for policy in ('baseline','be'):
            cols={'side':'side','initial_risk_frac':f'initial_risk_frac_{policy}','net_r':f'net_r_{policy}','net_return':f'net_return_{policy}','exit_reason':f'exit_reason_{policy}','exit_time':f'exit_time_{policy}','signal_confirm_time':f'signal_confirm_time_{policy}','be_armed':'be_armed'}
            available={k:v for k,v in cols.items() if v in raw}
            sub=raw.loc[:,list(available.values())].rename(columns={v:k for k,v in available.items()}).copy()
            sub['policy']=policy; sub['period']=period
            sub['fee_r']=COST/pd.to_numeric(sub.initial_risk_frac,errors='coerce')
            sub['month']=pd.to_datetime(sub.signal_confirm_time,utc=True).dt.to_period('M').astype(str)
            parts.append(sub)
    out=pd.concat(parts,ignore_index=True)
    if out.groupby(['period','policy']).size().to_dict() != {('development','baseline'):1460,('development','be'):1460,('later','baseline'):111,('later','be'):111}: raise ValueError('unexpected ETH 3m 1R pair denominators')
    return out

def run(output: Path, official: bool)->None:
    if official and not _clean([Path(__file__),CONFIG,PLAN,RECEIPT,TEST]): raise RuntimeError('official requires committed clean builder/config/plan/receipt/test')
    config=json.loads(CONFIG.read_text()); _require(config)
    if output.exists() and any(output.iterdir()): raise FileExistsError(f'refusing overwrite: {output}')
    output.mkdir(parents=True,exist_ok=False)
    baseline=load_baseline(); pairs=load_be_pairs()
    baseline.to_csv(output/'baseline_event_ledger.csv.gz',index=False,compression='gzip')
    pairs.to_csv(output/'eth3m_be_fixed_entry_pairs.csv.gz',index=False,compression='gzip')
    gate=baseline.assign(cost_budget_group=np.where(baseline.cost_budget_pass,'pass_fee_r_le_0_5','excluded_fee_r_gt_0_5'))
    _summary(gate,['stream','period','cost_budget_group']).to_csv(output/'cost_budget_gate_summary.csv',index=False)
    _summary(baseline,['stream','period','fee_r_bucket']).to_csv(output/'fee_r_bucket_summary.csv',index=False)
    _summary(baseline,['stream','period','side','exit_reason']).to_csv(output/'baseline_direction_exit_summary.csv',index=False)
    _summary(baseline,['stream','period','month']).to_csv(output/'baseline_monthly_summary.csv',index=False)
    _summary(pairs,['period','policy','side','exit_reason']).to_csv(output/'eth3m_be_direction_exit_summary.csv',index=False)
    _summary(pairs,['period','policy','month']).to_csv(output/'eth3m_be_monthly_summary.csv',index=False)
    be_summary=_summary(pairs,['period','policy'])
    be_summary.to_csv(output/'eth3m_be_policy_summary.csv',index=False)
    manifest={'official':official,'config_sha256':sha256(CONFIG),'plan_sha256':sha256(PLAN),'receipt_sha256':sha256(RECEIPT),
              'input_sha256':config['input_sha256'],'baseline_rows':len(baseline),'baseline_stream_period_counts':{f'{a}|{b}':int(c) for (a,b),c in baseline.groupby(['stream','period']).size().items()},
              'be_pair_rows':len(pairs),'baseline_event_ledger_sha256':sha256(output/'baseline_event_ledger.csv.gz'),'baseline_event_ledger_bytes':(output/'baseline_event_ledger.csv.gz').stat().st_size,
              'nonblind_history':True,'no_ohlcv_read':True,'scope':'existing frozen-trade descriptive diagnostic; no replay or account curve'}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
    print(json.dumps(manifest,sort_keys=True))

def main()->None:
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); p.add_argument('--official',action='store_true'); a=p.parse_args(); run(a.output,a.official)
if __name__=='__main__': main()
