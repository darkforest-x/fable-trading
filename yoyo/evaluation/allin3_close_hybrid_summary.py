"""Cash accounting over saved native hybrid outcomes; no candle execution.

One full-equity pending order reserves the slot from decision until cancellation
or actual position exit. Subsequent signals while busy are skipped permanently.
Current paper semantics cap a trade at its margin and refill below 100 to 1000;
no MAE-derived fictitious liquidation is imposed. Native independent fills do
not establish exchange margin, funding, order-book capacity or live returns.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.allin3_close_hybrid import EXP, ROOT
from yoyo.evaluation.range_framework import dump


def ns(s):
    return pd.to_datetime(s, utc=True).dt.as_unit('ns').astype('int64')


def account(orders, cfg, seed, start='2023-01-01', net_column='net'):
    """Reserve capital using decision/cancellation/fill/exit events, never prices."""
    cut = pd.Timestamp(start, tz='UTC').value
    t = orders[orders.decision_ns >= cut].copy()
    rng = np.random.default_rng(seed)
    t = t.iloc[np.lexsort((rng.random(len(t)), t.decision_ns.to_numpy()))]
    equity = float(cfg['initial']); initial = equity
    busy = -1; refills = 0; cash = 0.; skipped = 0; waits = 0
    ledger = [dict(time_ns=cut,kind='initial',equity=equity,pnl=0.,cashflow=0.)]
    funding = []
    for r in t.to_dict('records'):
        if r['decision_ns'] < busy:
            skipped += 1; continue
        busy = int(r['release_ns'])
        if not r['filled']:
            waits += 1
            funding.append(dict(trade_id=r['trade_id'],decision_ns=r['decision_ns'],
                                release_ns=busy,status=r['status'],pnl=0.,notional=0.))
            continue
        margin = equity; notional = margin*cfg['leverage']
        pnl = max(-margin, notional*float(r[net_column])/1e4)
        equity += pnl
        ledger.append(dict(time_ns=busy,kind='exit',trade_id=r['trade_id'],
            equity=equity,pnl=pnl,cashflow=0.,reason=int(r['reason']),
            order_mode=r['order_mode'],net_bp=r[net_column],notional=notional))
        funding.append(dict(trade_id=r['trade_id'],decision_ns=r['decision_ns'],release_ns=busy,
            status='funded',pnl=pnl,notional=notional,reason=int(r['reason']),order_mode=r['order_mode']))
        if equity < cfg['refill_below']:
            add = cfg['refill_to']-equity
            cash += add; refills += 1; equity = float(cfg['refill_to'])
            ledger.append(dict(time_ns=busy,kind='refill',equity=equity,pnl=0.,cashflow=add))
    ledger = pd.DataFrame(ledger); funded = pd.DataFrame(funding)
    exits = ledger[ledger.kind == 'exit']
    profits = exits.pnl.to_numpy() if len(exits) else np.array([])
    dd = (1-ledger.equity/ledger.equity.cummax()).max()*100
    gain, loss = profits[profits > 0].sum(), -profits[profits < 0].sum()
    pnl = float(profits.sum())
    np.testing.assert_allclose(equity, initial+cash+pnl, rtol=1e-12, atol=1e-8)
    result = dict(initial=initial,ending_equity=equity,trading_pnl=pnl,cash_added=cash,
        total_cash_in=initial+cash,ending_over_total_cash_in=equity/(initial+cash),
        refills=refills,funded=len(exits),skipped_busy=skipped,reserved_but_unfilled=waits,
        win_pct=float(100*(profits>0).mean()) if len(profits) else None,
        profit_factor=float(gain/loss) if loss else None,realized_balance_max_dd_pct=float(dd),
        terminal_settlement_pnl=float(exits[exits.reason==0].pnl.sum()) if len(exits) else 0.,
        terminal_settlements=int((exits.reason==0).sum()) if len(exits) else 0)
    return result, ledger, funded


def stats(t, col='net'):
    if t.empty:
        return dict(n=0)
    a=t[col].to_numpy(float)
    return dict(n=len(t),mean_bp=float(a.mean()),median_bp=float(np.median(a)),
        win_pct=float(100*(a>0).mean()),profit_factor=float(a[a>0].sum()/-a[a<0].sum()) if (a<0).any() else None,
        terminal_count=int((t.reason==0).sum()))


def paired(orders, trades):
    x=orders[['trade_id','event','control_id','week']].merge(trades[['trade_id','net']],on='trade_id',how='left')
    x['net']=x.net.fillna(0.)  # unfilled opportunity earns zero, not dropped
    wide=x.pivot(index=['event','week'],columns='control_id',values='net')
    wide=wide.reindex(columns=[0,1,2,3]).dropna()
    if wide.empty: return dict(n=0)
    signal=wide[0].to_numpy(); random=wide[[1,2,3]].mean(axis=1).to_numpy()
    d=pd.Series(signal-random,index=wide.index)
    grouped=d.groupby(level='week').sum().to_numpy()
    rng=np.random.default_rng(20261029)
    shuffled=(rng.choice([-1.,1.],size=(10000,len(grouped)))*grouped).sum(axis=1)/len(d)
    return dict(n=len(d),weeks=len(grouped),signal_mean_per_opportunity_bp=float(signal.mean()),
        random_mean_per_opportunity_bp=float(random.mean()),excess_bp=float(d.mean()),
        weekly_signflip_two_sided_p=float((1+(np.abs(shuffled)>=abs(d.mean())).sum())/10001))


def summarize():
    cfg=json.loads((EXP/'config.json').read_text()); dirs=sorted((EXP/'symbols').iterdir())
    errors=[str(p/'error.json') for p in dirs if not (p/'done.json').exists()]
    if errors: raise RuntimeError(f'Incomplete native runs: {errors[:5]}')
    orders=[]; decisions=[]; paths={k:[] for k in ('ohlc','olhc')}; resolutions={k:[] for k in paths}
    for p in dirs:
        if (p/'decisions.csv.gz').exists():
            d=pd.read_csv(p/'decisions.csv.gz')
            if len(d): decisions.append(d)
        if not (p/'orders.csv.gz').exists(): continue
        o=pd.read_csv(p/'orders.csv.gz');orders.append(o)
        for path in paths:
            t=pd.read_csv(p/path/'trades.csv.gz'); paths[path].append(t)
            try: op=pd.read_csv(p/path/'opportunities.csv.gz')
            except pd.errors.EmptyDataError: op=pd.DataFrame()
            if len(op): resolutions[path].append(op[op.status!='filled'])
    orders=pd.concat(orders,ignore_index=True)
    decisions=pd.concat(decisions,ignore_index=True)
    orders['decision_ns']=ns(orders.decision_time)
    orders.to_csv(EXP/'orders.csv.gz',index=False);decisions.to_csv(EXP/'decisions.csv.gz',index=False)
    result=dict(config=cfg,symbols=len(dirs),signal_decisions=int((decisions.control_id==0).sum()),
        decision_status=decisions[decisions.control_id==0].status.value_counts().to_dict(),paths={})
    for path, pieces in paths.items():
        trades=pd.concat(pieces,ignore_index=True)
        trades.to_csv(EXP/f'{path}-trades.csv.gz',index=False)
        cancelled=pd.concat(resolutions[path],ignore_index=True) if resolutions[path] else pd.DataFrame(columns=['trade_id','status','release_time'])
        q=orders.merge(trades[['trade_id','net','net_notional_bp','reason','entry_time','exit_time']],
                       on='trade_id',how='left',validate='one_to_one')
        q['filled']=q.exit_time.notna()
        q['release_time']=q.exit_time
        q['status']=np.where(q.filled,'filled','missing')
        canc=cancelled.set_index('trade_id')
        mask=~q.filled
        q.loc[mask,'release_time']=q.loc[mask,'trade_id'].map(canc.release_time)
        q.loc[mask,'status']=q.loc[mask,'trade_id'].map(canc.status)
        if q.release_time.isna().any():raise ValueError('Missing native fill/cancel resolution')
        q['release_ns']=ns(q.release_time)
        if (q.release_ns<q.decision_ns).any():raise ValueError('Resolution before decision')
        q.to_csv(EXP/f'{path}-resolved-orders.csv.gz',index=False)
        main=q[q.control_id==0];sig=trades[trades.control_id==0]
        first,ledger,funded=account(main,cfg['capital'],cfg['capital']['tie_seed'])
        ledger.to_csv(EXP/f'{path}-account-ledger.csv.gz',index=False)
        funded.to_csv(EXP/f'{path}-funded.csv.gz',index=False)
        tie=[account(main,cfg['capital'],cfg['capital']['tie_seed']+i)[0] for i in range(10)]
        controls=[account(q[q.control_id==i],cfg['capital'],cfg['capital']['tie_seed'])[0] for i in (1,2,3)]
        yearly={year:account(main,cfg['capital'],cfg['capital']['tie_seed'],f'{year}-01-01')[0]
                for year in (2025,2026)}
        result['paths'][path]=dict(signal_orders=len(main),signal_fills=len(sig),
            signal_market_fills=int((sig.order_mode=='market').sum()),signal_limit_fills=int((sig.order_mode=='limit').sum()),
            unfilled_status=main[~main.filled].status.value_counts().to_dict(),
            independent=stats(sig),independent_completed=stats(sig[sig.reason!=0]),
            by_mode={name:stats(g) for name,g in sig.groupby('order_mode')},
            paired=paired(orders,trades),paired_2026=paired(orders[orders.week>='2026-01-01'],trades),
            account=first,fees_only_account=account(main,cfg['capital'],cfg['capital']['tie_seed'],net_column='net_notional_bp')[0],
            control_accounts=controls,tie_seed_sensitivity=dict(ending_min=min(r['ending_equity'] for r in tie),
                ending_median=float(np.median([r['ending_equity'] for r in tie])),ending_max=max(r['ending_equity'] for r in tie),
                pnl_min=min(r['trading_pnl'] for r in tie),pnl_max=max(r['trading_pnl'] for r in tie)),
            restart_years=yearly)
    result['source_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    result['limits']='Single reserved-order capital projection, not native margin/funding or real-live return. Both full intrabar paths retained; no per-trade path stitching. Controls are conditional on signal geometry. 2023-2025 model training period; selected strategy remains retrospective.'
    dump(EXP/'summary.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':summarize()
