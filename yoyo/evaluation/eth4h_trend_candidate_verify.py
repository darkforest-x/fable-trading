"""Independent saved-ledger accounting checks; no signal selection or reruns.

Reads the fixed pre-May2026 source only to reconcile entry timing, risk sizing,
and matched-control strata. CSV float round trips preserve permutation ties.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.eth4h_trend_candidate import OUT, ROOT, write_report
from yoyo.evaluation.pine_allin_eth4h_replay import digest, inference, load_source, save_json


def main():
    target = OUT / 'independent_validation.json'
    if target.exists():
        raise RuntimeError('Refuse to overwrite completed verification')
    frozen = subprocess.check_output(['git', 'show', 'HEAD:yoyo/evaluation/eth4h_trend_candidate_verify.py'])
    import hashlib
    assert hashlib.sha256(frozen).hexdigest() == digest(Path(__file__))
    payload = json.loads((OUT / 'summary.json').read_text())
    frame, _ = load_source()
    checks, concentration, annual = {}, [], []
    for row in payload['summary']:
        stem = f"{row['window']}_{row['arm']}"
        t = pd.read_csv(OUT / f'{stem}_trades.csv', float_precision='round_trip',
                        parse_dates=['entry_time', 'exit_time', 'signal_time'])
        e = pd.read_csv(OUT / f'{stem}_equity.csv', float_precision='round_trip', parse_dates=['time'])
        c = pd.read_csv(OUT / f'{stem}_controls.csv', float_precision='round_trip')
        fee = .001
        fees = t.qty * (t.entry_price + t.exit_price) * fee
        net = t.direction * t.qty * (t.exit_price - t.entry_price) - fees
        checks[stem+'_cash_identity'] = bool(np.allclose(t.net_pnl, net, rtol=1e-12))
        checks[stem+'_fees_identity'] = bool(np.allclose(t.fees, fees, rtol=1e-12))
        checks[stem+'_final_account'] = bool(np.isclose(500+net.sum(), e.equity.iloc[-1], rtol=1e-12))
        checks[stem+'_reported_return'] = bool(np.isclose((e.equity.iloc[-1]/500-1)*100,row['return_pct']))
        checks[stem+'_close_drawdown'] = bool(np.isclose((1-e.equity/e.equity.cummax().clip(lower=500)).max()*100,row['close_dd_pct']))
        sig, ent = t.signal_i.to_numpy(int), t.entry_i.to_numpy(int)
        marked = e.set_index('time').equity.reindex(t.entry_time).to_numpy()
        signal_close = frame.close.iloc[sig].to_numpy()
        distance = np.minimum(4*frame.atr.iloc[sig].to_numpy(), signal_close*.03)
        lev = np.ones(len(t)) if row['arm']=='S0_immediate_1x' else np.minimum(1., .005/(distance/signal_close))
        checks[stem+'_signal_sizing'] = bool(np.allclose(t.qty,marked*lev/signal_close,rtol=1e-12))
        checks[stem+'_next_open_fill'] = bool(np.array_equal(ent,sig+1) and np.allclose(t.entry_price,frame.open.iloc[ent]))
        a,b = c.case_signal_i.to_numpy(int),c.control_signal_i.to_numpy(int)
        checks[stem+'_control_strata'] = bool(
            np.array_equal(frame.open_time.iloc[a].dt.strftime('%Y-%m'),frame.open_time.iloc[b].dt.strftime('%Y-%m'))
            and np.array_equal(frame.hk_hour.iloc[a]//6,frame.hk_hour.iloc[b]//6)
            and np.array_equal(frame.vol_bin.iloc[a],frame.vol_bin.iloc[b]) and (np.abs(a-b)>12).all())
        controlnet = c.direction*(c.control_exit_price/c.control_entry_price-1)-fee*(1+c.control_exit_price/c.control_entry_price)
        checks[stem+'_control_returns'] = bool(np.allclose(controlnet,c.control_net_return,atol=1e-12))
        means = c.groupby('trade_id').control_net_return.mean()
        checks[stem+'_matched_means'] = bool(np.allclose(t.loc[t.matched,'control_mean'],means.reindex(t.index[t.matched]),atol=1e-12))
        checks[stem+'_safe_dates'] = bool(t.exit_time.max() <= pd.Timestamp('2026-05-01',tz='UTC'))
        if row['arm']=='S5_close_only':
            new = inference(t)
            old = next(x for x in payload['ranking'] if x['window']==row['window'])
            checks[stem+'_roundtrip_inference'] = all(new[k]==old[k] for k in ('ranking_permutation_p','matched_month_cluster_signflip_p'))
            sorted_net = t.net_return.sort_values(ascending=False)
            duration = (e.time.iloc[-1]-e.time.iloc[0]).total_seconds()/86400/365.25
            concentration.append({'window':row['window'],'positive_trades':int((t.net_pnl>0).sum()),
                                  'trades':len(t),'cagr_pct':((e.equity.iloc[-1]/500)**(1/duration)-1)*100,
                                  'largest5_positive_share_pct':row['largest_5_share_of_positive_pnl_pct'],
                                  'net_bp_excluding_largest3':sorted_net.iloc[3:].mean()*1e4})
            if row['window']=='continuous':
                previous=500.
                # Assign a bar closing at Jan1 midnight to the preceding year.
                for year,g in e.groupby((e.time-pd.Timedelta(nanoseconds=1)).dt.year):
                    last=float(g.equity.iloc[-1])
                    annual.append({'year':int(year),'return_pct':(last/previous-1)*100,'end_equity':last})
                    previous=last
    qa={'passed':all(checks.values()),'checks':checks,'concentration':concentration,'annual':annual,
        'verification_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'native_parity':False,'market_replay_rerun':False,
        'limitation':'Accounting and sizing checks do not independently validate the intrabar execution engine.'}
    save_json(target,qa)
    assert qa['passed'],[k for k,v in checks.items() if not v]
    write_report(payload)
    print(json.dumps({'passed_checks':len(checks),'annual':annual,'concentration':concentration},indent=2))


if __name__=='__main__':
    main()
