"""Describe the last two calendar months of the authenticated SPIKE ledger.

Owner request, 2026-09-25: counts, win rate, excursion and detailed attribution.
No new entry features or trading policies. Inputs are the existing per-trade
outcomes and signal statuses; window selection uses actual entry timestamps.
MFE uses only the parent's conservative pre-exit excursion. No human decisions
are inferred from hindsight. R totals and drawdowns are not account returns.
"""
from pathlib import Path
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v128_recent_report import block_inference

ROOT = Path('experiments/active/exp-spike-manual-system-20260925-v1')
OUT = ROOT / 'recent_two_months_v1'
START = pd.Timestamp('2026-07-25T00:00:00Z')
END = pd.Timestamp('2026-09-25T00:00:00Z')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def window(series):
    return series.ge(START) & series.lt(END)


def drawdown(values):
    """Closed-trade additive R drawdown including the initial zero balance."""
    equity = np.r_[0., np.cumsum(values)]
    return float((np.maximum.accumulate(equity) - equity).max())


def streak(values):
    best = run = 0
    for value in values:
        run = run + 1 if value < 0 else 0
        best = max(best, run)
    return best


def describe(frame):
    f = frame.sort_values(['exit_time', 'entry_time', 'trade_key'])
    v = f.net_r
    wins, losses = v[v > 0], v[v < 0]
    m = f[f.control_in_window]
    floating = f.mfe_known_net_r.gt(0)
    result = dict(n=len(f), wins=len(wins), losses=len(losses),
        breakeven=int(v.eq(0).sum()), win_rate=float(v.gt(0).mean()),
        net_mean_r=float(v.mean()), net_sum_r=float(v.sum()),
        net_median_r=float(v.median()), gross_mean_r=float(f.gross_r.mean()),
        fee_mean_r=float(f.fee_r.mean()), fee_median_r=float(f.fee_r.median()),
        risk_median_pct=float(100*f.initial_risk_frac.median()),
        net_mean_bp=float(f.net_bp.mean()),
        pf_r=float(wins.sum()/-losses.sum()) if len(losses) else None,
        avg_win_r=float(wins.mean()) if len(wins) else None,
        avg_loss_r=float(losses.mean()) if len(losses) else None,
        max_win_r=float(v.max()), max_loss_r=float(v.min()),
        closed_trade_drawdown_r=drawdown(v), max_loss_streak=streak(v),
        net_sum_without_best_r=float(v.sum()-v.max()),
        mfe_median_r=float(f.mfe_known_r.median()),
        mfe_mean_r=float(f.mfe_known_r.mean()),
        mfe_q75_r=float(f.mfe_known_r.quantile(.75)),
        mfe_max_r=float(f.mfe_known_r.max()),
        mfe_upper_mean_r=float(f.mfe_upper_r.mean()),
        excursion_ambiguous=int(f.stop_bar_excursion_ambiguous.sum()),
        close_peak_median_r=float(f.close_peak_r.median()),
        net_float_positive=int(floating.sum()),
        net_float_then_loss=int((floating & v.lt(0)).sum()),
        gross_win_net_loss=int((f.gross_r.gt(0) & v.lt(0)).sum()),
        holding_median_hours=float(f.holding_hours.median()),
        matched_n=len(m), matched_target_mean_r=float(m.net_r.mean()) if len(m) else None,
        matched_random_mean_r=float(m.control_net_r.mean()) if len(m) else None,
        matched_excess_mean_r=float((m.net_r-m.control_net_r).mean()) if len(m) else None,
        matched_target_mean_bp=float(m.net_bp.mean()) if len(m) else None,
        matched_random_mean_bp=float(m.control_net_bp.mean()) if len(m) else None,
        matched_excess_mean_bp=float((m.net_bp-m.control_net_bp).mean()) if len(m) else None,
        inherited_matches=int(f.matched.sum()),
        boundary_control_exclusions=int((f.matched & ~f.control_in_window).sum()))
    # Random controls have their own risk distance: use price bp for inference.
    inference = block_inference(m.net_bp-m.control_net_bp, m.utc_week, seed=925129)
    result.update({f'paired_bp_{key}': value for key, value in inference.items()})
    # Descriptive excursion bands only; not proposed targets or exit rules.
    for level in [1, 2, 3, 5, 10]:
        hit = f.mfe_known_r.ge(level)
        result[f'mfe_ge{level}_n'] = int(hit.sum())
        result[f'mfe_ge{level}_then_loss_n'] = int((hit & v.lt(0)).sum())
    return result


