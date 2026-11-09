"""Prefer better previously scored close signals competing for the same 12H entry."""
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_delivery_control as admin
from yoyo.evaluation import cloud_close_open_interest as oi

EXP = admin.EXP.parent / 'exp-cloud-close-score-priority-20261109-v1'
MODEL = EXP.parent / 'exp-signal-center-score-20261106-v1'


def rerank(rows, predictions):
    """Use only current-close forecasts; preserve unscored positions in the original order."""
    parents = rows.loc[rows.tp_r.eq(0), ['event_id', 'symbol', 'entry_ms', 'entry_order']].copy()
    parents['crc'] = parents.symbol.map(lambda symbol: zlib.crc32(symbol.encode()))
    parents = parents.sort_values(['entry_ms', 'entry_order', 'crc', 'symbol', 'event_id'])
    parents['original_rank'] = parents.groupby('entry_ms').cumcount()
    parents['score'] = parents.event_id.map(predictions)
    parents['new_rank'] = parents.original_rank
    for _, present in parents.loc[parents.score.notna()].groupby('entry_ms'):
        reordered = present.sort_values(['score', 'original_rank'], ascending=[False, True])
        parents.loc[reordered.index, 'new_rank'] = present.original_rank.to_numpy()
    if parents.duplicated(['entry_ms','new_rank']).any():
        raise ValueError('Scored order failed to preserve every entry slot')
    order = parents.set_index('event_id').new_rank
    return rows.assign(entry_order=rows.event_id.map(order)), parents


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Completed same-close score comparison')
    forecasts = pd.read_csv(MODEL / 'results/scores_oos.csv.gz', usecols=['signal_id','tf','fold','score'])
    forecasts = forecasts[forecasts.tf.eq('12H')].set_index('signal_id', verify_integrity=True)
    timestamps = pd.to_datetime(forecasts.index.str.split('|', regex=False).str[2].astype('int64'), utc=True)
    if (forecasts.fold.to_numpy() != ('test_' + timestamps.year.astype(str))).any() or not forecasts.score.between(0,1).all():
        raise ValueError('Invalid frozen year or score')
    archive = admin.archive
    extra = set(json.loads((archive.EXP / 'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP / 'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec = json.loads((admin.EXP / 'config.json').read_text())['settlements']
    spec.update(json.loads((oi.EXP / 'config.json').read_text())['other_funded_settlements'])
    new_settlements = json.loads((EXP / 'config.json').read_text())['additional_settlements']
    spec.update(new_settlements)
    minute_bars = pd.concat([pd.read_csv(path / 'settlement_index_1m.csv') for path in (admin.EXP, oi.EXP)])
    marks = minute_bars.groupby('symbol')[['low','close','high']].mean().to_dict('index')
    marks.update(admin.reference_prices(new_settlements, directory=EXP))
    paths = archive.pool(extra)
    with archive.market_quotes(extra):
        _, selected = admin.study.tenth.trend.inputs('Binance', paths[~paths.is_control])
        ready = admin.adjusted_legs(selected, extra, spec)
        ranked, decisions = rerank(ready, forecasts.score)
        decisions.to_csv(EXP / 'candidate_order.csv.gz', index=False)
        reference = pd.read_csv(admin.study.EXP / 'funded_tenth.csv.gz').set_index('event_id')
        if set(admin.admitted(ready).event_id) != set(reference.index):
            raise ValueError('Original priority did not reproduce the funded reference')
        admitted = admin.admitted(ranked)
        metrics, trades, equity = admin.fund(admitted.copy(), marks, 'proxy')
    trades.to_csv(EXP / 'funded.csv.gz')
    equity.to_csv(EXP / 'hourly.csv.gz', index_label='hour_end_utc')
    early = set(reference.index[reference.entry_ms < pd.Timestamp('2025-01-01', tz='UTC').value // 1_000_000])
    if not early.issubset(trades.index) or not np.allclose(reference.loc[sorted(early), 'cash_pnl'],
                                                         trades.loc[sorted(early), 'cash_pnl'], atol=1e-6):
        raise ValueError('Priority changed the unscored development-period account')
    original = pd.read_csv(admin.study.EXP / 'account_metrics.csv')
    original = original[(original.policy == 'tenth') & (original.scenario == 'actual')]
    if len(original) != len(metrics):
        raise ValueError('Missing original comparison metrics')
    summary = dict(reference=original.set_index('period')[list(metrics['all'])].to_dict('index'),
                   score_priority=metrics, eligible=len(decisions), scored=int(decisions.score.notna().sum()),
                   signal_ranks_changed=int(decisions.new_rank.ne(decisions.original_rank).sum()),
                   common_funded=len(set(reference.index) & set(trades.index)), original_funded=len(reference),
                   selected_delivery_legs=int(admitted.delivery.sum()))
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == '__main__':
    main()
