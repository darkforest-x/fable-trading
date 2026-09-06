# Next hypothesis — reduce exposure after confirmation, do not flatten it

Status: proposed only; not implemented, registered or financially evaluated.
V18 is completed/rejected. Do not rerun it as unfinished or call its slight D
improvement profitable. The owner's broader profitability goal remains active.

## Evidence and bounded novelty

All251 V18 D+0.309162bp, CI[-2.243298,3.235923],p.414,net-16.769005bp.
138 still-confirmed full exits contribute -231.185671 event-bp relative V17;
3 waiting stops -48.865177;11 eventual slow exits+357.650523. Only4 of22
V16 winners cut by V17 recover;18 remain nonpositive. This is retrospective
mechanism evidence, not a causal entry filter or proof a remainder will win.

Read-only source comparison:

- V1 config.json:115, `15m_half`: native15 opposite STATE partial with
  confirmations2; current hourly_impulse.py:902 handles that state branch.
  It is not native5 two-bar confirmation while native15 still aligns.
- V1 already explored crossing count, efficiency and volume grids. Do not
  repeat those factors under a new name and claim novelty.
- V2 config.json:8,14: slope-gated entries and fixed1R/2R realization with
  optional hourly takeover. Different entries and triggers, not this branch.
- V16 config.json:31 and hourly_impulse.py:501: true native5 edge partial only
  when actual-open gross strictly exceeds20bp. No failed-confirm reduction.
- V17 immediate failed full and V18 delayed failed full are now rejected.

Directories for the above config references are respectively
`experiments/active/exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1`,
`experiments/active/exp-btcusdtp-1h-staged-realisation-preholdout-20260906-v2`,
`experiments/active/exp-btcusdtp-1h-dual-partial-preholdout-20260906-v16`.
Engine is `yoyo/layers/l3_backtest/hourly_impulse.py` at cbb25d3; line numbers
refer to that revision, not a promise future lines remain fixed.

## One proposed change

Use exact V18 candidate baseline. Only replace a confirmed failed-economics
100% liquidation with a50% ORIGINAL-notional risk reduction. Remaining50%
keeps original native15 true-colour exit/K1 extreme hard stop/72h deadline.
Retain count2, exact adjacent completed native5 bars, source continuity,
latest completed aligned native15 and actual next raw5 OPEN gross<=20bp.
No additional duration, MA, entry gate, stop multiple or threshold search.

The existing profitable first real-edge50% realization is unchanged. After
ANY partial, no further fast reduction. On second-bar gross>20bp cancel
pending as V18; do not invent an untriggered profitable half. A partial at
nonpositive net is loss realization/risk reduction, not TP. A still-open or
censored remainder does not make total trade PnL known. Costs are weighted
by original notional and sum to unchanged20bp, not twice full cost or free
remainder. Stress30bp changes fees only, not20bp trigger decisions.

This may worsen residual losses. Do not average V16/V18 completed returns:
the early losing half can replace a later profitable fast half, and slow
exit occupancy can change. Do not rerun only the18 lost winners or138 fulls.

## Before any new outcome read

1. Create/register a new experiment under existing repo main, freeze single
   fraction50% without grid, exact new API semantics, all251/462/154triples/
   97unknown and evidence plan; commit builder/config/plan/tests first.
2. Synthetic tests: no-option V18 full parity, pending cancel/priority,
   profitable first half unaffected, confirmed losing half timing and fields,
   cost equality, fast-once-only, gap/intrabar stop, slow priority, deadline,
   missing-source/censored remainder and long/short symmetry.
3. Freeze all own fast/slow contexts before old outcomes. OFF anchor all V18
   old fields across six ledgers. New event paths without confirmed failed
   reduction retain old fields. Real replay both arms/own controls; no
   borrowed future values or inherited serial masks.
4. Keep original denominator and matched unknowns. Show realized half plus
   residual contribution, recovered trends, deeper losses, waiting stops,
   all4half results, absolute/control D/I and monthly uncertainty. Old fully
   failed exits were nonpositive; do not assume portfolio winners unchanged.
5. Same pre2025 prefix only, including earlier warmup. No new2025+ price
   access/holdout, no source downloads, dependency installs, TV write,
   training, production promotion or orders. This proposal does not authorize
   these separate actions. Matching61.35% remains below90%; don't relax it.

Financial acceptance unchanged. Even a positive reused-development outcome
would need a separately frozen time-independent evidence path. No profitability
claim or goal completion until actually supported.
