# V8 validation report

## Overall assessment: share with caveats; reject profitability claim

Study builder/config/plan/tests committed as e460539 before first V8 prices.
The sole real-price run completed with `rejected_development_no_audit`; source
receipt materialized219551 development rows through2024-12-31T23:55Z, not2025+
prices. The physical archive hash is a provenance check, not a2025 audit run.
Diagnostic builder061f254 reads saved results only. No outcomes were discarded.

## Independent checks

- Full old5m saved-column parity against V7: case/control trades, both request
  ledgers, all286matching rows including3unmatched, and959serialzones. Exact UTC
  timestamps (1ns changes fail), 1e-12 float serialization tolerance. Only old
  empty string/CSV null normalization; new mg diagnostics do not replace oldltf.
- Both arms'286cases+849controls have identical IDs/direction/entry time/price,
  K1stop/ATR/risk. All2270gross/net/netR equations and72h horizons verified.
- Native15m flip exits200case/680control: previous management open+15m=current
  open; currentopen+15m=available=exit. Fixed hardstops86case/169control;50/124
  occur away from15m boundaries. Slow management did not slow the raw5 risk loop.
- Pairing:283same3controlgroups, no reassignment, sameparent IDs;3unmatched I
  unknown, all286caseD finite. D=-2.118717bp,I=-3.077437bp; all959serialdelta
  +1.204571bp. MeanDelta does not itself establish statistical significance.
- An independent loop (not calling single_pending_ledger) reproduced every
  selection from fold/time/occupied_until. Native15m selects908zones/262trades;
 51skipped=7expired+20weakrelease+24requests. The24independent newtrade outcomes
  contain20losses/4wins; do not treat its chosen subset as original286improvement.
- L3 regression and independent context tests cover0/+5/+10 entry phases,
  initialalignment/opposition/unknown, gaps/segments, futureprefix stability,
  exact management availability and hardstoppriority. Test count is recorded
  in DELIVERY.md after the report-builder tests are included.

## Re-runnable saved-ledger checks (no price read)

```python
import json
import numpy as np
import pandas as pd
from pathlib import Path
from yoyo.evaluation.hourly_impulse_management_research import assert_saved_parity, paired_effects
from yoyo.evaluation.hourly_impulse_transition_research import read_frame

p=Path('experiments/active/exp-btcusdtp-1h-management-spec-preholdout-20260906-v8/results')
old=p.parent.parent/'exp-btcusdtp-1h-frozen-source-preholdout-20260906-v7/results'
s=json.loads((p/'summary.json').read_text())
arms={}
for minutes in (5,15):
    d=p/f'{minutes}m_native40'
    arms[minutes]={}
    for label in ('case','control'):
        t=read_frame(d/f'{label}_trades.csv.gz')
        assert t.closed.all() and np.isfinite(t.net_return).all()
        np.testing.assert_allclose(t.gross_return,t.direction*(t.exit_price/t.entry_price-1),rtol=1e-10,atol=1e-12)
        np.testing.assert_allclose(t.net_return,t.gross_return-.002,rtol=1e-10,atol=1e-12)
        np.testing.assert_allclose(t.net_r,t.net_return/t.risk_pct,rtol=1e-10,atol=1e-12)
        assert (t.exit_time>=t.entry_time).all() and (t.exit_time<=t.entry_time+pd.Timedelta(hours=72)).all()
        if minutes==5:
            assert_saved_parity(read_frame(old/f'{label}_trades.csv.gz'),t)
        else:
            cols=['event_id','entry_time','entry_price','initial_stop','signal_atr','risk_pct','risk_atr','direction']
            assert_saved_parity(arms[5][label][cols],t[cols])
            flip=t.loc[t.outcome.eq('transition_colour_exit')]
            assert (flip.transition_trigger_previous_open_time+pd.Timedelta(minutes=15)==flip.transition_trigger_open_time).all()
            assert (flip.transition_trigger_open_time+pd.Timedelta(minutes=15)==pd.to_datetime(flip.transition_trigger_available_at,utc=True)).all()
            assert (pd.to_datetime(flip.transition_trigger_available_at,utc=True)==flip.exit_time).all()
            stop=t.loc[t.outcome.eq('hard_stop')]
            np.testing.assert_allclose(stop.exit_price,stop.initial_stop,rtol=0,atol=1e-12)
        arms[minutes][label]=t
    arms[minutes]['episodes']=read_frame(d/'case_request_outcomes.csv.gz')
    arms[minutes]['pairs']=read_frame(d/'matched_request_outcomes.csv')
    serial=read_frame(d/'single_pending_zone_ledger.csv.gz')
    serial['occupied_until']=pd.to_datetime(serial.occupied_until,utc=True)
    chosen=set()
    for _,part in serial.groupby('fold'):
        free=pd.Timestamp.min.tz_localize('UTC')
        for row in part.sort_values(['mother_decision_time','event_id']).itertuples():
            if row.mother_decision_time>=free:
                chosen.add(row.event_id)
                free=row.occupied_until.ceil('5min')
    assert chosen==set(serial.loc[serial.portfolio_selected,'event_id'])
    arms[minutes]['serial']=serial
a,b=arms[5],arms[15]
frames,effects=paired_effects(a['episodes'],b['episodes'],a['pairs'],b['pairs'],a['serial'],b['serial'])
assert [effects[k]['n'] for k in ('case_delta','excess_delta','serial_delta')]==[286,283,959]
for k,f in frames.items():
    assert_saved_parity(read_frame(p/(k+'.csv')),f)
    np.testing.assert_allclose(effects[k]['mean_bp'],s['effects'][k]['mean_bp'],atol=1e-12)
print('PASS: 2270 formulas/clocks, unchanged entries, fixed286/283/959 pairings and independent serial loop')
```

## Required caveats

Ledger audit verifies saved clock chains and computation, not an independent
second reconstruction of every original OHLC first-flip/collision. L3 has
synthetic first-event/priority tests, and5m historical regression passed. Do not
claim an additional independent price replay or forward confirmation.

The treatment bundles aggregation, colour state and3h20m→10h memory, not pure
observation cadence. Initial15m state differs between cases282aligned/4opposite
and controls640/209. Controls intentionally keep original5m matching; no new
15mstate balancing. Four folds,24months and286requests are reused development.
Monthly block CI/signflip is approximate under cross-month dependence and
repeated research. Rawreturns andpaired differences are heavy-tailed; no
outlier trimming or normality-based success inference. Funding/depth/variable
slippage omitted;20bp fixed contract is not a claim of exact live costs.

## Report QA boundary

Standalone V8 report preserves the old complete V1–V7 artifacts byte-for-byte.
One native binned-count distribution, all286requests including zero/unknown
categories, explicit unequal/open intervals, no density implication or tail
trimming. Full narrative/definitions/rules/folds/controls/serial/risks retained.
Official portable packaging receipt and final hashes recorded in DELIVERY.md;
structural_only must not be described as browser/mobile/light-dark visual QA.
