"""Reinvest only fee-net profit left after reserving the original runner stop risk.

This builder reuses the frozen parent paths, the existing profit-confirm-entry
opportunities, and the cash-sharing VectorBT adapter in
``cloud_close_reinvest_runner``. Its only execution change is the per-add-on
quantity cap derived at the order callback from the realized 2R partial and
the still-open original runner's fixed stop risk.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_profit_confirm_entry as confirmation
from yoyo.evaluation import cloud_close_reinvest_runner as adapter
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_thirds as thirds


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-profit-budget-add-20261109-v1'
BASELINE = repair.EXP / 'thirds' / 'funded_OKX.csv.gz'
HOURLY_BASELINE = repair.EXP / 'thirds' / 'hourly_OKX.csv.gz'
OPPORTUNITIES = confirmation.EXP / 'opportunities.csv.gz'
NEUTRAL_CASH_PNL_EPS = 1e-8
PERIODS = {'all': (None, None), 'dev': (None, '2025-01-01T00:00Z'),
           '2025': ('2025-01-01T00:00Z', '2026-01-01T00:00Z'),
           '2026': ('2026-01-01T00:00Z', None)}


def _one_event_smoke(paths: pd.DataFrame, opportunities: pd.DataFrame,
                     candidate: pd.Series, slip: float) -> dict:
    event_id = str(candidate.event_id)
    event_paths = paths[paths.event_id.eq(event_id)].copy()
    selected, _ = adapter._selected_originals(event_paths, 9)
    if len(selected) != 1:
        raise ValueError(f'Mini event did not select exactly one original: {event_id}')
    start = int(selected.entry_ms.iloc[0])
    end = int(selected.exit_bar_ms.iloc[0]) + wallet.HOUR
    event_opportunities = opportunities[opportunities.event_id.eq(event_id)].copy()
    if len(event_opportunities) != 1:
        raise ValueError(f'Mini event does not have exactly one opportunity: {event_id}')

    baseline_data = adapter.prepare(selected, event_opportunities.iloc[0:0], 9,
                                    start=start, end=end, reinvest=False)
    base_positions, _, _, base_meta = adapter.account(baseline_data, slip=slip)
    policy_data = adapter.prepare(selected, event_opportunities, 9,
                                  start=start, end=end, reinvest=True,
                                  profit_only_budget=True)
    positions, addons, _, meta = adapter.account(policy_data, slip=slip)
    if len(addons) != 1:
        raise ValueError(f'Profit-budget mini did not fund one add-on: {event_id}')
    addon = addons.iloc[0]
    if addon.side not in (-1, 1) or int(addon.side) != int(candidate.side):
        raise ValueError('Mini add-on direction differs from its source event')
    if addon.target_notional_cap < addon.notional - 1e-6:
        raise ValueError('Mini add-on exceeded the original target-leg notional')
    if addon.available_cash_before < addon.notional - 1e-6:
        raise ValueError('Mini add-on exceeded its order-time free cash')
    if addon.stop_risk_used_usdt > addon.profit_budget_usdt + 1e-6:
        raise ValueError('Mini add-on stop loss exceeded its net-profit budget')
    if addon.profit_budget_usdt <= 0. or addon.addon_unit_stop_loss <= 0.:
        raise ValueError('Mini add-on has no valid positive risk budget')
    if addon.entry_fee <= 0. or addon.exit_fee <= 0.:
        raise ValueError('Mini add-on did not charge both execution fees')
    if not np.isclose(positions.base_cash_pnl.iloc[0], base_positions.cash_pnl.iloc[0],
                      rtol=1e-10, atol=1e-6):
        raise ValueError('Profit-budget policy changed the original position cash flow')
    if not np.isclose(meta['cash_from_orders'], meta['cash_from_position_ledger'],
                      rtol=1e-10, atol=1e-6):
        raise ValueError('Mini VectorBT cash ledger does not reconcile')
    return dict(event_id=event_id, side=int(addon.side), slip=float(slip),
                target_net_profit_usdt=float(addon.target_net_profit_usdt),
                runner_stop_loss_usdt=float(addon.runner_stop_loss_usdt),
                available_profit_budget_usdt=float(addon.profit_budget_usdt),
                addon_unit_stop_loss_usdt=float(addon.addon_unit_stop_loss),
                max_quantity_from_profit=float(addon.max_quantity_from_profit),
                actual_quantity=float(addon.quantity),
                actual_stop_risk_used_usdt=float(addon.stop_risk_used_usdt),
                unused_profit_budget_usdt=float(addon.stop_risk_budget_unused_usdt),
                target_notional_cap=float(addon.target_notional_cap),
                order_time_free_cash=float(addon.available_cash_before),
                actual_addon_notional=float(addon.notional),
                actual_addon_entry_fee=float(addon.entry_fee),
                actual_addon_exit_fee=float(addon.exit_fee),
                addon_cash_pnl=float(addon.cash_pnl),
                parent_cash_pnl=float(positions.cash_pnl.iloc[0]),
                original_cash_pnl_without_addon=float(base_positions.cash_pnl.iloc[0]),
                account_cash_reconciliation=float(meta['cash_from_orders']-
                                                  meta['cash_from_position_ledger']),
                final_nav=float(meta['final_nav']),
                original_funded=meta['original_funded'], addon_funded=meta['reinvest_funded'],
                baseline_final_nav=float(base_meta['final_nav']))


def smoke() -> dict:
    """Run one actual long and short candidate, plus a 5bp cost replay for each."""
    paths, opportunities, _ = adapter.read_inputs()
    base, _ = adapter._selected_originals(paths, 9)
    candidates, counts = adapter._validate_reinvestment_candidates(base, opportunities)
    if len(candidates) != 381:
        raise ValueError(f'Frozen candidate count changed: {len(candidates)}')
    results = []
    for side in (1, -1):
        side_candidates = (candidates[candidates.side.eq(side)]
                           .sort_values(['entry_ms', 'entry_order', 'symbol', 'event_id']))
        if side_candidates.empty:
            raise ValueError(f'No existing profit-confirm candidate for side={side}')
        # Attempt a small number only if an event's fee-net budget is zero. The
        # selected candidate is recorded so a skip cannot silently stand in as
        # a successful budget-protection check.
        funded = None
        for candidate in side_candidates.head(8).itertuples(index=False):
            row = pd.Series(candidate._asdict())
            try:
                actual = _one_event_smoke(paths, opportunities, row, 0.)
            except ValueError as exc:
                if 'did not fund one add-on' in str(exc):
                    continue
                raise
            stressed = _one_event_smoke(paths, opportunities, row, .0005)
            funded = dict(actual=actual, stress_5bp=stressed)
            break
        if funded is None:
            raise ValueError(f'No funded profit-budget mini among first eight side={side} candidates')
        results.append(funded)
    return dict(adapter='existing VectorBT cash-sharing reinvest adapter, optional profit-only hook',
                candidates=len(candidates), candidate_counts=counts,
                long_and_short_budget_smoke=results)


def _risk_summary(positions: pd.DataFrame, reentries: pd.DataFrame) -> dict:
    status = positions.reinvest_status.value_counts(dropna=False).to_dict()
    if not len(reentries):
        return dict(status_counts=status, funded_addons=0, target_net_profit_usdt=0.,
                    runner_stop_loss_reserved_usdt=0., available_profit_budget_usdt=0.,
                    realized_stop_risk_used_usdt=0., max_budget_overrun_usdt=0.)
    overrun = (reentries.stop_risk_used_usdt - reentries.profit_budget_usdt).clip(lower=0.)
    return dict(status_counts=status, funded_addons=len(reentries),
                target_net_profit_usdt=float(reentries.target_net_profit_usdt.sum()),
                runner_stop_loss_reserved_usdt=float(reentries.runner_stop_loss_usdt.sum()),
                available_profit_budget_usdt=float(reentries.profit_budget_usdt.sum()),
                realized_stop_risk_used_usdt=float(reentries.stop_risk_used_usdt.sum()),
                max_budget_overrun_usdt=float(overrun.max()))


def _metrics_rows(label: str, metrics: dict) -> list[dict]:
    return [dict(policy=label, period=period, **values)
            for period, values in metrics.items()]


def _score_positions(positions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Keep account cash untouched; score tiny fee-net parent residuals as flat."""
    score = positions.copy()
    raw_cash = pd.to_numeric(score.cash_pnl, errors='raise').astype(float)
    raw_return = pd.to_numeric(score.net_return, errors='raise').astype(float)
    neutral = raw_cash.abs().le(NEUTRAL_CASH_PNL_EPS)
    scored_cash = raw_cash.mask(neutral, 0.)
    scored_return = raw_return.mask(neutral, 0.)
    score['cash_pnl'] = scored_cash
    score['net_return'] = scored_return

    keys = [column for column in ('event_id', 'parent_id', 'symbol', 'side',
                                   'entry_ms', 'exit_bar_ms', 'position_type')
            if column in positions]
    audit = positions[keys].copy()
    audit['cash_pnl_raw_usdt'] = raw_cash.to_numpy()
    audit['net_return_raw'] = raw_return.to_numpy()
    audit['cash_pnl_scored_usdt'] = scored_cash.to_numpy()
    audit['net_return_scored'] = scored_return.to_numpy()
    audit['neutralized_as_flat'] = neutral.to_numpy()
    classification = dict(trades=int(len(score)),
                          raw_positive_gt_zero=int((raw_cash > 0.).sum()),
                          raw_negative_lt_zero=int((raw_cash < 0.).sum()),
                          positive=int((scored_cash > 0.).sum()),
                          negative=int((scored_cash < 0.).sum()),
                          neutral=int(neutral.sum()))
    return score, audit, classification


