"""Rank simultaneous native 12H candidates on the lagged change in realized funding."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_score_priority as scores

admin = scores.admin
EXP = scores.EXP.parent / 'exp-cloud-close-funding-rank-20261109-v1'
INPUT = scores.EXP.parent / 'exp-cloud-close-funding-pressure-20261109-v1/signal_pressure.csv.gz'


def market_spec(directory=EXP):
    spec, minute = {}, []
    for source, key in [(admin.EXP,'settlements'), (scores.oi.EXP,'other_funded_settlements'),
                        (scores.EXP,'additional_settlements')]:
        spec.update(json.loads((source/'config.json').read_text())[key])
        minute.append(pd.read_csv(source/'settlement_index_1m.csv'))
    if directory != EXP:
        spec.update(json.loads((EXP/'config.json').read_text())['additional_settlements'])
        minute.append(pd.read_csv(EXP/'settlement_index_1m.csv'))
    prices = pd.concat(minute).groupby('symbol')[['low','close','high']].mean().to_dict('index')
    extra = json.loads((directory/'config.json').read_text())['additional_settlements']
    if extra:
        spec.update(extra)
        prices.update(admin.reference_prices(extra, directory=directory))
    return spec, prices


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed funding-rank comparison')
    panel = pd.read_csv(INPUT, usecols=['event_id','entry_ms','side','pressure']).set_index('event_id',verify_integrity=True)
    expected = pd.read_csv(scores.EXP/'candidate_order.csv.gz',usecols=['event_id','entry_ms']).set_index('event_id')
    panel = panel.sort_index()
    if not expected.sort_index().entry_ms.equals(panel.entry_ms) or not np.array_equal(
            panel.side, panel.index.str.rsplit('|',n=1).str[-1].astype(int)):
        raise ValueError('Funding-pressure observations must cover the exact source signal roster and clocks')
    if not panel.pressure.dropna().map(np.isfinite).all():
        raise ValueError('Nonfinite funding ranks')
    compare(-panel.pressure, EXP, 'negative_signed_pressure')


def compare(priority, directory, column):
    """Compare a single precomputed priority against the frozen NAV/10 portfolio."""
    if not priority.index.is_unique or not priority.dropna().map(np.isfinite).all():
        raise ValueError('Admission priority must be unique by event and finite when observed')
    archive = admin.archive
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec, marks = market_spec(directory)
    paths = archive.pool(extra)
    with archive.market_quotes(extra):
        _, selected = admin.study.tenth.trend.inputs('Binance',paths[~paths.is_control])
        ready = admin.adjusted_legs(selected,extra,spec)
        ranked,decisions = scores.rerank(ready,priority)
        if set(decisions.event_id)!=set(priority.index):
            raise ValueError('The market-lifetime adjusted 12H universe changed')
        decisions.rename(columns={'score':column}).to_csv(directory/'admission_priority.csv.gz',index=False)
        taken = admin.admitted(ranked)
        deliveries = taken.loc[taken.delivery,['event_id','symbol','tp_r','exit_bar_ms','delivery_ambiguous']]
        deliveries.to_csv(directory/'selected_deliveries.csv', index=False)
        metrics,parents,eq = admin.fund(taken.copy(),marks,'proxy')
        parents.to_csv(directory/'funded.csv.gz')
        eq.to_csv(directory/'hourly.csv.gz',index_label='hour_end_utc')
        baseline = pd.read_csv(admin.study.EXP/'account_metrics.csv')
        baseline = baseline[(baseline.policy=='tenth') & (baseline.scenario=='actual')].set_index('period')
        result = dict(reference=baseline[list(metrics['all'])].to_dict('index'),ranked=metrics,
                      eligible=len(priority), usable=int(priority.notna().sum()),
                      reordered=int(decisions.new_rank.ne(decisions.original_rank).sum()),
                      delivery_legs=len(deliveries), development_pass=admin.study.tenth.trend.second.meets(metrics['dev']))
        if result['development_pass'] and metrics['dev']['final_usdt'] > baseline.loc['dev','final_usdt']:
            result['extra_5bp_market_slip'] = admin.fund(taken.copy(),marks,'proxy',slip=.0005)[0]
    (directory/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__ == '__main__':
    main()
