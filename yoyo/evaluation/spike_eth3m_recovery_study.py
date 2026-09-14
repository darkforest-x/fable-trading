"""Pre-registered 1-USDT V8 recovery study, development then frozen evaluation.

Only the hash-bound pre-May-2026 context is loadable. All signal/initial-stop
features come from the frozen V8 context; no signal feature is searched. The
matched controls use the signal bar's ATR/close percentile against its prior
120 bars (closed data only), same UTC month, asset and initial direction.
"""
import argparse
import hashlib
import json
import pickle
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_recovery_exit import prepare, replay_entry
from yoyo.evaluation.spike_recovery_cash import simulate
from yoyo.evaluation.spike_v7_fast import assert_reference_sources_unchanged

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-spike-eth3m-recovery-20260914-v2'
CONFIG = EXP / 'config.json'
RESULTS = EXP / 'results'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + '\n')


def load_context(cfg):
    assert_reference_sources_unchanged()
    path = ROOT / cfg['context']
    if sha(path) != cfg['context_sha256']:
        raise ValueError('context changed')
    with path.open('rb') as handle:
        ctx = pickle.load(handle)
    if ctx['frame'].index.max() >= pd.Timestamp('2026-05-01T00:00:00Z'):
        raise ValueError('context crosses the authorized research boundary')
    return ctx


def exit_grid(cfg):
    rows = []
    for tp in cfg['take_profit_r']:
        for mode in ['none', 'entry', 'cost']:
            for trigger in [None] if mode == 'none' else cfg['trigger_r']:
                rows.append(dict(take_profit_r=tp, protection_mode=mode, trigger_r=trigger))
    return rows


def cash_grid():
    rows = [dict(schedule='fixed', factor=1., reset_mode='recovery')]
    for schedule, factors in [('double', [1.5, 2.]), ('debt', [1., 1.5, 2.]), ('factorial', [2.])]:
        for factor in factors:
            for reset in ['recovery', 'net_win', 'be_or_win']:
                rows.append(dict(schedule=schedule, factor=factor, reset_mode=reset))
    return rows


