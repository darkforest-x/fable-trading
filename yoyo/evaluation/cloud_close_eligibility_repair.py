"""Repair four previous OKX studies against an independently checked, value-based eligibility mask."""
import argparse
import json

import pandas as pd

from yoyo.evaluation import cloud_close_concentration as concentration
from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_okx_agreement as agreement
from yoyo.evaluation import cloud_close_prior_expectancy as expectancy
from yoyo.evaluation import cloud_close_reclaim_failure as reclaim
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = leader.wallet.ROOT / 'experiments/active/exp-cloud-close-eligibility-repair-20261109-v1'


def check_parent_sets(paths):
    independent = pd.read_csv(agreement.EXP / 'event_screen.csv.gz',
                              usecols=['event_id', 'parent_id', 'is_control', 'eligible_90d'])
    roster = paths[paths.tp_r.eq(0)]
    actual = roster[~roster.is_control]
    control = roster[roster.is_control & roster.parent_id.isin(actual.parent_id)]
    reference = independent[independent.eligible_90d]
    reference_real = reference[~reference.is_control]
    reference_ctrl = reference[reference.is_control & reference.parent_id.isin(reference_real.parent_id)]
    for sample, expected, n in [(actual, reference_real, 11780), (control, reference_ctrl, 10816)]:
        if len(sample) != n or set(sample.event_id) != set(expected.event_id):
            raise ValueError('Event roster does not match independently ranked OKX inputs')


def repair_thirds(folder):
    paths = thirds.okx_paths()
    check_parent_sets(paths)
    table, counts = thirds.run_venue('OKX', paths, folder)
    funded = pd.read_csv(folder / 'funded_OKX.csv.gz', usecols=['event_id'])
    base = pd.read_csv(leader.EXP / 'funded_1.0.csv.gz', usecols=['event_id'])
    if len(funded) != 1569 or not funded.event_id.equals(base.event_id):
        raise ValueError('Partial fraction changed identities or order of funded events')
    frozen = pd.read_csv(thirds.EXP / 'account_metrics.csv')
    binance_rows = frozen[frozen.venue.eq('Binance')]
    binance_summary = json.loads((thirds.EXP / 'summary.json').read_text())
    if len(binance_rows) != len(table) or binance_rows.duplicated(['period', 'scenario']).any():
        raise ValueError('Incomplete frozen Binance results')
    combined = pd.concat([table, binance_rows], ignore_index=True)
    thirds.write_results(combined, {'OKX': counts, 'Binance': binance_summary['counts']['Binance']}, folder)


def main():
    studies = {'thirds': repair_thirds, 'concentration': concentration.main,
               'prior-expectancy': expectancy.main, 'reclaim-failure': reclaim.main}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', choices=studies, action='append')
    selected = parser.parse_args().study or studies
    for label in selected:
        folder = EXP / label
        folder.mkdir(exist_ok=True)
        if (folder / 'summary.json').is_file():
            raise FileExistsError(f'Corrected results already exist at {folder}')
        studies[label](folder)
    if not all((EXP/label/'summary.json').is_file() for label in studies):
        return
    summary = {label: json.loads((EXP/label/'summary.json').read_text()) for label in studies}
    summary['thirds_binance_original_ledgers'] = str(thirds.EXP.relative_to(leader.wallet.ROOT))
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
