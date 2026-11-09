"""Keep the resting 2R partial unless the first post-entry 12H close breaks the key."""
import gc
import json
from contextlib import nullcontext

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_archive_complete as complete
from yoyo.evaluation import cloud_close_target_expiry as expiry

EXP = complete.EXP.parent / 'exp-cloud-close-breakout-expiry-20261109-v1'
TWELVE_HOURS = 12 * expiry.archive.native.wallet.HOUR


def choose(venue, paths):
    """Read high/low of [entry-12h, entry) and close of [entry, entry+12h)."""
    _, decisions = expiry.time_in_force(paths)
    decisions['first_close_breakout'] = False
    decisions['exact_bars'] = False
    for symbol, rows in decisions.groupby('symbol'):
        bars = expiry.trend.market.market_bars(venue, symbol)
        entry = rows.entry_ms.to_numpy()
        signal = bars.reindex(entry - TWELVE_HOURS)
        subsequent = bars.close.reindex(entry).to_numpy()
        boundary = np.where(rows.side.eq(1), signal.high, signal.low)
        known = np.isfinite(subsequent) & np.isfinite(boundary)
        decisions.loc[rows.index, 'exact_bars'] = known
        decisions.loc[rows.index, 'first_close_breakout'] = known & (
            rows.side.to_numpy() * (subsequent - boundary) > 0
        )
    decisions.loc[~decisions.first_close_breakout, 'choice'] = 'partial_2_3'
    changed = decisions.choice.eq('full_runner')
    if (decisions.loc[changed, 'exit_bar_ms'] < decisions.loc[changed, 'expiry_ms']).any():
        raise ValueError('An already-executed partial order was cancelled retroactively')
    return expiry.trend.rotation._rotated_paths(paths, decisions), decisions


def main():
    if (EXP / 'summary.json').is_file():
        raise FileExistsError('Breakout-conditioned partial expiry has already been evaluated')
    archive = expiry.archive
    extra = set(json.loads((archive.EXP / 'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((complete.EXP / 'summary.json').read_text())['new_archive_cohorts']['priced'])
    summary = {}
    records = []
    for venue in ('OKX', 'Binance'):
        if venue == 'OKX':
            paths = expiry.trend.thirds.okx_paths()
            expiry.trend.repair.check_parent_sets(paths)
            baseline = expiry.trend.repair.EXP / 'thirds/funded_OKX.csv.gz'
        else:
            paths = archive.pool(extra)
            baseline = complete.EXP / 'funded_normal_partial.csv.gz'
        with (archive.market_quotes(extra) if venue == 'Binance' else nullcontext()):
            selected, decisions = choose(venue, paths)
            real = selected[~selected.is_control]
            controls = selected[selected.is_control & selected.parent_id.isin(real.parent_id)]
            metrics, positions, hourly = expiry.trend.market.fund(venue, real, 0.)
            original = pd.read_csv(baseline, usecols=['event_id'])
            if set(original.event_id) != set(positions.index):
                raise ValueError('Changing only the partial limit displaced original funded entries')
            funded = decisions[decisions.event_id.isin(positions.index)]
            positions.to_csv(EXP / f'funded_{venue}.csv.gz')
            hourly.to_csv(EXP / f'hourly_{venue}.csv.gz', index_label='hour_end_utc')
            summary[venue] = dict(full=metrics['all'], dev=metrics['dev'],
                                  dev_pass=expiry.trend.second.meets(metrics['dev']),
                                  full_pass=expiry.trend.second.meets(metrics['all']),
                                  parents_missing_bars=int((~funded.exact_bars).sum()),
                                  original_late_fills_cancelled=int(funded.choice.eq('full_runner').sum()))
            records.extend(dict(venue=venue, scenario='actual', period=p, **m) for p, m in metrics.items())
            if summary[venue]['dev_pass']:
                paired = real[real.parent_id.isin(controls.parent_id)]
                for name, cohort, slip in [('paired', paired, 0.), ('control', controls, 0.), ('stress', real, .0005)]:
                    checked = expiry.trend.market.fund(venue, cohort, slip)[0]
                    records.extend(dict(venue=venue, scenario=name, period=p, **m) for p, m in checked.items())
        del paths, selected, real, controls, positions, hourly
        gc.collect()
    pd.DataFrame(records).to_csv(EXP / 'account_metrics.csv', index=False)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
