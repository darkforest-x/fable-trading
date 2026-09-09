"""Cashbook comparison on the same fixed, matchable event identities.

Source: exp-altseason-multivenue-20260910-v1 fixed event/control artifacts.
The original three random schedules have unequal candidate coverage and remain
contextual references. This supplement takes valid control_number=0 and its
valid matched actual event, then runs the unchanged cashbook independently for
each side. It does not compute signals, features, exits or new random matches.

Matching is conditional on control availability, never on realized returns.
Candidate quantities match over the full61-day window, while within-month
counts can differ because frozen controls were selected within the same UTC
week. Monthly NAV returns inherit holdings; monthly candidate/trade counts use
each side's actual entry clock. A positive paired result applies only to the
matched subset and is not an estimate for unmatched candidates or future coins.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_engine import ARMS
from yoyo.evaluation.altseason_portfolio import run_portfolio
from yoyo.evaluation.altseason_research import (
    END, EXPERIMENT, PERIODS, ROOT, SCOPES, START,
    portfolio_periods, read_events, scope_rows,
)


def matched_sets(actual: pd.DataFrame, controls: pd.DataFrame):
    """One-to-one valid original/control0 pairs, with no outcome ranking.

    Inputs are already restricted to a single scope, timeframe and arm.
    ``full_candidates`` in output summaries counts valid original events;
    ``full_candidate_rows`` also retains original nonfillable rows.
    """
    required_actual = {'event_id', 'valid'}
    required_controls = {'event_id', 'matched_event_id', 'valid', 'control_number'}
    if not required_actual.issubset(actual.columns):
        raise ValueError('Actual candidate identity/valid schema is missing')
    if not required_controls.issubset(controls.columns):
        raise ValueError('Control candidate identity/valid schema is missing')
    for name, frame in (('actual', actual), ('control', controls)):
        if frame.event_id.duplicated().any():
            raise ValueError('Duplicate ' + name + ' event ids')
        if len(frame) and not frame.valid.isin([True, False]).all():
            raise ValueError('Explicit boolean validity required')
    full = actual.loc[actual.valid.eq(True)].copy()
    zero = controls.loc[controls.valid.eq(True) & controls.control_number.eq(0)].copy()
    if zero.matched_event_id.duplicated().any():
        raise ValueError('A decision has more than one valid control0')
    zero = zero.loc[zero.matched_event_id.isin(full.event_id)].copy()
    paired = full.loc[full.event_id.isin(zero.matched_event_id)].copy()
    if len(paired) != len(zero):
        raise AssertionError('Paired candidate identity counts differ')
    if len(paired):
        pairs = paired.set_index('event_id').join(
            zero.set_index('matched_event_id'), rsuffix='_control', how='inner')
        # These are frozen matching contracts, not future outcome filters.
        for key in ('venue', 'asset', 'instrument', 'minutes', 'arm', 'exit_rule'):
            if key in pairs and not pairs[key].eq(pairs[key+'_control']).all():
                raise ValueError('Frozen pair disagrees on ' + key)
        a_week = pd.DatetimeIndex(pairs.decision_time).normalize()
        c_week = pd.DatetimeIndex(pairs.decision_time_control).normalize()
        a_week -= pd.to_timedelta(a_week.weekday, unit='d')
        c_week -= pd.to_timedelta(c_week.weekday, unit='d')
        if not (a_week == c_week).all():
            raise ValueError('Frozen controls are outside the matched UTC week')
    return full, paired, zero


def compare_account(actual, controls, prices, scope, minutes, arm, outdir=None):
    """Run two unchanged accounts and align full/first31/last30 NAV summaries."""
    full, paired, random = matched_sets(actual, controls)
    actual_path, actual_selected = run_portfolio(paired, prices, START, END, minutes)
    random_path, random_selected = run_portfolio(random, prices, START, END, minutes)
    if outdir is not None:
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        prefix = f'{scope}_{minutes}_{arm}'
        for suffix, frame in (('actual', actual_path), ('random0', random_path),
                              ('actual_selected', actual_selected), ('random0_selected', random_selected)):
            frame.to_csv(outdir/(prefix+'_'+suffix+'.csv.gz'), index=False)
    actual_summary = portfolio_periods(actual_path, actual_selected, scope, minutes, arm)
    random_summary = portfolio_periods(random_path, random_selected, scope, minutes, arm)
    rows = []
    for a, c in zip(actual_summary, random_summary):
        if a['period'] != c['period']:
            raise AssertionError('Cashbook periods disagree')
        start, end = PERIODS[a['period']]
        def count(frame):
            return int((frame.entry_time.ge(start) & frame.entry_time.lt(end)).sum()) if len(frame) else 0
        nf, npair, nc = count(full), count(paired), count(random)
        input_rows = int((actual.decision_time.ge(start) & actual.decision_time.lt(end)).sum()) if len(actual) else 0
        rows.append(dict(scope=scope, minutes=minutes, arm=arm, period=a['period'],
            paired_actual_return_pct=a['return_pct'], paired_random_return_pct=c['return_pct'],
            paired_excess_return_pct=a['return_pct']-c['return_pct'],
            paired_actual_mdd_pct=a['max_drawdown_pct'], paired_random_mdd_pct=c['max_drawdown_pct'],
            paired_candidates=npair, paired_random_candidates_by_clock=nc,
            full_candidates=nf, full_candidate_rows=input_rows,
            matched_fraction=npair/nf if nf else np.nan,
            paired_actual_trades=a['trades'], paired_random_trades=c['trades'],
            paired_actual_boundary_marks=a['boundary_marks'], paired_random_boundary_marks=c['boundary_marks'],
            paired_actual_peak_positions=a['peak_positions'], paired_random_peak_positions=c['peak_positions'],
            actual_opening_equity=a['opening_equity'], random_opening_equity=c['opening_equity'],
            actual_closing_equity=a['closing_equity'], random_closing_equity=c['closing_equity'],
            control_number=0,
            method='one-to-one valid matched event ids; independent unchanged cashbooks; no outcome selection',
            period_definition='calendar close NAV with holdings inherited; candidate/trade counts by each side entry clock',
            interpretation='matched subset only; unavailable controls do not imply zero event returns'))
    return rows


def run_comparisons(events, controls, price_map, output):
    rows = []
    for scope in SCOPES:
        actual_pool, control_pool = scope_rows(events, scope), scope_rows(controls, scope)
        for minutes in (60, 240):
            for arm in ARMS:
                actual = actual_pool.loc[actual_pool.minutes.eq(minutes) & actual_pool.arm.eq(arm)].copy()
                random = control_pool.loc[control_pool.minutes.eq(minutes) & control_pool.arm.eq(arm)].copy()
                rows.extend(compare_account(actual, random, price_map[minutes], scope, minutes, arm,
                                            Path(output)/'paired_portfolios'))
    return pd.DataFrame(rows)


def _committed_sources():
    paths = [Path(__file__).resolve(), ROOT/'yoyo/evaluation/altseason_engine.py',
             ROOT/'yoyo/evaluation/altseason_portfolio.py', ROOT/'yoyo/evaluation/altseason_research.py']
    for path in paths:
        committed = subprocess.check_output(['git', 'show', 'HEAD:'+str(path.relative_to(ROOT))], cwd=ROOT)
        if committed != path.read_bytes():
            raise ValueError('Commit exact paired-account source and dependencies before running: '+str(path))
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=EXPERIMENT/'results')
    args = parser.parse_args()
    code_sha = _committed_sources()
    source_hashes = {}
    inputs = []
    for name in ('events.csv.gz', 'controls.csv.gz'):
        path = args.results/name
        source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        inputs.append(read_events(path))
    events, controls = inputs
    # Read only already-frozen source marks; never build features or recalculate exits.
    price_map = {60: {}, 240: {}}
    references = pd.concat([frame[['instrument', 'minutes', 'features_path']] for frame in inputs]).drop_duplicates()
    if references.duplicated(['instrument', 'minutes']).any():
        raise ValueError('An instrument/timeframe references conflicting frozen features')
    for ref in references.itertuples():
        path = Path(ref.features_path)
        source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        price_map[int(ref.minutes)][ref.instrument] = pd.read_pickle(path)[['open', 'close']]
    summary = run_comparisons(events, controls, price_map, args.results)
    output = args.results/'paired_portfolio_summary.csv'
    summary.to_csv(output, index=False)
    manifest = dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),
        code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        code_sha256=code_sha, input_sha256=source_hashes, output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        results_rows=len(summary), control_number=0, reestimated_signals=False, reestimated_exits=False,
        full_candidate_definition='valid original candidates; unfillable originals retained in full_candidate_rows',
        existing_random_reference='unchanged; original all-candidate accounts versus variable-coverage schedules are not a paired comparison',
        limitations='Conditional matched subset; one frozen random schedule; no new inferential significance or funding/impact claim.')
    (args.results/'paired_portfolio_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(dict(output=str(output),rows=len(summary))), flush=True)


if __name__ == '__main__':
    main()
