"""Measure one preassigned 12H delay for every original breakout signal.

Read symbol, direction, original confirmation clock and entry-to-stop distance
known at that clock. The only new entry input is the actual next scheduled H1
open. No subsequent criterion determines parent inclusion.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_branch as branch

EXP = branch.EXP.parent / 'exp-cloud-close-breakout-fixed-delay-20261109-v1'


def delayed_symbol(symbol, parents, source, delay):
    bars12, hourly, _, _, _ = branch._load_symbol(symbol, source)
    cutoff = source['account']['end_ms']
    quotes, clocks, _ = branch.engine._prepare_symbol(bars12, hourly.loc[hourly.index < cutoff])
    events = []
    for row in parents.itertuples():
        stamp = int(row.confirmation_ms) + delay
        event = dict(event_id=row.event_id, parent_id=row.parent_id, symbol=symbol,
                     source_row_number=len(events), week=row.week, side=int(row.side),
                     is_control=True, confirmation_ms=int(row.confirmation_ms),
                     key_open_ms=int(row.key_open_ms), entry_status='pending', entry_i=None,
                     stop_basis='original_parent_relative_risk_at_delayed_open')
        i = int(clocks.searchsorted(stamp))
        if stamp >= cutoff or i >= len(clocks) or clocks[i] != stamp:
            event['entry_status'] = 'no_exact_delayed_open_or_beyond_cutoff'
        else:
            price = float(quotes[i, 0])
            risk = price * row.risk_fraction
            assert np.isfinite(risk) and risk > 0
            event.update(entry_i=i, entry_ms=stamp, entry_price=price, risk_distance=risk,
                         risk_fraction=row.risk_fraction, stop_price=price-row.side*risk)
        events.append(event)
    rows, _ = branch._event_paths(events, quotes, clocks, source)
    return pd.DataFrame(rows)


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Completed fixed-delay comparison')
    config = json.loads((EXP / 'config.json').read_text())
    source = json.loads(branch.CONFIG_PATH.read_text())
    audit = pd.read_csv(branch.EXP / 'candidate_audit.csv.gz')
    parents = audit.loc[audit.arm.eq('breakout') & ~audit.is_control & audit.risk_fraction.gt(0)]
    expected = json.loads((branch.EXP / 'summary.json').read_text())
    assert len(parents) == expected['candidate_events']['breakout']['executable_actual_paths']
    delay = config['delay_bars'] * branch.STEP
    paths = pd.concat([delayed_symbol(sym, sample, source, delay)
                       for sym, sample in parents.groupby('symbol')], ignore_index=True)
    assert set(paths.parent_id) == set(parents.parent_id) and paths.parent_id.is_unique
    valid = paths.loc[paths.status.eq('executed')]
    assert (valid.entry_ms - valid.confirmation_ms).eq(delay).all()
    assert valid.entry_fee_rate.eq(branch.TAKER).all() and valid.exit_fee_rate.eq(branch.TAKER).all()
    metrics, funded, hourly, admission = branch._account_scenario('fixed_delay', valid)
    paths.to_csv(EXP / 'paths.csv.gz', index=False)
    funded.to_csv(EXP / 'funded.csv.gz', index=False)
    hourly.to_csv(EXP / 'equity.csv.gz', index_label='hour_end_utc')
    branch._write_json(EXP / 'summary.json', dict(immediate=expected['accounts']['breakout']['actual'],
        delayed=dict(metrics=metrics, admission=admission), parents=len(paths), exact_later_opens=len(valid),
        missing_scheduled_open=paths.loc[~paths.status.eq('executed'),'exit_reason'].value_counts().to_dict(),
        source_experiment=branch.EXP.name, config=config))
    print(json.dumps(branch._json_safe(metrics['all']), ensure_ascii=False))


if __name__ == '__main__':
    main()
