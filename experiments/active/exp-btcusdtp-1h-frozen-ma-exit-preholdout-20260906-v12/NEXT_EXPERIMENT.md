# Next question after V12 — prior4h colour only

Status: deduplicated proposal, NOT implemented, registered as a new experiment,
run, selected or accepted. V12 goal turn made actual progress and rejected its
single additional exit; profitability objective is still active and unachieved.

## Evidence and non-duplication

- V12:20 additional exits are all old losses, but10 improve/10 worsen. All251
  D+0.3573bp, CI crosses0; retained231 net-8.625bp. Exit-only repair too small.
- V1 `results/selection.json` has `require_ma_slope=true`,111 trades net-6.9918bp
  with old `exit_mode=colour`; not a new idea to repeat hourly slope.
- V3 `config.json`, `hourly_impulse_context_matching.py:arm_mask` jointly require
  context side AND signed slope>0, with common-valid support248, not all251.
  Its prior4h_trend net-16.559bp is not evidence for colour-only on V5 trueflip.
- `yoyo/data/hourly_impulse_context.py:add_prior_4h_context` calls context valid
  only if MA,ATR and3-bar slope are finite. That is an extra warmup/availability
  condition beyond a pure40-bar SMA/HL2-side condition. Do not silently inherit it.

Sources are bounded V1--V12 code/config/record checks, not proof that no related
method exists elsewhere. No raw prices were loaded during this deduplication.

## Smallest independent research proposal

Keep original251 case mothers/462 controls/154 triples and V5 native5m SMA40
true aligned-to-opposite transition exit, K1 stop,72h and20bp. Add only:
direction equals the latest fully completed4h HL2>=SMA40 side available at K1
OPEN. Equality uses original side convention. Do not include the developing
4h bar, K1's future close, hourly/4h slope, ATR strength or extension threshold.

Compute colour validity from40 complete contiguous4h bars and finite MA/HL2;
verify source continuity from context availability through K1 OPEN and age<4h.
Do not require the optional slope or ATR warmup merely to identify colour.
Preserve every request. Known opposite rejects entry and earns zero under this
policy; unknown context remains unknown, not a profitable abstention. Apply each
control's OWN entry-time gate; never transfer the case gate or rematch outcomes.

Before reading arm results save all713 context/gate rows and hashes. Require
original baseline all-field parity. Evaluate full251 opportunity-normalized D,
same154 fixed-group I with97 unknown, plus selected-trade economics/counts and
zero/unknown rates for both cases and controls. Do not report only the retained
winning pool. Preserve single-position semantics and verify whether occupancy
can change; baseline currently has no skips but that is evidence to check.

This does not solve old61.35% matching support or create fresh independent data.
Reused development remains exploratory. First write a separate experiment plan,
configuration, tests and register/commit its builder before any new price run.
No grid search, V12 boundary add-on, holdout, live/TradingView or production change.
