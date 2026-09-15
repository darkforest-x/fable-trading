"""Synthetic accounting and frozen selection checks; never read market data."""
from dataclasses import asdict
import copy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import bb_stoch_optimization as opt
from yoyo.evaluation.bb_stoch_parameter_replay import ParamSpec


def test_partial_censor_reserves_remaining_exit_fee():
    frame = pd.DataFrame({'close': [100., 110., 90.]})
    row = dict(entry_i=0, exit_i=2, entry_price=100., side=1, censored=True,
               exit_price=90., net_pnl=-.155, fills=[
                   dict(i=0, qty=1., price=100.), dict(i=1, qty=.5, price=110.)])
    curve, metrics = opt.liquidation_curve(frame, [row], 0, 1000.)
    np.testing.assert_allclose(curve, [1000., 999.8, 1009.79, 999.8])
    assert metrics['net_liquidation_mark_usdt'] == pytest.approx(-.2)
    assert metrics['close_mtm_drawdown_usdt'] == pytest.approx(9.99)


def test_closed_short_curve_realizes_each_fill_at_its_bar():
    frame = pd.DataFrame({'close': [100., 90., 100., 130.]})
    row = dict(entry_i=0, exit_i=2, entry_price=100., side=-1, censored=False,
               exit_price=100., net_pnl=4.805, fills=[
                   dict(i=0, qty=1., price=100.), dict(i=1, qty=.5, price=90.),
                   dict(i=2, qty=.5, price=100.)])
    curve, metrics = opt.liquidation_curve(frame, [row], 0, 1000.)
    np.testing.assert_allclose(curve, [1000., 999.8, 1009.81, 1004.805, 1004.805])
    assert metrics['net_liquidation_mark_usdt'] == pytest.approx(4.805)


def test_grid_selector_uses_cash_neighborhood_and_trade_floor():
    cfg = dict(bb_lengths=[100, 150, 200, 300, 400], bb_multiples=[2.],
               stop_fractions=[.03], development_start='2026-01-01', development_end='2026-03-01',
               minimum_natural_trades=20, minimum_trades_each_month=5,
               minimum_eligible_neighbors_including_self=3)
    results = {}
    for n, cash in zip(cfg['bb_lengths'], [1000., -100., 200., 210., 190.]):
        spec = ParamSpec(bb_length=n)
        results[opt.identity(spec)] = dict(params=asdict(spec), modes={
            mode: dict(stats=dict(natural=20, net_r=-cash, month_natural_counts={'2026-01':10,'2026-02':10}),
                       equity=dict(net_liquidation_mark_usdt=cash, close_mtm_drawdown_usdt=50.))
            for mode in ['tv', 'adverse_first']})
    chosen = opt.select_candidates(results, cfg)
    assert chosen['peak'] == 'bb100_m2_sl3'
    assert chosen['primary'] == 'bb300_m2_sl3'
    assert chosen['finalists'] == ['bb200_m2_sl3', 'bb300_m2_sl3', 'bb100_m2_sl3']
    worse = copy.deepcopy(results)
    worse['bb100_m2_sl3']['modes']['tv']['stats']['month_natural_counts']['2026-02'] = 0
    assert opt.select_candidates(worse, cfg)['peak'] == 'bb300_m2_sl3'


def test_freeze_failure_prevents_price_read(monkeypatch):
    called = []
    def reject(*args):
        raise ValueError('Uncommitted selection')
    monkeypatch.setattr(opt, 'code_receipt', reject)
    monkeypatch.setattr(opt, 'read_prefix', lambda *args: called.append(args))
    with pytest.raises(ValueError, match='Uncommitted selection'):
        opt.main('recheck')
    assert called == []


@pytest.mark.parametrize('stage,marker', [('development','selection.json'),('development','recheck_code_receipt.json'),('recheck','recheck_code_receipt.json')])
def test_recorded_stages_fail_before_price_read(monkeypatch, tmp_path, stage, marker):
    config = json.loads((opt.EXP/'config.json').read_text())
    (tmp_path/'config.json').write_text(json.dumps(config))
    (tmp_path/marker).write_text('{}')
    monkeypatch.setattr(opt, 'EXP', tmp_path)
    monkeypatch.setattr(opt, 'read_prefix', lambda *args: pytest.fail('price reader reached'))
    with pytest.raises(ValueError, match='One-shot'):
        opt.main(stage)


def test_censored_control_kept_and_pair_not_redrawn():
    real = dict(signal_i=1, month='2026-01', net_pnl=1., entry_price=100., censored=False)
    controls = [dict(actual_signal_i=1, net_pnl=0., entry_price=100., censored=j==0) for j in range(5)]
    out = opt.control_summary([real], controls, dict(controls_per_trade=5))
    assert (out['drawn'], out['censored'], out['paired_n'], out['unpaired_n']) == (5, 1, 0, 1)
    assert out['all_boundary_mark_excess_bp'] == 100.
    assert out['p'] is None
