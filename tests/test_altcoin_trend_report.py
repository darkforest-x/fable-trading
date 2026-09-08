"""Evidence contract checks for the descriptive report, not return fitting."""
import pandas as pd
import pytest
import json
from yoyo.evaluation.altcoin_trend_report import case_exit_rows, side_rows, volatility_retention, verify_phase, digest, table


def test_report_tables_need_no_optional_package_and_escape_cells():
    result=table(pd.DataFrame({'name':['a|b\n<c>'],'net':[1.234]}),{'name':'名称','net':'净bp'})
    assert '| 名称 | 净bp |' in result
    assert 'a&#124;b<br>&lt;c&gt;' in result and '1.234' in result


def event(arm,**extra):
    row=dict(symbol='SOPH',minutes=240,fold='recent_test',signal_i=800,side=1,
             cohort='owner_illustration',arm=arm,entry_price=100.,initial_risk=10.,
             censored=False,decision_close_time='2026-08-01T04:00:00Z',
             portfolio_selected=True,net_bp=50.,net_r=.05,mfe_r=1.)
    row.update(extra);return row


def example():
    return dict(symbol='SOPH',minutes=240,fold='recent_test',signal_i=800,side=1,
                roles=['SOPH 240m · 本组净R最高'])


def test_exit_comparison_distinguishes_marked_open_trade_from_rule_exit():
    rows=[event('base',censored=True),event('exit_fixed3r'),event('signal5')]
    result=case_exit_rows(pd.DataFrame(rows),[example()])
    assert set(result.arm)=={'base','exit_fixed3r'}
    assert '尚非自然退出' in result.loc[result.arm.eq('base'),'状态'].item()
    assert result.loc[result.arm.eq('exit_fixed3r'),'状态'].item()=='规则已退出'


@pytest.mark.parametrize('changed', [{'entry_price':101.},{'initial_risk':11.}])
def test_exit_comparison_rejects_unequal_starting_risk(changed):
    with pytest.raises(ValueError,match='same entry'):
        case_exit_rows(pd.DataFrame([event('base'),event('exit_sma60',**changed)]),[example()])


def test_direction_diagnostics_use_original_portfolio_selection_only():
    rows=[event('base',cohort='high_vol',net_bp=30.),
          event('base',cohort='high_vol',net_bp=1000.,portfolio_selected=False),
          event('base',cohort='high_vol',side=-1,net_bp=-40.),
          event('exit_sma60',cohort='high_vol',net_bp=2000.)]
    result=side_rows(pd.DataFrame(rows))
    assert set(result.mean_net_bp)=={-40.,30.}
    assert result.n.sum()==2


def test_completed_report_inputs_reject_stale_summary_and_selection(tmp_path):
    summary=tmp_path/'summary.csv';summary.write_text('n\n3\n')
    lock=tmp_path/'selection.json';lock.write_text('{"version":2}')
    (tmp_path/'completion.json').write_text(json.dumps(dict(summary_sha256=digest(summary))))
    (tmp_path/'run_manifest.json').write_text(json.dumps(dict(identity=dict(selection_sha256=digest(lock)))))
    verify_phase(tmp_path,lock)
    lock.write_text('{"version":1}')
    with pytest.raises(ValueError,match='another selection'):verify_phase(tmp_path,lock)
    summary.write_text('n\n4\n')
    with pytest.raises(ValueError,match='summary changed'):verify_phase(tmp_path)


def test_tail_retention_is_same_event_subset_and_excludes_owner_examples():
    rows=[event('base',symbol='ALT',cohort='all_core',net_r=8.,censored=True),
          event('base',symbol='ALT',cohort='all_core',signal_i=900,net_r=-1.),
          event('base',symbol='ALT',cohort='high_vol',signal_i=900,net_r=-1.),
          event('base',symbol='SOPH',net_r=99.)]
    r=volatility_retention(pd.DataFrame(rows)).iloc[0]
    assert (r.n_all,r.n_kept,r.tail_n,r.tail_kept,r.largest_omitted_r)==(2,1,1,0,8.)
    assert r.tail_censored == 1
    rows[2]['net_r']=2.
    with pytest.raises(ValueError,match='different outcomes'):volatility_retention(pd.DataFrame(rows))