def main():
    own = Path(__file__).resolve().relative_to(Path.cwd())
    committed = subprocess.check_output(['git', 'show', f'HEAD:{own}'])
    if committed != own.read_bytes():
        raise ValueError('Commit builder before constructing results')
    receipt_path = ROOT/'summary_v1/receipt.json'
    receipt = json.loads(receipt_path.read_text())
    inference_path = Path('yoyo/evaluation/spike_v128_recent_report.py')
    if subprocess.check_output(['git','show',f'HEAD:{inference_path}']) != inference_path.read_bytes():
        raise ValueError('Uncommitted inference source')
    inputs = {str(receipt_path): sha(receipt_path), str(own): sha(own),
        str(inference_path): sha(inference_path)}
    frames = {}
    for name in ['all_trades', 'all_statuses', 'all_controls']:
        path = ROOT/'summary_v1'/f'{name}.csv.gz'
        inputs[str(path)] = sha(path)
        if inputs[str(path)] != receipt['files'][str(path)]:
            raise ValueError(f'Input changed: {path}')
        frames[name] = pd.read_csv(path)
    all_trades = frames['all_trades']
    for col in ['entry_time', 'exit_time', 'signal_close', 'control_exit_time']:
        all_trades[col] = pd.to_datetime(all_trades[col], utc=True)
    baseline = all_trades[all_trades.policy.eq('baseline')].copy()
    if baseline.trade_key.duplicated().any():
        raise ValueError('Duplicate trade key')
    recent = baseline[window(baseline.entry_time)].copy()
    if recent.censored.any() or recent.exit_time.ge(END).any():
        raise ValueError('Separate censored outcomes before calculating closed metrics')
    if not np.allclose(recent.net_r, recent.gross_r-recent.fee_r):
        raise ValueError('R cost identity mismatch')
    if not np.allclose(recent.fee_r, .002/recent.initial_risk_frac):
        raise ValueError('Original 20bp cost changed')
    controls = frames['all_controls'][['trade_key', 'control_signal_close']].copy()
    controls.control_signal_close = pd.to_datetime(controls.control_signal_close, utc=True)
    recent = recent.merge(controls, on='trade_key', how='left', validate='one_to_one')
    recent['control_in_window'] = (recent.matched & window(recent.control_signal_close)
        & recent.control_exit_time.lt(END) & recent.control_exit_time.ge(recent.control_signal_close))
    statuses = frames['all_statuses']
    statuses.signal_close = pd.to_datetime(statuses.signal_close, utc=True)
    statuses = statuses[statuses.policy.eq('baseline') & window(statuses.signal_close)].copy()
    carry = baseline[baseline.entry_time.lt(START) & baseline.exit_time.ge(START)].copy()
    keys = ['arm', 'symbol', 'timeframe_min']
    rows, side_rows, month_rows, exit_rows = [], [], [], []
    for key, f in recent.groupby(keys):
        identity = dict(zip(keys, key))
        status = statuses.copy()
        for field, value in identity.items():
            status = status[status[field].eq(value)]
        row = {**identity, **describe(f), 'signals':len(status),
            'skipped_in_position':int(status.status.eq('skipped_in_position').sum())}
        rows.append(row)
        for side, part in f.groupby('side'):
            side_rows.append({**identity, 'side':side, **describe(part)})
        for month, part in f.groupby(f.entry_time.dt.strftime('%Y-%m')):
            month_rows.append({**identity, 'month_utc':month, **describe(part)})
        for reason, part in f.groupby('exit_reason'):
            exit_rows.append({**identity, 'exit_reason':reason, **describe(part)})
    # Duplicate event episodes across arms must not be added as one portfolio.
    event_keys = ['symbol', 'timeframe_min', 'side', 'entry_time']
    duplicates = recent[recent.duplicated(event_keys, keep=False)]
    OUT.mkdir(exist_ok=True)
    metrics = pd.DataFrame(rows)
    # One prespecified family for this descriptive request: six ordinary groups.
    ordinary = metrics.index[metrics.arm.eq('ordinary')]
    ordered = metrics.loc[ordinary, 'paired_bp_p_one_sided'].sort_values()
    adjusted = np.minimum(1., np.maximum.accumulate(
        ordered.to_numpy()*np.arange(len(ordered),0,-1)))
    metrics.loc[ordered.index, 'paired_bp_p_holm_ordinary6'] = adjusted
    tables = {'trades':recent, 'signal_statuses':statuses, 'carry_in':carry,
        'metrics':metrics, 'by_side':pd.DataFrame(side_rows),
        'by_month':pd.DataFrame(month_rows), 'by_exit':pd.DataFrame(exit_rows),
        'overlapping_entries':duplicates}
    display = recent.copy()
    display['side'] = display.side.map({1:'多', -1:'空'})
    display['timeframe_min'] = display.timeframe_min.map({5:'5m',15:'15m',60:'1h'})
    display['exit_reason'] = display.exit_reason.map({
        'initial_stop':'初始止损', 'trailing_stop':'追踪止损',
        'opposite_v6_next_open':'反向信号后下一开盘'})
    display['initial_risk_frac'] *= 100
    display['net_float_then_loss'] = display.mfe_known_net_r.gt(0) & display.net_r.lt(0)
    display['manual_review'] = '尚未作出事前人工交易判断'
    labels = {'trade_key':'交易标识', 'symbol':'合约', 'timeframe_min':'周期', 'side':'方向',
        'entry_time_beijing':'入场北京时间', 'exit_time_beijing':'出场北京时间',
        'entry_price':'入场价', 'initial_stop':'初始止损价', 'exit_price':'出场价',
        'initial_risk_frac':'初始止损距离百分比', 'exit_reason':'退出原因',
        'gross_r':'毛盈亏R', 'fee_r':'固定成本R', 'net_r':'净盈亏R',
        'mfe_known_r':'可确认最大毛浮盈R', 'mfe_upper_r':'含退出K线歧义的毛浮盈上界R',
        'close_peak_r':'最大收盘毛浮盈R', 'holding_hours':'持仓小时',
        'net_float_then_loss':'曾有净浮盈但最终亏损', 'manual_review':'人工判断状态'}
    for arm, name in [('ordinary','普通交易逐笔'),('joint','联合交易逐笔')]:
        tables[name] = display[display.arm.eq(arm)].sort_values('entry_time')[list(labels)].rename(columns=labels)
    files = {}
    for name, frame in tables.items():
        path = OUT/f'{name}.csv'
        frame.to_csv(path, index=False)
        files[str(path)] = sha(path)
    result = dict(start_inclusive=str(START), end_exclusive=str(END),
        timezone_display='Asia/Shanghai', cohort='actual entry during two calendar months',
        policy='baseline', costs='unchanged fixed 20bp round trip; no realized funding series',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        inputs=inputs, files=files, trades=len(recent), signals=len(statuses),
        censored=0, carry_in=len(carry), exact_cross_arm_overlap_pairs=len(duplicates)//2,
        ordinary_trades=int(recent.arm.eq('ordinary').sum()),
        joint_trades=int(recent.arm.eq('joint').sum()),
        human_system_results_available=False, native_full_pine_parity=False,
        training_eligible=False, production_eligible=False,
        interpretation=['Arms are overlapping independent ledgers, not a portfolio.',
            'Max drawdown is additive closed-trade R within each stream.',
            'Conservative known MFE excludes unknowable exit-stop-bar wick order.',
            'Random controls match symbol/side/week/ATR bucket and may overlap.',
            'Comparative inference uses price bp, since control initial risk differs.',
            'Boundary controls excluded; compare matched target subset only.',
            'No trained predictor: AUC, top-decile and ranking permutation are inapplicable.',
            'Partial July and September are labelled; monthly totals are not equal-duration comparisons.',
            'This descriptive slice adds no filter, sizing or exit policy.'])
    (OUT/'receipt.json').write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({k:result[k] for k in ['trades','signals','ordinary_trades','joint_trades','carry_in']}, indent=2))


if __name__ == '__main__':
    main()
