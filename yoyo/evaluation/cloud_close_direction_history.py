"""Admit the side earning more on completed two-sided reference positions during the prior 90d."""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_tenth_sizing as tenth

EXP = tenth.EXP.parent / 'exp-cloud-close-direction-history-20261109-v1'
HOUR = tenth.archive.native.wallet.HOUR


def direction_scores(reference, clocks):
    """Use cash_pnl and notional of exits reported in [entry-12h-90d, entry-12h)."""
    if reference.event_id.duplicated().any() or not reference.side.isin([-1, 1]).all():
        raise ValueError('Invalid reference parent roster')
    if not (reference.notional.gt(0).all() and np.isfinite(reference[['cash_pnl', 'notional']]).all().all()):
        raise ValueError('Reference cash/quantity fields are incomplete')
    cut = clocks.to_numpy() - 12 * HOUR
    output = pd.DataFrame(index=clocks)
    for direction, label in [(1, 'long'), (-1, 'short')]:
        history = reference[reference.side.eq(direction)].sort_values('exit_bar_ms')
        reported = history.exit_bar_ms.to_numpy() + HOUR
        first = np.searchsorted(reported, cut - 90 * 24 * HOUR, side='left')
        end = np.searchsorted(reported, cut, side='left')
        cash = np.r_[0., history.cash_pnl.cumsum()]
        size = np.r_[0., history.notional.cumsum()]
        paid = size[end] - size[first]
        output[label] = np.divide(cash[end] - cash[first], paid,
                                  out=np.full(len(clocks), np.nan), where=paid > 0)
        output[label + '_count'] = end - first
    difference = output.long - output.short
    output['side'] = np.sign(difference.fillna(0.)).astype(int)
    return output


def simulate(paths, slip):
    ready = tenth.split.pair_data(paths[paths.tp_r.eq(0)], paths[paths.tp_r.eq(2)], 9)
    metrics, positions, curve = tenth.split.simulate(ready, 2 / 3, slip, exposure=.9)
    if not np.isclose(1000 + positions.cash_pnl.sum(), curve.nav.iloc[-1], rtol=1e-8, atol=1e-5):
        raise ValueError('Current cash and actual paid parent PnL do not reconcile')
    return metrics, positions, curve


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Direction history has already been evaluated')
    reference = pd.read_csv(tenth.EXP / 'funded_OKX.csv.gz')
    original = tenth.trend.thirds.okx_paths()
    tenth.trend.repair.check_parent_sets(original)
    _, paths = tenth.trend.inputs('OKX', original)
    scores = direction_scores(reference, pd.Index(paths.entry_ms.unique()).sort_values())
    desired = paths.entry_ms.map(scores.side)
    if desired.isna().any():
        raise ValueError('Missing decision')
    allowed = paths[desired.eq(0) | desired.eq(paths.side)]
    real = allowed[~allowed.is_control]
    controls = allowed[allowed.is_control & allowed.parent_id.isin(real.parent_id)]
    metrics, positions, curve = simulate(real, 0.)
    base = json.loads((tenth.EXP / 'summary.json').read_text())['OKX']
    summary = dict(full=metrics['all'], dev=metrics['dev'], dev_pass=tenth.trend.second.meets(metrics['dev']),
                   full_pass=tenth.trend.second.meets(metrics['all']), eligible_after_gate=len(real)//2,
                   eligible_before_gate=int((~paths.is_control).sum())//2,
                   dev_gain_over_reference=metrics['dev']['final_usdt'] > base['dev']['final_usdt'])
    records = [dict(venue='OKX', scenario='actual', period=p, **m) for p, m in metrics.items()]
    if summary['dev_pass']:
        paired = real[real.parent_id.isin(controls.parent_id)]
        for name, cohort, slip in [('paired', paired, 0.), ('control', controls, 0.), ('stress', real, .0005)]:
            checked = simulate(cohort, slip)[0]
            records.extend(dict(venue='OKX', scenario=name, period=p, **m) for p, m in checked.items())
    scores.to_csv(EXP / 'decisions_OKX.csv.gz', index_label='entry_ms')
    positions.to_csv(EXP / 'funded_OKX.csv.gz')
    curve.to_csv(EXP / 'hourly_OKX.csv.gz', index_label='hour_end_utc')
    pd.DataFrame(records).to_csv(EXP / 'account_metrics.csv', index=False)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
