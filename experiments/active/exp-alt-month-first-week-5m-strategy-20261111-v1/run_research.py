"""Shared-account selection on early data and later evaluation of frozen monthly first-touch strategies."""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from prepare import BAR, CACHE, CFG, HERE, PRICE_COLS

START = pd.Timestamp(CFG['start_utc']).value // 1_000_000
SPLIT = pd.Timestamp(CFG['split_utc']).value // 1_000_000
END = pd.Timestamp(CFG['end_exclusive_utc']).value // 1_000_000
PERIODS = {'early':(START,SPLIT), 'late':(SPLIT,END), 'full':(START,END)}
OBJECTIVES = {'win':'win_rate_pct','return':'net_return_pct','frequency':'trades_funded','value':'return_to_drawdown'}


def configurations():
    result = {}
    for rule in CFG['entry_rules']:
        for name, changes in CFG['variants']:
            params = {**CFG['baseline'], **changes}
            if params['tp_mode'] == 'target_distance' and rule == 'continue_outside':
                continue
            result[f'{rule}-{name}'] = (rule, params)
    return result


class Inputs:
    def __init__(self):
        self.array = np.load(CACHE/'windows.npy',mmap_mode='r')
        events = pd.read_csv(HERE/'source_events.csv.gz')
        self.windows = {e:pd.DataFrame(self.array[i],columns=PRICE_COLS,copy=False)
                        for i,e in enumerate(events.event_id)}
        self.candidates = pd.read_csv(HERE/'entry_candidates.csv.gz')

    def clocks(self, rule, params, mode):
        rows = self.candidates[self.candidates.rule.eq(rule)].copy()
        if params['tp_mode'] == 'target_distance':
            rows = rows[~rows.entry_after_near]
        horizon = params['horizon_bars']
        rows = rows[rows.entry_ms + horizon*BAR <= rows.month_end_ms].copy()
        if mode in ('matched','placebo'):
            rows = rows[rows.control_idx >= 0].copy()
        if mode == 'placebo':
            idx = rows.control_idx.to_numpy(dtype=int)
            j = rows.grid_idx.to_numpy(dtype=int)
            control_open = self.array[j,idx,1]
            rows['target_price'] *= control_open/rows.entry_open
            rows['entry_open'] = control_open
            rows['entry_idx'] = idx
            rows['entry_ms'] = self.array[j,idx,0].astype('int64')
        j = rows.grid_idx.to_numpy(dtype=int)
        idx = rows.entry_idx.to_numpy(dtype=int)
        valid = np.isfinite(self.array[j[:,None],idx[:,None]+np.arange(horizon),1:]).all(axis=(1,2))
        return rows.loc[valid].reset_index(drop=True)


def replace_parquet(df, path):
    tmp = path.with_suffix('.tmp')
    df.to_parquet(tmp,index=False,compression='zstd')
    os.replace(tmp,path)


def independent(data, key, cfgs, bps, mode='signal'):
    from engine import simulate_batch
    rule, params = cfgs[key]
    if mode == 'matched':
        fills, marks = independent(data,key,cfgs,bps)
        ids = set(data.clocks(rule,params,mode).candidate_id)
        return fills[fills.candidate_id.isin(ids)], marks[marks.candidate_id.isin(ids)]
    folder = CACHE/f'{key}__{mode}__{bps}bp'
    folder.mkdir(parents=True,exist_ok=True)
    fp, mp = folder/'fills.parquet', folder/'marks.parquet'
    if fp.is_file() and mp.is_file():
        return pd.read_parquet(fp),pd.read_parquet(mp)
    candidates = data.clocks(rule,params,mode)
    fills, marks = simulate_batch(data.windows,candidates,**params,slippage_bps=bps,
                                  fee_market_bps=CFG['taker_fee_bps'],fee_limit_bps=CFG['maker_fee_bps'])
    if not fills.candidate_id.is_unique or not set(fills.candidate_id).issubset(candidates.candidate_id):
        raise ValueError(f'invalid fill keys for {key}')
    replace_parquet(fills,fp)
    replace_parquet(marks,mp)
    return fills, marks


def account_result(fills, marks, period, fraction):
    from account import run_account
    left,right = PERIODS[period]
    r = run_account(fills,marks,period=period,period_start_ms=left,period_end_ms=right,
                    initial_cash=1000.0,per_trade_fraction=fraction,max_slots=min(10,round(1/fraction)))
    s = r['summary']
    dd = abs(float(s['max_dd_intrabar_pct']))
    s['return_to_drawdown'] = float(s['net_return_pct'])/dd if dd>0 else np.nan
    return r