def policy_id(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()[:12]


def bounds(ctx, cfg, period):
    start, end = map(pd.Timestamp, cfg['periods'][period])
    if end > pd.Timestamp('2026-05-01T00:00:00Z'):
        raise ValueError('new holdout access is not authorized')
    frame = ctx['frame'].loc[ctx['frame'].index < end].copy()
    frame.attrs['minutes'] = 3
    raw = ctx['raw'].loc[frame.index]
    confirmed = frame.index + pd.Timedelta(minutes=3)
    mask = np.asarray(ctx['mask'].loc[frame.index], bool) & (confirmed >= start) & (confirmed < end)
    return frame, raw, np.flatnonzero(mask)


def opportunities(prepared, indices, policy):
    rows = []
    for i in indices:
        row = replay_entry(prepared, int(i), **policy)
        if row is not None:
            if not np.isfinite(row['net_r']):
                raise ValueError('unresolved gap outcome; cannot silently delete')
            rows.append(row)
    return rows


def account(rows, policy, ledger=True):
    return simulate(rows, record_ledger=ledger, **policy['cash'])


def parity(rows, cfg):
    path = ROOT / cfg['baseline_csv']
    if sha(path) != cfg['baseline_sha256']:
        raise ValueError('baseline CSV changed')
    expected = pd.read_csv(path)
    result = simulate(rows, initial_balance=1e12, schedule='fixed', leverage_cap=1000.)
    accepted = {r['signal_i'] for r in result['ledger'] if r['accepted']}
    actual = pd.DataFrame([r for r in rows if r['signal_i'] in accepted]).reset_index(drop=True)
    if len(actual) != len(expected):
        raise AssertionError(f'baseline count {len(actual)} != {len(expected)}')
    for col in ['signal_i', 'entry_i', 'exit_i', 'side', 'entry_price', 'exit_price', 'initial_stop', 'net_r']:
        np.testing.assert_allclose(actual[col], expected[col], rtol=1e-12, atol=1e-12, err_msg=col)
    for col in ['exit_reason', 'censored']:
        assert actual[col].tolist() == expected[col].tolist(), col
    return dict(trades=len(actual), matched_fields=10, baseline_sha256=sha(path))


def receipt():
    return dict(code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                config_sha256=sha(CONFIG), holdout_consumed=False,
                builder_sha256={name: sha(ROOT / 'yoyo/evaluation' / name) for name in
                                ['spike_eth3m_recovery_study.py', 'spike_recovery_exit.py', 'spike_recovery_cash.py']})


def develop(cfg):
    out = RESULTS / 'development'
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    ctx = load_context(cfg)
    frame, raw, indices = bounds(ctx, cfg, 'development')
    prepared = prepare(frame, raw)
    all_rows, policies = [], {}
    parity_result = None
    for exit_policy in exit_grid(cfg):
        rows = opportunities(prepared, indices, exit_policy)
        exit_id = policy_id(exit_policy)
        pd.DataFrame(rows).to_csv(out / (exit_id + '_opportunities.csv.gz'), index=False)
        if exit_policy == dict(take_profit_r=None, protection_mode='none', trigger_r=None):
            parity_result = parity(rows, cfg)
            print('frozen baseline parity', parity_result, flush=True)
        reference = simulate(rows, schedule='fixed', record_ledger=False)['summary']
        for cash in cash_grid():
            policy = dict(exit=exit_policy, cash=dict(cash, max_losses=6, leverage_cap=10.))
            pid = policy_id(policy)
            policies[pid] = policy
            summary = account(rows, policy, False)['summary']
            coverage = (summary['n_natural'] >= max(100, .5 * reference['n_natural'])
                        and summary['active_months'] >= .75 * reference['active_months'])
            passes = coverage and summary['final_balance'] > 1000 and summary['max_consecutive_net_loss'] <= 6
            all_rows.append(dict(policy_id=pid, exit_id=exit_id, **exit_policy, **policy['cash'], **summary,
                                 coverage_pass=coverage, hard_target_pass=passes))
        print('development exit', exit_id, 'candidates', len(rows), flush=True)
    table = pd.DataFrame(all_rows)
    table.to_csv(out / 'search.csv', index=False)
    passing = table[table.hard_target_pass]
    eligible = passing if len(passing) else table[table.coverage_pass]
    if eligible.empty:
        raise ValueError('no participating configuration even for diagnostic evaluation')
    ranked = eligible.sort_values(['final_balance', 'max_realized_drawdown', 'max_risk', 'policy_id'],
                                  ascending=[False, True, True, True])
    primary_id = str(ranked.iloc[0].policy_id)
    closest = table[table.coverage_pass].sort_values(['max_consecutive_net_loss', 'final_balance', 'policy_id'],
                                                   ascending=[True, False, True]).iloc[0].policy_id
    selected = dict(primary=policies[primary_id], closest_streak=policies[str(closest)])
    sensitivity = []
    primary = selected['primary']
    primary_rows = pd.read_csv(out / (policy_id(primary['exit']) + '_opportunities.csv.gz')).to_dict('records')
    for key, values in [('leverage_cap', [3., 5., 10., 20.]), ('max_losses', [3, 4, 5, 6]),
                        ('loss_trigger', ['any_net_loss', 'losing_stop'])]:
        for value in values:
            policy = dict(exit=primary['exit'], cash=dict(primary['cash'], **{key: value}))
            sensitivity.append(dict(changed_field=key, value=value, **account(primary_rows, policy, False)['summary']))
    pd.DataFrame(sensitivity).to_csv(out / 'sensitivity.csv', index=False)
    save(RESULTS / 'selection.json', dict(**receipt(), n_configurations=len(table), n_hard_target_pass=len(passing),
        selection_status='development_target_pass' if len(passing) else 'no_accepted_policy_diagnostic_only',
        primary_id=primary_id, closest_id=str(closest), policies=selected,
        search_sha256=sha(out / 'search.csv'), baseline_parity=parity_result))
    print(table.sort_values('final_balance', ascending=False)[['policy_id', 'final_balance', 'win_rate', 'max_consecutive_net_loss']].head(10).to_string(index=False))


def matched_control(frame, prepared, accepted, exit_policy, cfg):
    """Same ETH/month/side/causal volatility bucket; matched trade diagnostic.

    Equal target risk budgets are applied individually, not replayed as an
    executable random portfolio. Percentile thresholds use only the preceding
    120 bars; current ATR/close is known when this signal bar closes.
    """
    vol = frame.atr / frame.close
    prior = vol.shift(1).rolling(120, min_periods=120)
    q1, q2 = prior.quantile(1/3), prior.quantile(2/3)
    bucket = np.where(vol <= q1, 0, np.where(vol <= q2, 1, 2))
    months = frame.index.strftime('%Y-%m').to_numpy()
    pools = {}
    for month in sorted(set(months[int(r['signal_i'])] for r in accepted)):
        for b in range(3):
            pools[month, b] = np.flatnonzero((months == month) & (bucket == b) & q1.notna().to_numpy())
    rng = np.random.default_rng(cfg['seed'])
    details = []
    rejected_invalid = rejected_boundary = attempts = raw_signal_controls = 0
    for trade in accepted:
        i, side = int(trade['signal_i']), int(trade['side'])
        key = months[i], int(bucket[i])
        candidates = pools[key]
        candidates = candidates[(candidates != i) & (candidates + 1 < len(frame))]
        draws = []
        for random_i in rng.permutation(candidates):
            attempts += 1
            control = replay_entry(prepared, int(random_i), side_override=side, **exit_policy)
            if control is None or not np.isfinite(control['net_r']):
                rejected_invalid += 1
                continue
            if control['censored']:
                rejected_boundary += 1
                continue
            draws.append(float(control['net_r']))
            raw_signal_controls += int(prepared.raw_side[random_i] != 0)
            if len(draws) == cfg['controls_per_trade']:
                break
        if len(draws) != cfg['controls_per_trade']:
            raise ValueError('matched controls unavailable')
        mean = float(np.mean(draws))
        details.append(dict(signal_i=i, month=key[0], bucket=key[1], side=side, risk=trade['risk'],
                            signal_net_r=trade['net_r'], random_mean_net_r=mean,
                            excess_net_r=trade['net_r']-mean, excess_cash=trade['risk']*(trade['net_r']-mean)))
    data = pd.DataFrame(details)
    blocks = data.groupby('month').excess_net_r.sum().to_numpy()
    observed = float(blocks.sum())
    permuted = (rng.choice([-1, 1], size=(cfg['permutation_draws'], len(blocks))) * blocks).sum(axis=1)
    return data, dict(n=len(data), months=len(blocks), controls_per_trade=cfg['controls_per_trade'],
        attempts=attempts, rejected_invalid=rejected_invalid, rejected_boundary=rejected_boundary,
        raw_signal_controls=raw_signal_controls,
        signal_mean_net_r=float(data.signal_net_r.mean()), random_mean_net_r=float(data.random_mean_net_r.mean()),
        mean_excess_net_r=float(data.excess_net_r.mean()), sum_excess_cash=float(data.excess_cash.sum()),
        one_sided_month_signflip_p=float((1 + (permuted >= observed).sum()) / (1 + len(permuted))))


def evaluate(cfg):
    selection_path = RESULTS / 'selection.json'
    committed = subprocess.check_output(['git', 'show', 'HEAD:' + str(selection_path.relative_to(ROOT))], cwd=ROOT)
    if hashlib.sha256(committed).hexdigest() != sha(selection_path):
        raise ValueError('freeze and commit selection before evaluation')
    selection = json.loads(selection_path.read_text())
    if selection['config_sha256'] != sha(CONFIG):
        raise ValueError('config changed after selection')
    if selection['builder_sha256'] != receipt()['builder_sha256']:
        raise ValueError('builder changed after selection; freeze invalidated')
    out = RESULTS / 'evaluation'
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    ctx = load_context(cfg)
    policies = dict(selection['policies'])
    policies['same_exit_fixed_1u'] = dict(exit=policies['primary']['exit'], cash=dict(schedule='fixed', factor=1., reset_mode='recovery'))
    policies['original_v8_fixed_1u'] = dict(exit=dict(take_profit_r=None, protection_mode='none', trigger_r=None),
                                          cash=dict(schedule='fixed', factor=1., reset_mode='recovery'))
    for schedule, reset in [('factorial', 'be_or_win'), ('debt', 'recovery'), ('double', 'net_win')]:
        policies['user_reference_' + schedule] = dict(exit=dict(take_profit_r=3., protection_mode='entry', trigger_r=1.),
            cash=dict(schedule=schedule, factor=2., reset_mode=reset, max_losses=6, leverage_cap=10.))
    summaries, controls = [], []
    for period in ['development', 'validation', 'preholdout', 'continuous_pre']:
        frame, raw, indices = bounds(ctx, cfg, period)
        prepared = prepare(frame, raw)
        cache = {}
        for name, policy in policies.items():
            eid = policy_id(policy['exit'])
            if eid not in cache:
                cache[eid] = opportunities(prepared, indices, policy['exit'])
            result = account(cache[eid], policy)
            tag = period + '_' + name
            pd.DataFrame(result['ledger']).to_csv(out / (tag + '_ledger.csv.gz'), index=False)
            pd.DataFrame(result['cycles']).to_csv(out / (tag + '_cycles.csv'), index=False)
            summaries.append(dict(period=period, name=name, policy_id=policy_id(policy), **result['summary']))
            if name in {'primary', 'same_exit_fixed_1u', 'original_v8_fixed_1u'} and period != 'continuous_pre':
                accepted = [r for r in result['ledger'] if r['accepted'] and not r['censored']]
                detail, summary = matched_control(frame, prepared, accepted, policy['exit'], cfg)
                detail.to_csv(out / (tag + '_matched.csv.gz'), index=False)
                controls.append(dict(period=period, name=name, **summary))
            print(period, name, result['summary']['final_balance'], 'streak', result['summary']['max_consecutive_net_loss'], flush=True)
    pd.DataFrame(summaries).to_csv(out / 'summary.csv', index=False)
    pd.DataFrame(controls).to_csv(out / 'controls.csv', index=False)
    save(out / 'receipt.json', dict(**receipt(), selection_sha256=sha(selection_path), policies=policies,
                                  summary_sha256=sha(out / 'summary.csv'), controls_sha256=sha(out / 'controls.csv')))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['develop', 'evaluate'])
    args = parser.parse_args()
    for path in [CONFIG, Path(__file__), ROOT / 'yoyo/evaluation/spike_recovery_exit.py',
                 ROOT / 'yoyo/evaluation/spike_recovery_cash.py']:
        committed = subprocess.check_output(['git', 'show', 'HEAD:' + str(path.relative_to(ROOT))], cwd=ROOT)
        if hashlib.sha256(committed).hexdigest() != sha(path):
            raise ValueError('commit builders and config before computing outcomes')
    cfg = json.loads(CONFIG.read_text())
    {'develop': develop, 'evaluate': evaluate}[args.phase](cfg)


if __name__ == '__main__':
    main()
