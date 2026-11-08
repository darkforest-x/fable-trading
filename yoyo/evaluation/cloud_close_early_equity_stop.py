"""Extend the previously registered account-level close drawdown stop to two earlier thresholds."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_equity_stop as parent

EXP = parent.ROOT/'experiments/active/exp-cloud-close-early-equity-stop-20261109-v1'


def main():
    cfg = json.loads((EXP/'config.json').read_text())
    events, _ = parent.read_inputs()
    real = events[~events.is_control].copy()
    ctl = events[events.is_control].copy()
    grid = parent.build_quote_grid(events)
    rows, audits = [], {}
    for threshold in cfg['thresholds']:
        result = parent.simulate(real,threshold,grid=grid)
        parent._write_sim(threshold,result,EXP)
        rows.extend(parent._metric_rows(threshold,result))
        audits[threshold] = result['audit']
        del result
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(parent._pass_gate,axis=1)]
    pick = None if eligible.empty else float(eligible.drawdown_stop_pct.iloc[0])/100
    checks = {}
    if pick is not None:
        paired = real[real.parent_id.isin(ctl.parent_id)]
        for label,pool,slip in [('matched_actual',paired,0.),('matched_control',ctl,0.),('stress',real,.0005)]:
            sim = parent.simulate(pool,pick,grid=grid,market_slippage=slip)
            checks[label] = dict(metrics=parent._period_rows(sim),audit=sim['audit'])
            del sim
            gc.collect()
    (EXP/'summary.json').write_text(json.dumps(dict(pick=pick,audits=audits,checks=checks),
                                               indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
