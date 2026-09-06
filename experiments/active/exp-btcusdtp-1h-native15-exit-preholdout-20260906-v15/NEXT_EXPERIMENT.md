# Next hypothesis — event-based partial realization, not slower full liquidation

Status: bounded proposal only; no next runner/config/registry or outcomes yet.
Do not call V15 profitable or repeat its native5/15 comparison. Goal staysactive.

## Evidence driving this question

V15 native15 on251:19 oldloss→win but17 oldwin→loss;50 new hard stops outweigh
187 improved average colour paths.187 candidate losses include53 heldMFE≥1R,
134below1R. Old low-MFE paths sometimes later trend; MFE is never an entry gate
or an executable fill.219 initiallynative15 aligned requests still netnegative.

## Proposed single event-policy addition

Keep V15 native15 current full cohort, entry/K1 extreme/72h/20bp/5m risk clock.
First qualifying completed native5 true aligned→opposite transition while the
latest fully completed native15 observation remains aligned: if actual next
executable5mopen full-notional directional grossreturn strictly exceeds20bp,
realize50% of original notional. Remaining50% continues unmodified native15
trueflip/K1stop. No repeated partials, no stop move, no R or MA-length grid,
no new initial-state or4h/20h gate. This tests one partial-realization mechanism.

Before implementation settle exact conflict priority: source gaps and hard-stop
open precede any discretionary partial; simultaneous native15 full-exit takes
precedence; unfinished native bars invalid; current5mopen is known at action,
its high/low/close cannot select fills. Seed/reset trueflip causally for both
streams; at most one partial. Persist all intermediate triggers and both states.
Costs stay proportional total exit notional (0.5+0.5=1), and30bp stress retained;
real fixed ticket fees/latency remain limitations. A profitable partial does NOT
ensure positive full-trade net if remainder loses. No syntheticMFE cashout.

## Dedup and denominator requirements

Bounded prior source review: V1 partial_colour exits half on native15 opposite
state then second confirmation; V2 uses slope111 cohort staged1R/2R with1h
takeover; neither is this fixed251 dual-clock trueflip profitable partial.
Check exact code/spec before calling it new. No broad global novelty claim.

Preregister before outcomes, commitbuilder before price run. Freeze both own
states and original251/462/154/97; require unchanged native15 baseline all-field
parity to V15 candidate before new partial outcomes. Main D/I compare against
that same15m anchor; retain original5m contextual reference without selecting
best of three. Recompute whole251-intention serial occupancy. All failures,
winner sacrifices, fees and partial marks remain visible. Same strict financial
gates; matching61.35% still prevents final acceptance. No fresh holdout claim,
TV/deployment/training/live changes. If fails, do not mutatethreshold to rescue.