def _score_metrics(positions: pd.DataFrame, equity: pd.DataFrame) -> tuple[dict, pd.DataFrame, dict]:
    score, audit, all_counts = _score_positions(positions)
    start = equity.index[0] - pd.Timedelta(hours=1)
    end = equity.index[-1]
    period_counts = {}
    for period, (lo, hi) in PERIODS.items():
        low = pd.Timestamp(lo) if lo else start
        high = pd.Timestamp(hi) if hi else end
        low_ms, high_ms = low.value // 1_000_000, high.value // 1_000_000
        period_fills = score[(score.entry_ms >= low_ms) & (score.exit_bar_ms < high_ms)]
        pnl = period_fills.cash_pnl
        period_counts[period] = dict(trades=int(len(period_fills)),
                                     positive=int((pnl > 0.).sum()),
                                     negative=int((pnl < 0.).sum()),
                                     neutral=int((pnl == 0.).sum()))
    metrics = {period: wallet.summarize(score, equity, *bounds)
               for period, bounds in PERIODS.items()}
    return metrics, audit, dict(all=all_counts, by_period=period_counts)


def _read_hourly(path: Path) -> pd.DataFrame:
    equity = pd.read_csv(path)
    if 'hour_end_utc' not in equity:
        raise ValueError(f'Hourly NAV file lacks hour_end_utc: {path}')
    equity.index = pd.to_datetime(equity.pop('hour_end_utc'), utc=True)
    if not equity.index.is_unique or not equity.index.is_monotonic_increasing:
        raise ValueError('Saved hourly NAV index is not unique and ordered')
    required = {'nav', 'open_nav', 'open_exit_nav', 'lower_nav', 'upper_nav'}
    if not required.issubset(equity.columns):
        raise ValueError(f'Saved hourly NAV lacks required fields: {required-set(equity.columns)}')
    return equity


