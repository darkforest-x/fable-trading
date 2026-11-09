"""Fund a breakout only after observing a profitable, still-active earlier V1.7 reclaim."""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_confirmed_winner_add as add
from yoyo.evaluation import cloud_close_breakout_delay_historical as later

branch = add.branch
wallet = branch.wallet
EXP = add.EXP.parent/'exp-cloud-close-reclaim-then-break-20261109-v1'


def signals(events,marks):
    chronological = events.copy()
    chronological['is_breakout'] = chronological.arm.eq('breakout')
    chronological = chronological.sort_values(['entry_ms','is_breakout','symbol'])
    references = {}
    used = set()
    winners = {}
    for row in chronological.itertuples():
        ref = references.get(row.symbol)
        if not row.is_breakout:
            if ref is None or ref.exit_bar_ms<row.entry_ms:
                references[row.symbol] = row
            continue
        if (ref is None or ref.side!=row.side or ref.entry_ms>=row.entry_ms or
                ref.exit_bar_ms<row.entry_ms or
                (ref.exit_bar_ms==row.entry_ms and ref.exit_phase=='open') or ref.event_id in used):
            continue
        px = marks[row.event_id]
        if ref.side*(px-ref.entry_price)<=wallet.TAKER*(px+ref.entry_price):
            continue
        used.add(ref.event_id)
        winners[row.event_id] = ref.event_id
    return winners


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed two-stage cloud entry study')
    source = branch.HISTORICAL_EXP
    cfg = json.loads((source/'config.json').read_text())
    raw = pd.read_csv(source/cfg['outputs']['paths_csv_gz'],dtype={'symbol':str},float_precision='round_trip')
    events = raw[~raw.is_control & raw.status.eq('executed')]
    matches = signals(events,add.confirmation_marks(events))
    original = events[events.event_id.isin(matches)].copy()
    delayed = pd.read_csv(later.EXP/'delayed_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    delayed = delayed[delayed.event_id.isin(matches)].copy()
    if (len(matches)!=len(set(matches.values())) or set(delayed.event_id)!=set(matches) or
            not original.entry_ms.eq(original.confirmation_ms).all()):
        raise ValueError('Expected a unique two-stage pair and a scheduled delay per opportunity')
    source_events = original[['event_id','symbol','side','confirmation_ms','entry_price','stop_price']].copy()
    source_events['prior_reclaim'] = source_events.event_id.map(matches)
    source_events.to_csv(EXP/'two_stage_signals.csv.gz',index=False)
    out = {}
    quotes = branch._quote_loader(cfg)
    for arm,plans in [('immediate',original),('delay12h',delayed[delayed.status.eq('executed')])]:
        data = wallet.prepare(plans,9,quote_loader=quotes)
        for slip in [0.,.0005]:
            key = arm+('_stress' if slip else '')
            funds,eq = wallet.account(data,slip=slip)
            if not np.isclose(1000+funds.cash_pnl.sum(),eq.nav.iat[-1],rtol=1e-9,atol=.005):
                raise ValueError('Two-stage portfolio cash mismatch')
            funds.to_csv(EXP/f'funded_{key}.csv.gz',index=False)
            eq.to_csv(EXP/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
            out[key] = dict(metrics={p:wallet.summarize(funds,eq,*bounds) for p,bounds in branch.PERIODS.items()},
                opportunities=len(plans),skips=data['skips'],gap_hours=data['gap_marks'],
                early_tails=int((funds.exit_reason.eq('series_tail_close')&funds.exit_bar_ms.lt(wallet.END-wallet.HOUR)).sum()))
            print(key,out[key]['metrics']['all'],flush=True)
    base = json.loads((source/'summary.json').read_text())['accounts']['breakout']['actual']['metrics']
    branch._write_json(EXP/'summary.json',dict(unfiltered=base,cases=out,matched_pairs=len(matches)))


if __name__=='__main__':
    main()
