"""Task62 V3: independent 10R/20R targets crossed with break-even timing.

Owner authorized the target x protection grid on 2026-10-19. Admission uses
only the frozen V1 signal/confirmation OHLC and four-bar structural stop.
Future minute OHLC is an outcome only. Opposite arrows and occupancy are
absent. All historical splits/controls/costs remain frozen; no production use.
The vector two-phase oracle independently audits the scalar V1 exit engine.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import (
    EXP as V1, Policy, load_minutes, prepare, trace, metrics, weekly_test,
)
from yoyo.evaluation.eth_stoch_tail_forensics import EXP as V2, endpoint

EXP = Path('experiments/active/exp-eth5m-stoch-target-be-20261019-v3')


def policies(cfg):
    result = []
    for target in cfg['targets_r']:
        result.append(Policy(f'tp{target}_none', target=target, target_tick=.01))
        for destination in ('price', 'net'):
            for trigger in cfg['be_triggers_r']:
                result.append(Policy(f'tp{target}_{destination}_{trigger:g}r',
                    target=target, be=trigger, net_be=destination == 'net', target_tick=.01))
    return result


def oracle(ctx, r, end, p, cost=.002):
    """Vector first-event ordering before/after a single close-driven stop move.

    Signed prices make long/short symmetric. A close cannot activate protection
    in a minute with a prior stop/target exit. The new floor starts at j+1.
    """
    start = int(r.entry_i)
    opening = r.side * ctx['o'][start:end]
    favorable = (ctx['h'] if r.side == 1 else -ctx['l'])[start:end]
    adverse = (ctx['l'] if r.side == 1 else -ctx['h'])[start:end]
    closing = r.side * ctx['c'][start:end]
    entry = r.side * r.entry
    floor = r.side * r.stop
    ceiling = r.side * (round((r.entry + r.side*p.target*r.risk)/.01)*.01)
    destination = r.entry + r.side*(cost*r.entry if p.net_be else 0)
    destination = (np.ceil(destination*100-1e-8) if r.side == 1 else np.floor(destination*100+1e-8))/100
    new_floor = r.side*destination

    def event(offset, stop):
        hits = np.flatnonzero((adverse[offset:] <= stop) | (favorable[offset:] >= ceiling))
        return offset + int(hits[0]) if len(hits) else len(opening)

    first = event(0, floor)
    activated = -1
    if p.be:
        hits = np.flatnonzero(closing[:first]-entry >= max(p.be*r.risk, new_floor-entry))
        if len(hits):
            activated = int(hits[0])
            floor = new_floor
            first = event(activated+1, floor)
    if first == len(opening):
        return dict(exit_i=end-1, exit_price=float(ctx['c'][end-1]), reason='boundary',
                    be_activation_i=start+activated if activated >= 0 else -1,
                    be_stop_price=float(destination), ambiguous=0)
    if opening[first] <= floor:
        reason, price = 'stop_gap', opening[first]
    elif opening[first] >= ceiling:
        reason, price = 'target_gap', opening[first]
    elif adverse[first] <= floor:
        reason, price = 'stop', floor
    else:
        reason, price = 'target', ceiling
    return dict(exit_i=start+first, exit_price=float(r.side*price), reason=reason,
                be_activation_i=start+activated if activated >= 0 else -1,
                be_stop_price=float(destination),
                ambiguous=int(reason == 'stop' and favorable[first] >= ceiling))


def replay(ctx, events, arms, cfg, out, population):
    quiet = dict(ctx, arrow=np.zeros_like(ctx['arrow']))
    records = []
    for p in arms:
        for r in events.itertuples(index=False):
            end = endpoint(ctx, cfg, r.segment)
            z = trace(quiet, int(r.entry_i), int(r.side), float(r.entry), float(r.stop),
                      p, cost=cfg['round_trip_cost'], end_i=end)
            check = oracle(ctx, r, end, p, cfg['round_trip_cost'])
            assert z['exit_i'] == check['exit_i'] and z['reason'] == check['reason'], (p.name, r.pair_id, z, check)
            assert abs(z['exit_price']-check['exit_price']) < 1e-8, (p.name, r.pair_id, z, check)
            assert z['be_activated'] == (check['be_activation_i'] >= 0)
            assert z['ambiguous_old_stop_target'] == check['ambiguous']
            records.append(dict(pair_id=r.pair_id, policy=p.name, target_r=p.target,
                be_trigger_r=p.be, be_destination='none' if not p.be else ('net' if p.net_be else 'price'),
                entry_i=r.entry_i, entry_time=r.entry_time, side=r.side, entry=r.entry,
                stop=r.stop, risk=r.risk, segment=r.segment, week=r.week,
                fee_r=cfg['round_trip_cost']*r.entry/r.risk,
                be_activation_i=check['be_activation_i'], be_stop_price=check['be_stop_price'], **z))
        print(population, p.name, len(events), flush=True)
    rows = pd.DataFrame(records)
    rows['gross_r'] = rows.gross_points/rows.risk
    rows['target_hit'] = rows.reason.isin(['target', 'target_gap'])
    rows['protected_stop'] = rows.be_activated & rows.reason.isin(['stop', 'stop_gap'])
    for src, dst in [('entry_i', 'entry_local'), ('exit_i', 'exit_local'), ('be_activation_i', 'be_activation_minute_local')]:
        rows[dst] = [str(ctx['minute_index'][i].tz_convert('Asia/Shanghai')) if i >= 0 else '' for i in rows[src]]
    rows.to_csv(out/f'{population}_trades.csv.gz', index=False)
    return rows


def aggregate(rows):
    m = metrics(rows)
    # Independent overlapping trades cannot define an account drawdown.
    m.pop('closed_points_drawdown')
    closed = rows[rows.closed]
    return dict(**m, gross_r=float(closed.gross_r.sum()),
        pf_r=float(closed.loc[closed.net_r > 1e-8, 'net_r'].sum() / -closed.loc[closed.net_r < -1e-8, 'net_r'].sum()),
        target_hits=int(rows.target_hit.sum()), protected_stops=int(rows.protected_stop.sum()),
        original_stops=int((rows.reason.isin(['stop', 'stop_gap']) & ~rows.be_activated).sum()),
        net_zero=int(closed.net_points.abs().le(1e-8).sum()),
        protected_stop_net_points=float(rows.loc[rows.protected_stop, 'net_points'].sum()),
        censored_mtm_net_points=float(rows.loc[~rows.closed, 'net_points'].sum()),
        censored_mtm_net_r=float(rows.loc[~rows.closed, 'net_r'].sum()),
        mtm_inclusive_net_points=float(rows.net_points.sum()), mtm_inclusive_net_r=float(rows.net_r.sum()))


def analyze(signal, random, arms, out):
    summaries, inference, changes = [], [], []
    for p in arms:
        s = signal[signal.policy.eq(p.name)]
        c = random[random.policy.eq(p.name)]
        base = signal[signal.policy.eq(f'tp{int(p.target)}_none')]
        merged = s.merge(base, on='pair_id', suffixes=('', '_base'), validate='one_to_one')
        assert not (merged.target_hit & ~merged.target_hit_base).any()
        pair = s.merge(c, on='pair_id', suffixes=('', '_random'), validate='one_to_one')
        pair = pair[pair.closed & pair.closed_random].copy()
        pair['excess_points'] = pair.net_points-pair.net_points_random
        for seg in ('all', 'development', 'validation', 'examples'):
            a = s if seg == 'all' else s[s.segment.eq(seg)]
            b = c if seg == 'all' else c[c.segment.eq(seg)]
            q = merged if seg == 'all' else merged[merged.segment.eq(seg)]
            z = pair if seg == 'all' else pair[pair.segment.eq(seg)]
            summaries.append(dict(policy=p.name, segment=seg, target_r=p.target,
                be_trigger_r=p.be, be_destination='none' if not p.be else ('net' if p.net_be else 'price'),
                **aggregate(a), baseline_target_hits=int(q.target_hit_base.sum()),
                retained_fraction=float(q.target_hit.sum()/q.target_hit_base.sum()) if q.target_hit_base.any() else None,
                sacrificed_targets=int((q.target_hit_base & ~q.target_hit).sum()),
                random_closed=int(b.closed.sum()), random_net_points=float(b.loc[b.closed, 'net_points'].sum()),
                random_net_r=float(b.loc[b.closed, 'net_r'].sum()),
                paired_n=len(z), paired_signal_net_points=float(z.net_points.sum()),
                paired_random_net_points=float(z.net_points_random.sum())))
            test = weekly_test(z.groupby('week').excess_points.sum())
            inference.append(dict(policy=p.name, segment=seg, pairs=len(z),
                excess_points=float(z.excess_points.sum()), **test))
            q = q[q.closed & q.closed_base].copy()
            q['delta'] = q.net_points-q.net_points_base
            saved = q.protected_stop & ~q.target_hit_base
            lost = q.target_hit_base & ~q.target_hit
            same_target = q.target_hit_base & q.target_hit
            for label, mask in [('rescued_original_stops', saved), ('sacrificed_targets', lost),
                                ('retained_targets', same_target), ('other', ~(saved | lost | same_target))]:
                t = q[mask]
                changes.append(dict(policy=p.name, segment=seg, category=label, n=len(t),
                    baseline_net_points=float(t.net_points_base.sum()), net_points=float(t.net_points.sum()),
                    delta_net_points=float(t.delta.sum()),
                    delta_net_r=float((t.net_r-t.net_r_base).sum())))
            assert abs(sum(changes[-j]['delta_net_points'] for j in range(1, 5))-q.delta.sum()) < 1e-7
    summary = pd.DataFrame(summaries)
    inf = pd.DataFrame(inference)
    for seg in inf.segment.unique():
        group = inf[inf.segment.eq(seg)].dropna(subset=['p']).sort_values('p')
        top = 0.
        for rank, (i, r) in enumerate(group.iterrows()):
            top = max(top, min(1., r.p*(len(group)-rank)))
            inf.loc[i, 'holm_p'] = top
    summary.to_csv(out/'summary.csv', index=False)
    inf.to_csv(out/'matched_inference.csv', index=False)
    pd.DataFrame(changes).to_csv(out/'attribution.csv', index=False)
    selection = {}
    for metric in ('net_points', 'net_r'):
        selection[metric] = {}
        for segment in ('development', 'all'):
            t = summary[summary.segment.eq(segment)].sort_values([metric, 'policy'], ascending=[False, True])
            chosen = str(t.iloc[0].policy)
            detail = summary[summary.policy.eq(chosen)].astype(object)
            detail = detail.where(pd.notna(detail), None)
            selection[metric][segment] = dict(policy=chosen,
                all_segments=detail.to_dict('records'))
    selection['caveat'] = 'All-history ranking is hindsight. Chronological selection also reuses exposed history; neither is forward evidence.'
    selection['metrics_not_applicable'] = 'No classifier/ranking score: AUC/top-decile score metrics undefined; matched random economic null reported.'
    (out/'selection.json').write_text(json.dumps(selection, indent=2, allow_nan=False)+'\n')
    return summary


def baseline_parity(signal, random):
    count = 0
    for name, rows in [('signal', signal), ('random', random)]:
        old = pd.read_csv(V2/'results'/f'{name}_brackets.csv.gz')
        for target in (10, 20):
            a = rows[rows.policy.eq(f'tp{target}_none')].set_index('pair_id').sort_index()
            b = old[old.policy.eq(f'target_{target}r_no_opposite')].set_index('pair_id').sort_index()
            assert a.index.equals(b.index)
            for col in ('exit_i', 'reason', 'closed'):
                assert a[col].equals(b[col]), (name, target, col)
            for col in ('exit_price', 'net_points', 'net_r'):
                assert np.allclose(a[col], b[col], rtol=0, atol=1e-8), (name, target, col)
            count += len(a)
    return count


def diagnostics(out):
    """Reconcile every saved row and explain selected arms without re-selection."""
    out = Path(out)
    s = pd.read_csv(out/'signal_trades.csv.gz')
    c = pd.read_csv(out/'random_trades.csv.gz')
    summary = pd.read_csv(out/'summary.csv')
    selection = json.loads((out/'selection.json').read_text())
    for rows in (s, c):
        assert not rows.duplicated(['policy', 'pair_id']).any()
        assert rows.groupby('policy').size().nunique() == 1
        assert rows.risk.between(0, 8+1e-8, inclusive='right').all()
        assert np.allclose(rows.gross_points, rows.side*(rows.exit_price-rows.entry), atol=1e-8, rtol=0)
        assert np.allclose(rows.net_points, rows.gross_points-rows.entry*.002, atol=1e-8, rtol=0)
        assert np.allclose(rows.net_r, rows.net_points/rows.risk, atol=1e-8, rtol=0)
        assert (rows.exit_i >= rows.entry_i).all()
        assert (rows.loc[rows.protected_stop, 'be_activation_i'] < rows.loc[rows.protected_stop, 'exit_i']).all()
        assert rows.reason.isin(['target', 'target_gap', 'stop', 'stop_gap', 'boundary']).all()
        assert (rows.closed == rows.reason.ne('boundary')).all()
    for r in summary.itertuples(index=False):
        a = s[s.policy.eq(r.policy)]
        if r.segment != 'all':
            a = a[a.segment.eq(r.segment)]
        q = a[a.closed]
        assert len(a) == r.orders and len(q) == r.closed
        assert abs(q.net_points.sum()-r.net_points) < 1e-7
        assert r.target_hits+r.protected_stops+r.original_stops+r.censored == r.orders
        assert r.target_hits+r.sacrificed_targets == r.baseline_target_hits

    selected = sorted({selection[metric][part]['policy'] for metric in ('net_points', 'net_r')
                       for part in ('development', 'all')})
    years = []
    baseline_tests = []
    for name in selected:
        a = s[s.policy.eq(name)]
        for year, q in a.groupby(pd.to_datetime(a.entry_time).dt.year):
            years.append(dict(policy=name, year=int(year), **aggregate(q)))
        target = int(a.target_r.iloc[0])
        b = s[s.policy.eq(f'tp{target}_none')]
        z = a.merge(b, on='pair_id', suffixes=('', '_base'), validate='one_to_one')
        z = z[z.closed & z.closed_base].copy()
        z['delta'] = z.net_points-z.net_points_base
        for seg in ('all', 'development', 'validation', 'examples'):
            q = z if seg == 'all' else z[z.segment.eq(seg)]
            baseline_tests.append(dict(policy=name, segment=seg, pairs=len(q),
                delta_net_points=float(q.delta.sum()), **weekly_test(q.groupby('week').delta.sum()),
                caveat='Post-selection descriptive uncertainty; not an independent policy discovery test.'))
    pd.DataFrame(years).to_csv(out/'selected_years.csv', index=False)
    pd.DataFrame(baseline_tests).to_csv(out/'selected_baseline_deltas.csv', index=False)

    entries = pd.read_csv(V1/'results_v1/entries.csv')
    drawings = pd.read_csv(V2/'results/drawings.csv')
    entries['signal_utc'] = pd.to_datetime(entries.signal_time, utc=True)
    drawings['signal_utc'] = pd.to_datetime(drawings.signal_local, utc=True)
    drawmap = drawings[['ordinal', 'signal_utc', 'eligible']].merge(
        entries[['pair_id', 'signal_utc']], on='signal_utc', how='left', validate='one_to_one')
    draw_rows = drawmap[drawmap.eligible].merge(s, on='pair_id', validate='one_to_many')
    draw_rows.drop(columns='signal_utc').to_csv(out/'drawing_outcomes.csv', index=False)
    # Convenient full ledgers for the all-history and development selections.
    s[s.policy.isin(selected)].to_csv(out/'selected_signal_trades.csv', index=False)
    common = s.groupby('pair_id').closed.all()
    common_ids = common[common].index
    common_rank = s[s.pair_id.isin(common_ids)].groupby('policy').agg(
        n=('pair_id', 'size'), net_points=('net_points', 'sum'), net_r=('net_r', 'sum')).reset_index()
    common_rank.sort_values('net_points', ascending=False).to_csv(out/'common_closed_ranking.csv', index=False)
    facts = dict(checked_ledger_rows=len(s)+len(c), summary_groups=len(summary),
        selected_policies=selected, drawing_eligible=int(drawmap.eligible.sum()),
        drawing_skipped=int((~drawmap.eligible).sum()), drawing_paths=len(draw_rows),
        all_arms_closed_common_signals=len(common_ids),
        common_closed_best_points=str(common_rank.sort_values('net_points', ascending=False).iloc[0].policy),
        common_closed_best_r=str(common_rank.sort_values('net_r', ascending=False).iloc[0].policy),
        cost_r_quantiles=(entries.entry*.002/entries.risk).quantile([.1, .5, .9]).to_dict(),
        positive_net_points_arms_by_segment=summary.groupby('segment').net_points.apply(lambda x: int(x.gt(0).sum())).to_dict(),
        highest_mtm_net_points_by_segment=summary.groupby('segment').mtm_inclusive_net_points.max().to_dict(),
        ambiguity_counts=s.groupby('policy').ambiguous_old_stop_target.sum().to_dict(),
        note='No classifier or account simulation: AUC, top-decile score and account drawdown are not defined.')
    (out/'verification.json').write_text(json.dumps(facts, indent=2, allow_nan=False)+'\n')
    return facts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='results')
    ap.add_argument('--audit-only', action='store_true')
    ap.add_argument('--analyze-only', action='store_true')
    ap.add_argument('--path-builder-commit', help='Required provenance for resuming already saved path ledgers.')
    args = ap.parse_args()
    if args.audit_only:
        print(json.dumps(diagnostics(EXP/args.out)))
        return
    cfg = json.loads((EXP/'config.json').read_text())
    source = json.loads((V1/'config.json').read_text())
    source.update(round_trip_cost=cfg['round_trip_cost'])
    out = EXP/args.out
    if args.analyze_only:
        if not args.path_builder_commit:
            ap.error('--analyze-only requires the observed --path-builder-commit')
        commit = subprocess.check_output(['git', 'rev-parse', args.path_builder_commit], text=True).strip()
    else:
        out.mkdir(parents=True, exist_ok=False)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    minutes, facts = load_minutes(source)
    arms = policies(cfg)
    entries = pd.read_csv(V1/'results_v1/entries.csv')
    controls = pd.read_csv(V1/'results_v1/control_entries.csv')
    if args.analyze_only:
        s = pd.read_csv(out/'signal_trades.csv.gz')
        c = pd.read_csv(out/'random_trades.csv.gz')
    else:
        ctx = prepare(minutes)
        s = replay(ctx, entries, arms, source, out, 'signal')
        c = replay(ctx, controls, arms, source, out, 'random')
    summary = analyze(s, c, arms, out)
    parity = baseline_parity(s, c)
    facts.update(builder_commit_at_start=commit, signals=len(entries), controls=len(controls),
        analysis_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        arms=len(arms), independent_oracle_paths=len(s)+len(c), v2_baseline_parity_paths=parity,
        production_eligible=False, training_eligible=False,
        total_net_positive_arms=int(summary[summary.segment.eq('all')].net_points.gt(0).sum()))
    (out/'receipt.json').write_text(json.dumps(facts, indent=2)+'\n')
    diagnostics(out)
    print(json.dumps(dict(output=str(out), oracle_paths=len(s)+len(c), baseline_parity=parity)), flush=True)


if __name__ == '__main__':
    main()