def _rescore_existing(*, write: bool) -> dict:
    """Rescore saved parent and hourly ledgers without running any account order."""
    funded_path = EXP / 'funded.csv.gz'
    hourly_path = EXP / 'hourly.csv.gz'
    if not funded_path.is_file() or not hourly_path.is_file():
        raise FileNotFoundError(f'Rescore needs existing funded and hourly ledgers in {EXP}')
    positions = pd.read_csv(funded_path, float_precision='round_trip')
    equity = _read_hourly(hourly_path)
    metrics, audit, counts = _score_metrics(positions, equity)
    all_counts = counts['all']
    if all_counts['trades'] == 1569:
        expected = dict(positive=414, negative=1049, neutral=106)
        observed = {key: all_counts[key] for key in expected}
        if observed != expected:
            raise ValueError(f'Frozen full-period parent classification changed: {observed}')
    if len(positions) and not np.isclose(metrics['all']['final_usdt'],
                                          float(equity.nav.iloc[-1]), rtol=1e-12, atol=1e-6):
        raise ValueError('Scoring changed saved account NAV')

    prior_metrics_path = EXP / 'account_metrics.csv'
    raw_saved_metrics = []
    if prior_metrics_path.is_file():
        prior = pd.read_csv(prior_metrics_path)
        raw_saved_metrics = prior[prior.policy.eq('profit_only_reinvested')].to_dict('records')
    summary = dict(experiment_id='exp-cloud-close-profit-budget-add-20261109-v1',
                   scoring='Neutralize parent cash PnL with absolute value <= 1e-8 USDT; set the matching net_return to zero for metrics only.',
                   source_files=dict(funded=str(funded_path), hourly=str(hourly_path)),
                   source_rows=dict(funded=len(positions), hourly=len(equity)),
                   neutralization_epsilon_usdt=NEUTRAL_CASH_PNL_EPS,
                   parent_classification=counts,
                   parent_metrics=metrics,
                   original_metrics_as_saved=raw_saved_metrics,
                   raw_account_cash_and_hourly_nav_preserved=True)
    if write:
        pd.DataFrame(_metrics_rows('profit_only_reinvested_neutralized', metrics)).to_csv(
            EXP / 'account_metrics_neutralized.csv', index=False)
        audit.to_csv(EXP / 'funded_score_audit_neutralized.csv.gz', index=False)
        (EXP / 'summary_neutralized.json').write_text(
            json.dumps(summary, indent=2, allow_nan=False) + '\n')
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='Run bounded long/short mini checks only')
    parser.add_argument('--rescore-existing', action='store_true',
                        help='Rescore saved funded/hourly ledgers without replaying an account')
    args = parser.parse_args()
    if args.smoke:
        print(json.dumps(smoke(), indent=2, allow_nan=False))
        return
    if args.rescore_existing:
        print(json.dumps(_rescore_existing(write=True), indent=2, allow_nan=False))
        return

    paths, opportunities, frozen = adapter.read_inputs()
    _, no_add_positions, _, no_add_equity, no_add_meta, no_add_selected = adapter.simulate(
        paths, opportunities.iloc[0:0], reinvest=False)
    if set(no_add_selected.event_id) != set(frozen.event_id):
        raise ValueError('Zero-add original event set differs from the frozen funded ledger')
    baseline_check = adapter._baseline_reconciliation(no_add_positions, frozen)
    hourly_check = adapter._hourly_reconciliation(no_add_equity, HOURLY_BASELINE)
    if (baseline_check['max_abs_cash_pnl_diff'] > 1e-6
            or baseline_check['max_abs_notional_diff'] > 1e-6
            or any(baseline_check['identity_mismatches'].values())
            or max(hourly_check['max_abs_by_field'].values()) > 1e-6):
        raise ValueError(f'Zero-add baseline reconciliation failed: {baseline_check} {hourly_check}')
    no_add_metrics, _, no_add_classification = _score_metrics(no_add_positions, no_add_equity)
    rows = _metrics_rows('zero_add_replay', no_add_metrics)
    del no_add_positions, no_add_equity, no_add_selected
    gc.collect()

    _, positions, reentries, equity, meta, selected = adapter.simulate(
        paths, opportunities, profit_only_budget=True)
    if set(selected.event_id) != set(frozen.event_id):
        raise ValueError('Profit-budget account changed the frozen original event set')
    metrics, score_audit, classification = _score_metrics(positions, equity)
    rows.extend(_metrics_rows('profit_only_reinvested', metrics))
    dev = metrics['dev']
    dev_pass = bool(wallet.qualifies(dev) and dev['payoff_usdt'] >= 2.5 and dev['trades'] >= 100)
    checks = {}
    if dev_pass:
        all_paths, all_opportunities, _ = adapter.read_inputs()
        control_paths = thirds.okx_paths()
        control_paths = control_paths[control_paths.is_control & control_paths.tp_r.isin([0., 2.])
                                      & control_paths.parent_id.isin(all_paths.parent_id)].copy()
        control_parent_ids = set(control_paths.loc[control_paths.tp_r.eq(0.), 'parent_id'])
        actual_paths = all_paths[all_paths.parent_id.isin(control_parent_ids)]
        raw_opps = pd.read_csv(OPPORTUNITIES, float_precision='round_trip',
                               dtype={'symbol': str}, keep_default_na=False)
        raw_opps = raw_opps[raw_opps.is_control].copy()
        for label, event_paths, event_opps, slip in (
                ('paired_actual', actual_paths, all_opportunities, 0.),
                ('matched_control', control_paths, raw_opps, 0.),
                ('stress_5bp', all_paths, all_opportunities, .0005)):
            _, check_positions, addon_subset, check_equity, check_meta, _ = adapter.simulate(
                event_paths, event_opps, slip=slip, profit_only_budget=True)
            values, _, check_classification = _score_metrics(check_positions, check_equity)
            checks[label] = dict(metrics=values, account=check_meta,
                                 parent_classification=check_classification,
                                 addon_metrics=adapter._reentry_trade_metrics(addon_subset),
                                 earned_profit_risk=_risk_summary(check_positions, addon_subset))
            rows.extend(_metrics_rows(label, values))
            gc.collect()

    EXP.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(EXP / 'account_metrics.csv', index=False)
    positions.to_csv(EXP / 'funded.csv.gz', index=False)
    score_audit.to_csv(EXP / 'funded_score_audit.csv.gz', index=False)
    reentries.to_csv(EXP / 'reinvested.csv.gz', index=False)
    equity.to_csv(EXP / 'hourly.csv.gz', index_label='hour_end_utc')
    summary = dict(experiment_id='exp-cloud-close-profit-budget-add-20261109-v1',
                   baseline_reconciliation=baseline_check,
                   baseline_hourly_reconciliation=hourly_check,
                   zero_add_account=no_add_meta,
                   zero_add_parent_classification=no_add_classification,
                   original_event_set_matches_frozen=len(selected) == len(frozen),
                   development_pass=dev_pass, parent_level_metrics=metrics,
                   parent_classification=classification,
                   addon_leg_metrics=adapter._reentry_trade_metrics(reentries),
                   earned_profit_risk=_risk_summary(positions, reentries),
                   account=meta, checks=checks)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(pd.DataFrame(rows)[lambda frame: frame.period.eq('all')].to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
