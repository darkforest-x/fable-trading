"""Freeze and replay one ICT entry filter with the original V8 exit.

Entry eligibility consumes only planned next observed open timestamps. Existing
per-entry outcomes are hash pinned and reused before rebuilding serial admission.
Random controls use closed-bar features through the signal bar, with prior-120
relative-volatility reference windows; exit outcomes alone may use future bars.
This module rejects new holdout data and never touches execution or model state.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_fanshen_study as base
from yoyo.evaluation.spike_v8_ict import filter_opportunities, matched_controls, session_annotation, session_mask
from yoyo.evaluation.spike_recovery_exit import prepare
from yoyo.evaluation.spike_net_recovery_cash import simulate_recovery

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-v8-ict-multitf-20260915-v1'
BUILDERS = [
    'yoyo/evaluation/spike_v8_ict_multitf.py', 'yoyo/evaluation/spike_v8_ict.py',
    'yoyo/evaluation/spike_fanshen_study.py', 'yoyo/evaluation/spike_fanshen_exit.py',
    'yoyo/data/spike_fanshen_prefix.py', 'yoyo/evaluation/spike_burst_replay.py',
    'yoyo/evaluation/spike_v8_lowtf_study.py', 'yoyo/evaluation/spike_v6_wvf_study.py',
    'yoyo/evaluation/spike_v7_fast.py', 'yoyo/evaluation/spike_recovery_exit.py',
    'yoyo/evaluation/spike_recovery_cash.py', 'yoyo/evaluation/spike_net_recovery_cash.py',
    str((EXP/'config.json').relative_to(ROOT)), str((EXP/'PROJECT_PLAN.md').relative_to(ROOT)),
]


def assert_same_trades(actual, reference):
    """All-day control must reproduce archived admissions and outcomes."""
    if len(actual) != len(reference):
        raise AssertionError(f'all-day count changed: {len(actual)} vs {len(reference)}')
    if len(actual):
        observed = pd.DataFrame(actual)
        for col in ['signal_i', 'entry_i', 'exit_i', 'side', 'entry_price', 'initial_stop', 'exit_price', 'gross_r', 'net_r']:
            np.testing.assert_allclose(observed[col], reference[col], rtol=1e-12, atol=1e-12, err_msg=col)
        for col in ['exit_reason', 'censored', 'exit_at_open', 'entry_time', 'exit_time']:
            if col.endswith('time'):
                assert pd.to_datetime(observed[col], utc=True).tolist() == pd.to_datetime(reference[col], utc=True).tolist(), col
            else:
                assert observed[col].tolist() == reference[col].tolist(), col
    return dict(rows=len(actual), numeric_fields=9, other_fields=5, passed=True)


def run():
    cfg = json.loads((EXP/'config.json').read_text())
    assert cfg['sessions'] == ['all', 'union']
    assert cfg['policies'] == ['original'] and cfg['primary'] == 'original'
    assert pd.Timestamp(cfg['end']) <= pd.Timestamp('2026-05-01T00:00:00Z')
    for rel in BUILDERS:
        subprocess.run(['git', 'cat-file', '-e', 'HEAD:'+rel], cwd=ROOT, check=True)
        subprocess.run(['git', 'diff', '--exit-code', 'HEAD', '--', rel], cwd=ROOT, check=True)
    old = ROOT/cfg['previous_experiment']/'results'
    if base.sha(old/'manifest.json') != cfg['previous_result_manifest_sha256']:
        raise ValueError('previous result manifest changed')
    previous_manifest = json.loads((old/'manifest.json').read_text())
    if previous_manifest['holdout_consumed']:
        raise ValueError('refuse holdout-derived opportunity source')
    verified = set()

    def read_result(name):
        if base.sha(old/name) != previous_manifest['files'][name]:
            raise ValueError('archived result changed: '+name)
        verified.add(name)
        return pd.read_csv(old/name)

    out = EXP/'results'
    out.mkdir(exist_ok=False)
    receipt = dict(builder_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                   builders={p:base.sha(ROOT/p) for p in BUILDERS}, holdout_consumed=False,
                   holdout_consumption_number=0, no_parameter_selection=True,
                   previous_result_manifest_sha256=cfg['previous_result_manifest_sha256'])
    base.save(out/'run_receipt.json', receipt)
    summaries, cash_summaries, inputs, parity, membership = [], [], [], [], []
    for minutes, frame, raw, admission, input_receipt in base.contexts(cfg):
        inputs.append(dict(minutes=minutes, **input_receipt))
        prepared = prepare(frame, raw)
        signals = None  # Original V8 exit does not consume Stoch or WVF exit signals.
        first = max(pd.Timestamp(cfg['available_start']), frame.index[cfg['minimum_warmup_bars']]+pd.Timedelta(minutes=minutes))
        starts = {'common':pd.Timestamp(cfg['common_start']), 'available':first}
        if first > starts['common']:
            raise ValueError('common window has insufficient warmup')
        replay_cache = {}
        for name, policy in {'original': None}.items():
            opportunities = read_result(f'{minutes}m_{name}_opportunities.csv.gz')
            opportunity_rows = opportunities.to_dict('records')
            for t in opportunity_rows:
                i = int(t['signal_i'])
                assert bool(admission.iloc[i]), 'cached entry no longer V8 admitted'
                assert pd.Timestamp(t['entry_time']) == frame.index[i+1], 'cached entry clock mismatch'
                assert pd.Timestamp(t['entry_time']) < pd.Timestamp(cfg['end'])
                assert np.isfinite(t['net_r'])
            for window, start in starts.items():
                selected = [t for t in opportunity_rows if pd.Timestamp(t['entry_time']) >= start]
                all_day = base.serial(selected)
                reference = read_result(f'{minutes}m_{window}_{name}_trades.csv.gz')
                parity.append(dict(kind='archived_all_day', minutes=minutes, window=window, policy=name,
                                   **assert_same_trades(all_day, reference)))
                all_ids = {int(t['signal_i']) for t in all_day}
                for session in cfg['sessions']:
                    filtered = filter_opportunities(selected, session)
                    rows = base.serial(filtered)
                    if session == 'union':
                        assert session_mask([t['entry_time'] for t in rows], 'union').all()
                    tag = f'{minutes}m_{window}_{name}_{session}'
                    trades = pd.DataFrame(rows, columns=opportunities.columns)
                    trades['entry_ny'] = pd.to_datetime(trades.entry_time, utc=True).dt.tz_convert('America/New_York').astype(str)
                    trades['ict_segment'] = session_annotation(trades.entry_time)
                    trades.to_csv(out/(tag+'_trades.csv.gz'), index=False)
                    if session == 'union' and name == 'original':
                        indices = np.array([int(t['signal_i']) for t in filtered], dtype=int)
                        parity.append(dict(kind='original_engine_union', minutes=minutes, window=window,
                            policy=name, **base.assert_parity(frame, raw, indices, rows)))
                    detail, control = matched_controls(frame, rows, policy, policy_key=name, session=session,
                        start=start, end=pd.Timestamp(cfg['end']), seed=cfg['seed']+minutes,
                        replay_fn=lambda i,p,s: base.replay(prepared, signals, i, p, s),
                        controls_per_trade=cfg['controls_per_trade'], permutations=cfg['permutations'], cache=replay_cache)
                    detail.to_csv(out/(tag+'_controls.csv.gz'), index=False)
                    ids = {int(t['signal_i']) for t in rows}
                    summary = dict(minutes=minutes, window=window, start=str(start), end=cfg['end'], policy=name,
                        session=session, candidates=len(filtered), rejected_by_session=len(selected)-len(filtered),
                        retained_all_day=len(ids & all_ids), new_after_filter=len(ids-all_ids),
                        dropped_all_day=len(all_ids-ids), **base.stats(rows), **control)
                    summaries.append(summary)
                    membership.append(dict(minutes=minutes, window=window, policy=name, session=session,
                        entry_signal_ids=sorted(ids), new_after_filter=sorted(ids-all_ids), dropped_all_day=sorted(all_ids-ids)))
                    for schedule in ['fixed', 'double']:
                        result = simulate_recovery(filtered, schedule=schedule, **cfg['cash'])
                        base.save(out/(tag+f'_{schedule}_cash.json'), result)
                        cash_summaries.append(dict(minutes=minutes, window=window, policy=name, session=session,
                                                   schedule=schedule, **result['summary']))
                    print(tag, 'n', summary['natural'], 'netR', round(summary['sum_net_r'],3),
                          'streak', summary['max_net_loss_streak'], 'matched', control.get('matched_n'), flush=True)
            pd.DataFrame(summaries).to_csv(out/'progress_summary.csv', index=False)
        base.save(out/'input_receipts.json', inputs)
        base.save(out/'baseline_parity.json', parity)
    summary = pd.DataFrame(summaries)
    summary['p_holm'] = np.nan
    for window in summary.window.unique():
        group = summary.loc[(summary.window == window) & summary.p.notna()].sort_values('p')
        if len(group):
            summary.loc[group.index,'p_holm'] = np.minimum(1, np.maximum.accumulate(group.p.to_numpy()*np.arange(len(group),0,-1)))
    summary.to_csv(out/'summary.csv', index=False)
    pd.DataFrame(cash_summaries).to_csv(out/'cash_summary.csv', index=False)
    base.save(out/'membership.json', membership)
    base.save(out/'verified_previous_inputs.json', {n:previous_manifest['files'][n] for n in sorted(verified)})
    base.save(out/'manifest.json', dict(**receipt, files={p.name:base.sha(p) for p in sorted(out.iterdir()) if p.is_file()}))
    print('COMPLETE', len(summary), 'summary rows',len(cash_summaries),'cash rows',len(parity),'parity groups',flush=True)


if __name__ == '__main__':
    run()
