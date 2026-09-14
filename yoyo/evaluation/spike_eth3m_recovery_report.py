"""Complete matched diagnostics and audit frozen recovery research deliveries.

This never selects parameters. It adds the fixed-1U comparator for the already
frozen best escalation exit, then evaluates all displayed arms against the
same-asset/month/side/prior-volatility random-entry diagnostic. New output is
separate from the immutable development and evaluation files.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_eth3m_recovery_study import (
    ROOT, EXP, CONFIG, RESULTS, sha, save, receipt, load_context, bounds,
    opportunities, policy_id, account, matched_control,
)
from yoyo.evaluation.spike_recovery_exit import prepare


def audit(ledger, cycles, summary):
    accepted = ledger[ledger.accepted]
    np.testing.assert_allclose(1000 + accepted.pnl.sum(), summary['final_balance'], atol=1e-8)
    np.testing.assert_allclose(cycles.net_pnl.sum(), accepted.pnl.sum(), atol=1e-8)
    np.testing.assert_allclose(accepted.equity_after, accepted.equity_before + accepted.pnl, atol=1e-8)
    natural = accepted[~accepted.censored.astype(bool)]
    run = maximum = 0
    for r in natural.net_r:
        run = run + 1 if r < -1e-9 else 0
        maximum = max(maximum, run)
    assert maximum == summary['max_consecutive_net_loss']
    assert len(natural) == summary['n_natural']
    assert summary['n_candidates'] == summary['n_accepted'] + summary['n_blocked'] + summary['n_rejected_capacity'] + summary['n_rejected_ruined']
    return dict(natural=len(natural), ledger_cash_audited=True, cycle_cash_audited=True,
                streak_audited=True, candidate_accounting_audited=True)


def main():
    source = Path(__file__)
    committed = subprocess.check_output(['git', 'show', 'HEAD:' + str(source.relative_to(ROOT))], cwd=ROOT)
    import hashlib
    if hashlib.sha256(committed).hexdigest() != sha(source):
        raise ValueError('commit the diagnostic builder before running')
    cfg = json.loads(CONFIG.read_text())
    frozen = json.loads((RESULTS / 'selection.json').read_text())
    if frozen['builder_sha256'] != receipt()['builder_sha256']:
        raise ValueError('frozen study source drift')
    evaluation = RESULTS / 'evaluation'
    er = json.loads((evaluation / 'receipt.json').read_text())
    if sha(evaluation / 'summary.csv') != er['summary_sha256']:
        raise ValueError('evaluation summary drift')
    out = RESULTS / 'diagnostics'
    out.mkdir(exist_ok=False)
    summaries = pd.read_csv(evaluation / 'summary.csv').to_dict('records')
    controls = pd.read_csv(evaluation / 'controls.csv').to_dict('records')
    already = {(r['period'], r['name']) for r in controls}
    audits, extra = [], []
    ctx = load_context(cfg)
    for period in cfg['periods']:
        frame, raw, indices = bounds(ctx, cfg, period)
        prepared = prepare(frame, raw)
        name = 'best_escalation_exit_fixed_1u'
        policy = dict(exit=frozen['policies']['best_escalation']['exit'], cash=dict(schedule='fixed', factor=1., reset_mode='recovery'))
        result = account(opportunities(prepared, indices, policy['exit']), policy)
        tag = period + '_' + name
        pd.DataFrame(result['ledger']).to_csv(out / (tag + '_ledger.csv.gz'), index=False)
        pd.DataFrame(result['cycles']).to_csv(out / (tag + '_cycles.csv'), index=False)
        summaries.append(dict(period=period, name=name, policy_id=policy_id(policy), **result['summary']))
        for summary in [r for r in summaries if r['period'] == period]:
            name = summary['name']
            tag = period + '_' + name
            source_dir = out if name == 'best_escalation_exit_fixed_1u' else evaluation
            ledger = pd.read_csv(source_dir / (tag + '_ledger.csv.gz'))
            cycles = pd.read_csv(source_dir / (tag + '_cycles.csv'))
            audits.append(dict(period=period, name=name, **audit(ledger, cycles, summary)))
            natural = ledger[ledger.accepted & ~ledger.censored.fillna(True).astype(bool)]
            exit_policy = policy['exit'] if name == 'best_escalation_exit_fixed_1u' else er['policies'][name]['exit']
            if (period, name) not in already:
                detail, control = matched_control(frame, prepared, natural.to_dict('records'), exit_policy, cfg)
                detail.to_csv(out / (tag + '_matched.csv.gz'), index=False)
                controls.append(dict(period=period, name=name, **control))
            cost_r = natural.gross_r - natural.net_r
            # Price-level zero PnL does not terminate a net-loss run.
            zero_price = natural.gross_r.abs() < 1e-8
            extra.append(dict(period=period, name=name, mean_net_r=float(natural.net_r.mean()),
                median_cost_r=float(cost_r.median()), median_initial_stop_pct=float((natural.risk/natural.notional).median()*100),
                price_be_exits=int(zero_price.sum()), price_be_net_losses=int((zero_price & natural.net_r.lt(0)).sum()),
                net_3r=int(natural.net_r.ge(3-1e-9).sum()), gross_3r=int(natural.gross_r.ge(3-1e-9).sum()),
                tp_exits=int(natural.reason.str.startswith('take_profit').sum()),
                losing_cycles=int((cycles.net_pnl.lt(-1e-9) & ~cycles.finish_reason.isin(['unfinished','boundary'])).sum())))
            print('audited/matched', tag, flush=True)
    pd.DataFrame(summaries).to_csv(out / 'summary.csv', index=False)
    pd.DataFrame(controls).to_csv(out / 'controls.csv', index=False)
    pd.DataFrame(extra).to_csv(out / 'trade_diagnostics.csv', index=False)
    save(out / 'audit.json', dict(**receipt(), diagnostic_builder_sha256=sha(source), audit=audits,
        added_comparator='Post-selection fixed-1U comparator for frozen best escalation exits; never used to select parameters.',
        summary_sha256=sha(out / 'summary.csv'), controls_sha256=sha(out / 'controls.csv')))


if __name__ == '__main__':
    main()
