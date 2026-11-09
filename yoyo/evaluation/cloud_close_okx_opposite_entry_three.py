"""Replay the frozen OKX inverse ETH/BTC account after filtering LONG opportunities."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_adverse_btc_rank as ranking
from yoyo.evaluation import cloud_close_alt_season_delivery as baseline
from yoyo.evaluation import cloud_close_okx_entry_three as features

season = baseline.season
wallet = season.wallet
EXP = baseline.EXP.parent / 'exp-cloud-close-okx-opposite-entry-three-20261109-v1'
ORIGINAL = baseline.EXP / 'opposite'


def gate(frame, strict):
    """Combine key-bar range/ATR, prior weekly pierces and relative base volume with nullable logic."""
    prior = frame.prior_touches.astype('Float64')
    span = frame.range_atr.astype('Float64')
    volume = frame.relative_volume.astype('Float64')
    return prior.eq(0) & (span.ge(1.5) if strict else span.gt(1)) & volume.gt(1)


def settle(plans, src, directory, original_tape):
    """Use the baseline delivery price for the same contract/clock and native index minutes elsewhere."""
    tape = plans.copy()
    known = original_tape[original_tape.exit_reason.eq('delisting_settlement')]
    prices = known[['symbol', 'exit_bar_ms', 'exit_price', 'exit_fee_rate']].drop_duplicates()
    if prices.duplicated(['symbol', 'exit_bar_ms']).any():
        raise ValueError('The original tape contains conflicting delivery prices')
    for r in prices.itertuples():
        mask = tape.exit_reason.eq('series_tail_close') & tape.symbol.eq(r.symbol) & tape.exit_bar_ms.eq(r.exit_bar_ms)
        tape.loc[mask, ['exit_reason', 'exit_price', 'exit_fee_rate']] = ['delisting_settlement', r.exit_price, r.exit_fee_rate]
    tape, extra = ranking.settle(tape, src, directory)
    keys = ['event_id', 'tp_r']
    a = tape.set_index(keys)
    b = original_tape.set_index(keys)
    shared = a.index.intersection(b.index)
    for k in ['side', 'symbol', 'entry_ms', 'exit_bar_ms', 'exit_phase', 'exit_reason', 'key_open_ms']:
        if not a.loc[shared, k].eq(b.loc[shared, k]).all():
            raise ValueError(f'Common paths diverged in {k}')
    for k in ['entry_price', 'exit_price', 'stop_price', 'exit_fee_rate', 'entry_order']:
        np.testing.assert_allclose(a.loc[shared, k], b.loc[shared, k], rtol=1e-12, atol=1e-14)
    return tape, extra


def replay(label, allowed, plans, src, quotes, original_tape, original_ids):
    directory = EXP / label
    directory.mkdir(exist_ok=True)
    selected = plans[plans.event_id.isin(allowed)]
    admitted, skips = wallet.admit(selected[selected.tp_r.eq(0)], 9)
    ids = set(admitted.event_id)
    tape, extra = settle(selected[selected.event_id.isin(ids)], src, directory, original_tape)
    baseline.parent.save_execution_tape(tape, directory)
    old = baseline.old
    data, _ = old.parent.assemble(tape, old.changed_limits(tape, quotes), 'OKX', quotes)
    market = old.exposure.underlying(data, quotes)
    runner = market[-1]
    if set(runner.event_id) != ids or not runner.event_id.is_unique:
        raise ValueError('Native account and pre-admission roster diverged')
    cases = {}
    for name, slip in [('fees', 0.), ('stress', .0005)]:
        case = baseline.exposure.simulate(data, market, quotes, pd.Series(1.5, index=runner.event_id), slip, directory, name)
        cases[name] = case
        print(label, name, case.get('metrics', {}).get('all', 'unresolved margin'), flush=True)
        gc.collect()
    return dict(cases=cases, eligible=len(allowed), funded=len(ids), long=int(runner.side.eq(1).sum()),
                short=int(runner.side.eq(-1).sum()), retained_original=len(ids & original_ids),
                newly_admitted=len(ids - original_ids), removed=len(original_ids - ids), skips=skips,
                additional_delivery=extra, missing_holding_hours=data['gap_marks'])


def main():
    if (EXP / 'summary.json').exists():
        raise FileExistsError('Completed three-condition transfer')
    pool, src, _, _ = season.source.correction.pool()
    _, quotes, _ = season.source.inputs()
    state = pool.entry_ms.map(season.direction(src))
    if state.isna().any():
        raise ValueError('Missing observable ETH/BTC regime')
    plans = pool[pool.side.mul(state).lt(0)]
    parents = plans[plans.tp_r.eq(0)].set_index('event_id', verify_integrity=True)
    original_tape = pd.read_csv(ORIGINAL / 'selected_paths.csv.gz', dtype={'symbol': str}, float_precision='round_trip')
    actual = pd.read_csv(ORIGINAL / 'funded_inverse_1.5x.csv.gz', index_col='event_id', float_precision='round_trip')
    original_ids = set(actual.index)
    baseline_roster, _ = wallet.admit(parents.reset_index(), 9)
    if set(baseline_roster.event_id) != original_ids or original_ids != set(original_tape.event_id):
        raise ValueError('Reconstructed inverse regime does not reproduce original funded parents')
    longs = parents[parents.side.eq(1)][['symbol', 'side', 'entry_ms', 'key_open_ms']]
    shorts = parents.index[parents.side.eq(-1)]
    print('Eligible inverse parents', len(longs), 'long', len(shorts), 'short', flush=True)
    frame = features.build_features(longs.reset_index(), src).reindex(longs.index)
    if not frame.index.is_unique or frame.prior_touches.isna().all() or frame.range_atr.isna().all():
        raise ValueError('Cannot reconstruct causal long-entry features')
    frame['primary'] = gate(frame, False)
    frame['strict'] = gate(frame, True)
    frame.to_csv(EXP / 'long_features.csv.gz')
    result = {}
    counts = {}
    for name in ['primary', 'strict']:
        flags = frame[name]
        counts[name] = dict(passed=int(flags.sum()), failed=int((~flags).sum()), unknown=int(flags.isna().sum()))
        result[name] = replay(name, shorts.union(flags.index[flags.fillna(False)]), plans, src, quotes, original_tape, original_ids)
        if flags.isna().any():
            label = name + '_allow_unknown'
            result[label] = replay(label, shorts.union(flags.index[flags.fillna(True)]), plans, src, quotes, original_tape, original_ids)
    original = json.loads((baseline.EXP / 'summary.json').read_text())['arms']['opposite']['cases']
    baseline.source.correction.archive.prices.safe_json(EXP / 'summary.json', dict(
        original={'fees': original['1.5x']['metrics'], 'stress': original['1.5x_stress']['metrics']},
        original_longs=int(actual.side.eq(1).sum()), original_shorts=int(actual.side.eq(-1).sum()),
        eligible_longs=len(longs), eligible_shorts=len(shorts), long_decisions=counts, arms=result))


if __name__ == '__main__':
    main()
