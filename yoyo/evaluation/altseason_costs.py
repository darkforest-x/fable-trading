"""Frozen-allocation fee/funding sensitivity, never a new trading strategy.

Uses only completed trade ledgers and public realized funding for accounting.
It does not recompute sizes or signals. Binance supplies settlement mark prices;
other venues use the matching native1H bar open, explicitly a price proxy.
Unknown exit intrabar order and funding records within five seconds of a
boundary trade clock are bracketed, not credited as exact held settlements.
Missing rates/files remain unknown. A traversed API does not prove the full
historical settlement schedule. Price impact and actual execution remain out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
EXPERIMENT=ROOT/'experiments/active/exp-altseason-multivenue-20260910-v1'


def trade_funding(row, funding, hourly):
    """Funding cost as a fraction of initial notional; signed positive=paid."""
    output=dict(funding_known=False,observed_funding_return=np.nan,
        observed_funding_low=np.nan,observed_funding_high=np.nan,
        settlement_count=0,ambiguous_settlements=0,proxy_price_count=0,
        missing_price_count=0,missing_rate_count=0,full_schedule='unknown')
    if funding is None or funding.empty:return output
    start=pd.Timestamp(row.entry_time);lower=pd.Timestamp(row.exit_time_lower);upper=pd.Timestamp(row.exit_time_upper)
    times=pd.to_datetime(funding.funding_time,unit='ms',utc=True)
    margin=pd.Timedelta(seconds=5)
    inside=(times>=start-margin)&(times<=upper+margin)
    f=funding.loc[inside].copy();t=times.loc[inside]
    # A nonempty fetched history can contain zero settlements during a short
    # hold. This is only zero observed cost, not proof of schedule completeness.
    total=0.;uncertain=0.;missing=0;proxy=0;ambiguous=0
    for idx,event in f.iterrows():
        stamp=t.loc[idx];rate=float(event.realized_rate)
        if not np.isfinite(rate):output['missing_rate_count']+=1;missing+=1;continue
        mark=float(event.mark_price)
        if not np.isfinite(mark):
            clock=stamp.floor('h')
            if clock not in hourly.index:
                output['missing_price_count']+=1;missing+=1;continue
            mark=float(hourly.loc[clock,'open']);proxy+=1
        value=rate*mark/float(row.entry_price)
        is_uncertain=(stamp<=start+margin or stamp>=lower-margin)
        if is_uncertain:uncertain+=abs(value);ambiguous+=1
        else:total+=value
    output.update(funding_known=missing==0,settlement_count=len(f),ambiguous_settlements=ambiguous,proxy_price_count=proxy)
    if not missing:output.update(observed_funding_return=total,observed_funding_low=total-uncertain,observed_funding_high=total+uncertain)
    return output


def diagnose(selected, load_funding, load_hourly):
    rows=[];cache={}
    for row in selected.loc[selected.portfolio_selected].itertuples():
        if row.event_id not in cache:
            cache[row.event_id]=trade_funding(row,load_funding(row.venue,row.symbol),load_hourly(row.features_path))
        x=dict(row._asdict());x.update(cache[row.event_id])
        x['actual_turnover_cost_return']=.001*(1+float(row.exit_price)/float(row.entry_price))
        x['turnover_net_return']=float(row.gross_return)-x['actual_turnover_cost_return']
        x['turnover_pnl']=float(row.notional)*x['turnover_net_return']
        x['observed_funding_pnl']=float(row.notional)*x['observed_funding_return']
        rows.append(x)
    details=pd.DataFrame(rows);out=[]
    for (scope,minutes,arm),g in details.groupby(['scope','minutes','arm']):
        known=g.loc[g.funding_known]
        base=g.realized_net_pnl.sum()
        item=dict(scope=scope,minutes=minutes,arm=arm,trades=len(g),
            frozen_base_return_pct=base/1000,
            frozen_40bp_return_pct=(base-.002*g.notional.sum())/1000,
            frozen_60bp_return_pct=(base-.004*g.notional.sum())/1000,
            frozen_actual_turnover_return_pct=g.turnover_pnl.sum()/1000,
            funding_known_trades=len(known),funding_known_fraction=len(known)/len(g),
            observed_funding_cost_pct=known.observed_funding_pnl.sum()/1000,
            known_cohort_turnover_return_pct=known.turnover_pnl.sum()/1000,
            known_cohort_after_observed_funding_pct=(known.turnover_pnl.sum()-known.observed_funding_pnl.sum())/1000,
            funding_unknown_trades=len(g)-len(known),proxy_settlements=int(g.proxy_price_count.sum()),
            ambiguous_settlements=int(g.ambiguous_settlements.sum()),
            uncertain_funding_range_pct=(known.notional*(known.observed_funding_high-known.observed_funding_low)).sum()/1000,
            full_funding_schedule='unknown',method='same fills and quantities; static attribution, not re-compounded portfolio')
        out.append(item)
    return details,pd.DataFrame(out)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results',type=Path,default=EXPERIMENT/'results')
    p.add_argument('--funding',type=Path,default=EXPERIMENT/'data/funding')
    args=p.parse_args()
    committed=subprocess.check_output(['git','show','HEAD:yoyo/evaluation/altseason_costs.py'],cwd=ROOT)
    if committed!=Path(__file__).read_bytes():raise ValueError('Commit cost builder before accounting')
    f=pd.read_csv(args.results/'portfolio_selections.csv.gz');fund_cache={};hour_cache={}
    for col in ('entry_time','exit_time','exit_time_lower','exit_time_upper'):f[col]=pd.to_datetime(f[col],utc=True)
    def funding(venue,symbol):
        key=(venue,symbol)
        if key not in fund_cache:
            path=args.funding/'normalized'/venue/(symbol+'_funding.csv.gz')
            if path.exists():
                manifest=json.loads(path.with_suffix('.manifest.json').read_text())
                if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['sha256']:raise ValueError('Funding hash mismatch')
                fund_cache[key]=pd.read_csv(path)
            else:fund_cache[key]=None
        return fund_cache[key]
    def hourly(path):
        path=str(path).replace('_240_features.pkl.gz','_60_features.pkl.gz')
        if path not in hour_cache:hour_cache[path]=pd.read_pickle(path)[['open']]
        return hour_cache[path]
    details,summary=diagnose(f,funding,hourly)
    details.to_csv(args.results/'cost_details.csv.gz',index=False)
    summary.to_csv(args.results/'cost_diagnostics.csv',index=False)
    print(json.dumps(dict(selected_trades=len(details),portfolios=len(summary),funding_full_schedule='unknown')))


if __name__=='__main__':main()
