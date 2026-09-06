# Next candidate: one-hour launch progress deadline

This is an executable-design recommendation after V10, NOT a preregistered or
executed V11. No new outcomes/prices have been read for it. Register a separate
experiment/config, tests and full implementation before running any real replay.
Do not amend V10, whose question is outcome-free matching capacity.

## One hypothesis, no grid

Anchor original251 direct K1s with V5 native5m SMA40 genuine aligned→opposite
transition exits. New candidate only adds a launch-progress deadline:

1. Keep entry time/price/direction, actual K1 extreme stop, fixed initial
   R=direction*(entry−stop),20bp roundtrip,72h max and raw5risk clock unchanged.
2. During the first60min after actual entry, examine completed5m CLOSE values
   only. If any reaches direction*(close−entry)>=0.5R, permanently disable this
   deadline for that trade; all later management is the original V5 policy.
3. At entry+60min, if still open and no such completed close has occurred,
   close fully at the actual raw5 open at that boundary, launch_timeout_exit.
4. First process the just-completed5m close; a qualifying close at exactly60min
   cancels timeout. Already-triggered hard stops, gap-through-stop and pending
   original exits have priority, so never close twice or inspect a stopped path.
5. Missing source or broken clock remains unknown/data failure, never treated
   as no-progress evidence. No reentry/partial/stop move/late barrier optimization.

60min is one signal timeframe;0.5R is a fixed half-risk progress hypothesis,
not an empirically selected optimum. Test exactly one setting. It can kill slow
winners or treat a brief oscillation as progress; it is not guaranteed improvement.
Do not call reaching0.5R proof of trend or a realized profit-taking event.

## Why this is distinct

V2 stage exits act after1R/2R profitability; V5 changed state to trueflip;
V6 waited before entry, changing price; V8/V9 slowed management. This candidate
is an after-entry, before-confirmed-progress time invalidation. Once progress
appears, it does not protect later giveback. Review full historical code first
to ensure this exact rule was not already attempted in another strategy branch.

## Evidence design to freeze before execution

- Byte-pin original251requests, old154triples/462controls and V5 anchor outputs;
  all-field anchor replay must pass before candidate outcomes. Same2023–2024
  development only; no audit2025+/holdout consumption or new training.
- D on every251 original opportunity; unaffected pairs remain exactly zero,
  missing remain unknown. Do not restrict scoring to timeout-affected trades.
- Fixed154 old matched groups, no new V10 maximum allocation. Each control uses
  its own fixed initialR, same deadline rule; I=D_case−mean(D_three_controls).
- Report all fourfold/month results, net levels, paired uncertainty, actual
  lost-winner/saved-loser mechanisms, single-position occupation and same cost
  stress contract. No future MFE or final winner input can select entry/candidate.
- V10 proved strict90%coverage unattainable on this population. Keep that failed
  gate explicit even if D or I improves; a finite mechanism test is not candidate
  acceptance. Any later deployment needs frozen prospective evidence and owner
  authorization, not this reused development period.

Relevant sources: V1–V7 consolidated report, V8/V9 standalone reports;
yoyo/layers/l3_backtest/hourly_impulse.py; V5 experiment
exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5. No implementation yet.