def screen(data, cfgs):
    cache = CACHE/'screen'
    cache.mkdir(exist_ok=True)
    screens = []
    for i,key in enumerate(cfgs):
        path = cache/(key+'.csv')
        if path.is_file():
            s = pd.read_csv(path).iloc[0].to_dict()
        else:
            fills,marks = independent(data,key,cfgs,CFG['base_market_slippage_bps'])
            s = dict(account_result(fills,marks,'early',.1)['summary'],strategy=key,fraction=.1)
            pd.DataFrame([s]).to_csv(path,index=False)
        screens.append(s)
        print(f'Screen {i+1}/{len(cfgs)} {key}: {s["trades_funded"]} trades, {s["net_return_pct"]:.2f}% return, {s["win_rate_pct"]:.1f}% win',flush=True)
    return pd.DataFrame(screens)


def ranking(scores, metric):
    keep = scores[(scores.trades_funded >= 100)&(scores.net_return_pct>0)&(scores.max_dd_intrabar_pct.abs()<=30)]
    keys = [metric] if metric=='net_return_pct' else [metric,'net_return_pct']
    return keep.sort_values(keys+['strategy','fraction'],ascending=[False]*len(keys)+[True,True])


def select(data,cfgs,scores):
    finalists = set()
    for metric in OBJECTIVES.values():
        finalists.update(ranking(scores,metric).head(3).strategy)
    if not finalists:
        return scores, {}
    extras = []
    for key in sorted(finalists):
        fills, marks = independent(data,key,cfgs,CFG['base_market_slippage_bps'])
        for fraction in (.05,.20):
            s = dict(account_result(fills,marks,'early',fraction)['summary'],strategy=key,fraction=fraction)
            extras.append(s)
    all_scores = pd.concat([scores,pd.DataFrame(extras)],ignore_index=True)
    picked = {}
    for name, metric in OBJECTIVES.items():
        best = ranking(all_scores,metric).iloc[0]
        picked[name] = {'strategy':best.strategy,'fraction':float(best.fraction)}
    return all_scores,picked


def evaluate(data,cfgs,picks):
    summaries = []
    pairs = sorted({(x['strategy'],x['fraction']) for x in picks.values()})
    for key, fraction in pairs:
        selected = ','.join(name for name,x in picks.items() if (x['strategy'],x['fraction'])==(key,fraction))
        for mode,bps in [('signal',CFG['base_market_slippage_bps']),('signal',CFG['stress_market_slippage_bps']),
                         ('matched',CFG['base_market_slippage_bps']),('placebo',CFG['base_market_slippage_bps'])]:
            fills,marks = independent(data,key,cfgs,bps,mode)
            for period in ('early','late','full'):
                result = account_result(fills,marks,period,fraction)
                summaries.append(dict(result['summary'],strategy=key,fraction=fraction,goals=selected,
                                      type=mode,market_slippage_bps=bps))
                if mode=='signal' and bps==CFG['base_market_slippage_bps'] and period=='full':
                    name=f'{key}__{round(fraction*100)}pct'
                    result['funded_trades'].to_csv(HERE/(name+'_trades.csv.gz'),index=False)
                    result['equity_5m'].to_csv(HERE/(name+'_equity.csv.gz'),index=False)
    return pd.DataFrame(summaries)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase',choices=('all','fills'),default='all')
    parser.add_argument('--shard',default='0/1')
    args = parser.parse_args()
    data=Inputs()
    cfgs=configurations()
    if args.phase=='fills':
        part,total=map(int,args.shard.split('/'))
        for i,key in enumerate(cfgs):
            if i%total==part:
                fills,marks=independent(data,key,cfgs,CFG['base_market_slippage_bps'])
                print(i+1,key,len(fills),len(marks),flush=True)
        return
    scores=screen(data,cfgs)
    scores,picks=select(data,cfgs,scores)
    scores.to_csv(HERE/'selection_period_all.csv',index=False)
    (HERE/'selected.json').write_text(json.dumps(picks,indent=2)+'\n')
    if picks:
        results=evaluate(data,cfgs,picks)
        results.to_csv(HERE/'selected_account_metrics.csv',index=False)
    print('Selected:',picks,flush=True)


if __name__=='__main__':
    main()
