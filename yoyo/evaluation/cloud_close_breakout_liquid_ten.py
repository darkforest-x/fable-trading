"""Replace a retrospective major-coin roster with the available prior 30-day turnover ranks."""
import argparse
import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_branch as branch
from yoyo.evaluation import cloud_close_turnover_universe as liquidity
from yoyo.evaluation import cloud_close_weak_rally_branch as markets

EXP = branch.EXP.parent / 'exp-cloud-close-breakout-liquid-ten-20261109-v1'
RANKS = EXP / 'contemporaneous_ranks.csv.gz'


def prepare():
    if RANKS.is_file():
        return
    directory = pd.read_csv(liquidity.wallet.SOURCE/'input_coverage.csv',dtype={'symbol':str})
    supplement = liquidity.shared_quotes(directory,liquidity.COMPLETE)
    parts = dict(tuple(supplement.groupby('symbol')))
    eligible,traded = {},{}
    for r in directory.itertuples():
        top = r.cohort=='top20_reused'
        folder = (liquidity.wallet.TOP if top else liquidity.wallet.SOURCE)/'inputs'/r.symbol
        eligible[r.symbol],traded[r.symbol] = liquidity.okx_values(folder,top,parts.get(r.symbol))
    for sym,files in markets.sources().items():
        if sym not in eligible:
            _,bars = markets.frames(files)
            eligible[sym] = liquidity.leader.trailing_return(bars.reset_index()).notna()
    frame = pd.DataFrame(eligible).fillna(False).astype(bool)
    if frame.shape[1]!=360:
        raise ValueError('Expected the same 360 contracts as the breakout')
    quote = pd.DataFrame(traded).reindex_like(frame).where(frame)
    count = frame.sum(axis=1)
    missing = count-quote.notna().sum(axis=1)
    higher = quote.rank(axis=1,ascending=False,method='min').sub(1)
    status = pd.DataFrame('unknown',index=frame.index,columns=frame.columns)
    status[higher.ge(10)] = 'outside'
    status[higher.add(missing,axis=0).lt(10)] = 'top10'
    status = status.where(frame & count.ge(10).to_numpy()[:,None]).rename_axis(index='entry_ms',columns='symbol')
    out = status.stack().dropna().rename('rank_status').reset_index()
    out['unknown_peers'] = out.entry_ms.map(missing)
    out['eligible_peers'] = out.entry_ms.map(count)
    tmp = RANKS.with_suffix('.tmp.gz')
    out.to_csv(tmp,index=False)
    tmp.replace(RANKS)
    print('eligible instrument-clocks',len(out),flush=True)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed as-of liquidity study')
    hist = branch.HISTORICAL_EXP
    config = json.loads((hist/'config.json').read_text())
    raw = pd.read_csv(hist/config['outputs']['paths_csv_gz'],dtype={'symbol':str},float_precision='round_trip')
    raw = raw[raw.arm.eq('breakout') & ~raw.is_control & raw.status.eq('executed')].copy()
    ranks = pd.read_csv(RANKS,dtype={'symbol':str})
    tape = raw.merge(ranks,on=['entry_ms','symbol'],how='left',validate='many_to_one')
    qty = tape.rank_status.fillna('not_eligible').value_counts().to_dict()
    quotes = branch._quote_loader(config)
    cases = {}
    for name,mask in [('top10',tape.rank_status.eq('top10')),
                      ('outside',tape.rank_status.eq('outside')),
                      ('classified',tape.rank_status.isin(['top10','outside']))]:
        events = tape[mask]
        if events.empty:
            cases[name] = dict(opportunities=0)
            continue
        data = branch.wallet.prepare(events,9,quote_loader=quotes)
        for slip in ([0.,.0005] if name=='top10' else [0.]):
            ident = name+('_stress' if slip else '')
            funded,eq = branch.wallet.account(data,slip=slip)
            if not np.isclose(eq.nav.iat[-1],1000+funded.cash_pnl.sum(),atol=.005):
                raise ValueError('As-of liquidity cash mismatch')
            funds = {p:branch.wallet.summarize(funded,eq,*bounds) for p,bounds in branch.PERIODS.items()}
            cases[ident] = dict(metrics=funds,opportunities=len(events),funded_assets=funded.symbol.nunique(),
                abnormal_exits=funded.loc[funded.gap_flag | (funded.exit_reason.eq('series_tail_close') &
                funded.exit_bar_ms.lt(branch.wallet.END-branch.HOUR)),'exit_reason'].value_counts().to_dict())
            funded.to_csv(EXP/f'funded_{ident}.csv.gz',index=False)
            eq.to_csv(EXP/f'hourly_{ident}.csv.gz',index_label='hour_end_utc')
            print(ident,funds['all'],flush=True)
    branch._write_json(EXP/'summary.json',dict(rank_coverage=qty,cases=cases))


if __name__=='__main__':
    cli = argparse.ArgumentParser()
    cli.add_argument('--prepare',action='store_true')
    if cli.parse_args().prepare:
        prepare()
    else:
        main()
