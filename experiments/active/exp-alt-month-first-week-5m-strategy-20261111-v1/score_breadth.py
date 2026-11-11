"""Explore the observed concentration of 15m positives in simultaneous touches.

At the UTC 15m close count unique symbols in the audited first-touch universe
that first touched the same side during that closed interval. Apply the
existing five-symbol breadth threshold to that count. The score is the frozen
pre-2025 monthly scorecard. Outcome-driven follow-up, including 2025/2026.
"""
import pandas as pd
from timeframe_score import Inputs, HERE, SPLIT, account_result, simulate_batch


def main():
    data = Inputs()
    scored = pd.read_csv(HERE/'timeframe_scores.csv.gz')
    scored = scored[scored.minutes.eq(15)&scored.entry_ms.ge(SPLIT)].copy()
    clocks = data.candidates[data.candidates.rule.eq('touch_close')].copy()
    clocks['entry_15m'] = (clocks.entry_ms.sub(300000)//900000+1)*900000
    count = clocks.groupby(['entry_15m','side']).symbol.nunique().rename('breadth')
    scored = scored.join(count,on=['entry_ms','side'],validate='many_to_one')
    assert scored.breadth.notna().all()
    cards = pd.read_csv(HERE/'timeframe_scorecard.csv')
    gates = cards[cards.timeframe_minutes.eq(15)].groupby('stage').threshold.first()
    summary, ledger, candidate = [], [], []
    for bps in (1,5):
        fills, marks = simulate_batch(data.windows,scored,slippage_bps=bps,tp_mode='fixed_pct',
                                    tp_value=.04,sl_pct=.02,horizon_bars=12)
        fills = fills.merge(scored[['candidate_id','breadth','pre_score','confirmation_score']],
                            on='candidate_id', validate='one_to_one')
        if bps==1:
            candidate = fills
        for stage in ('none','pre','confirmation'):
            f = fills if stage=='none' else fills[fills[stage+'_score']>=gates[stage]].copy()
            if stage!='none':
                f['entry_priority'] = f[stage+'_score']
            for min_breadth in (1,5):
                picked = f[f.breadth>=min_breadth]
                paths = marks[marks.candidate_id.isin(picked.candidate_id)]
                for period in ('2025','2026','forward'):
                    res = account_result(picked,paths,period,.05)
                    rows = res['funded_trades']
                    row = dict(res['summary'],stage=stage,min_breadth=min_breadth,slip_bps=bps,
                               distinct_entry_clocks=rows.entry_ms.nunique(),
                               distinct_entry_dates=pd.to_datetime(rows.entry_ms,unit='ms',utc=True).dt.date.nunique())
                    summary.append(row)
                    if bps==1 and period=='forward':
                        ledger.append(rows.assign(stage=stage,min_breadth=min_breadth))
    pd.DataFrame(summary).to_csv(HERE/'score_breadth_accounts.csv',index=False)
    pd.concat(ledger,ignore_index=True).to_csv(HERE/'score_breadth_funded_trades.csv.gz',index=False)
    candidate.to_csv(HERE/'score_breadth_candidates.csv.gz',index=False)


if __name__=='__main__':
    main()
